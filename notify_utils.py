"""Compare CSV baselines and send SMTP notification emails."""
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
    """Return per-file list of changed rows (by month). Empty list = no changes."""
    changes: list[FileChange] = []

    for name in TRACKED_FILES:
        old_df = _read_csv(baseline_dir / name)
        new_df = _read_csv(current_dir / name)
        if old_df.empty and new_df.empty:
            continue
        if old_df.empty:
            changes.append(FileChange(filename=name, rows=[{"note": "baseline missing; treated as full update"}]))
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


def _format_changes_html(changes: list[FileChange]) -> str:
    parts = ["<h2>香港一手短期库存 — 数据有更新</h2>"]
    for fc in changes:
        parts.append(f"<h3>{fc.filename}</h3><ul>")
        for r in fc.rows[:80]:
            if "note" in r:
                parts.append(f"<li>{r['note']}</li>")
            elif r.get("change") in ("added", "removed"):
                parts.append(f"<li>{r['month']}: {r['change']}</li>")
            else:
                parts.append(
                    f"<li>{r['month']} {r['field']}: {r['old']} → {r['new']}</li>"
                )
        if len(fc.rows) > 80:
            parts.append(f"<li>… 另有 {len(fc.rows) - 80} 条变更</li>")
        parts.append("</ul>")
    return "\n".join(parts)


def _format_changes_text(changes: list[FileChange]) -> str:
    lines = ["香港一手短期库存 — 数据有更新", ""]
    for fc in changes:
        lines.append(f"=== {fc.filename} ===")
        for r in fc.rows[:80]:
            if "note" in r:
                lines.append(f"  - {r['note']}")
            elif r.get("change") in ("added", "removed"):
                lines.append(f"  - {r['month']}: {r['change']}")
            else:
                lines.append(f"  - {r['month']} {r['field']}: {r['old']} -> {r['new']}")
        if len(fc.rows) > 80:
            lines.append(f"  … 另有 {len(fc.rows) - 80} 条变更")
        lines.append("")
    return "\n".join(lines)


def send_update_email(changes: list[FileChange], *, repo_url: str = "") -> None:
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
    subject = "[一手短期库存] 数据已更新"
    text_body = _format_changes_text(changes)
    html_body = _format_changes_html(changes)
    if repo_url:
        text_body += f"\n\n仓库: {repo_url}\n"
        html_body += f'<p>仓库: <a href="{repo_url}">{repo_url}</a></p>'

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as server:
            server.login(user, password)
            server.sendmail(mail_from, recipients, msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
            server.login(user, password)
            server.sendmail(mail_from, recipients, msg.as_string())

    print(f"已发送通知邮件至: {', '.join(recipients)}")
