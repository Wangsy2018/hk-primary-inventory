"""
GitHub Actions / 定时任务入口（版本标记: 2026-06-02-v4-chart-email-fix）
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pandas as pd

from notify_utils import (
    compare_with_baseline,
    send_manual_report_email,
    send_update_email,
    sync_baseline,
)

RUN_DAILY_VERSION = "2026-06-02-v4-chart-email-fix"

PROJECT_DIR = Path(__file__).resolve().parent
BASELINE_DIR = PROJECT_DIR / "data" / "baseline"
OUT_DIR = PROJECT_DIR / "out_inventory"
SCRIPT = PROJECT_DIR / "一手短期库存.py"


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location("inventory_main", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载脚本: {SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["inventory_main"] = mod
    spec.loader.exec_module(mod)
    return mod


def _email_when_no_change() -> bool:
    """手动 Run workflow 时，无数据变化也发邮件（附 PDF+PNG）。"""
    if os.environ.get("SEND_EMAIL_IF_NO_CHANGE", "0").strip() == "1":
        return True
    if os.environ.get("GITHUB_EVENT_NAME", "").strip() == "workflow_dispatch":
        return True
    return False


def _regenerate_chart_from_output() -> None:
    """生成交互式 HTML 看板（ECharts），供 GitHub Pages 网页版查看。（已停用研报 PDF/PNG）"""
    sys.path.insert(0, str(PROJECT_DIR))
    if "chart_dashboard" in sys.modules:
        importlib.reload(sys.modules["chart_dashboard"])
    import chart_dashboard
    chart_dashboard.generate(OUT_DIR)
    print("[run_daily] 已生成 dashboard.html")


def main() -> int:
    print(
        f"[run_daily] version={RUN_DAILY_VERSION}  "
        f"GITHUB_EVENT_NAME={os.environ.get('GITHUB_EVENT_NAME', '')!r}  "
        f"FORCE_GENERATE_CHART={os.environ.get('FORCE_GENERATE_CHART', '')!r}  "
        f"SEND_EMAIL_IF_NO_CHANGE={os.environ.get('SEND_EMAIL_IF_NO_CHANGE', '')!r}"
    )

    if not SCRIPT.exists():
        raise FileNotFoundError(SCRIPT)

    seed_only = os.environ.get("SEED_BASELINE_ONLY", "0") == "1"

    sys.path.insert(0, str(PROJECT_DIR))
    mod = _load_inventory_module()
    argv = [str(SCRIPT), "--out-dir", str(OUT_DIR)]

    old_argv = sys.argv
    try:
        sys.argv = argv
        mod.main()
    finally:
        sys.argv = old_argv

    _regenerate_chart_from_output()


    baseline_ready = (BASELINE_DIR / "instant_saleable_inventory_monthly.csv").exists()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{repo}" if repo else ""

    if not baseline_ready or seed_only:
        print("初始化 baseline（首次运行，不发邮件）…")
        sync_baseline(BASELINE_DIR, OUT_DIR)
        return 0

    changes = compare_with_baseline(BASELINE_DIR, OUT_DIR)

    if changes:
        print(f"检测到 {sum(len(c.rows) for c in changes)} 处变更，发送邮件（数据变更通知）…")
        send_update_email(changes, OUT_DIR, repo_url=repo_url)
        sync_baseline(BASELINE_DIR, OUT_DIR)
        (PROJECT_DIR / ".baseline_updated").write_text("1", encoding="utf-8")
        return 0

    if _email_when_no_change():
        print("数据无变化，但为手动 Run workflow，仍发送邮件（数据无变化通知）…")
        send_manual_report_email(OUT_DIR, repo_url=repo_url)
        return 0

    print("数据无变化（每日定时任务），不发送邮件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
