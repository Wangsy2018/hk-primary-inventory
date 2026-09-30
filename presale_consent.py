"""
地政总署「同意方案」月报：预售同意书的申请进度。

CSDI 上的 LAO_PCRD 只有**已批出**的预售同意书，所以链上看不出一个盘是
「还没申请」还是「申请了在排队」。地政总署每月另发三张表补这一段：

  https://www.landsd.gov.hk/doc/en/consent/monthly/t1_YYMM.pdf  当月批出的同意书
  https://www.landsd.gov.hk/doc/en/consent/monthly/t2_YYMM.pdf  截至月底待批的申请
  https://www.landsd.gov.hk/doc/en/consent/monthly/t3_YYMM.pdf  当月被拒 / 撤回 / 取消的申请

t2 是快照（截至月底所有待批申请），t1 / t3 是当月流水。地图上的「预售状态」
三档就是从这里来的：未申请 / 已申请（在 t2 里）/ 已批准（在 CSDI 图层里）。

PDF 没有表格线，pdfplumber 的 extract_tables 抽不出东西，按表头的字 x 坐标切列。
一条记录可能横跨好几行（公司名换行），以「地段号那一列有字」作为新记录的开头。

产出 data/consent/{pending,issued,rejected}.csv。
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import sys
import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import pdfplumber
import requests

logging.getLogger("pdfminer").setLevel(logging.CRITICAL)
logging.getLogger("pdfplumber").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

BASE = "https://www.landsd.gov.hk/doc/en/consent/monthly/{t}_{ym}.pdf"

# 表头里这些字标出每一列的位置；三张表列数不同，按实际表头动态切
HEAD_TOKENS = ("Lot", "Address", "Name", "Vendor", "Remarks")

TABLES = {"t1": "issued", "t2": "pending", "t3": "rejected"}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False          # 政府站点直连
    s.headers["User-Agent"] = "Mozilla/5.0 hk-primary-inventory"
    return s


def fetch(s: requests.Session, t: str, ym: str) -> bytes | None:
    r = s.get(BASE.format(t=t, ym=ym), timeout=90)
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        return None
    return r.content


def latest_ym(s: requests.Session, back: int = 6) -> tuple[str, bytes] | None:
    """月报发布有滞后，从本月往回找第一个能下到的月份。"""
    d = date.today()
    for _ in range(back):
        ym = f"{d.year % 100:02d}{d.month:02d}"
        b = fetch(s, "t2", ym)
        if b:
            return ym, b
        d = (pd.Timestamp(d.replace(day=1)) - pd.Timedelta(days=1)).date()
    return None


# ----------------------------------------------------------------------------
def _columns(words: list[dict]) -> tuple[list[str], list[float]] | None:
    """从表头那一行推列名和列边界。"""
    lines: dict[int, list[dict]] = {}
    for w in words:
        lines.setdefault(round(w["top"] / 3), []).append(w)
    for k in sorted(lines):
        line = sorted(lines[k], key=lambda a: a["x0"])
        txt = " ".join(a["text"] for a in line)
        if "Remarks" in txt and "Lot" in txt and "Address" in txt:
            # 「Lot No.」是两个词，合成一个
            merged: list[dict] = []
            for a in line:
                if merged and a["x0"] - merged[-1]["x1"] < 4:
                    merged[-1] = {**merged[-1], "text": merged[-1]["text"] + " " + a["text"], "x1": a["x1"]}
                else:
                    merged.append(dict(a))
            names = [a["text"] for a in merged]
            cuts = [0.0]
            for a, b in zip(merged, merged[1:]):
                cuts.append((a["x1"] + b["x0"]) / 2)
            cuts.append(1e5)
            return names, cuts
    return None


# 只要「住宅预售同意书」那一节；同一份 PDF 里还有转让同意书、非住宅、汇总和注释
SECT_WANT = re.compile(r"(?i)^Presale Consent for Residential Development")
SECT_OTHER = re.compile(r"(?i)^(Consent to Assign|Presale Consent for Non-Residential|"
                        r"Consent to Assign for Non-Residential|Summary|Explanatory Notes)")

# 一条记录内部的行距约 10 点，记录之间 20 点以上
ROW_GAP = 15.0


def parse_pdf(data: bytes) -> pd.DataFrame:
    recs: list[dict] = []
    names: list[str] | None = None
    cuts: list[float] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        section_ok = False
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False)
            if not words:
                continue
            head = _columns(words)
            if head and names is None:
                names, cuts = head      # 列宽锁在第一份表头：后面每页表头一样，
                                        # 逐页重认会因为换行差异切出不同列数，把跨页的记录对错位
            if names is None:
                continue

            lines: list[tuple[float, list[dict]]] = []
            buck: dict[int, list[dict]] = {}
            for w in words:
                buck.setdefault(round(w["top"] / 4), []).append(w)
            for k in sorted(buck):
                ln = sorted(buck[k], key=lambda a: a["x0"])
                lines.append((min(a["top"] for a in ln), ln))

            prev_top, in_row = None, False
            for top, ln in lines:
                txt = " ".join(a["text"] for a in ln).strip()
                if SECT_WANT.match(txt):
                    section_ok, in_row, prev_top = True, False, None
                    continue
                if SECT_OTHER.match(txt):
                    section_ok, in_row, prev_top = False, False, None
                    continue
                if not section_ok or re.fullmatch(r"\d+", txt) or txt.startswith(("Note", "Lands Department", "Particulars of")):
                    continue
                if any(a["text"].startswith(("Lot", "Address", "Development", "Authorized", "Person")) for a in ln) \
                        and "Remarks" in txt:
                    in_row, prev_top = False, None      # 表头行
                    continue
                if "Remarks" in txt or txt.startswith(("Authorized", "Person and", "Development Holding")):
                    continue

                cells = [""] * len(names)
                for w in ln:
                    mid = (w["x0"] + w["x1"]) / 2
                    ci = max(i for i in range(len(names)) if cuts[i] <= mid)
                    cells[ci] = (cells[ci] + " " + w["text"]).strip()
                if not any(cells):
                    continue
                new_row = (prev_top is None) or (top - prev_top > ROW_GAP)
                prev_top = top
                if new_row or not in_row or not recs:
                    recs.append({n: c for n, c in zip(names, cells)})
                    in_row = True
                else:
                    for n, c in zip(names, cells):
                        if c:
                            recs[-1][n] = (recs[-1].get(n, "") + " " + c).strip()

    df = pd.DataFrame(recs)
    if df.empty:
        return df
    ren = {}
    for c in df.columns:
        lc = c.lower()
        if lc.startswith("lot"):
            ren[c] = "lot"
        elif lc.startswith("address"):
            ren[c] = "address"
        elif lc == "name":
            ren[c] = "development"
        elif lc == "vendor":
            ren[c] = "vendor"
        elif lc.startswith("units"):
            ren[c] = "units"
        elif lc.startswith("date"):
            ren[c] = "est_completion"
    df = df.rename(columns=ren)
    for c in ("lot", "address", "development", "vendor"):
        if c in df:
            df[c] = df[c].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
    if "units" in df:
        df["units"] = pd.to_numeric(df.units.astype(str).str.extract(r"(\d[\d,]*)")[0]
                                    .str.replace(",", ""), errors="coerce")
    keep = [c for c in ("lot", "address", "development", "vendor", "est_completion", "units") if c in df]
    df = df[keep]
    key = [c for c in ("lot", "address", "development") if c in df]
    return df[df[key].fillna("").apply(lambda r: any(str(x).strip() for x in r), axis=1)].reset_index(drop=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="地政总署同意方案月报（预售申请进度）")
    ap.add_argument("--out", default="data/consent")
    ap.add_argument("--ym", default="", help="指定月份 YYMM，默认自动找最新")
    ap.add_argument("--max-age-hours", type=float, default=0,
                    help="产出比这个新就跳过。月报一个月一张，没必要一天下四次")
    a = ap.parse_args()
    stamp = Path(a.out) / "fetched_at.txt"
    # 同 ura_projects：时间戳落在仓库里，看 mtime 在 CI 里永远是「刚刚」
    if a.max_age_hours and not a.ym and stamp.exists():
        try:
            age = (pd.Timestamp.utcnow() - pd.Timestamp(stamp.read_text().strip())).total_seconds() / 3600
        except Exception:               # noqa: BLE001
            age = None
        if age is not None and 0 <= age < a.max_age_hours:
            print(f"[consent] {age:.1f} 小时前跑过，跳过", file=sys.stderr)
            return 0
    s = _session()
    if a.ym:
        ym, t2 = a.ym, fetch(s, "t2", a.ym)
        if t2 is None:
            print(f"[consent] 下不到 {ym} 的月报", file=sys.stderr)
            return 1
    else:
        got = latest_ym(s)
        if not got:
            print("[consent] 最近 6 个月都下不到月报", file=sys.stderr)
            return 1
        ym, t2 = got
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    total = {}
    for t, label in TABLES.items():
        raw = t2 if t == "t2" else fetch(s, t, ym)
        if raw is None:
            continue
        df = parse_pdf(raw)
        df.insert(0, "ym", f"20{ym[:2]}-{ym[2:]}")
        df.to_csv(out / f"{label}.csv", index=False)
        total[label] = len(df)
    stamp.write_text(pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")
    print(f"[consent] {ym} 月报：" + "、".join(f"{k} {v} 条" for k, v in total.items()), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
