"""
GitHub Actions / 定时任务入口：
1. 运行一手短期库存抓取、回推并生成 PDF/PNG 图表
2. 与 data/baseline 对比
3. 有变更 → 发邮件（仅附 PDF+PNG）
4. 无变更 + 手动 Run → 仍发邮件（仅附 PDF+PNG）
5. 无变更 + 每日定时 → 不发邮件
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from notify_utils import (
    compare_with_baseline,
    send_manual_report_email,
    send_update_email,
    sync_baseline,
)

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


def _is_manual_run() -> bool:
    return os.environ.get("GITHUB_EVENT_NAME", "").strip() == "workflow_dispatch"


def main() -> int:
    if not SCRIPT.exists():
        raise FileNotFoundError(SCRIPT)

    seed_only = os.environ.get("SEED_BASELINE_ONLY", "0") == "1"
    manual = _is_manual_run()

    mod = _load_inventory_module()
    # CI/定时任务也生成图表，供邮件附件使用
    argv = [str(SCRIPT), "--out-dir", str(OUT_DIR)]

    old_argv = sys.argv
    try:
        sys.argv = argv
        mod.main()
    finally:
        sys.argv = old_argv

    baseline_ready = (BASELINE_DIR / "instant_saleable_inventory_monthly.csv").exists()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{repo}" if repo else ""

    if not baseline_ready or seed_only:
        print("初始化 baseline（首次运行，不发邮件）…")
        sync_baseline(BASELINE_DIR, OUT_DIR)
        return 0

    changes = compare_with_baseline(BASELINE_DIR, OUT_DIR)

    if changes:
        print(f"检测到 {sum(len(c.rows) for c in changes)} 处变更，发送邮件（PDF+PNG）…")
        send_update_email(changes, OUT_DIR, repo_url=repo_url)
        sync_baseline(BASELINE_DIR, OUT_DIR)
        (PROJECT_DIR / ".baseline_updated").write_text("1", encoding="utf-8")
        return 0

    if manual:
        print("数据无变化，但为手动 Run workflow，仍发送最新图表邮件…")
        send_manual_report_email(OUT_DIR, repo_url=repo_url)
        return 0

    print("数据无变化（定时任务），不发送邮件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
