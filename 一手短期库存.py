from __future__ import annotations

import argparse
import io
import json
import os
import math
import re
import time
import zipfile
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

LANDREG_BASE = "https://www.landreg.gov.hk"
LANDREG_INDEX = f"{LANDREG_BASE}/tc/monthly/agreement.htm"
# 年份段页面背后的数据源；t1.json = 单位数，t2.json = 合约金额
LANDREG_JSON_DIR = f"{LANDREG_BASE}/json/monthly_agt-pri"
LANDREG_PRIMARY_KEY = "Number of Primary Sales for ASP Residential Building Units"
LANDREG_SECONDARY_KEY = "Number of Secondary Sales for ASP Residential Building Units"

# 待批预售楼花同意书（住宅）：地政总署按月归档，每月一份 PDF，末页「Summary」有合计。
# 用英文版：中文版 2016/2017 的措辞和现在不一样，英文版十年来只换过一个词。
LANDSD_CONSENT_INDEX = (
    "https://www.landsd.gov.hk/en/resources/land-info-stat/"
    "dev-control-compliance/consent/presale.html"
)
LANDSD_PENDING_PDF = "https://www.landsd.gov.hk/doc/en/consent/monthly/t2_{yymm}.pdf"
# 2018 起: "... applications pending approval : N" + "... units involved : M"
# 2016-17: "... pending approval : N"              + "... units pending approval : M"
PENDING_SUMMARY_RE = re.compile(
    r"Total no\. of Pre-?sale Consent \(Residential\)(?: applications)? pending approval"
    r"\s*:\s*([\d,]+)\s*"
    r"Total no\. of residential units (?:involved|pending approval)\s*:\s*([\d,]+)",
    re.I,
)

# 中原城市领先指数 CCL：周度，1993-12 至今
CCL_CHART_URL = "https://hk.centanet.com/CCI/api/Index/CCLChart"
CCL_REFERER = "https://hk.centanet.com/CCI/index"

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = PROJECT_DIR / "out_inventory"
# 待批预售楼花逐月历史：每月一份 PDF，抓一次就落盘，提交进仓库避免重复下载
PENDING_HISTORY_CSV = PROJECT_DIR / "data" / "history" / "pending_presale_monthly.csv"


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


def _requests_session(referer: str = "https://portal.csdi.gov.hk/geoportal/") -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
            "Referer": referer,
            "Accept": "application/json,text/plain,*/*",
        }
    )
    return s


def _http_get(s: requests.Session, url: str, **kw) -> requests.Response:
    """本机全局代理常把港府站点的隧道挡掉，连不上时自动绕过代理直连重试一次。"""
    try:
        return s.get(url, **kw)
    except requests.exceptions.RequestException:
        if not s.trust_env:
            raise
        print("  [网络] 经代理访问失败，改为直连重试 …")
        s.trust_env = False
        s.proxies = {}
        return s.get(url, **kw)


def fetch_arcgis_layer_fields(layer_url: str) -> pd.DataFrame:
    s = _requests_session()
    r = _http_get(s, f"{layer_url}?f=pjson", timeout=60)
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
    meta = _http_get(s, f"{layer_url}?f=pjson", timeout=60)
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
        r = _http_get(s, f"{layer_url}/query", params=params, timeout=120)
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


def _landreg_rows_to_frame(
    rows: dict[tuple[int, int], dict[str, int]], start: date, end: date
) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "month": f"{y:04d}-{m:02d}",
                "primary_units": v.get("primary_units"),
                "secondary_units": v.get("secondary_units"),
            }
            for (y, m), v in rows.items()
        ]
    )
    months = [month_str(d) for d in month_range_inclusive(start, end)]
    df = pd.DataFrame({"month": months}).merge(df, on="month", how="left")
    for c in ("primary_units", "secondary_units"):
        df[c] = df[c].fillna(0).astype(int)
    return df


