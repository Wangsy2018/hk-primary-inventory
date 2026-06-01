"""
GitHub Actions / 定时任务入口：
1. 运行一手短期库存抓取与回推
2. 与 data/baseline 对比
3. 有变更则发邮件并更新 baseline
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from notify_utils import compare_with_baseline, send_update_email, sync_baseline

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


def main() -> int:
    if not SCRIPT.exists():
        raise FileNotFoundError(SCRIPT)

    skip_chart = os.environ.get("SKIP_CHART", "1") == "1"
    seed_only = os.environ.get("SEED_BASELINE_ONLY", "0") == "1"

    mod = _load_inventory_module()
    argv = [
        str(SCRIPT),
        "--out-dir",
        str(OUT_DIR),
        "--skip-chart" if skip_chart else "",
    ]
    argv = [a for a in argv if a]

    # 注入 argv 供 argparse
    old_argv = sys.argv
    try:
        sys.argv = argv
        mod.main()
    finally:
        sys.argv = old_argv

    baseline_ready = (BASELINE_DIR / "instant_saleable_inventory_monthly.csv").exists()

    if not baseline_ready or seed_only:
        print("初始化 baseline（首次运行或不发邮件）…")
        sync_baseline(BASELINE_DIR, OUT_DIR)
        return 0

    changes = compare_with_baseline(BASELINE_DIR, OUT_DIR)
    if not changes:
        print("数据无变化，不发送邮件。")
        return 0

    print(f"检测到 {sum(len(c.rows) for c in changes)} 处变更，发送邮件…")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    repo_url = f"https://github.com/{repo}" if repo else ""
    send_update_email(changes, repo_url=repo_url)
    sync_baseline(BASELINE_DIR, OUT_DIR)

    # 供 workflow 判断是否需要 commit baseline
    (PROJECT_DIR / ".baseline_updated").write_text("1", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
