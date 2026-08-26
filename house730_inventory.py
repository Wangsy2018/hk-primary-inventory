# -*- coding: utf-8 -*-
"""house730 一手项目库存表。

网站 www.house730.com 在 Cloudflare 后面（所以老代码要 Selenium + 长延迟），
但它的数据接口 api.house730.com 没有任何防护：不走 CF，appsignature 也不校验。
所以这里全部走 HTTP + JSON，769 个期数几十秒抓完。

口径（按需求）：
- 期数合并成项目：英文地址 + main developer 都一致即同一项目
- 只要项目里**任何一期**有 First Sales Date，整个项目算已开售
- 表里只放已开售项目；余货加总也只算这些项目
"""
from __future__ import annotations

import argparse
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

API = "https://api.house730.com"
COMMON = {"language": "en-us", "platform": "pc", "cityen": "hk", "appkey": "730responsive"}
HKT = timezone(timedelta(hours=8))
INVALID_TS = -62135596800  # 接口用这个值表示「没有日期」
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = PROJECT_DIR / "out_inventory" / "projects_inventory.csv"
# 首次出售日期 / Estimated Material Date 基本不变，抓过就落盘，提交进仓库。
# 每天只补：本地没有的期数 + 还没开售的期数（它们随时可能开）。
SALE_PROCESS_CSV = PROJECT_DIR / "data" / "history" / "house730_sale_process.csv"

_PHASE_TAIL = re.compile(
    r"\s*[,，]?\s*(?:phase|期數|期数)\s*[IVXLC0-9]+[A-Z]?\b.*$|\s*第\s*[一二三四五六七八九十0-9]+\s*期.*$",
    re.I,
)
_PHASE_NUM = re.compile(r"(?:phase|第)\s*([IVXLC0-9]+)", re.I)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8,
          "IX": 9, "X": 10, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
          "七": 7, "八": 8, "九": 9, "十": 10}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 本机全局代理会挡掉部分站点
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
        "Referer": "https://www.house730.com/",
    })
    return s


class ApiError(RuntimeError):
    """取数失败。宁可报错也不返回 0 —— 静默补 0 会让余货凭空少掉一整个楼盘。"""