def fetch_landreg_primary_periods(s: requests.Session) -> list[tuple[str, str]]:
    """取「一手及二手买卖」类别下的全部年份段。

    年份段清单以 `var pastStatJson=[...]` 内联在主页里，不需要渲染 JS。
    返回 [(显示文本, 页面 slug)]，顺序同页面：新的在前。
    """
    html = _http_get(s, LANDREG_INDEX, timeout=60).text
    m = re.search(r"var\s+pastStatJson\s*=\s*(\[.*?\])\s*\n\s*pastStatInit", html, re.S)
    if not m:
        raise RuntimeError("主页未找到 pastStatJson，土地注册处页面结构可能已变。")
    periods: list[tuple[str, str]] = []
    for cat in json.loads(m.group(1)):
        for b in cat.get("buttons") or []:
            slug = str(b.get("link_url") or "").rsplit("/", 1)[-1]
            if slug.endswith(".htm"):
                slug = slug[: -len(".htm")]
            # 一手成交系列：当年为 agt-primary，历史段为 agt-pri-N
            if slug == "agt-primary" or slug.startswith("agt-pri-"):
                periods.append((str(b.get("title") or "").strip(), slug))
    return periods


def build_landreg_primary_monthly_via_json(start: date, end: date) -> pd.DataFrame:
    """直接取年份段页面背后的 t1.json，字段自带 Year / Month，不必推年份。"""
    s = _requests_session(referer=LANDREG_INDEX)
    periods = fetch_landreg_primary_periods(s)
    if not periods:
        raise RuntimeError("未找到一手成交年份段，土地注册处页面结构可能已变。")

    rows: dict[tuple[int, int], dict[str, int]] = {}
    # 从最老的年份段开始写，新段覆盖旧段：重叠月份以最新一版为准
    for title, slug in reversed(periods):
        r = _http_get(s, f"{LANDREG_JSON_DIR}/{slug}/t1.json", timeout=60)
        if r.status_code != 200:
            print(f"  土地注册处 {title}（{slug}）: HTTP {r.status_code}，跳过")
            continue
        got = 0
        for rec in r.json():
            y = _to_int_safe(rec.get("Year"))
            mo = _to_int_safe(rec.get("Month"))  # 年合计行的 Month 是 "Total"
            pri = _to_int_safe(rec.get(LANDREG_PRIMARY_KEY))
            sec = _to_int_safe(rec.get(LANDREG_SECONDARY_KEY))
            if y is None or mo is None or pri is None or not (1 <= mo <= 12):
                continue
            rows[(y, mo)] = {"primary_units": pri, "secondary_units": sec or 0}
            got += 1
        print(f"  土地注册处 {title}（{slug}）: {got} 个月")

    _assert_landreg_coverage(rows, start, end)
    return _landreg_rows_to_frame(rows, start, end)


def _assert_landreg_coverage(
    rows: dict[tuple[int, int], int], start: date, end: date
) -> None:
    """抓漏了就报错，让上层回退 Selenium —— 静默补 0 会直接污染回推结果。"""
    if not rows:
        raise RuntimeError("土地注册处一手成交为空。")
    latest = max(date(y, m, 1) for (y, m) in rows)
    if latest < (end + relativedelta(months=-3)).replace(day=1):
        raise RuntimeError(f"土地注册处数据只到 {month_str(latest)}，距 {month_str(end)} 太远。")
    wanted = [d for d in month_range_inclusive(start, min(end, latest))]
    missing = [d for d in wanted if (d.year, d.month) not in rows]
    if missing:
        raise RuntimeError(
            f"{month_str(start)}~{month_str(min(end, latest))} 缺 {len(missing)} 个月，"
            f"例如 {', '.join(month_str(d) for d in missing[:6])}"
        )


def build_landreg_primary_monthly(
    start: date,
    end: date,
    *,
    allow_selenium: bool = True,
    headless: bool = True,
) -> pd.DataFrame:
    """优先走 JSON 通道；失败再回退到 Selenium 抓渲染后的表格。"""
    try:
        return build_landreg_primary_monthly_via_json(start, end)
    except Exception as e:
        if not allow_selenium:
            raise
        print(f"  [landreg] JSON 通道失败（{e}），回退 Selenium …")
        return build_landreg_primary_monthly_via_selenium(start, end, headless=headless)


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
    rows: dict[tuple[int, int], dict[str, int]] = {}

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

                if table_type != "1" or row_type not in (1, 2):
                    continue

                current_year = calendar_year(start_year, end_year, y_index)
                if start_year != end_year and current_year > end_year:
                    continue

                raw = (element.text or "").replace(",", "").strip()
                if not raw:
                    continue
                key = "primary_units" if row_type == 1 else "secondary_units"
                rows.setdefault((current_year, month), {})[key] = int(raw)
            except StaleElementReferenceException:
                continue
            except (ValueError, TypeError):
                continue

        driver.back()
        _time.sleep(2)

    driver.quit()

    return _landreg_rows_to_frame(rows, start, end)


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


