"""
项目地图数据：把 CSDI 上带坐标的官方记录串成「地 → 楼 → 售」一条链。

  卖地 / 换地 / 契约修订（地政总署）→ 批则 → 动工 → 预售同意 → 入伙纸（屋宇署 / 地政总署）

匹配全靠坐标 + 地段号：同一地盘的记录在各图层里坐标几乎一致（中位数 3 米），
跨图层没有共同的项目编号，地段号是唯一能对上的键，但只有地政总署的记录才有。

产出 out_inventory/land_chain.json，看板的地图块直接读它。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CSDI = "https://portal.csdi.gov.hk/server/rest/services/{layer}/FeatureServer/0/query"

# CSDI 的服务 ID 是发布时生成的，一直没变过。真变了去 services 目录按 name 找：
# https://portal.csdi.gov.hk/server/rest/services?f=pjson
LAYERS = {
    "presale": "common/landsd_rcd_1637303511514_65978",   # LAO_PCRD 批出预售同意书
    "landsale": "common/landsd_rcd_1631600922343_18223",  # LAO_LSR 卖地记录
    "leasemod": "common/landsd_rcd_1637225104335_55505",  # LAOLMC 已签立契约修订
    "exchange": "common/landsd_rcd_1637224372675_83413",  # LAOLEC 已签立换地
    "lotext":   "common/landsd_rcd_1637904522249_2213",   # LAO_LEE 已签立地段扩展
    "bd_plan":  "common/bd_rcd_1629267205235_1286",       # BDMD53 已批图则
    "bd_start": "common/bd_rcd_1629267205235_33875",      # BDMD54 已发施工同意书
    "bd_notify": "common/bd_rcd_1629267205236_22761",     # BDMD55 已收到上盖工程动工通知
    "bd_op":    "common/bd_rcd_1629267205236_397",        # BDMD56 已发佔用许可证
}

# 只看 2010 年起的卖地 / 换地 / 契约修订 / 批则 / 预售；更早的不进（屋宇署月报 2011-06 起才有）
FROM_YEAR = 2010
PRESALE_FROM_YEAR = FROM_YEAR
LAND_FROM_YEAR = FROM_YEAR
LAND_LOOKBACK_YEARS = 15      # 土地记录早于项目动工 / 预售超过这个年数，就不算这个项目的来源

HKT = timezone(timedelta(hours=8))


# ----------------------------------------------------------------------------
# 抓取
# ----------------------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 hk-primary-inventory"
    return s


def _get_json(s: requests.Session, url: str, params: dict) -> dict:
    """先走系统代理，代理把政府站点挡了就直连。"""
    last = None
    for trust in (True, False):
        s.trust_env = trust
        try:
            r = s.get(url, params=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception as e:      # noqa: BLE001
            last = e
    raise RuntimeError(f"CSDI 请求失败: {url} {last}")


def fetch_layer(s: requests.Session, layer: str) -> pd.DataFrame:
    rows, offset = [], 0
    while True:
        j = _get_json(s, CSDI.format(layer=layer), {
            "where": "1=1", "outFields": "*", "f": "json",
            "orderByFields": "OBJECTID", "resultOffset": offset, "resultRecordCount": 1000,
        })
        if "error" in j:
            raise RuntimeError(f"CSDI 返回错误: {layer} {j['error']}")
        feats = j.get("features", [])
        rows += [f["attributes"] for f in feats]
        if len(feats) < 1000:
            break
        offset += 1000
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError(f"CSDI 图层为空: {layer}")
    return df


def fetch_all(s: requests.Session | None = None) -> dict[str, pd.DataFrame]:
    s = s or _session()
    out = {}
    for key, layer in LAYERS.items():
        out[key] = fetch_layer(s, layer)
        print(f"  [land_chain] {key:<9} {len(out[key]):>6} 条")
    return out


# ----------------------------------------------------------------------------
# 规范化
# ----------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(np.radians(lon2 - lon1) / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


_LOT_SKIP = {"AND", "THE", "EXTENSION", "TO", "EXTENSIONS", "THERETO", "OF", "NOS", "NO",
             "REMAINING", "PORTION", "SECTION", "SUBSECTION", "SEC", "SS", "RP", "EXT"}
# 官方短码（预售同意书 / 卖地记录里的写法）；地址里的全名取词时从后往前凑，凑出表里的才算
_LOT_ABBR = {"IL", "ML", "KIL", "NKIL", "KML", "NKML", "RBL", "STTL", "TMTL", "TPTL", "FSSTL", "KTIL", "AIL", "AML",
             "TKOTL", "KCTL", "YLTL", "TWTL", "TWIL", "TYTL", "HHIL", "HHML", "SKWIL", "ALCIL", "CWIL", "CWML", "TCTL",
             "SIL", "SCIL", "SBIL", "SBML", "YTIL", "YTML", "TSWTL", "QBML", "QBIL", "JBTL", "TLTL", "CPTL", "MWL",
             "THL", "SL", "SAIL", "SAML", "SOIL", "SHIL", "SLIL", "SDIL", "SFIL", "SGIL", "SUIL", "SEIL", "SKIL",
             "SSIL", "SMIL", "PSIL", "HSKTL", "CKWL", "CLKL", "GL", "LL", "KCL", "WL", "MTRL", "SCSL"}
_LOT_ALIAS = {"APLIL": "ALCIL", "APIL": "ALCIL", "ALIL": "ALCIL", "SKWIL": "SIL"}   # 筲箕湾官方写 S.I.L.
# 地址里地段前缀之前常常还有街名、岛名、区名，遇到这些词就从它后面开始取
_LOT_CUT = re.compile(r"\b(?:STREET|ROAD|LANE|AVENUE|PATH|TERRACE|DRIVE|CIRCUIT|SQUARE|PLAZA|ISLAND|AREA|ESTATE|VILLAGE|"
                      r"SITE|PHASE|SECTION|JUNCTION|CORNER|OFF|AT|IN|ON|OPPOSITE|NEAR|LOTS?|R\.?P\.?|EXT|EXTS)\b|[\d,;:()&/]")


def canon_lots(text) -> frozenset:
    """各库写法不同的地段号统一成短码：
    'Kowloon Inland Lot No. 10557' / 'KIL 10557' / 'N.K.I.L. 6584' -> 'KIL 10557' / 'NKIL 6584'
    '6 Ying Hong Street Lantau Island Tung Chung Town Lot No. 36' -> 'TCTL 36'
    'Lot No. 313 in Demarcation District No. 355' / 'D.D. 92 Lot 2640' -> 'DD355 LOT 313' / 'DD92 LOT 2640'
    """
    t = re.sub(r"<br\s*/?>", " ", str(text or "")).upper()
    t = re.sub(r"\b((?:[A-Z]\.){2,})", lambda m: m.group(1).replace(".", ""), t)   # T.K.O.T.L. -> TKOTL
    t = re.sub(r"\bMA\s+WAN\b", "MWL", t)          # 'MA WAN 392' 就是马湾地段
    out = set()
    # 丈量约份地段，两种语序都有；认出来后从文本里抹掉，免得被下面的规则误读
    dd = (r"LOT\s+(?:NO\.?\s*)?(\d+)\b[^,;]{0,40}?\bIN\s+(?:DEMARCATION\s+DISTRICT|DD)\s*(?:NO\.?)?\s*(\d+)",
          r"(?:DEMARCATION\s+DISTRICT|DD)\s*(?:NO\.?)?\s*(\d+)\s+LOT\s+(?:NO\.?\s*)?(\d+)\b")
    for pat, order in ((dd[0], (1, 2)), (dd[1], (2, 1))):
        for m in re.finditer(pat, t):
            out.add(f"DD{m.group(order[1])} LOT {m.group(order[0])}")
        t = re.sub(pat, " ", t)
    for m in re.finditer(r"([A-Z][A-Z .]*?)\s+LOT\s+NO\.?\s*(\d+)", t):
        prefix = _LOT_CUT.split(m.group(1))[-1]
        words = [w for w in prefix.replace(".", "").split() if w not in _LOT_SKIP and (len(w) > 1 or w == "O")]
        abbr = ""
        for k in range(1, min(5, len(words)) + 1):        # 取最长的、在官方表里的前缀
            cand = "".join(w[0] for w in words[-k:]) + "L"
            if _LOT_ALIAS.get(cand, cand) in _LOT_ABBR:
                abbr = _LOT_ALIAS.get(cand, cand)
        if not abbr and words:
            abbr = "".join(w[0] for w in words[-4:]) + "L"
        if abbr:
            out.add(f"{abbr} {m.group(2)}")
    # 已经是短码的写法。IL / ML 只有两个字母，单位数也可能只有一位（HSKTL 1），
    # 所以放宽到 1~6 个字母 + 1~5 位数，再用官方短码表过滤，避免把随便一个 "L 3" 当成地段
    for m in re.finditer(r"\b([A-Z]{1,6}L)\s+(\d{1,5})\b", t):
        ab = _LOT_ALIAS.get(m.group(1), m.group(1))
        if ab in _LOT_ABBR:
            out.add(f"{ab} {m.group(2)}")
    # 测量约份（Survey District）：'Lot 1074 in SD 3'
    for m in re.finditer(r"LOT\s+(?:NO\.?\s*)?(\d+)\b[^,;]{0,40}?\bIN\s+(?:SURVEY\s+DISTRICT|SD)\s*(?:NO\.?)?\s*(\d+)", t):
        out.add(f"SD{m.group(2)} LOT {m.group(1)}")
    # 'Lots 724 & 726 in DD 332' —— 一条记录里多个地段共用一个约份号
    m = re.search(r"LOTS\s+([\d\s&,and]+?)\s+IN\s+(?:DEMARCATION\s+DISTRICT|DD)\s*(?:NO\.?)?\s*(\d+)", t)
    if m:
        for num in re.findall(r"\d+", m.group(1)):
            out.add(f"DD{m.group(2)} LOT {num}")
    return frozenset(out)


def norm_ap(s) -> frozenset:
    s = str(s or "").split(" - ")[0]
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return frozenset(t for t in s.split() if len(t) > 1)


def norm_co(s) -> str:
    s = re.sub(r"\b(limited|ltd|company|co|holdings?|development|developments|investments?)\b", "",
               str(s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


# 只关心私人住宅：房協 / 房委会 / 政府的资助出售、公屋、简约公屋、过渡性房屋一律不要
NON_PRIVATE_OWNER = ("房協", "房委会")
_NON_PRIVATE_PARTY = re.compile(r"HOUSING SOCIETY|HOUSING AUTHORITY|HOUSING DEPARTMENT|ARCHITECTURAL SERVICES|"
                                r"URBAN RENEWAL AUTHORITY.*SUBSIDI|HONG KONG SETTLERS", re.I)
_NON_PRIVATE_TYPE = re.compile(r"Public rental|Public housing|Light public|Transitional|Subsidi[sz]ed|Home ownership|"
                               r"Green form|Starter home|Elderly hous|Residential care|Hostel|Dormitor|Staff quarter|Government quarter|Departmental quarter|Married quarter", re.I)


def is_public(party: str = "", btype: str = "") -> bool:
    return bool(_NON_PRIVATE_PARTY.search(str(party or ""))) or bool(_NON_PRIVATE_TYPE.search(str(btype or "")))


def company_keys(text) -> frozenset:
    """'Macfull Limited (China Overseas Land & Investment)' / 'A Ltd<br/>B Ltd' -> 每个名字一个规范 token。"""
    t = re.sub(r"<br\s*/?>", ";", str(text or ""))
    return frozenset(k for k in (norm_co(x) for x in re.split(r"[;()]", t)) if len(k) >= 5)


def company_sim(a: frozenset, b: frozenset) -> float:
    """两组公司名的最大相似度，容拼写错（Investmant / Haircourt 这种官方录入错误不少）。"""
    best = 0.0
    for x in a:
        for y in b:
            if x == y:
                return 1.0
            best = max(best, difflib.SequenceMatcher(None, x, y).ratio())
    return best


HK_BBOX = (22.13, 22.60, 113.80, 114.50)      # lat_min, lat_max, lon_min, lon_max


def clean_xy(df: pd.DataFrame) -> pd.DataFrame:
    """几个图层里都有零星记录的坐标落到深圳甚至更远，当缺失处理。"""
    lat = pd.to_numeric(df["lat"], errors="coerce")
    lon = pd.to_numeric(df["lon"], errors="coerce")
    ok = (lat >= HK_BBOX[0]) & (lat <= HK_BBOX[1]) & (lon >= HK_BBOX[2]) & (lon <= HK_BBOX[3])
    df = df.copy()
    df["lat"] = lat.where(ok)
    df["lon"] = lon.where(ok)
    return df


def to_num(x):
    return pd.to_numeric(pd.Series(x).astype(str).str.replace(r"<br\s*/?>.*", "", regex=True)
                         .str.replace(",", "").str.extract(r"(-?\d+\.?\d*)")[0], errors="coerce")


def ym(df: pd.DataFrame, ycol="SEARCH01_EN", mcol="SEARCH02_EN") -> pd.Series:
    y = pd.to_numeric(df[ycol], errors="coerce")
    m = pd.to_numeric(df[mcol], errors="coerce")
    return y.astype("Int64").astype(str) + "-" + m.astype("Int64").astype(str).str.zfill(2)


def owner_of(vendor: str) -> str:
    v = str(vendor or "").upper()
    if "MTR CORPORATION" in v or "KOWLOON-CANTON RAILWAY" in v:
        return "港铁"
    # 九铁西铁沿线物业发展公司：以站名命名的专属公司（元朗、朗屏、荃湾西 TW5/TW6/TW7…）
    if re.match(r"^(YUEN LONG|TSUEN WAN WEST|LONG PING|TIN SHUI WAI|KAM SHEUNG ROAD|NAM CHEONG|"
                r"TUEN MUN|WEST RAIL)[A-Z0-9 ]*PROPERTY DEVELOPMENT LIMITED$", v.strip()):
        return "港铁"
    if "URBAN RENEWAL AUTHORITY" in v:
        return "市建局"
    if "HOUSING SOCIETY" in v:
        return "房協"
    if "HOUSING AUTHORITY" in v or "HOUSING DEPARTMENT" in v:
        return "房委会"
    if re.search(r"\bMTR CORP", v):
        return "港铁"
    if "HONG KONG RESORT" in v:
        return "愉景湾"
    return "私人"


def _clean_zh(n: str) -> str:
    n = re.sub(r"[（(][^)）]*[)）]?", " ", str(n))               # 括号里的期数 / 子名
    n = re.sub(r"第\s*[一二三四五六七八九十0-9A-Z\-]+\s*期", " ", n)
    n = re.split(r"\s*[-–—]\s*", n)[0]                          # "迎海 - 迎海．御峰" 取前半
    n = n.replace("發展項目", "").replace("待定", "").strip(" -–,·．")
    return n


def _clean_en(n: str) -> str:
    n = re.sub(r"\(.*?\)?$", " ", str(n))
    n = re.sub(r"\bphase\s+[\w-]+\s+of\s+", "", n, flags=re.I)
    n = re.split(r"\s*[-–—]\s*", n)[0]
    n = re.sub(r"\bphase\s+[\w-]+\b|\bdevelopment\b|\bpending\b", " ", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip(" -–,")


def site_name(zh_names, en_names) -> tuple[str, str]:
    def pick(names, fn):
        cands = [c for c in (fn(n) for n in names if str(n).strip()) if c]
        if not cands:
            return ""
        top = Counter(cands).most_common()
        best = max(top[0][1] for _ in [0])
        return min((c for c, k in top if k == best), key=len)
    return pick(zh_names, _clean_zh), pick(en_names, _clean_en)


_PHASE_CN = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7",
             "八": "8", "九": "9", "十": "10", "十一": "11", "十二": "12", "十三": "13"}
_PHASE_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9,
                "X": 10, "XI": 11, "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16}


def phase_token(text) -> str:
    """期数标签统一成「阿拉伯数字 + 字母」：

    'Phase IVA of LOHAS Park -Wings at Sea' / 'PHASE IVA' / '日出康城 第5A期 -MALIBU' -> '4A'
    '日出康城第XIIC期' -> '12C'（XII 是罗马数字、C 是子期编号，不能当成罗马数字 100）
    '西沙灣發展項目 (第1A(2)期)' -> '1A(2)'
    """
    t = re.sub(r"\s+", " ", str(text or "")).strip().upper()
    for zh, ar in _PHASE_CN.items():          # 第一期 / 第二期 …
        t = t.replace(f"第{zh}期", f"第{ar}期").replace(f"第 {zh} 期", f"第{ar}期")
    # 后缀字母必须紧跟数字且后面不再是字母，否则 'PHASE 1 OF THE HENLEY' 会被读成 '1O'
    m = (re.search(r"PHASE\s*([IVXLC]+|\d+)\s*([A-Z](?![A-Z]))?\s*(\(\d+\))?", t)
         or re.search(r"第\s*([IVXLC]+|\d+)\s*([A-Z](?![A-Z]))?\s*(\(\d+\))?\s*期", t)
         or re.fullmatch(r"([IVXLC]+|\d+)\s*([A-Z](?![A-Z]))?\s*(\(\d+\))?", t))
    if not m:
        return ""
    head, suffix, paren = m.group(1), m.group(2) or "", m.group(3) or ""
    if head.isdigit():
        num = int(head)
    else:
        # 罗马数字后面常直接跟子期字母（XIIC = 第 12 期 C），取表里能对上的最长前缀
        num = None
        for k in sorted(_PHASE_ROMAN, key=len, reverse=True):
            if head.startswith(k):
                num, suffix = _PHASE_ROMAN[k], head[len(k):] + suffix
                break
        if num is None:
            return ""
    return f"{num}{suffix}{paren}"


# ----------------------------------------------------------------------------
# 并查集 + 地盘聚类
# ----------------------------------------------------------------------------
class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def f(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def u(self, a, b):
        ra, rb = self.f(a), self.f(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def cluster(df: pd.DataFrame, radius: float, same) -> pd.Series:
    """radius 米内且 same(i,j) 成立的记录归为一个地盘。"""
    n = len(df)
    uf = UF(n)
    lat, lon = df.lat.values, df.lon.values
    for i in range(n):
        d = haversine(lat[i], lon[i], lat[i + 1:], lon[i + 1:])
        for k in np.where(np.nan_to_num(d, nan=np.inf) <= radius)[0]:
            j = i + 1 + int(k)
            if same(i, j):
                uf.u(i, j)
    roots = [uf.f(i) for i in range(n)]
    ids = {r: k for k, r in enumerate(sorted(set(roots)))}
    return pd.Series([ids[r] for r in roots], index=df.index)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def build(raw: dict[str, pd.DataFrame]) -> dict:
    # ---------- 预售 ----------
    ps = raw["presale"].rename(columns={
        "NAME_EN": "lot", "ADDRESS_EN": "address", "ADDRESS_TC": "address_zh", "SEARCH03_EN": "vendor",
        "NSEARCH01_EN": "name_en", "NSEARCH01_TC": "name_zh", "NSEARCH04_EN": "ap",
        "NSEARCH13_EN": "units", "NSEARCH11_EN": "est_completion", "LATITUDE": "lat", "LONGITUDE": "lon"})
    for c in ("lot", "address", "address_zh", "vendor", "name_en", "name_zh", "ap"):
        ps[c] = ps[c].fillna("").astype(str)
    ps["units"] = to_num(ps.units).fillna(0)
    ps["ym"] = ym(ps)
    ps["yr"] = pd.to_numeric(ps.SEARCH01_EN, errors="coerce")
    ps["owner"] = ps.vendor.map(owner_of)
    ps = clean_xy(ps)
    ps = ps[(ps.yr >= PRESALE_FROM_YEAR) & (ps.units > 0) & (ps.lat.notna() | (ps.lot.map(canon_lots).map(len) > 0))]
    ps = ps[~ps.owner.isin(NON_PRIVATE_OWNER) & ~ps.vendor.map(is_public)]
    ps = ps.reset_index(drop=True)
    ps["lots"] = ps.lot.map(canon_lots)
    ps["ap_n"] = ps.ap.map(norm_ap)
    # 同一地段号，或 30 米内同一认可人士 -> 同一地盘（同一屋苑的各期）
    lots, aps = ps.lots.values, ps.ap_n.values
    ps["site"] = cluster(ps, 30, lambda i, j: bool(lots[i] & lots[j]) or len(aps[i] & aps[j]) >= 2)
    # 地段号相同但相距超过 30 米的（大型屋苑）也要合并
    uf = UF(int(ps.site.max()) + 1)
    by_lot: dict[str, int] = {}
    for s, ls in zip(ps.site, ps.lots):
        for lt in ls:
            if lt in by_lot:
                uf.u(by_lot[lt], s)
            else:
                by_lot[lt] = s
    ps["site"] = [uf.f(s) for s in ps.site]

    # ---------- 屋宇署 ----------
    # 动工证据 = 5.4 施工同意书 ∪ 5.5 上盖动工通知（5.5 比 5.4 晚约一个月，各自都有漏登，取并集）。
    # 住宅塔楼登记时一定带伙数；0 伙的是休憩空间上的凉亭、厕所、花架这类附属构筑物，不算动工证据。
    def _bd_works(df, table):
        df = df.rename(columns={"ADDRESS_EN": "address", "NSEARCH02_EN": "btype", "NSEARCH03_EN": "units",
                                "NSEARCH08_EN": "ap", "NSEARCH10_EN": "applicant", "LATITUDE": "lat", "LONGITUDE": "lon"})
        for c in ("address", "btype", "ap", "applicant"):
            df[c] = df[c].fillna("").astype(str)
        df["units"] = to_num(df.units).fillna(0)
        df["ym"] = ym(df)
        df["table"] = table
        return df
    b_start = pd.concat([_bd_works(raw["bd_start"], "5.4"), _bd_works(raw["bd_notify"], "5.5")], ignore_index=True)
    b_start = clean_xy(b_start)
    # 只要私人住宅：过渡性房屋、简约公屋、公屋、资助出售、宿舍这些不要，房協 / 房委会 / 建筑署申请的也不要
    b_start = b_start[~b_start.btype.map(lambda x: is_public("", x)) & ~b_start.applicant.map(lambda x: is_public(x, ""))]
    b_start["lots"] = b_start.address.map(canon_lots)
    res_like = b_start.btype.str.contains(r"Apartment|Residential|House|Domestic|Villa|Flat|Composite", case=False, regex=True)
    b_start = b_start[b_start.lat.notna() & ((b_start.units > 0) | res_like)].reset_index(drop=True)
    b_start["owner"] = b_start.applicant.map(owner_of)
    b_start["ap_n"] = b_start.ap.map(norm_ap)
    b_start["app_n"] = b_start.applicant.map(norm_co)
    aps, apps, lots_ = b_start.ap_n.values, b_start.app_n.values, b_start.lots.values
    b_start["site"] = cluster(b_start, 30, lambda i, j: len(aps[i] & aps[j]) >= 2 or (apps[i] and apps[i] == apps[j]) or bool(lots_[i] & lots_[j]))
    # 同一地段号、相距超过 30 米的也并成一个地盘（大地盘 5.4 和 5.5 的点可以差一百多米）
    uf = UF(int(b_start.site.max()) + 1)
    by_lot = {}
    for st, ls_ in zip(b_start.site, b_start.lots):
        for lt in ls_:
            if lt in by_lot:
                uf.u(by_lot[lt], st)
            else:
                by_lot[lt] = st
    b_start["site"] = [uf.f(x) for x in b_start.site]

    b_plan = raw["bd_plan"].rename(columns={"ADDRESS_EN": "address", "NSEARCH05_EN": "ap",
                                            "NSEARCH03_EN": "dom_gfa", "LATITUDE": "lat", "LONGITUDE": "lon"})
    b_plan["dom_gfa"] = to_num(b_plan.dom_gfa).fillna(0)
    b_plan["ym"] = ym(b_plan)
    b_plan = clean_xy(b_plan)
    b_plan = b_plan[b_plan.ym >= f"{FROM_YEAR}-01"]
    b_plan = b_plan[~b_plan["NSEARCH07_EN"].fillna("").map(lambda x: is_public(x, ""))]
    b_plan["lots"] = b_plan.address.fillna("").map(canon_lots)
    b_plan = b_plan[b_plan.lat.notna()].reset_index(drop=True)
    b_plan["ap_n"] = b_plan.ap.map(norm_ap)

    b_op = raw["bd_op"].rename(columns={"ADDRESS_EN": "address", "NSEARCH05_EN": "units",
                                        "NSEARCH10_EN": "ap", "LATITUDE": "lat", "LONGITUDE": "lon"})
    b_op["units"] = to_num(b_op.units).fillna(0)
    b_op["ym"] = ym(b_op)
    b_op = clean_xy(b_op)
    b_op = b_op[~b_op["NSEARCH12_EN"].fillna("").map(lambda x: is_public(x, "")) & ~b_op["NSEARCH04_EN"].fillna("").map(lambda x: is_public("", x))]
    b_op["lots"] = b_op.address.fillna("").map(canon_lots)
    b_op = b_op[(b_op.units > 0) & b_op.lat.notna()].reset_index(drop=True)
    b_op["ap_n"] = b_op.ap.map(norm_ap)

    # ---------- 地政总署土地记录 ----------
    ls = raw["landsale"].rename(columns={
        "NAME_EN": "lot", "ADDRESS_EN": "address", "SEARCH03_EN": "disposal", "NSEARCH01_EN": "date",
        "NSEARCH02_EN": "use", "NSEARCH03_EN": "area", "NSEARCH04_EN": "premium_m",
        "NSEARCH06_EN": "party", "LATITUDE": "lat", "LONGITUDE": "lon"})
    ls = ls[ls.use.astype(str).str.contains("RESIDENTIAL", case=False)].copy()
    ls["kind"] = "卖地(" + ls.disposal.astype(str).str.title().str.replace("Letter A/B", "換地權益書") + ")"
    ls["premium_m"] = to_num(ls.premium_m)
    ls["area"] = to_num(ls.area)

    def _lease(df, kind, area_col=None):
        df = df.rename(columns={"NAME_EN": "lot", "ADDRESS_EN": "address", "NSEARCH01_EN": "date",
                                "LATITUDE": "lat", "LONGITUDE": "lon"})
        if kind == "契约修订":
            df = df.rename(columns={"NSEARCH02_EN": "use", "NSEARCH03_EN": "premium"})
        else:
            df = df.rename(columns={"NSEARCH02_EN": "area_ha", "NSEARCH03_EN": "use", "NSEARCH04_EN": "premium"})
            df["area"] = to_num(df.area_ha) * 10000
        df["use"] = df.use.astype(str).str.replace(r"<br\s*/?>.*", "", regex=True).str.strip()
        df = df[df.use.str.contains(r"Residential|Virtually Unrestricted|Private Sector", case=False, regex=True)].copy()
        df["kind"] = kind
        df["premium_m"] = to_num(df.premium) / 1e6
        df["party"] = ""
        return df

    land = pd.concat([
        ls, _lease(raw["exchange"], "换地"), _lease(raw["leasemod"], "契约修订"), _lease(raw["lotext"], "地段扩展"),
    ], ignore_index=True)
    land["date"] = pd.to_datetime(land.date.astype(str).str.replace(r"<br\s*/?>.*", "", regex=True),
                                  errors="coerce", format="mixed")
    land = clean_xy(land)
    land = land[land.date.dt.year >= LAND_FROM_YEAR]
    land = land[~land.party.fillna("").map(lambda x: is_public(x, "")) & ~land.use.fillna("").str.contains("Private Sector Participation|Subsidi", case=False, regex=True)]
    land["lots"] = land.lot.map(canon_lots)
    land = land[land.lat.notna() | (land.lots.map(len) > 0)].reset_index(drop=True)
    land["area"] = to_num(land.area) if "area" in land else np.nan
    for c in ("use", "party", "address"):
        land[c] = land[c].fillna("").astype(str).str.replace(r"<br\s*/?>", "; ", regex=True)

    # ---------- 聚合成地盘 ----------
    def agg_sites(df, extra):
        g = df.groupby("site")
        out = pd.DataFrame({
            "lat": g.lat.median(), "lon": g.lon.median(), "units": g.units.sum(), "n": g.size(),
            "first_ym": g.ym.min(), "last_ym": g.ym.max(),
            "aps": g.ap_n.agg(lambda s: frozenset().union(*s)),
            "lots": g.lots.agg(lambda s: frozenset().union(*s)),
        })
        for k, fn in extra.items():
            out[k] = g.apply(fn)
        return out[out.lat.notna()].reset_index(drop=True)

    PS = agg_sites(ps, {
        "names": lambda d: list(dict.fromkeys(d.sort_values("ym").name_en)),
        "names_zh": lambda d: list(dict.fromkeys(d.sort_values("ym").name_zh.fillna(""))),
        "address": lambda d: d.address.iloc[-1],
        "address_zh": lambda d: d.address_zh.iloc[-1],
        "vendor": lambda d: d.vendor.iloc[-1],
        "owner": lambda d: next(o for o in ("港铁", "市建局", "房協", "房委会", "愉景湾", "私人") if o in set(d.owner)),
        "ap": lambda d: d.ap.iloc[-1],
        "phases": lambda d: [{"name": r.name_zh or r.name_en, "name_en": r.name_en, "ym": r.ym, "units": int(r.units)}
                             for r in d.sort_values("ym").itertuples()],
    })
    BD = agg_sites(b_start, {
        "units": lambda d: int(max(d[d.table == "5.4"].units.sum(), d[d.table == "5.5"].units.sum())),
        "address": lambda d: d.sort_values("units", ascending=False).address.iloc[0], "applicant": lambda d: d.applicant.iloc[-1],
        "btype": lambda d: d.btype.iloc[-1], "ap": lambda d: d.ap.iloc[-1],
        "owner": lambda d: next(o for o in ("港铁", "市建局", "房協", "房委会", "愉景湾", "私人") if o in set(d.owner)),
        "app_n": lambda d: norm_co(d.applicant.iloc[-1]),
        "cos": lambda d: frozenset().union(*d.applicant.map(company_keys)),
    })

    # ---------- 批则 / 入伙 -> 动工地盘 ----------
    def attach_bd(src, radius, after=None):
        Ds = haversine(BD.lat.values[:, None], BD.lon.values[:, None], src.lat.values[None, :], src.lon.values[None, :])
        lot_ix: dict[str, list[int]] = {}
        for k, lts in enumerate(src.lots):
            for lt in lts:
                lot_ix.setdefault(lt, []).append(k)
        res = []
        for j in range(len(BD)):
            by_lot = {k for lt in BD.lots[j] for k in lot_ix.get(lt, [])}
            idx = by_lot | {int(k) for k in np.where(Ds[j] <= radius)[0]
                            if (Ds[j, k] <= 30 or len(BD.aps[j] & src.ap_n[k]) >= 2)
                            and not (src.lots[k] and BD.lots[j] and not (src.lots[k] & BD.lots[j]))}
            res.append(sorted(k for k in idx if after is None or src.ym[k] >= BD[after][j]))
        return res
    BD["plan_idx"] = attach_bd(b_plan, 120)
    BD["op_idx"] = attach_bd(b_op, 120, after="first_ym")     # 入伙纸不可能早于动工
    BD["plan_ym"] = BD.plan_idx.map(lambda ix: min(b_plan.ym[ix]) if ix else "")
    BD["op_ym"] = BD.op_idx.map(lambda ix: min(b_op.ym[ix]) if ix else "")
    BD["op_last_ym"] = BD.op_idx.map(lambda ix: max(b_op.ym[ix]) if ix else "")
    BD["op_units"] = BD.op_idx.map(lambda ix: int(b_op.units[ix].sum()) if ix else 0)

    # ---------- 土地记录 -> 地盘 ----------
    def attach_land(S, base_radius=60, use_company=False):
        Dl = haversine(S.lat.values[:, None], S.lon.values[:, None], land.lat.values[None, :], land.lon.values[None, :])
        Dl = np.nan_to_num(Dl, nan=np.inf)
        lot_index: dict[str, list[int]] = {}
        for k, lts in enumerate(land.lots):
            for lt in lts:
                lot_index.setdefault(lt, []).append(k)
        # 卖地记录的点是地块、屋宇署的点是楼，大地盘能差一两百米：半径随 √面积 放
        rad = base_radius + np.sqrt(land.area.fillna(0).values)
        res = []
        for i in range(len(S)):
            by_lot = {k for lt in S.lots[i] for k in lot_index.get(lt, [])}
            near = {int(k) for k in np.where(Dl[i] <= rad)[0]
                    if not (S.lots[i] and land.lots[k] and not (S.lots[i] & land.lots[k]))}   # 地段号明显不同的否决
            by_co = set()
            if use_company and S.cos[i]:
                for k in np.where(Dl[i] <= 600)[0]:
                    if company_sim(S.cos[i], land.cos[k]) >= 0.85:
                        by_co.add(int(k))
            # 地段号对上的不看距离：来源库里有些点的坐标错到几十公里外
            idx = sorted(by_lot | near | by_co, key=lambda k: (k not in by_lot, land.date[k] if pd.notna(land.date[k]) else pd.Timestamp.max))
            res.append([(k, "地段号" if k in by_lot else "坐标" if k in near else "公司名") for k in idx])
        return res
    land["cos"] = land.party.map(company_keys)
    PS["land_idx"] = attach_land(PS)
    BD["land_idx"] = attach_land(BD, use_company=True)
    # 批地必须早于动工 / 预售，否则接到的是同址旧楼或隔壁盘；地段号对上的例外（同一地段转手再发展）
    def _in_time(ix, limit_ym):
        # 也不能太早：几十年前卖过的地，现在的项目是收购旧楼重建，那笔卖地不是它的来源
        floor_ym = f"{int(limit_ym[:4]) - LAND_LOOKBACK_YEARS}-01"
        out = []
        for k, how in ix:
            d = land.date[k]
            if pd.isna(d):
                out.append((k, how))
                continue
            dy = d.strftime("%Y-%m")
            if dy < floor_ym:
                continue
            if how == "地段号" or dy <= limit_ym:
                out.append((k, how))
        return out
    BD["land_idx"] = [_in_time(ix, BD.first_ym[j]) for j, ix in enumerate(BD.land_idx)]
    PS["land_idx"] = [_in_time(ix, PS.first_ym[i]) for i, ix in enumerate(PS.land_idx)]
    PS["land_how"] = [dict(ix) for ix in PS.land_idx]
    BD["land_how"] = [dict(ix) for ix in BD.land_idx]
    PS["land_idx"] = [[k for k, _ in ix] for ix in PS.land_idx]
    BD["land_idx"] = [[k for k, _ in ix] for ix in BD.land_idx]

    # ---------- 预售地盘 -> 动工地盘（一对多）----------
    # A: 30 米内直接算同一地盘
    # B: 30~300 米，认可人士相同且单位数对得上（或单位数几乎相等）
    # C: 1.5 公里内共享同一份土地记录，且认可人士 / 申请人一致 —— 日出康城这种跨一公里的大盘
    D = haversine(PS.lat.values[:, None], PS.lon.values[:, None], BD.lat.values[None, :], BD.lon.values[None, :])
    PS_cos = PS.vendor.map(company_keys)
    bd_of: list[list[int]] = [[] for _ in range(len(PS))]
    for i in range(len(PS)):
        picked = set(int(j) for j in np.where(D[i] <= 30)[0])
        if not picked:
            cands = []
            for j in np.where((D[i] > 30) & (D[i] <= 300))[0]:
                pu, bu = PS.units[i], BD.units[j]
                ap_ok = len(PS.aps[i] & BD.aps[j]) >= 2
                u_close = abs(pu - bu) / max(bu, 1) <= 0.10 or (pu < bu and pu / bu >= 0.3)
                if ap_ok and u_close:
                    cands.append((0, D[i, j], int(j)))
                elif abs(pu - bu) / max(bu, 1) <= 0.05:
                    cands.append((1, D[i, j], int(j)))
            if cands:
                picked.add(min(cands)[2])
        my_land = set(PS.land_idx[i])
        for j in np.where(D[i] <= 1500)[0]:
            j = int(j)
            if j in picked:
                continue
            ap_same = len(PS.aps[i] & BD.aps[j]) >= 2
            party_same = company_sim(PS_cos[i], BD.cos[j]) >= 0.85 or (PS.owner[i] == BD.owner[j] != "私人")
            shared_land = bool(my_land & set(BD.land_idx[j]))
            lot_same = bool(PS.lots[i] & BD.lots[j])
            if lot_same or (shared_land and (ap_same or party_same)) or (ap_same and party_same):
                picked.add(j)
        bd_of[i] = sorted(picked, key=lambda j: D[i, j])
    PS["bd"] = bd_of
    BD["presold"] = False
    BD.loc[sorted({j for js in bd_of for j in js}), "presold"] = True

    def direct_hits(src, lat, lon, lots, radius, aps=None, after="", units=None):
        """没有动工地盘可挂时，批则 / OP 直接按地段号或半径找：30 米内直接算，再远要认可人士相同或伙数对得上。"""
        hit = {k for k in range(len(src)) if lots & src.lots[k]}
        d = haversine(lat, lon, src.lat.values, src.lon.values)
        for k in np.where(d <= radius)[0]:
            k = int(k)
            if src.lots[k] and lots and not (lots & src.lots[k]):
                continue
            u_ok = units and "units" in src and abs(src.units[k] - units) / units <= 0.15
            if d[k] <= 30 or aps is None or len(aps & src.ap_n[k]) >= 2 or u_ok:
                hit.add(k)
        return sorted(k for k in hit if src.ym[k] >= after)

    def bd_agg(js):
        if not js:
            return None
        sub = BD.iloc[js]
        return {
            "units": int(sub.units.sum()), "first_ym": sub.first_ym.min(),
            "plan_ym": min([x for x in sub.plan_ym if x], default=""),
            "op_ym": min([x for x in sub.op_ym if x], default=""), "op_units": int(sub.op_units.sum()),
            "applicant": sub.applicant.iloc[0], "n": len(js),
        }

    def land_records(ix, how=None):
        recs = []
        for k in ix:
            r = land.iloc[k]
            recs.append({
                "kind": r.kind, "date": r.date.strftime("%Y-%m-%d") if pd.notna(r.date) else "",
                "basis": (how or {}).get(k, ""),
                "lot": str(r.lot)[:80], "use": r.use[:60],
                "premium_m": None if pd.isna(r.premium_m) else round(float(r.premium_m), 1),
                "area": None if pd.isna(r.area) else int(r.area), "party": r.party[:80],
            })
        return recs

    def source_of(owner, recs):
        kinds = {r["kind"].split("(")[0] for r in recs}
        if owner == "港铁":
            return "港铁上盖"
        if owner in ("市建局", "愉景湾"):
            return owner
        if "卖地" in kinds:
            return "公开卖地"
        if "换地" in kinds:
            return "换地补地价"
        if "契约修订" in kinds or "地段扩展" in kinds:
            return "契约修订补地价"
        return "未知"

    # ---------- 输出 ----------
    sites = []
    used_land: set[int] = set()
    for i, p in PS.iterrows():
        how = dict(p.land_how)
        for j in p.bd:                      # 动工地盘按坐标 / 公司接到的土地记录也算这个盘的，但地段号明显不同的不要
            for k, h in BD.land_how[j].items():
                if p.lots and land.lots[k] and not (p.lots & land.lots[k]):
                    continue
                how.setdefault(k, h)
        idx = list(p.land_idx) + [k for k in how if k not in p.land_idx]
        used_land.update(idx)
        recs = land_records(idx, how)
        b = bd_agg(p.bd)
        if b is None:
            # 5.4/5.5 漏登的盘（启德 1F-1、屯门凱和山这类）：批则和入伙纸直接找
            pl = direct_hits(b_plan, p.lat, p.lon, p.lots, 120, p.aps)
            op = direct_hits(b_op, p.lat, p.lon, p.lots, 120, p.aps, units=p.units)
            if pl or op:
                b = {"units": None, "first_ym": "", "plan_ym": min(b_plan.ym[pl]) if pl else "",
                     "op_ym": min(b_op.ym[op]) if op else "", "op_units": int(b_op.units[op].sum()) if op else 0,
                     "applicant": "", "n": 0}
        zh, en = site_name(p.names_zh, p.names)
        op_done = b is not None and b["op_units"] >= 0.5 * p.units
        sites.append({
            "id": f"p{i}", "lat": round(float(p.lat), 6), "lon": round(float(p.lon), 6),
            "stage": "已预售·已入伙" if op_done else "已预售",
            "name": zh or en or re.sub(r"[（(][^)）]*[)）]", "", str(p.address_zh or p.address)).strip(), "name_en": en, "phases": p.phases,
            "address": p.address_zh or p.address, "address_en": p.address, "owner": p.owner, "vendor": p.vendor,
            "source": source_of(p.owner, recs), "land": recs,
            "presale_units": int(p.units), "presale_first": p.first_ym, "presale_last": p.last_ym,
            "plan_ym": b["plan_ym"] if b else "", "start_ym": b["first_ym"] if b else "",
            "bd_units": b["units"] if b else None, "bd_sites": b["n"] if b else 0,
            "bd_missing": bool(b and b["n"] == 0),       # 屋宇署 5.4/5.5 没登记，只有批则 / 入伙纸
            "op_ym": b["op_ym"] if b else "", "op_units": b["op_units"] if b else 0,
            "ap": str(p.ap).split(" - ")[0], "applicant": b["applicant"] if b else "",
        })
    for j, b in BD.iterrows():
        if b.presold or b.first_ym < f"{LAND_FROM_YEAR}-01":
            continue
        if b.units == 0 and not re.search(r"Apartment|Residential|House|Domestic|Villa|Flat|Composite", str(b.btype), re.I):
            continue        # 0 伙的非住宅（学校、货仓）只用来匹配，不出图
        recs = land_records(b.land_idx, b.land_how)
        used_land.update(b.land_idx)
        sites.append({
            "id": f"b{j}", "lat": round(float(b.lat), 6), "lon": round(float(b.lon), 6),
            "stage": "已入伙·未预售" if b.op_units >= 0.5 * b.units else "动工未预售",
            "name": re.sub(r"\s+\d+\.\d+\.\d+/\(\d+\)$", "", str(b.address)), "name_en": "", "phases": [],
            "address": "", "owner": b.owner, "vendor": "",
            "source": source_of(b.owner, recs), "land": recs,
            "presale_units": 0, "presale_first": "", "presale_last": "",
            "plan_ym": b.plan_ym, "start_ym": b.first_ym, "bd_units": int(b.units),
            "op_ym": b.op_ym, "op_units": int(b.op_units),
            "ap": str(b.ap), "applicant": str(b.applicant), "btype": str(b.btype),
        })
    # 卖了/换了地但屋宇署还没动工的：只收真正会出楼的（卖地、换地，以及付了地价的契约修订）
    for k, r in land.iterrows():
        if k in used_land or pd.isna(r.date) or r.date.year < LAND_FROM_YEAR or pd.isna(r.lat):
            continue
        if r.kind == "契约修订" and not (r.premium_m and r.premium_m >= 100):
            continue
        if r.kind == "地段扩展":
            continue
        # 有批则没动工也算未开上盖；批则按地段号或半径（60+√面积）找
        plan_hits = [k for k in range(len(b_plan)) if r.lots & b_plan.lots[k]]
        if not plan_hits:
            dp = haversine(r.lat, r.lon, b_plan.lat.values, b_plan.lon.values)
            plan_hits = [int(k) for k in np.where(dp <= 60 + np.sqrt(r.area if pd.notna(r.area) else 0))[0]
                         if not (b_plan.lots[k] and not (r.lots & b_plan.lots[k]))]
        plan_hits = [k for k in plan_hits if b_plan.ym[k] >= r.date.strftime("%Y-%m")]
        op_hits = [k for k in range(len(b_op)) if (r.lots & b_op.lots[k]) and b_op.ym[k] >= r.date.strftime("%Y-%m")]
        src = source_of("私人", [{"kind": r.kind}])
        sites.append({
            "id": f"l{k}", "lat": round(float(r.lat), 6), "lon": round(float(r.lon), 6),
            "stage": "已入伙·未预售" if op_hits else "批地未动工",
            "name": str(r.address)[:60] or str(r.lot)[:60], "name_en": "", "phases": [],
            "address": str(r.address), "owner": "私人", "vendor": "",
            "source": src, "land": land_records([k]),
            "presale_units": 0, "presale_first": "", "presale_last": "",
            "plan_ym": min(b_plan.ym[plan_hits]) if plan_hits else "", "start_ym": "", "bd_units": None,
            "op_ym": min(b_op.ym[op_hits]) if op_hits else "", "op_units": int(b_op.units[op_hits].sum()) if op_hits else 0,
            "area": None if pd.isna(r.area) else int(r.area),
            "premium_m": None if pd.isna(r.premium_m) else round(float(r.premium_m), 1),
            "ap": "", "applicant": "",
        })

    # ---------- 可疑匹配先摘出来 ----------
    # 坐标接上但买家 ≠ 屋宇署申请人、批则 / 动工 / 预售早于批地、预售伙数和屋宇署伙数差一倍以上：
    # 这些先不进记录，单独放在 review 里，核实了再放回
    review = []
    for st in sites:
        if st["stage"] == "批地未动工":
            continue
        keep = []
        for rec in st["land"]:
            probs = []
            ym0 = rec["date"][:7]
            if not rec["kind"].startswith("卖地"):      # 契约修订 / 换地在批则之后签是正常流程，不查
                keep.append(rec)
                continue
            if rec["basis"] != "地段号":
                for key, label in (("start_ym", "动工"), ("plan_ym", "批则"), ("presale_first", "预售")):
                    if st.get(key) and ym0 and st[key] < ym0:
                        probs.append(f"{label} {st[key]} 早于批地")
                if rec["basis"] == "坐标" and st.get("applicant") and rec["party"] \
                        and company_sim(company_keys(rec["party"]), company_keys(st["applicant"])) < 0.85:
                    probs.append("买家≠屋宇署申请人")
                if rec["basis"] == "公司名":
                    probs.append("只靠公司名对上")
            if st.get("bd_units") and st.get("presale_units"):
                ratio = st["presale_units"] / st["bd_units"]
                if ratio > 1.3 or ratio < 0.5:
                    probs.append(f"预售 {st['presale_units']} 伙 vs 屋宇署 {st['bd_units']} 伙")
            if probs:
                review.append({**rec, "site": st["name"], "site_id": st["id"], "problems": probs})
            else:
                keep.append(rec)
        if len(keep) != len(st["land"]):
            st["land"] = keep
            st["source"] = source_of(st["owner"], keep)

    # 汇总
    by_src = Counter()
    by_src_units = Counter()
    for s in sites:
        if s["stage"].startswith("已预售"):
            by_src[s["source"]] += 1
            by_src_units[s["source"]] += s["presale_units"]
    stage_cnt = Counter(s["stage"] for s in sites)
    stage_units = Counter()
    for s in sites:
        stage_units[s["stage"]] += s["presale_units"] or s["bd_units"] or 0

    return {
        "generated": datetime.now(HKT).strftime("%Y-%m-%d %H:%M HKT"),
        "as_of": {"presale": ps.ym.max(), "bd_start": b_start.ym.max(), "bd_op": b_op.ym.max(),
                  "land": land.date.max().strftime("%Y-%m-%d")},
        "counts": {"presale_records": int(len(ps)), "presale_sites": int(len(PS)),
                   "bd_sites": int(len(BD)), "land_records": int(len(land))},
        "summary": {
            "by_source": [{"source": k, "sites": by_src[k], "units": by_src_units[k]}
                          for k, _ in by_src_units.most_common()],
            "by_stage": [{"stage": k, "sites": stage_cnt[k], "units": stage_units[k]}
                         for k in ("已预售", "已预售·已入伙", "动工未预售", "已入伙·未预售", "批地未动工")],
        },
        "sites": sites,
        "review": review,
    }


# ----------------------------------------------------------------------------
# 销售状态：接 house730 逐盘余货（英文名 / 门牌号+街名两条路对）
# ----------------------------------------------------------------------------
_STREET = re.compile(r"(?:NO\.?\s*)?(\d+[A-Z]?)\s+([A-Z'’ ]+?\s(?:ROAD|STREET|LANE|AVENUE|PATH|TERRACE|DRIVE|WAY|CIRCUIT|CRESCENT|SQUARE|VILLAS?|GARDENS?|PLACE|HILL|BAY))\b")


def _addr_norm(text) -> str:
    t = str(text or "").upper().replace(",", " ")
    return re.sub(r"\bAVE\b\.?", "AVENUE", re.sub(r"\bST\b\.?", "STREET", re.sub(r"\bRD\b\.?", "ROAD", t)))


def addr_key(text) -> str:
    """'No. 8 Castle Road, Mid-Levels' / '8 CASTLE RD' -> '8 CASTLE ROAD'"""
    m = _STREET.search(_addr_norm(text))
    return f"{m.group(1)} {re.sub(r'[^A-Z ]', '', m.group(2)).strip()}" if m else ""


_SUFFIX = r"(?:ROAD|STREET|LANE|AVENUE|PATH|TERRACE|DRIVE|WAY|CIRCUIT|CRESCENT|SQUARE|VILLAS?|GARDENS?|PLACE|HILL|BAY)"
_SPAN = re.compile(r"(?:NOS?\.?\s*)?((?:\d+[A-Z]?)(?:\s*(?:[-–—&,]|AND)\s*\d+[A-Z]?)*)\s+([A-Z'’ ]+?\s" + _SUFFIX + r")\b")


def addr_spans(text) -> list[tuple[str, int, int]]:
    """把地址拆成「街名 + 门牌区间」，一条地址可能有好几段。

    '26-40A Whampoa Street, 83-85 Baker Street' -> [('WHAMPOA STREET',26,40), ('BAKER STREET',83,85)]
    '16 and 18 Cape Road' -> [('CAPE ROAD',16,18)]，'No. 8 Castle Road' -> [('CASTLE ROAD',8,8)]

    屋宇署地盘的地址常写成门牌范围（'62-76 Main Street'），SRPE 写具体门牌（'68 Main St'），
    只比首尾号码会错过，落在区间内才是同一块地。
    """
    out = []
    for m in _SPAN.finditer(_addr_norm(text)):
        nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
        if not nums:
            continue
        street = re.sub(r"[^A-Z ]", "", m.group(2)).strip()
        out.append((street, min(nums), max(nums)))
    return out


def addr_overlap(a, b) -> bool:
    """两条地址是否指同一块地：同一条街，且门牌区间有交集。"""
    sa, sb = addr_spans(a), addr_spans(b)
    for st1, lo1, hi1 in sa:
        for st2, lo2, hi2 in sb:
            if st1 == st2 and lo1 <= hi2 and lo2 <= hi1:
                return True
    return False


def street_key(text) -> str:
    """只取街名，不要门牌：'62-76 Main Street Ap Lei Chau' / '68 MAIN ST' -> 'MAIN STREET'。

    屋宇署的地盘只有一串地址、没有项目名，门牌又常是 '62-76' 这种范围，
    对不上具体门牌，只能靠街名 + 距离。
    """
    m = _STREET.search(_addr_norm(text))
    return re.sub(r"[^A-Z ]", "", m.group(2)).strip() if m else ""


def name_key(text) -> str:
    """'LA MIRABELLE Phase II' / 'Phase XIIIB of LOHAS Park – LA MIRABELLE' 的每一段 -> 'LAMIRABELLE'。"""
    t = str(text or "").upper()
    t = re.sub(r"\bPHASE\s*[\w-]+\b|\bSERIES\b|\bDEVELOPMENT\b|\bPENDING\b|\bTOWERS?\b", " ", t)
    t = re.sub(r"\b(I{1,3}|IV|VI{0,3}|IX|X{1,3}|[0-9]+[A-Z]?)\b$", " ", t.strip())
    return re.sub(r"[^A-Z]", "", t)


_ADDRESSY = re.compile(r"\d.*\b" + _SUFFIX + r"\b|\b" + _SUFFIX + r"\b.*\d")


def looks_like_address(text) -> bool:
    """'15 Gough Hill Road' 这种「名字」其实是地址 —— 去掉数字后两个不同门牌会撞成同一个 key。"""
    return bool(_ADDRESSY.search(str(text or "").upper()))


def name_keys(text) -> set:
    parts = re.split(r"\s*[-–—:]\s*|\(|\)", str(text or ""))
    return {k for k in (name_key(x) for x in parts) if len(k) >= 4}


def _match_house730(sites: list[dict], h: pd.DataFrame) -> set[int]:
    """把 house730 的行挂到地盘上，返回用掉的行号。

    只靠门牌 + 街名会撞车（黃金海灣和峻巒的地址都是「青山公路 18 号」，差 50 公里），
    所以名字 / 地址对上之后还要坐标在 2 公里内；一个 house730 项目只认一个最近的地盘，
    否则同一份余货会在地图上重复计好几次。
    """
    for c in ("lat", "lon"):
        if c not in h:
            h[c] = np.nan
    h["lat"] = pd.to_numeric(h.lat, errors="coerce")
    h["lon"] = pd.to_numeric(h.lon, errors="coerce")
    by_name: dict[str, set] = {}
    by_addr: dict[str, set] = {}
    for i, r in h.iterrows():
        for nm in str(r.get("phase_names") or "").split("|") + [str(r.get("project") or "")]:
            for k in name_keys(nm):
                by_name.setdefault(k, set()).add(i)
        ak = addr_key(r.get("address"))
        if ak:
            by_addr.setdefault(ak, set()).add(i)

    MAX_KM = 2.0
    cand: dict[int, list[tuple[int, float, int]]] = {}      # house730 行 -> [(优先级, 距离, 地盘序号)]
    # house730 的坐标偶有错得离谱的（首岸标到中环去了），所以距离只在需要分辨同名同门牌时才当否决
    for si, s in enumerate(sites):
        if not s["stage"].startswith("已预售"):
            continue
        hits: dict[int, int] = {}
        ak = addr_key(s.get("address_en"))
        for i in (by_addr.get(ak, set()) if ak else set()):
            hits[i] = 0
        names = set(name_keys(s.get("name_en")))
        for ph in s.get("phases", []):
            names |= name_keys(ph.get("name_en") or "")
        for k in names:
            for i in by_name.get(k, set()):
                hits.setdefault(i, 1)
        for i, prio in hits.items():
            lat, lon = h.lat[i], h.lon[i]
            d = haversine(s["lat"], s["lon"], lat, lon) / 1000 if pd.notna(lat) else np.nan
            cand.setdefault(i, []).append((prio, 1e9 if pd.isna(d) else d, si))

    pick: dict[int, list[int]] = {}       # 地盘序号 -> [house730 行]
    for i, lst in cand.items():
        addr = sorted(x for x in lst if x[0] == 0)
        if len(addr) == 1:
            best = addr[0]                # 门牌 + 街名唯一命中，坐标错了也认（house730 有错标）
        elif addr:
            best = addr[0]                # 同一门牌多个地盘（青山公路 18 号），近者胜
        else:
            near = sorted(x for x in lst if x[1] <= MAX_KM)
            if not near:
                continue                  # 只靠名字、又隔着十万八千里，不认
            best = near[0]
        pick.setdefault(best[2], []).append(i)
    used: set[int] = set()
    for si, idxs in pick.items():
        sub = h.loc[sorted(idxs)]
        used |= set(idxs)
        fs = [str(x) for x in sub.first_sales_date if pd.notna(x) and str(x) not in ("nan", "NaT", "")]
        sites[si]["sale"] = {
            "projects": [str(x) for x in sub.project],
            "total": int(pd.to_numeric(sub.total_units, errors="coerce").fillna(0).sum()),
            "sold": int(pd.to_numeric(sub.sold_units, errors="coerce").fillna(0).sum()),
            "remaining": int(pd.to_numeric(sub.remaining_units, errors="coerce").fillna(0).sum()),
            "first_sales": min(fs) if fs else "",
        }
    return used


SRPE_INDEX = Path(__file__).resolve().parent / "data" / "srpe" / "index.csv"


def add_unmatched_sales(sites: list[dict], miss: pd.DataFrame | None) -> int:
    """house730 有余货、但链上没有地盘的项目，单独补成地图上的点。

    多数是现楼销售：楼建好了才卖，不需要预售同意书，所以地政总署的预售图层里根本没有，
    链上自然接不到（天御、樂啟都匯这类）。用 house730 自己的坐标落点，
    再用 SRPE 的官方索引补中文名和地址。
    """
    if miss is None or miss.empty:
        return 0
    srpe = None
    if SRPE_INDEX.exists():
        try:
            srpe = pd.read_csv(SRPE_INDEX, dtype={"devId": str})
            srpe["lat"] = pd.to_numeric(srpe.lat, errors="coerce")
            srpe["lon"] = pd.to_numeric(srpe.lon, errors="coerce")
            srpe = srpe[srpe.lat.notna()]
        except Exception:               # noqa: BLE001
            srpe = None
    n = 0
    for i, r in miss.iterrows():
        lat, lon = pd.to_numeric(r.get("lat"), errors="coerce"), pd.to_numeric(r.get("lon"), errors="coerce")
        if pd.isna(lat) or pd.isna(lon):
            continue
        name_zh, addr_zh, dev_ids = "", "", []
        if srpe is not None:
            d = haversine(lat, lon, srpe.lat.values, srpe.lon.values)
            near = srpe[(d <= 250)]
            keys = name_keys(str(r.get("project") or "")) | {
                k for nm in str(r.get("phase_names") or "").split("|") for k in name_keys(nm)}
            hit = near[near.name_en.fillna("").map(lambda x: bool(name_keys(x) & keys))]
            if len(hit) == 0 and len(near):
                ak = addr_key(r.get("address"))
                hit = near[near.address_en.fillna("").map(lambda x: addr_key(x) == ak)] if ak else near.iloc[0:0]
            if len(hit):
                name_zh = str(hit.iloc[0].name_zh or "")
                addr_zh = str(hit.iloc[0].address_zh or "")
                dev_ids = [str(x) for x in hit.devId]
        def _i(v):
            v = pd.to_numeric(v, errors="coerce")
            return 0 if pd.isna(v) else int(v)
        total, sold, rem = _i(r.get("total_units")), _i(r.get("sold_units")), _i(r.get("remaining_units"))
        _fs = r.get("first_sales_date")
        fs = "" if pd.isna(_fs) else str(_fs)
        sites.append({
            "id": f"h{i}", "lat": round(float(lat), 6), "lon": round(float(lon), 6),
            "stage": "现楼在售", "status": "在售" if rem > 0 else "已售罄·已入伙",
            "name": name_zh or str(r.get("project") or ""), "name_en": str(r.get("project") or ""),
            "phases": [], "address": addr_zh or str(r.get("address") or ""),
            "address_en": str(r.get("address") or ""), "owner": "私人", "vendor": "",
            "source": "未知", "land": [], "no_presale": True, "srpe_ids": dev_ids,
            "presale_units": 0, "presale_first": "", "presale_last": "",
            "plan_ym": "", "start_ym": "", "bd_units": total or None, "bd_sites": 0,
            "op_ym": "", "op_units": 0, "ap": "", "applicant": str(r.get("main_developer") or ""),
            "sale": {"projects": [str(r.get("project") or "")], "total": total, "sold": sold,
                     "remaining": rem, "first_sales": fs if fs not in ("", "nan", "NaT") else ""},
        })
        n += 1
    print(f"  [land_chain] 另补 {n} 个无预售同意书的在售点（现楼销售）")
    return n


SRPE_SUMMARY = Path(__file__).resolve().parent / "data" / "srpe" / "summary.csv"


def attach_srpe_projects(sites: list[dict]) -> int:
    """把 SRPE 的「独立发展项目」挂到地盘上，作为地盘下面的一层。

    港铁上盖、NOVO LAND、峻巒这类一块地分很多期卖的盘，预售同意书共用一个地段号
    （日出康城 21 份同意书全是 TKOTL 70 RP），地段号聚类必然并成一个地盘 —— 地价、
    批则这些确实属于整块地，但「哪一期在卖、卖了多少」属于每一期。

    SRPE 按《一手住宅物业销售条例》逐个发展项目登记（日出康城在它那里是 17 个），
    每个有自己的坐标、售楼书和成交纪录册，正好是「期」这一层的官方名册。
    """
    if not SRPE_INDEX.exists():
        return 0
    try:
        idx = pd.read_csv(SRPE_INDEX, dtype={"devId": str})
        if SRPE_SUMMARY.exists():
            idx = idx.merge(pd.read_csv(SRPE_SUMMARY, dtype={"devId": str}), on="devId", how="left")
        idx["lat"] = pd.to_numeric(idx.lat, errors="coerce")
        idx["lon"] = pd.to_numeric(idx.lon, errors="coerce")
        idx = idx[idx.lat.notna()].reset_index(drop=True)
    except Exception as e:      # noqa: BLE001
        print(f"  [land_chain] SRPE 名册读取失败，跳过分期: {e}")
        return 0

    for s in sites:
        s["projects"] = []
    lat = np.array([s["lat"] for s in sites]); lon = np.array([s["lon"] for s in sites])

    def site_names(s: dict) -> set:
        out = set()
        for ph in s.get("phases", []):
            for k in ("name", "name_en"):
                v = str(ph.get(k) or "")
                if v and v not in ("待定", "Pending", "nan") and not looks_like_address(v):
                    out.add(v)
        for k in ("name", "name_en"):
            if s.get(k) and not looks_like_address(s[k]):
                out.add(str(s[k]))
        return out

    n = 0
    for _, r in idx.iterrows():
        d = haversine(r.lat, r.lon, lat, lon)
        ak = addr_key(r.address_en)
        zh = "" if pd.isna(r.name_zh) else str(r.name_zh).strip()
        en = "" if pd.isna(r.name_en) else str(r.name_en).strip()
        nm = set() if looks_like_address(en) else name_keys(en)
        tok = phase_token(r.phase) or phase_token(en)
        best = None
        # 按可靠程度排：门牌+街名 > 项目名 > 期数标签 > 同街很近
        # 只靠「几百米内」会把隔壁盘也吸进来（安達臣道的安峯 / 峻然 / 灝然是三个不同的盘）
        for si in np.where(d <= 1500)[0]:
            s = sites[si]
            if s.get("no_presale"):
                continue
            names = site_names(s)
            if ak and addr_key(s.get("address_en")) == ak:
                prio = 0                      # 门牌 + 街名完全一致
            elif (zh and any(zh in x for x in names)) or (nm and any(nm & name_keys(x) for x in names)):
                prio = 1                      # 项目名一致
            elif tok and d[si] <= 300 and any(phase_token(ph.get("name")) == tok
                                              or phase_token(ph.get("name_en")) == tok for ph in s.get("phases", [])):
                prio = 2                      # 期数标签一致
            elif d[si] <= 120 and (addr_overlap(r.address_en, s.get("address_en"))
                                   or addr_overlap(r.address_en, s.get("name"))):
                # 同一条街、门牌落在地盘地址的区间内。屋宇署地盘只有地址没有项目名，只能靠这条；
                # 但一个地址区间里可能站着两个不同的盘（26-40A 黃埔街同时有映匯和 BAKER CIRCLE），
                # 所以只在这个地盘还没有别的名字时才认。
                prio = 3
            else:
                continue
            if best is None or (prio, d[si]) < best[0]:
                best = ((prio, d[si]), int(si))
        if best is None:
            continue
        s = sites[best[1]]
        tok = phase_token(r.phase) or phase_token(r.name_en)
        units = None
        for ph in s.get("phases", []):
            if tok and phase_token(ph.get("name")) == tok or (tok and phase_token(ph.get("name_en")) == tok):
                units = int(ph["units"]); break
        sold = pd.to_numeric(r.get("sold"), errors="coerce")
        s["projects"].append({
            "_prio": best[0][0], "devId": str(r.devId), "name": zh or str(r.name_en or ""), "name_en": str(r.name_en or ""),
            "phase": "" if pd.isna(r.phase) else str(r.phase), "token": tok,
            "lat": round(float(r.lat), 6), "lon": round(float(r.lon), 6),
            "units": units, "sold": None if pd.isna(sold) else int(sold),
            "active": str(r.get("active") or ""), "first_print": str(r.get("first_print") or "")[:10],
            "last_pasp": "" if pd.isna(r.get("last_pasp")) else str(r.get("last_pasp"))[:10],
        })
        n += 1
    # 一个地盘上如果挂着名字不同的盘，多半是弱匹配把隔壁盘吸了进来
    # （親海駅 与 擎海 都在同源街、期数都叫「第1期」）。只留证据最硬的那一组。
    dropped = 0
    for s in sites:
        ps = s.get("projects") or []
        groups: dict[str, list] = {}
        for p in ps:
            groups.setdefault(re.sub(r"[\s·．・]", "", (p["name"] or p["name_en"] or "").upper()), []).append(p)
        if len(groups) > 1:
            keep = min(groups.values(), key=lambda g: (min(x["_prio"] for x in g), -len(g)))
            dropped += len(ps) - len(keep)
            s["projects"] = keep
    for s in sites:
        for p in s["projects"]:
            p.pop("_prio", None)
        ps = s.get("projects") or []
        # 整个地盘只有一个发展项目：期数怎么写都无所谓，伙数就是地盘的
        if len(ps) == 1 and ps[0]["units"] is None and s.get("presale_units"):
            ps[0]["units"] = int(s["presale_units"])
        # 多期但只剩一期没对上、也只剩一期预售没被认领：按剩余配对
        elif len(ps) > 1:
            miss = [p for p in ps if p["units"] is None]
            claimed = {p["token"] for p in ps if p["units"] is not None and p["token"]}
            free = [ph for ph in s.get("phases", []) if phase_token(ph.get("name")) not in claimed
                    and phase_token(ph.get("name_en")) not in claimed]
            if len(miss) == 1 and len(free) == 1:
                miss[0]["units"] = int(free[0]["units"])
        ps.sort(key=lambda p: (p["first_print"] or "9999", p["phase"]))
    n -= dropped
    multi = sum(1 for s in sites if len(s.get("projects") or []) > 1)
    matched_units = sum(1 for s in sites for p in s["projects"] if p["units"] is not None)
    print(f"  [land_chain] SRPE 名册 {len(idx)} 个发展项目 -> 挂上 {n} 个；"
          f"{multi} 个地盘含多期，其中 {matched_units}/{n} 期对上了预售伙数")
    return n


def attach_sales(sites: list[dict], house730_csv: Path | None) -> pd.DataFrame | None:
    """挂 house730 余货并判定销售状态；返回没接到任何地盘的 house730 行。"""
    h = None
    if house730_csv and Path(house730_csv).exists():
        try:
            h = pd.read_csv(house730_csv)
        except Exception as e:      # noqa: BLE001
            print(f"  [land_chain] house730 表读取失败，销售状态按无余货数据处理: {e}")
    for s in sites:
        s["sale"] = None
    used = _match_house730(sites, h) if h is not None else set()

    recent = (pd.Timestamp.now(tz=HKT) - pd.DateOffset(months=24)).strftime("%Y-%m")
    for s in sites:
        st = s["stage"]
        if st == "动工未预售":
            s["status"] = "已动工·未预售"
        elif st == "批地未动工":
            s["status"] = "政府已卖地·未开上盖" if s["source"] == "公开卖地" else "换地补价·未开上盖"
        elif st == "已入伙·未预售":
            s["status"] = "已入伙·未预售"
        elif s["sale"] and s["sale"]["remaining"] > 0:
            s["status"] = "在售"
        elif s["sale"]:
            s["status"] = "已售罄·已入伙"
        elif h is None and st == "已预售":
            s["status"] = "已批预售"
        elif st == "已预售" and s["presale_first"] >= recent:
            s["status"] = "已批预售·未开售"
        else:
            s["status"] = "已售罄·已入伙"
    if h is None:
        return None
    miss = h.loc[[i for i in range(len(h)) if i not in used]].copy()
    print(f"  [land_chain] house730 {len(h)} 个在售项目，{len(used)} 个接到地盘；"
          f"未接上 {len(miss)} 个（余货 {int(pd.to_numeric(miss.remaining_units, errors='coerce').fillna(0).sum()):,} 伙）")
    return miss


# ----------------------------------------------------------------------------
def write_json(data: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False：漏网的 NaN 会让浏览器整份 JSON 解析失败，宁可在这里炸
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def same_content(a: dict, b: dict) -> bool:
    ka = {k: v for k, v in a.items() if k != "generated"}
    kb = {k: v for k, v in b.items() if k != "generated"}
    return ka == kb


def main() -> int:
    ap = argparse.ArgumentParser(description="项目地图数据（CSDI 官方记录串链）")
    ap.add_argument("--out", type=str, default="out_inventory/land_chain.json")
    ap.add_argument("--house730", type=str, default="out_inventory/projects_inventory.csv",
                    help="house730 逐盘在售表，用来标在售 / 售罄")
    ap.add_argument("--raw-cache", type=str, default="", help="调试用：把抓下来的原始表存/读这个目录")
    args = ap.parse_args()

    raw = None
    cache = Path(args.raw_cache) if args.raw_cache else None
    if cache and cache.exists() and all((cache / f"{k}.pkl").exists() for k in LAYERS):
        raw = {k: pd.read_pickle(cache / f"{k}.pkl") for k in LAYERS}
        print(f"  [land_chain] 用缓存 {cache}")
    if raw is None:
        raw = fetch_all()
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
            for k, df in raw.items():
                df.to_pickle(cache / f"{k}.pkl")

    data = build(raw)
    miss = attach_sales(data["sites"], Path(args.house730) if args.house730 else None)
    add_unmatched_sales(data["sites"], miss)
    attach_srpe_projects(data["sites"])
    st = Counter(); su = Counter()
    for x in data["sites"]:
        st[x["status"]] += 1
        su[x["status"]] += x["presale_units"] or x["bd_units"] or 0
    data["summary"]["by_status"] = [{"status": k, "sites": st[k], "units": su[k]} for k in st]
    out = Path(args.out)
    write_json(data, out)
    s = data["summary"]
    print(f"  [land_chain] 地盘 {len(data['sites'])} 个 -> {out}")
    for r in s["by_status"]:
        print(f"    {r['status']:<10} {r['sites']:>5} 个  {r['units']:>8,} 伙")
    return 0


if __name__ == "__main__":
    sys.exit(main())
