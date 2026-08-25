from __future__ import annotations

import argparse
import os
import math
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
import matplotlib

matplotlib.use("Agg")

from chart_report import generate_report_chart

# ------------------------------------------------------------
# 1. 原有下载、回推逻辑（完全保留）
# ------------------------------------------------------------
ARCGIS_LAYER_URL = (
    "https://portal.csdi.gov.hk/server/rest/services/common/"
    "landsd_rcd_1637303511514_65978/FeatureServer/0"
)
# PENDING_LAYER_URL = (
#     "https://portal.csdi.gov.hk/server/rest/services/common/"
#     "landsd_rcd_1637222762687_21369/FeatureServer/0"
# )

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = PROJECT_DIR / "out_inventory"


def month_str(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_month(s: str) -> date:
    m = re.fullmatch(r"(\d{4})-(\d{2})", s.strip())
    if not m:
        raise ValueError(f"月份格式应为 YYYY-MM，但收到: {s!r}")
    y = int(m.group(1))
    mo = int(m.group(2))
    if not (1 <= mo <= 12):
        raise ValueError(f"月份不合法: {s!r}")
    return date(y, mo, 1)


def month_range_inclusive(start: date, end: date) -> list[date]:
    if start > end:
        raise ValueError("start 不能晚于 end")
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur = (cur + relativedelta(months=1)).replace(day=1)
    return out


def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Referer": "https://portal.csdi.gov.hk/geoportal/",
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return s


def fetch_arcgis_layer_fields(layer_url: str) -> pd.DataFrame:
    s = _requests_session()
    r = s.get(f"{layer_url}?f=pjson", timeout=60)
    r.raise_for_status()
    fields = r.json().get("fields", [])
    return pd.DataFrame(
        [
            {
                "name": f.get("name"),
                "type": f.get("type"),
                "alias": f.get("alias"),
                "length": f.get("length"),
            }
            for f in fields
        ]
    )


def fetch_arcgis_features_all(
    layer_url: str,
    where: str = "1=1",
    out_fields: str = "*",
    page_size: int = 2000,
    sleep_s: float = 0.05,
) -> list[dict]:
    s = _requests_session()
    meta = s.get(f"{layer_url}?f=pjson", timeout=60)
    meta.raise_for_status()
    meta_j = meta.json()
    max_rc = int(meta_j.get("maxRecordCount", page_size) or page_size)
    page_size = min(page_size, max_rc)
    oid_field = meta_j.get("objectIdField") or "OBJECTID"

    out: list[dict] = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "orderByFields": f"{oid_field} ASC",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "f": "json",
        }
        r = s.get(f"{layer_url}/query", params=params, timeout=120)
        r.raise_for_status()
        feats = r.json().get("features") or []
        if not feats:
            break
        for f in feats:
            out.append(f.get("attributes") or {})
        offset += len(feats)
        if len(feats) < page_size:
            break
        if sleep_s:
            time.sleep(sleep_s)
    return out


