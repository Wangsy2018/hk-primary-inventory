"""
一手住宅物業銷售資訊網（SRPE）增量同步：只更新有变化的盘。

  每次跑：问「过去 N 天成交册 / 售楼书有更新」的盘 → 只重下这些盘的成交记录册 PDF →
  解析成逐单 → 改写这些盘在 summary / monthly 里的行 → 其余盘一行不动。

仓库里存汇总和逐单（data/srpe/，每盘一个 CSV），PDF 不进 git；成交册是累计册，重解析一份就是该盘全史。
本地首次灌种子：python3 srpe_sync.py --seed srpe_data/dev
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests

logging.getLogger("pdfminer").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

API = "https://www.srpe.gov.hk/api/SrpeWebService"
HKT = timezone(timedelta(hours=8))
DATA_DIR = Path(__file__).resolve().parent / "data" / "srpe"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


# ----------------------------------------------------------------------------
# 抓取（港府站点拒海外代理出口，走代理失败就直连）
# ----------------------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Origin": "https://www.srpe.gov.hk",
                      "Referer": "https://www.srpe.gov.hk/opip/index_for_all_residential"})
    return s


def _req(s: requests.Session, method: str, url: str, **kw):
    last = None
    for trust in (True, False):
        s.trust_env = trust
        try:
            r = s.request(method, url, timeout=kw.pop("timeout", 120), **kw)
            r.raise_for_status()
            return r
        except Exception as e:          # noqa: BLE001
            last = e
    raise RuntimeError(f"SRPE 请求失败 {url}: {last}")


def api(s, path, payload):
    r = _req(s, "POST", f"{API}/{path}", json=payload, headers={"Content-Type": "application/json"})
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"SRPE {path} 返回 code={j.get('code')}: {j.get('remarks')}")
    return j["resultData"]


def fetch_index(s) -> list[dict]:
    payload = {"language": "en", "broadDistrictId": "A", "planningAreaId": "A", "planningAreaIdString": "All",
               "firstPrintYear": "A", "searchByYearOnly": False, "planningAreaIds": ["A"],
               "fromPath": "disclaimer_index_for_all_residential", "actionType": "Index For All Residential",
               "page": None, "limit": None, "actionId": "x"}
    return api(s, "DistrictAreaSearch/getDistrictAreaSearchResult", payload)["list"]


def fetch_changed(s, days: int) -> tuple[list[str], list[str]]:
    """过去 days 天：新上载首版售楼书的盘、成交册有更新的盘。"""
    d = str(days)
    payload = {"language": "en", "searchSalesBrochure": True, "salesBrochureDay": d, "searchTransactions": True,
               "transactionsDay": d, "searchPriceList": False, "priceListDay": "1", "searchSalesArrangement": False,
               "salesArrangementDay": "1", "from": "disclaimer_newly_upload_sales_brochure_price_list",
               "actionType": "Newly Uploaded Sales Documents", "actionId": "x"}
    rd = api(s, "DistrictAreaSearch/getUploadSearchResult", payload)
    return ([x["id"] for x in rd.get("devListSaleBrochure") or []],
            [x["id"] for x in rd.get("devListTransactions") or []])


def fetch_detail(s, dev_id: str) -> dict:
    return api(s, "Map/getMapDevResultById", {"language": "en", "devId": str(dev_id), "timeStamp": 0})


def fetch_register(s, dev_id: str, f: dict) -> bytes:
    url = f"{API}/download/all_development_map/trx/x/{f['id']}/{f['fileName']}/en?devId={dev_id}"
    r = _req(s, "GET", url, timeout=300)
    if r.content[:4] != b"%PDF":
        raise RuntimeError(f"不是 PDF: {r.content[:80]!r}")
    return r.content


# ----------------------------------------------------------------------------
# 解析成交记录册
# ----------------------------------------------------------------------------
_DATE = re.compile(r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}$")
_COLS = ["pasp", "asp", "terminated", "block", "floor", "unit", "carpark", "price", "revision", "terms", "related"]


def parse_register(pdf_bytes: bytes) -> pd.DataFrame:
    import pdfplumber
    rows = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for p in pdf.pages:
            for t in (p.extract_tables() or []):
                for row in t:
                    if not row or len(row) < 8 or not _DATE.match((row[0] or "").strip()):
                        continue
                    c = [(x or "").replace("\n", " ").strip() for x in row]
                    # 成交价格子里可能还带改价备注、车位价，只取第一个金额
                    m = re.search(r"\$?\s*(\d{1,3}(?:,\d{3})+|\d{5,})", c[7])
                    price = int(m.group(1).replace(",", "")) if m else None
                    if price is not None and price > 5_000_000_000:
                        price = None
                    rows.append([c[0], c[1], c[2], c[3], c[4], c[5], c[6], price, c[8] if len(c) > 8 else "",
                                 c[9] if len(c) > 9 else "", c[10] if len(c) > 10 else ""])
    df = pd.DataFrame(rows, columns=_COLS)
    for col in ("pasp", "asp", "terminated"):
        df[col] = pd.to_datetime(df[col].str.replace("/", "-"), format="%d-%m-%Y", errors="coerce")
    return df


def summarize(dev_id: str, df: pd.DataFrame, register_updated: str, now: pd.Timestamp) -> tuple[dict, pd.DataFrame]:
    live = df[df.terminated.isna()]
    s = dict(devId=dev_id, register_updated=register_updated, rows=len(df), sold=len(live),
             terminated=int(df.terminated.notna().sum()),
             first_pasp=df.pasp.min().strftime("%Y-%m-%d") if len(df) else "",
             last_pasp=df.pasp.max().strftime("%Y-%m-%d") if len(df) else "",
             amount_m=round(live.price.sum() / 1e6, 1) if len(live) else 0.0,
             avg_price_m=round(live.price.mean() / 1e6, 2) if len(live) and live.price.notna().any() else None,
             parsed_at=now.strftime("%Y-%m-%d"))
    m_sold = live.groupby(live.pasp.dt.strftime("%Y-%m").rename("month")).agg(sold=("price", "size"), amount_m=("price", lambda x: round(x.sum() / 1e6, 1)))
    term = df[df.terminated.notna()]
    m_term = term.groupby(term.terminated.dt.strftime("%Y-%m").rename("month")).size().rename("terminated")
    monthly = m_sold.join(m_term, how="outer").fillna(0).reset_index()
    monthly.insert(0, "devId", dev_id)
    monthly["sold"] = monthly["sold"].astype(int); monthly["terminated"] = monthly["terminated"].astype(int)
    daily = live.groupby(live.pasp.dt.strftime("%Y-%m-%d").rename("date")).size().rename("sold").reset_index()
    daily.insert(0, "devId", dev_id)
    return s, monthly[["devId", "month", "sold", "terminated", "amount_m"]], daily


# ----------------------------------------------------------------------------
# 表的读写
# ----------------------------------------------------------------------------
def index_rows(lst: list[dict]) -> pd.DataFrame:
    rows = []
    for d in lst:
        b = d.get("brochure") or {}
        rows.append(dict(devId=str(d["id"]), name_zh=d.get("chnName") or "", name_en=d.get("engName") or "",
                         phase=d.get("engPhaseNo") or d.get("engPhaseName") or "",
                         address_en=(d["addresses"] or [{}])[0].get("engAddress") or "",
                         address_zh=(d["addresses"] or [{}])[0].get("chnAddress") or "",
                         area_zh=(d.get("planningArea1") or {}).get("planningAreaNameChn") or "",
                         lat=d.get("latitude"), lon=d.get("longtitude"), active=d.get("active"),
                         first_print=(b.get("dateOfPrint") or "")[:10], first_upload=(d.get("earlistPublicationTime") or "")[:10],
                         suspended=(d.get("dateSuspendSales") or "")[:10], completed=(d.get("dateCompleteSales") or "")[:10],
                         website=d.get("website") or ""))
    return pd.DataFrame(rows)


def load(name: str, cols: list[str]) -> pd.DataFrame:
    p = DATA_DIR / name
    if p.exists():
        return pd.read_csv(p, dtype={"devId": str})
    return pd.DataFrame(columns=cols)


def upsert(table: pd.DataFrame, rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if table.empty:
        return rows.reset_index(drop=True)
    drop = table.set_index(keys).index.isin(rows.set_index(keys).index)
    return pd.concat([table[~drop], rows], ignore_index=True)


def save_register(dev_id: str, df: pd.DataFrame) -> None:
    """逐单也进仓库：每盘一个 CSV，只有册子变了的盘才会改写。"""
    d = DATA_DIR / "registers"; d.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for col in ("pasp", "asp", "terminated"):
        out[col] = out[col].dt.strftime("%Y-%m-%d")
    out.to_csv(d / f"{dev_id}.csv", index=False)


def save(name: str, df: pd.DataFrame, sort: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.sort_values(sort).to_csv(DATA_DIR / name, index=False)


# ----------------------------------------------------------------------------
class Tables:
    def __init__(self):
        self.summary = load("summary.csv", ["devId"]); self.monthly = load("monthly.csv", ["devId", "month"])
        self.daily = load("daily.csv", ["devId", "date", "sold"])
        for t in (self.summary, self.monthly, self.daily):
            t["devId"] = t["devId"].astype(str)

    def put(self, srow: dict, mrows: pd.DataFrame, drows: pd.DataFrame) -> None:
        dev_id = srow["devId"]
        self.summary = upsert(self.summary, pd.DataFrame([srow]), ["devId"])
        self.monthly = pd.concat([self.monthly[self.monthly.devId != dev_id], mrows], ignore_index=True)
        self.daily = pd.concat([self.daily[self.daily.devId != dev_id], drows], ignore_index=True)

    def save(self, now: pd.Timestamp) -> None:
        # 近 30 / 90 天是相对今天的，每次都从 daily 重算，没重下的盘也会随时间往下掉
        d = self.daily.copy(); d["date"] = pd.to_datetime(d["date"])
        s30 = d[d.date >= now - pd.Timedelta(days=30)].groupby("devId").sold.sum()
        s90 = d[d.date >= now - pd.Timedelta(days=90)].groupby("devId").sold.sum()
        self.summary["sold_30d"] = self.summary.devId.map(s30).fillna(0).astype(int)
        self.summary["sold_90d"] = self.summary.devId.map(s90).fillna(0).astype(int)
        save("summary.csv", self.summary, ["devId"]); save("monthly.csv", self.monthly, ["devId", "month"])
        save("daily.csv", self.daily, ["devId", "date"])


def process_dev(s, dev_id: str, pdf_bytes: bytes | None, now, T: Tables, register_updated="") -> bool:
    if pdf_bytes is None:
        det = fetch_detail(s, dev_id)
        tr = det.get("transactions") or []
        if not tr:
            return False
        f = tr[0]["file"]; register_updated = (tr[0].get("updateDateTime") or "")[:16]
        prev = T.summary[T.summary.devId == dev_id]
        if len(prev) and str(prev.iloc[0].get("register_updated", "")) == register_updated:
            return False          # 册子没换，不用重下
        pdf_bytes = fetch_register(s, dev_id, f)
    df = parse_register(pdf_bytes)
    T.put(*summarize(dev_id, df, register_updated, now))
    save_register(dev_id, df)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="SRPE 增量同步")
    ap.add_argument("--days", type=int, default=2, help="回看几天的「新上载」（默认 2，留一天重叠）")
    ap.add_argument("--seed", type=str, default="", help="用本地已下的 srpe_data/dev/<id>/trx_*.pdf 灌种子（不联网下 PDF）")
    ap.add_argument("--refresh-index", action="store_true", help="强制刷新索引")
    args = ap.parse_args()

    s = _session()
    now = pd.Timestamp.now(tz=HKT).tz_localize(None)
    T = Tables()

    if args.seed:
        root = Path(args.seed); n = 0
        lst = fetch_index(s); save("index.csv", index_rows(lst), ["devId"])
        for dd in sorted(root.iterdir()):
            pdfs = sorted(dd.glob("trx_*.pdf")); det_p = dd / "detail.json"
            if not pdfs:
                continue
            upd = ""
            if det_p.exists():
                tr = json.loads(det_p.read_text(encoding="utf-8")).get("transactions") or []
                upd = (tr[0].get("updateDateTime") or "")[:16] if tr else ""
            try:
                n += process_dev(s, dd.name, pdfs[-1].read_bytes(), now, T, upd)
            except Exception as e:          # noqa: BLE001
                print(f"  ! {dd.name} 解析失败: {str(e)[:80]}")
        T.save(now)
        print(f"[srpe] 种子：{n} 个盘，summary {len(T.summary)} 行，monthly {len(T.monthly)} 行，daily {len(T.daily)} 行")
        return 0

    new_dev, changed = fetch_changed(s, args.days)
    print(f"[srpe] 过去 {args.days} 天：新售楼书 {len(new_dev)} 个盘，成交册更新 {len(changed)} 个盘")
    if new_dev or args.refresh_index or not (DATA_DIR / "index.csv").exists():
        lst = fetch_index(s); save("index.csv", index_rows(lst), ["devId"])
        print(f"[srpe] 索引已刷新：{len(lst)} 个盘")
    done = skipped = failed = 0
    for dev_id in dict.fromkeys(new_dev + changed):
        try:
            ok = process_dev(s, dev_id, None, now, T)
            done += ok; skipped += (not ok)
        except Exception as e:          # noqa: BLE001
            failed += 1; print(f"  ! {dev_id} {type(e).__name__}: {str(e)[:100]}")
        time.sleep(0.3)
    T.save(now)
    print(f"[srpe] 更新 {done} 个盘，跳过（册子未变）{skipped}，失败 {failed}；summary {len(T.summary)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
