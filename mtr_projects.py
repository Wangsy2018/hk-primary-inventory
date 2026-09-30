"""
港铁上盖物业发展项目 —— 取自港铁年报的「Hong Kong Property」章节。

港铁的上盖用地不经地政总署卖地库（铁路沿线用地是随铁路方案批给港铁的），
CSDI 三个土地图层查不到，land_chain 只能靠预售同意书的卖方认出港铁盘，
看不出是哪一期、哪年招标、给了哪个发展商。

**港铁不公布招标金额。** 政府卖地（NSEARCH04/08）和市建局（新闻稿）都公布中标价和落标价，
港铁的招标新闻稿只写「招标已批予某某财团」，一个数字都没有——上盖是分成 / 实物分配，
本来就没有一个可比的地价。所以这里能补的是：哪一期、哪个发展商、哪年批出、多少楼面。

年报里有三张现成的表（一年一份 PDF，覆盖全部项目，比逐篇扒新闻稿干净得多）：
  Property Development Packages Completed during the year and awarded
      站 / 项目名 / 发展商 / 类型 / 楼面 / 招标批出日期 / 预计落成
  Property Development Packages to be Awarded        待招标的储备
  West Rail Line Property Development Plan           西铁沿线（港铁只作发展代理）

  https://www.mtr.com.hk/archive/corporate/en/investor/annual{年}/E{章}.pdf

注意：港铁企业站（www.mtr.com.hk/en/corporate/…）2026-09 起长期返回维护页，
但 /archive/ 这条静态路径一直可用，走的不是同一套系统。该站对连续请求限速很凶
（连扫几百个 URL 后会整段拒绝），所以这里每份 PDF 之间留 4 秒。

产出 data/mtr/packages.csv。
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import time
import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

logging.getLogger("pdfminer").setLevel(logging.CRITICAL)
logging.getLogger("pdfplumber").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

BASE = "https://www.mtr.com.hk/archive/corporate/en/investor/annual{y}/E{c}.pdf"
MAINT_SIZE = 158797          # 企业站维护页，当 404 用
TYPES = ("Residential", "Retail", "Office", "Kindergarten", "Hotel", "Commercial",
         "Car park", "Carpark", "Community")

TABLES = {
    "已批出": "Developers",
    "待招标": "Period of",
    "西铁": "Station/Site",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False          # 政府 / 港铁站点直连
    s.headers["User-Agent"] = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
    return s


def fetch_chapter(s: requests.Session, year: int, ch: int) -> bytes | None:
    r = s.get(BASE.format(y=year, c=ch), timeout=120)
    if not r.headers.get("content-type", "").startswith("application/pdf"):
        return None
    if len(r.content) == MAINT_SIZE or not r.content.startswith(b"%PDF"):
        return None
    return r.content


def find_report(s: requests.Session, year: int | None = None, back: int = 3) -> tuple[int, bytes] | None:
    """年报章节编号每年会挪，按内容找含物业表那一章。"""
    years = [year] if year else [date.today().year - i for i in range(back)]
    for y in years:
        for ch in range(10, 17):
            raw = fetch_chapter(s, y, ch)
            time.sleep(4)
            if not raw:
                continue
            try:
                with pdfplumber.open(io.BytesIO(raw)) as pdf:
                    txt = "\n".join((p.extract_text() or "") for p in pdf.pages)
            except Exception:       # noqa: BLE001
                continue
            if "Property Development Packages" in txt:
                print(f"[mtr] 用 {y} 年报第 E{ch} 章", file=sys.stderr)
                return y, raw
    return None


# ----------------------------------------------------------------------------
def _cells(line, cuts):
    out = [""] * (len(cuts) - 1)
    for w in sorted(line, key=lambda a: a["x0"]):
        mid = (w["x0"] + w["x1"]) / 2
        for i in range(len(cuts) - 1):
            if cuts[i] <= mid < cuts[i + 1]:
                out[i] = (out[i] + " " + w["text"]).strip()
                break
    return out


def _columns(pg, marker):
    """表头那一行的单元格边界就是列边界。"""
    for t in pg.find_tables():
        row = t.rows[0]
        if not row.cells or any(c is None for c in row.cells):
            continue
        head = " ".join((pg.crop(c).extract_text() or "").replace("\n", " ") for c in row.cells)
        if marker in head:
            return [c[0] for c in row.cells] + [row.cells[-1][2]], t.bbox
    return None


def parse_table(pg, marker: str, keys: tuple[str, ...]) -> list[dict]:
    got = _columns(pg, marker)
    if not got:
        return []
    cuts, bbox = got
    # 年报正文是双栏排版，表格只占页面的一条横带。不限 x 范围的话，表格下面那一栏的
    # 正文会被一起当成表格行读进来（「待招标」那张表整段正文都会混进去）
    words = [w for w in pg.extract_words(keep_blank_chars=False, extra_attrs=["fontname"])
             if w["top"] > bbox[3] and w["x0"] >= bbox[0] - 6 and w["x1"] <= bbox[2] + 6]
    buck: dict[int, list] = {}
    for w in words:
        buck.setdefault(round(w["top"] / 4), []).append(w)

    recs: list[dict] = []
    station = ""
    for k in sorted(buck):
        line = buck[k]
        head = " ".join(w["text"] for w in sorted(line, key=lambda a: a["x0"]))
        # 脚注和合计之后就不是表格内容了
        if re.match(r"(?i)^(notes?\s*:|total\b|[#*^]\s|\d\s+[A-Z])", head):
            break
        # 站名自成一行、用半粗体排；靠字体认最稳——靠「右边几列是空的」会把
        # 「SEASONS PLACE / PARK SEASONS / GRAND SEASONS」这种折行的项目名误当成站名
        if all("Semibold" in w["fontname"] or "Bold" in w["fontname"] for w in line):
            station = " ".join(w["text"] for w in sorted(line, key=lambda a: a["x0"]))
            continue
        c = _cells(line, cuts)
        if not any(c):
            continue
        ti = keys.index("type") if "type" in keys else -1
        typ = c[ti] if 0 <= ti < len(c) else ""
        if ti >= 0 and any(typ.startswith(t) for t in TYPES):
            recs.append({"station": station, **{k2: (c[i] if i < len(c) else "") for i, k2 in enumerate(keys)}})
        elif ti < 0 and c[0] and len(c) > 1 and c[1]:
            recs.append({"station": station, **{k2: (c[i] if i < len(c) else "") for i, k2 in enumerate(keys)}})
        elif recs:
            for i, k2 in enumerate(keys):
                if i < len(c) and c[i]:
                    recs[-1][k2] = (recs[-1][k2] + " " + c[i]).strip()
    return recs


AWARDED = ("name", "developer", "type", "gfa", "award", "completion")
PIPELINE = ("name", "type", "gfa", "tender_period", "completion")
WESTRAIL = ("name", "site_ha", "award", "completion")


def build(raw: bytes) -> pd.DataFrame:
    rows = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        for pg in pdf.pages:
            t = pg.extract_text() or ""
            if "Property Development Packages" not in t and "West Rail Line Property" not in t:
                continue
            for label, marker, keys in (("已批出", "Developers", AWARDED),
                                        ("待招标", "Period of", PIPELINE),
                                        ("西铁", "Station/Site", WESTRAIL)):
                for r in parse_table(pg, marker, keys):
                    rows.append({"table": label, **r})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in df.columns:
        df[c] = df[c].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    if "gfa" in df:
        df["gfa"] = pd.to_numeric(df.gfa.str.replace(r"[^\d]", "", regex=True), errors="coerce")
    if "award" in df:
        # 「December 2016」「By phases from 2012 to 2014」都可能出现，只取形如 月+年 / 年
        df["award_ym"] = pd.to_datetime(
            df.award.str.extract(r"([A-Z][a-z]+ \d{4})")[0], errors="coerce").dt.strftime("%Y-%m")
        df.loc[df.award_ym.isna(), "award_ym"] = df.award.str.extract(r"\b(\d{4})\b")[0]
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="港铁上盖物业发展项目（年报物业章节）")
    ap.add_argument("--out", default="data/mtr")
    ap.add_argument("--year", type=int, default=0, help="指定年报年份，默认自动找最新")
    ap.add_argument("--max-age-hours", type=float, default=0,
                    help="产出比这个新就跳过。年报一年一份，没必要天天下")
    a = ap.parse_args()
    out = Path(a.out)
    stamp = out / "fetched_at.txt"
    # 时间戳落在仓库里：CI 每轮都是全新 checkout，看文件 mtime 永远是「刚刚」
    if a.max_age_hours and stamp.exists():
        try:
            age = (pd.Timestamp.utcnow() - pd.Timestamp(stamp.read_text().strip())).total_seconds() / 3600
        except Exception:               # noqa: BLE001
            age = None
        if age is not None and 0 <= age < a.max_age_hours:
            print(f"[mtr] {age:.1f} 小时前跑过，跳过", file=sys.stderr)
            return 0
    s = _session()
    got = find_report(s, a.year or None)
    if not got:
        print("[mtr] 找不到年报物业章节（港铁站点可能在限速，沿用上次数据）", file=sys.stderr)
        return 1
    year, raw = got
    df = build(raw)
    if df.empty:
        print("[mtr] 物业表解析为空，页面结构可能变了", file=sys.stderr)
        return 1
    df.insert(0, "report_year", year)
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "packages.csv", index=False)
    stamp.write_text(pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    n = df.table.value_counts().to_dict()
    print(f"[mtr] {year} 年报：" + "、".join(f"{k} {v} 行" for k, v in n.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