def fetch_pending_available_months(s: requests.Session) -> list[str]:
    """地政总署预售同意书索引页列出的全部月份，形如 ["2013-01", …]。"""
    html = _http_get(s, LANDSD_CONSENT_INDEX, timeout=60).text
    yms = sorted(set(re.findall(r"consent/presale/(\d{4})(\d{2})\.html", html)))
    return [f"{y}-{m}" for y, m in yms]


def fetch_pending_month(s: requests.Session, ym: str) -> tuple[int, int] | None:
    """某个月的待批预售楼花（住宅）：返回 (申请数, 住宅单位数)，取不到返回 None。

    合计写在 PDF 末页的 Summary 里，不必解析表格。
    """
    import pdfplumber

    yymm = ym[2:4] + ym[5:7]
    r = s.get(LANDSD_PENDING_PDF.format(yymm=yymm), timeout=120)
    if r.status_code != 200:
        return None
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        # Summary 固定在最后一两页
        text = "\n".join((pg.extract_text() or "") for pg in pdf.pages[-2:])
    m = PENDING_SUMMARY_RE.search(re.sub(r"\s+", " ", text))
    if not m:
        return None
    return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))


def _read_pending_history(history_csv: Path) -> pd.DataFrame:
    cols = ["month", "pending_units", "pending_applications"]
    if not history_csv.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(history_csv, dtype={"month": str})
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols]


def build_pending_presale_monthly(
    start: date,
    end: date,
    history_csv: Path,
    *,
    refresh_latest: int = 2,
    backfill: bool = False,
) -> pd.DataFrame:
    """待批预售楼花逐月序列。

    每月一份 PDF、下载不便宜，所以历史抓一次就落盘到 history_csv，
    日常只补「历史里还没有的月份」+ 最近 refresh_latest 个月（防事后修订）。
    backfill=True 时忽略已有历史，把区间内所有月份重抓一遍。
    """
    s = _requests_session(referer=LANDSD_CONSENT_INDEX)
    available = fetch_pending_available_months(s)
    if not available:
        raise RuntimeError("地政总署预售同意书索引页没解析出月份，页面结构可能已变。")

    wanted = {month_str(d) for d in month_range_inclusive(start, end)}
    candidates = [ym for ym in available if ym in wanted]

    hist = _read_pending_history(history_csv)
    known = set(hist["month"].astype(str)) if not hist.empty else set()
    if backfill:
        todo = candidates
    else:
        todo = [ym for ym in candidates if ym not in known]
        todo += [ym for ym in candidates[-refresh_latest:] if ym not in todo]
    todo = sorted(set(todo))

    if todo:
        print(f"  待批预售楼花: 需抓取 {len(todo)} 个月（历史已有 {len(known)} 个月）")
    rows = []
    for ym in todo:
        got = fetch_pending_month(s, ym)
        if got is None:
            print(f"    {ym}: PDF 缺失或 Summary 解析失败，跳过")
            continue
        apps, units = got
        rows.append({"month": ym, "pending_units": units, "pending_applications": apps})
        print(f"    {ym}: {apps} 个申请 / {units:,} 伙")

    if rows:
        fresh = pd.DataFrame(rows)
        hist = hist[~hist["month"].isin(fresh["month"])]
        hist = pd.concat([hist, fresh], ignore_index=True)

    hist = hist.dropna(subset=["month"]).sort_values("month").reset_index(drop=True)
    hist["pending_units"] = hist["pending_units"].astype("Int64")
    hist["pending_applications"] = hist["pending_applications"].astype("Int64")
    history_csv.parent.mkdir(parents=True, exist_ok=True)
    hist.to_csv(history_csv, index=False, encoding="utf-8-sig")

    months = [month_str(d) for d in month_range_inclusive(start, end)]
    out = pd.DataFrame({"month": months}).merge(hist, on="month", how="left")
    print(f"  待批预售楼花: 区间内 {int(out['pending_units'].notna().sum())}/{len(out)} 个月有值")
    return out


