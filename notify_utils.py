"""Compare CSV baselines and send SMTP emails with chart attachments."""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd

TRACKED_FILES = (
    "presale_approvals_monthly.csv",
    "landreg_primary_monthly.csv",
    "instant_saleable_inventory_monthly.csv",
)

COLUMN_LABELS = {
    "presale_approved_units": "预售批出伙数",
    "primary_units": "一手成交伙数",
    "instant_saleable_inventory": "即时可售货量",
}


@dataclass
class FileChange:
    filename: str
    rows: list[dict] = field(default_factory=list)


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"month": str})
    if "month" in df.columns:
        df = df.sort_values("month").reset_index(drop=True)
    return df


def compare_with_baseline(baseline_dir: Path, current_dir: Path) -> list[FileChange]:
    changes: list[FileChange] = []

    for name in TRACKED_FILES:
        old_df = _read_csv(baseline_dir / name)
        new_df = _read_csv(current_dir / name)
        if old_df.empty and new_df.empty:
            continue
        if old_df.empty:
            changes.append(
                FileChange(filename=name, rows=[{"note": "baseline missing; treated as full update"}])
            )
            continue

        key = "month"
        merged = old_df.merge(new_df, on=key, how="outer", suffixes=("_old", "_new"), indicator=True)
        file_rows: list[dict] = []

        for _, row in merged.iterrows():
            if row["_merge"] == "left_only":
                file_rows.append({"month": row[key], "change": "removed"})
                continue
            if row["_merge"] == "right_only":
                file_rows.append({"month": row[key], "change": "added"})
                continue

            for col in old_df.columns:
                if col == key:
                    continue
                old_v = row.get(f"{col}_old")
                new_v = row.get(f"{col}_new")
                if pd.isna(old_v) and pd.isna(new_v):
                    continue
                try:
                    same = int(old_v) == int(new_v)
                except (TypeError, ValueError):
                    same = str(old_v) == str(new_v)
                if not same:
                    label = COLUMN_LABELS.get(col, col)
                    file_rows.append(
                        {
                            "month": row[key],
                            "field": label,
                            "old": old_v,
                            "new": new_v,
                        }
                    )

        if file_rows:
            changes.append(FileChange(filename=name, rows=file_rows))

    return changes


def sync_baseline(baseline_dir: Path, current_dir: Path) -> None:
    baseline_dir.mkdir(parents=True, exist_ok=True)
    for name in TRACKED_FILES:
        src = current_dir / name
        if src.exists():
            (baseline_dir / name).write_bytes(src.read_bytes())


def _brief_summary(changes: list[FileChange], out_dir: Path) -> tuple[str, str]:
    """简短正文（不罗列 CSV 文件名），供邮件正文使用。"""
    n = sum(len(c.rows) for c in changes)
    inv_path = out_dir / "instant_saleable_inventory_monthly.csv"
    latest_line = ""
    if inv_path.exists():
        inv = _read_csv(inv_path)
        if not inv.empty and "instant_saleable_inventory" in inv.columns:
            last = inv.iloc[-1]
            latest_line = (
                f"最新月份 {last['month']}，即时可售货量 "
                f"{int(last['instant_saleable_inventory']):,} 伙。"
            )

    meta_line = _chart_meta_line(out_dir)
    text = (
        "香港一手短期库存数据已更新。\n\n"
        f"共检测到 {n} 处数值变更。\n"
        f"{latest_line}\n"
        f"{meta_line}\n\n"
        "请查看 GitHub Pages 网页版看板（含图表）。"
    )
    html = (
        "<h2>香港一手短期库存 — 数据已更新</h2>"
        f"<p>共检测到 <b>{n}</b> 处数值变更。</p>"
        f"<p>{latest_line}</p>"
        f"<p><small>{meta_line}</small></p>"
        "<p>请查看 GitHub Pages 网页版看板（含图表）。</p>"
    )
    return text, html


def _chart_meta_line(out_dir: Path) -> str:
    meta_path = out_dir / "report_chart.meta.txt"
    if not meta_path.exists():
        return "图表版本：未生成 meta（可能是旧代码）。"
    return "图表版本：" + meta_path.read_text(encoding="utf-8").strip().replace("\n", " | ")


def _manual_no_change_summary(out_dir: Path) -> tuple[str, str]:
    inv_path = out_dir / "instant_saleable_inventory_monthly.csv"
    latest_line = ""
    if inv_path.exists():
        inv = _read_csv(inv_path)
        if not inv.empty and "instant_saleable_inventory" in inv.columns:
            last = inv.iloc[-1]
            latest_line = (
                f"最新月份 {last['month']}，即时可售货量 "
                f"{int(last['instant_saleable_inventory']):,} 伙。"
            )
    meta_line = _chart_meta_line(out_dir)
    text = (
        "本次为 GitHub 手动运行。\n"
        "数据与上次记录一致，无新增变更。\n"
        f"{latest_line}\n"
        f"{meta_line}\n\n"
        "请查看 GitHub Pages 网页版看板（含图表）。"
    )
    html = (
        "<h2>香港一手短期库存 — 手动检查</h2>"
        "<p>数据与上次记录<strong>一致</strong>，无新增变更。</p>"
        f"<p>{latest_line}</p>"
        f"<p><small>{meta_line}</small></p>"
        "<p>请查看 GitHub Pages 网页版看板（含图表）。</p>"
    )
    return text, html


def send_report_email(
    out_dir: Path,
    *,
    subject: str,
    text_body: str,
    html_body: str,
    repo_url: str = "",
) -> None:
    """发送邮件：正文简短说明（不再附 PDF/PNG 研报图，数据变更请看网页版看板）。"""
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").strip()
    mail_from = os.environ.get("SMTP_FROM", user).strip()
    to_raw = os.environ.get("NOTIFY_EMAIL_TO", "").strip()

    if not all([host, user, password, mail_from, to_raw]):
        raise RuntimeError(
            "邮件未配置：请设置 SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, "
            "SMTP_FROM, NOTIFY_EMAIL_TO"
        )


    recipients = [x.strip() for x in to_raw.split(",") if x.strip()]
    if repo_url:
        text_body += f"\n\n仓库: {repo_url}\n"
        html_body += f'<p>仓库: <a href="{repo_url}">{repo_url}</a></p>'

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)


    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=120) as server:
            server.login(user, password)
            server.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=120) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.sendmail(mail_from, recipients, msg.as_string())


    print(f"已发送邮件至: {", ".join(recipients)}")


def send_update_email(changes: list[FileChange], out_dir: Path, *, repo_url: str = "") -> None:
    text, html = _brief_summary(changes, out_dir)
    send_report_email(
        out_dir,
        subject="[一手短期库存] 数据已更新",
        text_body=text,
        html_body=html,
        repo_url=repo_url,
    )


def send_manual_report_email(out_dir: Path, *, repo_url: str = "") -> None:
    text, html = _manual_no_change_summary(out_dir)
    send_report_email(
        out_dir,
        subject="[一手短期库存] 手动检查（数据无变化）",
        text_body=text,
        html_body=html,
        repo_url=repo_url,
    )