class _Gate:
    """全局限流闸。

    api.house730.com 是令牌桶：突发几十次就返回 429（Cloudflare 的
    "Just a moment..." 页），几秒后恢复。光靠每个线程各自退避没用 ——
    退避期间别的线程还在继续把桶打空。所以用一个共享的「不早于」时间戳，
    任何线程吃到 429 就把全体一起按住。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._not_before = 0.0

    def wait(self, spacing: float = 0.12) -> None:
        """顺带做基础节流：每次放行至少间隔 spacing 秒，避免一上来就把桶打空。"""
        while True:
            with self._lock:
                now = time.monotonic()
                gap = self._not_before - now
                if gap <= 0:
                    self._not_before = now + spacing
                    return
            time.sleep(min(gap, 3.0))

    def brake(self, seconds: float) -> None:
        with self._lock:
            self._not_before = max(self._not_before, time.monotonic() + seconds)


_gate = _Gate()


def api_json(s: requests.Session, ep: str, *, tries: int = 8, **params):
    """GET 一个接口，429 时全局退避重试；重试到底仍失败就抛错。

    绝不返回空值凑数：静默补 0 会让某个楼盘的货量凭空消失，而且从结果上看不出来。
    """
    delay = 2.0
    last = ""
    for attempt in range(tries):
        _gate.wait()
        try:
            r = s.get(f"{API}/{ep}", params={**COMMON, **params}, timeout=60)
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200 and r.content:
                try:
                    return r.json()
                except ValueError:
                    last = "响应不是 JSON"
            elif r.status_code == 429:
                last = "429 限流"
                _gate.brake(delay + random.uniform(0, 1.0))
            else:
                last = f"HTTP {r.status_code}"
        if attempt < tries - 1:
            delay = min(delay * 1.8, 45.0)
    raise ApiError(f"{ep} estateId={params.get('estateId')} 重试 {tries} 次仍失败（{last}）")


def api_post(s: requests.Session, ep: str, body: dict, *, tries: int = 8, **params):
    """POST 版本，同样走全局闸。列表接口也会被限流。"""
    delay = 2.0
    last = ""
    for attempt in range(tries):
        _gate.wait()
        try:
            r = s.post(f"{API}/{ep}", params={**COMMON, **params}, json=body, timeout=60)
        except requests.exceptions.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        else:
            if r.status_code == 200 and r.content:
                try:
                    return r.json()
                except ValueError:
                    last = "响应不是 JSON"
            elif r.status_code == 429:
                last = "429 限流"
                _gate.brake(delay + random.uniform(0, 1.0))
            else:
                last = f"HTTP {r.status_code}"
        if attempt < tries - 1:
            delay = min(delay * 1.8, 45.0)
    raise ApiError(f"{ep} 重试 {tries} 次仍失败（{last}）")


def _hk_date(ts) -> date | None:
    if not ts or ts == INVALID_TS or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).astimezone(HKT).date()
    except (ValueError, OSError, OverflowError):
        return None


def fetch_all_estates(s: requests.Session, page_size: int = 100, lang: str = "en-us") -> list[dict]:
    """全部一手期数。列表里已带英文地址与 main developer，合并不必逐个点进去。"""
    out: list[dict] = []
    page = 1
    while True:
        res = (api_post(s, "NewEstate/SearchNewEstate",
                        {"pageIndex": page, "pageCount": page_size},
                        language=lang) or {}).get("result") or {}
        rows = res.get("data") or []
        out += rows
        if not rows or len(out) >= int(res.get("count") or 0):
            break
        page += 1
    return out


def fetch_sale_process(s: requests.Session, estate_id) -> dict[str, date | None]:
    items = (api_json(s, "NewEstate/GetNewEstateSaleProcess", estateId=estate_id) or {}).get("result") or []
    return {i.get("itemNameWithCulture", ""): _hk_date(i.get("itemValue")) for i in items}


def fetch_units(s: requests.Session, estate_id) -> tuple[int, int]:
    """(总单位数, 已售数)。status == "2" 即已售，就是网页那张横道图的底层数据。

    未开售的期数确实会返回空 result，那是真的 0；取数失败则会抛 ApiError。
    """
    res = (api_json(s, "NewEstate/GetNewEstateRoomById", estateId=estate_id) or {}).get("result") or {}
    total = sold = 0
    for b in res.get("buildingDetails") or []:
        for _floor, rooms in (b.get("rooms") or {}).items():
            for u in rooms or []:
                total += 1
                if str(u.get("status")) == "2":
                    sold += 1
    return total, sold


def load_sale_process_cache(path: Path) -> dict:
    """estateId -> (首次出售日期, Estimated Material Date)。"""
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out = {}
    for r in df.itertuples(index=False):
        def d(v):
            v = str(v).strip()
            return date.fromisoformat(v) if v and v not in ("nan", "None", "") else None
        out[str(r.estate_id)] = (d(r.first_sales_date), d(r.estimated_material_date))
    return out


def save_sale_process_cache(path: Path, cache: dict) -> None:
    rows = [{"estate_id": k,
             "first_sales_date": v[0].isoformat() if v[0] else "",
             "estimated_material_date": v[1].isoformat() if v[1] else ""}
            for k, v in sorted(cache.items(), key=lambda x: str(x[0]))]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def norm_address(a: str | None) -> str:
    a = (a or "").lower()
    a = re.sub(r"[,\.，、]", " ", a)
    a = re.sub(r"\bno\s+", "", a)          # "No. 1 Wetland Park" -> "1 wetland park"
    a = re.sub(r"\bo(?=\d)", "", a)         # 录入错字：Wings At Sea 的 "O1 Lohas Park Road"
    a = re.sub(r"([0-9]+)號", r"\1 ", a)
    return re.sub(r"\s+", " ", a).strip()


def _addr_tokens(a: str) -> tuple[frozenset, frozenset]:
    toks = [t for t in re.split(r"[\s/]+", a) if t]
    nums = frozenset(t for t in toks if any(c.isdigit() for c in t))
    return frozenset(toks), nums


def same_address(a: str, b: str) -> bool:
    """地址相同，或一个是另一个加了区名之类的后缀。

    "19 shing fung road" vs "19 shing fung road kai tak" -> 同
    "1 wetland park"     vs "1 wetland park road"        -> 同
    "1 wetland park"     vs "9 wetland park"             -> 不同（门牌号必须一致）
    """
    if not a or not b:
        return False
    if a == b:
        return True
    ta, na = _addr_tokens(a)
    tb, nb = _addr_tokens(b)
    if na != nb or not (na or nb):   # 门牌/期号不一致，或两边都没有数字 -> 不合并
        return False
    return ta <= tb or tb <= ta


_DEV_STOP = {"company", "companies", "limited", "ltd", "holding", "holdings",
             "group", "co", "inc", "properties", "property", "development",
             "developments", "land", "lands", "estate", "estates", "and", "the"}


def dev_tokens(d: str | None) -> frozenset[str]:
    """发展商拆成词集合。

    同一个联营体在不同期数里写法会飘：Villa Garda I 是
    "MTR，SINO LAND，K.WAH & CHINA MERCHANTS"，II 是
    "MTR，SINO，K.WAH & CHINA MERCHANTS "。整串比对会判成两家，
    拆成词再比重合度就能对上。
    """
    words = re.split(r"[^a-z0-9]+", (d or "").lower())
    return frozenset(w for w in words if w and w not in _DEV_STOP)


def same_developer(a: frozenset[str], b: frozenset[str]) -> bool:
    """词集合重合度 >= 0.6 即视为同一发展商。

    阈值要挡住康城路 1 號的邻居：那里 5 家发展商共用同一地址，
    Villa Garda 的 {mtr, sino, k, wah, china, merchants} 与
    LA MIRABELLE 的 {sino} 重合度只有 0.17，不会误并。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    inter = len(a & b)
    return inter / len(a | b) >= 0.6