def fetch_ccl_monthly(start: date, end: date) -> pd.DataFrame:
    """中原城市领先指数 CCL：周度序列取每月最后一个观测值作为该月时点数。"""
    s = _requests_session(referer=CCL_REFERER)
    r = _http_get(s, CCL_CHART_URL, timeout=90)
    r.raise_for_status()
    raw = (r.json() or {}).get("rawData") or {}

    values = raw.get("ccl") or []
    # 以「合约期结束日」为观测日，与网站图表 x 轴一致
    dates = raw.get("realContractEndDate") or raw.get("times") or []
    if not values or len(values) != len(dates):
        raise RuntimeError(f"CCL 数据异常: {len(values)} 个值 / {len(dates)} 个日期")

    obs = pd.DataFrame({"d": pd.to_datetime(dates, errors="coerce"), "ccl": values})
    obs = obs.dropna(subset=["d", "ccl"])
    obs["month"] = obs["d"].dt.strftime("%Y-%m")
    # 每月最后一周的读数
    last = obs.sort_values("d").groupby("month", as_index=False).last()[["month", "d", "ccl"]]
    last = last.rename(columns={"d": "obs_date"})
    last["obs_date"] = last["obs_date"].dt.strftime("%Y-%m-%d")
    last["ccl"] = last["ccl"].astype(float).round(2)

    months = [month_str(d) for d in month_range_inclusive(start, end)]
    out = pd.DataFrame({"month": months}).merge(last, on="month", how="left")
    got = int(out["ccl"].notna().sum())
    print(f"  中原城市领先指数 CCL: 全量 {len(obs)} 个周度点，区间内 {got}/{len(out)} 个月有值")
    return out


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
    ap.add_argument("--no-landreg-selenium", action="store_true",
                    help="禁用 Selenium 回退，只走 JSON 通道")
    ap.add_argument("--selenium-visible", action="store_true", help="显示 Chrome（调试）")
    ap.add_argument("--dump-arcgis-fields", action="store_true")
    ap.add_argument("--skip-chart", action="store_true", help="不生成图表（仅本地调试时用）")
    ap.add_argument("--pending-backfill", action="store_true",
                    help="重抓区间内全部月份的待批预售楼花 PDF（首次建历史时用）")
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

    print("[1/5] 正在下载政府 ArcGIS 预售批出伙数 …")
    approvals = build_presale_approvals_monthly(start=start, end=end)
    approvals.to_csv(out_dir / "presale_approvals_monthly.csv", index=False, encoding="utf-8-sig")

    if args.landreg_primary_csv:
        print("[2/5] 读取本地一手成交 CSV …")
        sales = pd.read_csv(args.landreg_primary_csv, dtype={"month": str})
        months = [month_str(d) for d in month_range_inclusive(start, end)]
        sales = pd.DataFrame({"month": months}).merge(sales, on="month", how="left")
        sales["primary_units"] = sales["primary_units"].fillna(0).astype(int)
    else:
        print("[2/5] 正在获取土地注册处一手成交（含二手）…")
        sales = build_landreg_primary_monthly(
            start=start,
            end=end,
            allow_selenium=not args.no_landreg_selenium,
            headless=not args.selenium_visible,
        )

    sales.to_csv(out_dir / "landreg_primary_monthly.csv", index=False, encoding="utf-8-sig")

    # 这两个是看板的补充序列，任何一个挂掉都不该拖垮主流程
    print("[3/5] 正在获取待批预售楼花与中原城市领先指数 …")
    try:
        pending = build_pending_presale_monthly(
            start, end, PENDING_HISTORY_CSV, backfill=args.pending_backfill
        )
        pending.to_csv(out_dir / "pending_presale_monthly.csv", index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"  [待批] 获取失败，本次跳过: {e}")
    try:
        ccl = fetch_ccl_monthly(start, end)
        ccl.to_csv(out_dir / "ccl_monthly.csv", index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"  [CCL] 获取失败，本次跳过: {e}")

    print("[4/5] 计算即时可售货量 …")
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
    print("\n[5/5] 正在生成研报图表 …")
    generate_report_chart(inv, out_dir)  # 不再传递 pending_df
    print("图表生成完毕。")


if __name__ == "__main__":
    main()

