"""研报图表：与本地 Inventory with chart 版一致，并修复 Linux 中文字体。"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent


def setup_chinese_font() -> str:
    import matplotlib.font_manager as fm

    font_paths: list[Path] = []
    bundled = PROJECT_DIR / "assets" / "fonts" / "NotoSansSC-Regular.otf"
    if bundled.exists():
        font_paths.append(bundled)

    if os.name != "nt":
        for pattern in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        ):
            p = Path(pattern)
            if p.exists():
                font_paths.append(p)
        fonts_root = Path("/usr/share/fonts")
        if fonts_root.exists():
            font_paths.extend(sorted(fonts_root.rglob("NotoSansCJK*.ttc"))[:4])

    for path in font_paths:
        try:
            fm.fontManager.addfont(str(path))
            name = fm.FontProperties(fname=str(path)).get_name()
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            print(f"[图表] 使用字体: {name} ({path})")
            return name
        except Exception as e:
            print(f"[图表] 字体加载失败 {path}: {e}")

    for font in (
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Micro Hei",
    ):
        available = {f.name for f in fm.fontManager.ttflist}
        if font in available:
            plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            print(f"[图表] 使用中文字体: {font}")
            return font

    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    print("[图表] 警告: 未找到中文字体")
    return "DejaVu Sans"


def prepare_chart_frame(inv_df: pd.DataFrame) -> pd.DataFrame:
    df = inv_df.copy()
    df["date"] = pd.to_datetime(df["month"].astype(str) + "-01")
    df = df.sort_values("date").reset_index(drop=True)

    end_date = df["date"].max().date()
    chart_start = max(date(end_date.year - 10, 1, 1), df["date"].min().date())
    chart_end = end_date

    all_months = pd.date_range(start=chart_start, end=chart_end, freq="MS")
    full = pd.DataFrame({"date": all_months}).merge(df, on="date", how="left")

    for col in ("presale_approved_units", "primary_units", "instant_saleable_inventory"):
        full[col] = pd.to_numeric(full[col], errors="coerce").fillna(0)
    full["presale_approved_units"] = full["presale_approved_units"].astype(int)
    full["primary_units"] = full["primary_units"].astype(int)
    full["instant_saleable_inventory"] = full["instant_saleable_inventory"].astype(int)

    inv_col = full["instant_saleable_inventory"]
    first_nonzero = (inv_col != 0).idxmax()
    if first_nonzero > 0 and inv_col.iloc[0] == 0:
        full.loc[: first_nonzero - 1, "instant_saleable_inventory"] = np.nan

    full["year"] = full["date"].dt.year
    full["month_num"] = full["date"].dt.month
    full["x_idx"] = np.arange(len(full))

    print(f"[图表] 月份数 {len(full)}，{chart_start} ~ {chart_end}")
    return full


def generate_report_chart(inv_df: pd.DataFrame, output_dir: Path) -> None:
    setup_chinese_font()
    full = prepare_chart_frame(inv_df)

    chart_start = full["date"].min().date()
    chart_end = full["date"].max().date()
    current_year = chart_end.year
    current_month = chart_end.month

    grouped = full.copy()
    grouped["quarter"] = grouped["month_num"].apply(lambda m: f"Q{(m - 1) // 3 + 1}")

    xtick_positions: list[float] = []
    xtick_labels: list[str] = []
    for (_year, quarter), sub in grouped[grouped["year"] < current_year].groupby(["year", "quarter"]):
        if sub.empty:
            continue
        x = sub["x_idx"].iloc[1] if len(sub) >= 2 else sub["x_idx"].iloc[0]
        xtick_positions.append(float(x))
        xtick_labels.append(quarter)

    curr_mask = grouped["year"] == current_year
    q1 = grouped[curr_mask & (grouped["month_num"] <= 3)]
    if not q1.empty:
        x = q1["x_idx"].iloc[1] if len(q1) >= 2 else q1["x_idx"].iloc[0]
        xtick_positions.append(float(x))
        xtick_labels.append("Q1")
    if current_month >= 4:
        for m in range(4, current_month + 1):
            row = grouped[(grouped["year"] == current_year) & (grouped["month_num"] == m)]
            if not row.empty:
                xtick_positions.append(float(row["x_idx"].iloc[0]))
                xtick_labels.append(f"{m}月")

    year_labels = []
    for y in range(chart_start.year, current_year + 1):
        yr = full[full["year"] == y]
        if not yr.empty:
            year_labels.append((float(yr["x_idx"].iloc[len(yr) // 2]), str(y)))

    bar_data = []
    for x, label in zip(xtick_positions, xtick_labels):
        if label in ("Q1", "Q2", "Q3", "Q4"):
            mask = (full["x_idx"] >= x - 1) & (full["x_idx"] <= x + 1)
        else:
            mask = full["x_idx"] == x
        sub = full[mask]
        bar_data.append((x, float(sub["presale_approved_units"].sum()), float(sub["primary_units"].sum())))

    annual_presale = full.groupby("year")["presale_approved_units"].sum()
    annual_primary = full.groupby("year")["primary_units"].sum()

    fig = plt.figure(figsize=(16, 9))
    gs = fig.add_gridspec(2, 1, height_ratios=[1.8, 1], hspace=0.12)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)

    bar_width = 0.7
    pf = pk = True
    for x, presale, primary in bar_data:
        ax1.bar(
            x - bar_width / 2,
            presale,
            width=bar_width,
            color="#3498DB",
            alpha=0.9,
            zorder=3,
            label="批出楼花单位数" if pf else "",
        )
        ax1.bar(
            x + bar_width / 2,
            -primary,
            width=bar_width,
            color="#E74C3C",
            alpha=0.9,
            zorder=3,
            label="一手成交单位数" if pk else "",
        )
        pf = pk = False

    ax1.axhline(0, color="black", linewidth=0.8)
    ax1.set_ylabel("单位数量（伙）", fontsize=12)
    ax1.legend(loc="upper left", frameon=True, fontsize=9, markerscale=0.8)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, pos: f"{abs(int(x)):,}"))
    ax1.grid(axis="y", alpha=0.3, linestyle="--")

    y_top, y_bottom = ax1.get_ylim()[1] * 0.95, ax1.get_ylim()[0] * 0.95
    for y, total in annual_presale.items():
        if total:
            mid = float(full[full["year"] == y]["x_idx"].iloc[len(full[full["year"] == y]) // 2])
            ax1.annotate(
                f"{y}: {int(total):,}伙",
                xy=(mid, y_top),
                ha="center",
                va="top",
                fontsize=9,
                fontweight="bold",
                color="#21618C",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#21618C", alpha=0.9),
            )
    for y, total in annual_primary.items():
        if total:
            mid = float(full[full["year"] == y]["x_idx"].iloc[len(full[full["year"] == y]) // 2])
            ax1.annotate(
                f"{y}: {int(total):,}伙",
                xy=(mid, y_bottom),
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                color="#922B21",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor="#922B21", alpha=0.9),
            )

    valid_inv = full.dropna(subset=["instant_saleable_inventory"])
    ax2.plot(
        valid_inv["x_idx"],
        valid_inv["instant_saleable_inventory"],
        color="#2C3E50",
        linewidth=2.2,
        alpha=0.9,
        label="即时可售货量",
    )
    ax2.fill_between(valid_inv["x_idx"], 0, valid_inv["instant_saleable_inventory"], color="#2C3E50", alpha=0.05)

    for y in range(chart_start.year, current_year + 1):
        yr = full[full["year"] == y]
        if yr.empty:
            continue
        last = yr.iloc[-1]
        if pd.isna(last["instant_saleable_inventory"]):
            continue
        ax2.scatter(last["x_idx"], last["instant_saleable_inventory"], color="#C0392B", s=50, zorder=5)
        ax2.annotate(
            f"{int(last['instant_saleable_inventory']):,}",
            (last["x_idx"], last["instant_saleable_inventory"]),
            textcoords="offset points",
            xytext=(0, 8),
            ha="center",
            fontsize=7,
            color="#C0392B",
            rotation=45,
        )

    ax2.set_ylabel("可售货量（伙）", fontsize=12)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, pos: f"{int(x):,}"))
    ax2.grid(axis="y", alpha=0.3, linestyle="--")

    for ax in (ax1, ax2):
        ax.set_xticks(xtick_positions)
        ax.set_xticklabels(xtick_labels, rotation=0, ha="center", fontsize=8.5)
        for xi in full[full["month_num"] == 1]["x_idx"]:
            ax.axvline(xi, color="gray", linestyle="--", alpha=0.25, linewidth=0.6)
        ax.tick_params(axis="x", which="major", pad=12)

    for x_pos, year_str in year_labels:
        ax2.text(
            x_pos,
            -0.22,
            year_str,
            transform=ax2.get_xaxis_transform(),
            ha="center",
            va="top",
            fontsize=10,
            fontweight="bold",
        )

    fig.suptitle("香港一手住宅市场：批出楼花、一手成交及即时可售货量", fontsize=16, fontweight="bold", y=0.97)
    ax1.set_title(
        f"数据范围：{chart_start.year}年{chart_start.month}月 — {chart_end.year}年{chart_end.month}月",
        fontsize=10,
        color="gray",
        pad=6,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    png_path = output_dir / "report_chart.png"
    pdf_path = output_dir / "report_chart.pdf"
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    print(f"[图表] 已导出: {png_path}")
    print(f"[图表] 已导出: {pdf_path}")