def norm_developer(d: str | None) -> str:
    return "".join(sorted(dev_tokens(d)))


def phase_order(name: str) -> tuple[int, str]:
    m = _PHASE_NUM.search(name or "")
    if not m:
        return (0, name or "")
    tok = m.group(1).upper()
    if tok.isdigit():
        return (int(tok), name or "")
    return (_ROMAN.get(tok, 99), name or "")


def base_name(name: str) -> str:
    return re.sub(r"\s*[,，]\s*$", "", _PHASE_TAIL.sub("", name or "").strip()).strip()


def _tidy(x: str) -> str:
    """去掉尾部的分隔符和光秃秃的 phase/期 字样。"""
    x = re.sub(r"[\s,，\-–—_/|]+$", "", x or "")
    x = re.sub(r"\s+(?:phase|phases|期)$", "", x, flags=re.I)
    return re.sub(r"[\s,，\-–—_/|]+$", "", x).strip()


def common_project_name(names: list[str]) -> str:
    """多期项目的项目名：取**多数期**共有的最长词前缀。

    不能用全体的最长公共前缀 —— PARK YOHO 有 7 期，其中 6 期叫
    "Park Yoho X"，第 7 期叫 "Park Vista (Park Yoho)"，全体公共前缀
    会被拉短成 "PARK"。改成过半数即可，再截掉结尾光秃秃的 Phase。

    "KT Marina 1" / "KT Marina 2"                  -> KT Marina
    "THE YOHO Hub II" / "The YOHO Hub"             -> THE YOHO Hub
    "DEEP WATER SOUTH PHASE 6A-X" / "...6B-Y"      -> DEEP WATER SOUTH
    """
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        return ""
    if len(names) == 1:
        return _tidy(base_name(names[0])) or _tidy(names[0])

    split = [n.split() for n in names]
    need = len(names) / 2
    for k in range(max(len(w) for w in split), 0, -1):
        counts: dict[str, list[str]] = {}
        for w in split:
            if len(w) < k:
                continue
            # 比前缀时忽略标点：一期写 "Wings At Sea II," 另一期写 "Wings At Sea,"
            key = " ".join(t.lower().strip(",.;:，、") for t in w[:k])
            counts.setdefault(key, []).append(" ".join(w[:k]))
        for key, originals in counts.items():
            if len(originals) > need:
                cand = _tidy(originals[0])
                if len(cand) >= 3:
                    return cand
    return _tidy(base_name(names[0]))


@dataclass
class Project:
    address: str
    developer: str
    phases: list[dict] = field(default_factory=list)


def build_projects(estates: list[dict]) -> list[Project]:
    """按「main developer 一致 + 地址可对上」把期数并成项目。

    用并查集而不是直接按 key 分组，因为地址有三种脏法：
    英文字段里混中文（Victoria Voyage Phase 1B 存的是「承豐道18號」）、
    带区名后缀（"...road kai tak"）、"No. 1 X" 与 "1 X Road" 混用。
    所以每期同时保留中英两个地址，任一能对上即视为同一项目。
    """
    n = len(estates)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    info = []
    for e in estates:
        addrs = [norm_address(e.get("detailAddressWithCulture")),
                 norm_address(e.get("_addr_zh"))]
        info.append(([a for a in addrs if a],
                     dev_tokens(e.get("mainDeveloperWithCulture"))))

    # 769 个期数两两比 = 29 万次集合运算，不到一秒；按地址分桶提速反而漏配
    # （Wetland Seasons Park 三期的地址首词分别是 "tin"/"9"/"9"）
    for x in range(n):
        ax, dx = info[x]
        for y in range(x + 1, n):
            ay, dy = info[y]
            if not dx or not dy:
                # 没有发展商时只认完全相同的地址，避免误并
                ok = any(a and a == b for a in ax for b in ay)
            else:
                ok = (same_developer(dx, dy)
                      and any(same_address(a, b) for a in ax for b in ay))
            if ok:
                union(x, y)

    buckets: dict[int, list[dict]] = {}
    for i, e in enumerate(estates):
        buckets.setdefault(find(i), []).append(e)

    out = []
    for root, phases in buckets.items():
        base = estates[root]
        out.append(Project(address=(base.get("detailAddressWithCulture") or "").strip(),
                           developer=(base.get("mainDeveloperWithCulture") or "").strip(),
                           phases=phases))
    return out