def _to_int_safe(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
        return int(v)
    s = str(v).strip().replace(",", "")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return None


def build_presale_approvals_monthly(
    start: date,
    end: date,
    layer_url: str = ARCGIS_LAYER_URL,
    issue_year_field: str = "SEARCH01_EN",
    issue_month_field: str = "SEARCH02_EN",
    residential_units_field: str = "NSEARCH13_EN",
) -> pd.DataFrame:
    rows = fetch_arcgis_features_all(layer_url=layer_url, out_fields="*")
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("ArcGIS 拉取结果为空。")

    df["issue_year"] = df[issue_year_field].apply(_to_int_safe)
    df["issue_month"] = df[issue_month_field].apply(_to_int_safe)
    df["approved_units"] = df[residential_units_field].apply(_to_int_safe)
    df = df.dropna(subset=["issue_year", "issue_month"])
    df = df[(df["issue_month"] >= 1) & (df["issue_month"] <= 12)]
    df["month"] = df.apply(
        lambda r: f"{int(r['issue_year']):04d}-{int(r['issue_month']):02d}", axis=1
    )

    g = (
        df.groupby("month", as_index=False)["approved_units"]
        .sum(min_count=1)
        .rename(columns={"approved_units": "presale_approved_units"})
    )
    months = [month_str(d) for d in month_range_inclusive(start, end)]
    out = pd.DataFrame({"month": months}).merge(g, on="month", how="left")
    out["presale_approved_units"] = out["presale_approved_units"].fillna(0).astype(int)
    return out


def build_landreg_primary_monthly_via_selenium(
    start: date,
    end: date,
    headless: bool = True,
) -> pd.DataFrame:
    import time as _time
    from selenium import webdriver
    from selenium.common.exceptions import StaleElementReferenceException
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    from webdriver_manager.chrome import ChromeDriverManager

    # 年份段链接与页面结构每年会变：遍历页面全部链接，按「一手成交 agt-pri 系列」+「目标年份范围」过滤，不用固定 nth-child
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(service=service, options=options)

    base_url = "https://www.landreg.gov.hk/tc/monthly/agreement.htm"
    driver.get(base_url)

    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    except Exception as e:
        driver.quit()
        raise RuntimeError(f"土地注册处页面加载超时: {e}")
    _time.sleep(3)

    link_rows = driver.execute_script(
        r"""
        const scope = document.querySelector('.past-content') || document;
        return Array.from(scope.querySelectorAll('a')).map(a => ({
            text: (a.innerText || a.textContent || '').trim(),
            href: a.href || a.getAttribute('href') || ''
        }));
        """
    )

    periods = []
    for row in link_rows:
        href = (row.get("href") or "").strip()
        if not href or "agt-pri" not in href:
            # 只取「一手成交(primary)」系列：agt-pri-* / agt-primary
            continue
        parsed = parse_period_range(row.get("text") or "")
        if parsed is None:
            continue
        s_year, e_year = parsed
        # 只保留落在目标年份范围内的年份段（含相邻一年兜底，防止年份边界出入）
        if e_year < start.year - 1 or s_year > end.year + 1:
            continue
        periods.append({"text": row.get("text"), "href": href})

    if not periods:
        driver.quit()
        raise RuntimeError("未找到一手成交年份段链接，请检查地政署页面结构是否有变。")

    pattern = re.compile(r"t([12])Y(\d+)r(\d+)_td(\d+)$")
    rows: dict[tuple[int, int], int] = {}

    def parse_period_range(text: str) -> tuple[int, int] | None:
        text = text.strip()
        if " - " in text:
            try:
                a, b = map(int, text.split(" - ", 1))
                return a, b
            except ValueError:
                return None
        if re.fullmatch(r"20\d{2}", text):
            y = int(text)
            return y, y
        return None

    def calendar_year(start_year: int, end_year: int, y_index: int) -> int:
        if start_year == end_year:
            return start_year
        return start_year + y_index - 1

    for period in periods:
        period_text = (period.get("text") or "").strip()
        period_url = (period.get("href") or "").strip()
        if not period_text or "20" not in period_text or not period_url:
            continue

        parsed = parse_period_range(period_text)
        if parsed is None:
            continue
        start_year, end_year = parsed

        print(f"  土地注册处时间段: {period_text}")

        driver.get(period_url)
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except Exception:
            continue

        elements = driver.find_elements(By.CSS_SELECTOR, '[id^="t1"], [id^="t2"]')

        for element in elements:
            try:
                element_id = element.get_attribute("id")
                match = pattern.match(element_id or "")
                if not match or int(match.group(4)) == 13:
                    continue

                table_type = match.group(1)
                y_index = int(match.group(2))
                row_type = int(match.group(3))
                month = int(match.group(4))

                if table_type != "1" or row_type != 1:
                    continue

                current_year = calendar_year(start_year, end_year, y_index)
                if start_year != end_year and current_year > end_year:
                    continue

                raw = (element.text or "").replace(",", "").strip()
                if not raw:
                    continue
                value = int(raw)
                rows[(current_year, month)] = value
            except StaleElementReferenceException:
                continue
            except (ValueError, TypeError):
                continue

        driver.back()
        _time.sleep(2)

    driver.quit()

    df = pd.DataFrame(
        [{"month": f"{y:04d}-{m:02d}", "primary_units": v} for (y, m), v in rows.items()]
    )
    months = [month_str(d) for d in month_range_inclusive(start, end)]
    df = pd.DataFrame({"month": months}).merge(df, on="month", how="left")
    df["primary_units"] = df["primary_units"].fillna(0).astype(int)
    return df


# def build_pending_presale_monthly(
#     start: date,
#     end: date,
#     layer_url: str = PENDING_LAYER_URL,
#     units_field: str = "NSEARCH11_TC",
#     last_update_field: str = "LASTUPDATE",
# ) -> pd.DataFrame:
#     """
#     从待批预售楼花图层提取数据，按最后更新日期（LASTUPDATE）聚合。
#     注意：该服务未启用历史版本管理，当前仅能获取最新快照，
#     因此每个月的待批存量仅代表该月有更新记录的申请单位数之和，
#     而非历史月末的真实存量。如需精确历史数据，请联系数据提供方。
#     """
#     rows = fetch_arcgis_features_all(layer_url=layer_url, out_fields="*")
#     df = pd.DataFrame(rows)
#     if df.empty:
#         raise RuntimeError("待批预售楼花 ArcGIS 拉取结果为空。")
#
#     if last_update_field not in df.columns:
#         raise RuntimeError(f"字段 '{last_update_field}' 不存在。可用字段: {list(df.columns)}")
#
#     def parse_lastupdate(v):
#         if pd.isna(v):
#             return None
#         try:
#             return date.fromtimestamp(int(v) / 1000)
#         except (ValueError, TypeError):
#             return None
#
#     df["update_date"] = df[last_update_field].apply(parse_lastupdate)
#     df = df.dropna(subset=["update_date"])
#
#     def parse_units(v):
#         if pd.isna(v):
#             return 0
#         s = str(v).replace(",", "").strip()
#         try:
#             return int(s)
#         except:
#             return 0
#
#     df["pending_units"] = df[units_field].apply(parse_units)
#     df = df[df["pending_units"] > 0]
#
#     df["month"] = df["update_date"].apply(month_str)
#     g = df.groupby("month", as_index=False)["pending_units"].sum().rename(
#         columns={"pending_units": "pending_presale_units"}
#     )
#
#     months = [month_str(d) for d in month_range_inclusive(start, end)]
#     out = pd.DataFrame({"month": months}).merge(g, on="month", how="left")
#     out["pending_presale_units"] = out["pending_presale_units"].fillna(0).astype(int)
#     return out


def compute_inventory_by_anchor_backcast(
    approvals: pd.DataFrame,
    sales: pd.DataFrame,
    anchor_month: str,
    anchor_inventory: int,
) -> pd.DataFrame:
    df = approvals.merge(sales, on="month", how="outer").fillna(0)
    df["presale_approved_units"] = df["presale_approved_units"].astype(int)
    df["primary_units"] = df["primary_units"].astype(int)
    df = df.sort_values("month").reset_index(drop=True)

    if anchor_month not in set(df["month"].tolist()):
        raise ValueError(f"锚点月份 {anchor_month} 不在数据范围内。")

    idx = df.index[df["month"] == anchor_month][0]
    inv = [None] * len(df)
    inv[idx] = int(anchor_inventory)

    for i in range(idx, 0, -1):
        inv[i - 1] = int(inv[i]) - int(df.loc[i, "presale_approved_units"]) + int(
            df.loc[i, "primary_units"]
        )

    for i in range(idx + 1, len(df)):
        inv[i] = int(inv[i - 1]) + int(df.loc[i, "presale_approved_units"]) - int(
            df.loc[i, "primary_units"]
        )

    df["instant_saleable_inventory"] = pd.Series(inv, dtype="int64")
    return df


# 图表逻辑见 chart_report.py（generate_report_chart）


# ------------------------------------------------------------
# 3. 主程序
# ------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="香港一手即时可售货量：自动下载并回推")
    ap.add_argument("--start", type=str, default=None, help="起始 YYYY-MM，默认锚点往前10年（从1月开始）")
    ap.add_argument("--end", type=str, default=None, help="结束 YYYY-MM，默认锚点月")
    ap.add_argument("--anchor-month", type=str, default="2026-04")
    ap.add_argument("--anchor-inventory", type=int, default=24500)
    ap.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR),
                    help="输出目录，默认脚本同目录 out_inventory")
    ap.add_argument("--landreg-primary-csv", type=str, default=None)
    ap.add_argument("--no-landreg-selenium", action="store_true")
    ap.add_argument("--selenium-visible", action="store_true", help="显示 Chrome（调试）")
    ap.add_argument("--dump-arcgis-fields", action="store_true")
    ap.add_argument("--skip-chart", action="store_true", help="不生成图表（仅本地调试时用）")
    args = ap.parse_args()

    # GitHub Actions：workflow 设 FORCE_GENERATE_CHART=1 时必定出图（供邮件附件）
    if os.environ.get("FORCE_GENERATE_CHART", "0").strip() == "1":
        args.skip_chart = False
    elif os.environ.get("SKIP_CHART", "0").strip() == "1":
        args.skip_chart = True

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dump_arcgis_fields:
        p = out_dir / "arcgis_layer_fields.csv"
        fetch_arcgis_layer_fields(ARCGIS_LAYER_URL).to_csv(p, index=False, encoding="utf-8-sig")
        print(f"已导出: {p}")
        return

    # if args.dump_pending_fields:
    #     p = out_dir / "pending_layer_fields.csv"
    #     fetch_arcgis_layer_fields(PENDING_LAYER_URL).to_csv(p, index=False, encoding="utf-8-sig")
    #     print(f"已导出: {p}")
    #     return

    anchor_d = parse_month(args.anchor_month)
    if args.start:
        start = parse_month(args.start)
    else:
        start = date(anchor_d.year - 10, 1, 1)
    end = parse_month(args.end) if args.end else date.today().replace(day=1)

    print(f"项目目录: {PROJECT_DIR}")
    print(f"输出目录: {out_dir}")
    print(f"时间范围: {month_str(start)} ~ {month_str(end)}")
    print(f"锚点: {args.anchor_month} = {args.anchor_inventory}")
    print()

    print("[1/3] 正在下载政府 ArcGIS 预售批出伙数 …")
    approvals = build_presale_approvals_monthly(start=start, end=end)
    approvals.to_csv(out_dir / "presale_approvals_monthly.csv", index=False, encoding="utf-8-sig")

    if args.landreg_primary_csv:
        print("[2/3] 读取本地一手成交 CSV …")
        sales = pd.read_csv(args.landreg_primary_csv, dtype={"month": str})
        months = [month_str(d) for d in month_range_inclusive(start, end)]
        sales = pd.DataFrame({"month": months}).merge(sales, on="month", how="left")
        sales["primary_units"] = sales["primary_units"].fillna(0).astype(int)
    elif args.no_landreg_selenium:
        raise ValueError("请提供 --landreg-primary-csv 或去掉 --no-landreg-selenium")
    else:
        print("[2/3] 正在抓取土地注册处一手成交（Chrome）…")
        sales = build_landreg_primary_monthly_via_selenium(
            start=start, end=end, headless=not args.selenium_visible
        )

    sales.to_csv(out_dir / "landreg_primary_monthly.csv", index=False, encoding="utf-8-sig")

    print("[3/3] 计算即时可售货量 …")
    inv = compute_inventory_by_anchor_backcast(
        approvals, sales, args.anchor_month, args.anchor_inventory
    )
    inv.to_csv(out_dir / "instant_saleable_inventory_monthly.csv", index=False, encoding="utf-8-sig")

    # 待批数据获取已注释
    # print("[4/4] 正在获取待批预售楼花单位数 …")
    # pending = build_pending_presale_monthly(start=start, end=end)
    # pending.to_csv(out_dir / "pending_presale_monthly.csv", index=False, encoding="utf-8-sig")

    # 合并输出 Excel（不含待批表）
    xlsx = out_dir / "instant_saleable_inventory_monthly.xlsx"
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        approvals.to_excel(w, sheet_name="presale_approvals", index=False)
        sales.to_excel(w, sheet_name="landreg_primary", index=False)
        inv.to_excel(w, sheet_name="inventory", index=False)
        # pending.to_excel(w, sheet_name="pending_presale", index=False)

    print()
    print("完成！")
    print(f"  Excel: {xlsx}")

    if args.skip_chart:
        print("已跳过图表生成（--skip-chart）。")
        return

    # ----- 生成投行级图表（不含待批）-----
    print("\n[4/4] 正在生成研报图表 …")
    generate_report_chart(inv, out_dir)  # 不再传递 pending_df
    print("图表生成完毕。")


if __name__ == "__main__":
    main()