def months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description="house730 一手项目库存表")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个期数（调试用）")
    ap.add_argument("--refresh-dates", action="store_true",
                    help="忽略本地缓存，重抓全部期数的销售进度")
    args = ap.parse_args()

    s = _session()
    today = datetime.now(HKT).date()

    print("[1/4] 拉取全部一手期数（中英各一份，地址两边互补）…")
    estates = fetch_all_estates(s)
    zh = {e["estateId"]: e.get("detailAddressWithCulture")
          for e in fetch_all_estates(s, lang="zh-hk")}
    for e in estates:
        e["estateId"] = str(e["estateId"])
        e["_addr_zh"] = zh.get(e["estateId"]) or zh.get(int(e["estateId"]))
    if args.limit:
        estates = estates[: args.limit]
    print(f"      {len(estates)} 个期数")

    cache = load_sale_process_cache(SALE_PROCESS_CSV)
    todo = [e for e in estates
            if args.refresh_dates
            or e["estateId"] not in cache
            or not cache[e["estateId"]][0]]        # 还没开售的，随时可能开
    print(f"[2/4] 销售进度：本地已有 {len(cache)} 期，需抓 {len(todo)} 期 …")
    if todo:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            procs = list(ex.map(lambda e: fetch_sale_process(s, e["estateId"]), todo))
        for e, pr in zip(todo, procs):
            cache[e["estateId"]] = (pr.get("First Sales Date"), pr.get("Estimated Material Date"))
        print(f"      {time.time() - t0:.0f}s")
        save_sale_process_cache(SALE_PROCESS_CSV, cache)
    for e in estates:
        fs, emd = cache.get(e["estateId"], (None, None))
        e["_first_sale"], e["_emd"] = fs, emd

    groups = build_projects(estates)
    # 任何一期开售 -> 整个项目算已开售
    launched = [v for v in groups if any(e.get("_first_sale") for e in v.phases)]
    print(f"      {len(estates)} 个期数 -> {len(groups)} 个项目，其中已开售 {len(launched)} 个")

    need = [e for v in launched for e in v.phases]
    print(f"[3/4] 拉取已开售项目各期单位状态（{len(need)} 期）…")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        units = list(ex.map(lambda e: fetch_units(s, e["estateId"]), need))
    for e, (tot, sold) in zip(need, units):
        e["_total"], e["_sold"] = tot, sold
    empty = [e for e in need if not e["_total"]]
    print(f"      {time.time() - t0:.0f}s；其中 {len(empty)} 期无单位表（未推出的期数，属正常）")

    print("[4/4] 合并汇总 …")
    rows = []
    for proj in launched:
        ph = sorted(proj.phases, key=lambda e: phase_order(e.get("estateNameEn") or ""))
        first = ph[0]
        def phase_label(e: dict) -> str:
            return ((e.get("estateNameEn") or "").strip()
                    or (e.get("estateNameWithCulture") or "").strip()
                    or (e.get("estateNameZH") or "").strip())
        labels = [phase_label(e) for e in ph]
        name = common_project_name(labels)
        display = f"{name} Series" if len(ph) > 1 else (labels[0] or name)

        total = sum(e.get("_total") or 0 for e in ph)
        sold = sum(e.get("_sold") or 0 for e in ph)
        remaining = total - sold
        fs = [e["_first_sale"] for e in ph if e.get("_first_sale")]
        emds = sorted({e["_emd"] for e in ph if e.get("_emd")})
        first_sale = min(fs)
        rows.append({
            "project": display or "(未命名)",
            "phases": len(ph),
            "phase_names": " | ".join(labels),
            "address": proj.address.strip(),
            "main_developer": proj.developer.strip(),
            "total_units": total,
            "sold_units": sold,
            "remaining_units": remaining,
            "remaining_pct": round(remaining / total * 100, 1) if total else None,
            "first_sales_date": first_sale.isoformat(),
            "months_since_launch": months_between(first_sale, today),
            "estimated_material_date": (
                emds[0].isoformat() if len(emds) == 1
                else (f"{emds[0].isoformat()} ~ {emds[-1].isoformat()}" if emds else "")
            ),
        })

    df = pd.DataFrame(rows).sort_values("remaining_units", ascending=False).reset_index(drop=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print()
    print(f"已开售项目 {len(df)} 个（{int(df['phases'].sum())} 期）")
    print(f"总货量 {int(df['total_units'].sum()):,} 伙 | "
          f"已售 {int(df['sold_units'].sum()):,} 伙 | "
          f"市场余货 {int(df['remaining_units'].sum()):,} 伙 "
          f"({df['remaining_units'].sum()/df['total_units'].sum()*100:.1f}%)")
    print(f"输出: {out}")


if __name__ == "__main__":
    main()
