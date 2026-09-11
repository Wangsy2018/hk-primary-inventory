"""
项目地图数据：把 CSDI 上带坐标的官方记录串成「地 → 楼 → 售」一条链。

  卖地 / 换地 / 契约修订（地政总署）→ 批则 → 动工 → 预售同意 → 入伙纸（屋宇署 / 地政总署）

匹配全靠坐标 + 地段号：同一地盘的记录在各图层里坐标几乎一致（中位数 3 米），
跨图层没有共同的项目编号，地段号是唯一能对上的键，但只有地政总署的记录才有。

产出 out_inventory/land_chain.json，看板的地图块直接读它。
"""
from __future__ import annotations

import argparse
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
    "bd_op":    "common/bd_rcd_1629267205236_397",        # BDMD56 已发佔用许可证
}

PRESALE_FROM_YEAR = 2012      # 屋宇署月报 2011-06 起才有，早于此的预售没链可串
LAND_FROM_YEAR = 2011

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


_LOT_SKIP = {"AND", "THE", "EXTENSION", "TO", "EXTENSIONS", "THERETO", "OF"}


def canon_lots(text) -> frozenset:
    """各库写法不同的地段号统一成短码：
    'Kowloon Inland Lot No. 10557' / 'KIL 10557' -> 'KIL 10557'
    'Lot No. 313 in Demarcation District No. 355' / 'Lot 313 RP in DD 355' -> 'DD355 LOT 313'
    """
    t = re.sub(r"<br\s*/?>", " ", str(text or "")).upper()
    t = re.sub(r"\b((?:[A-Z]\.){2,})", lambda m: m.group(1).replace(".", ""), t)   # T.K.O.T.L. -> TKOTL
    out = set()
    for m in re.finditer(r"([A-Z][A-Z ]*?)\s+LOT\s+NO\.?\s*(\d+)", t):
        words = [w for w in m.group(1).split() if w not in _LOT_SKIP]
        if words:
            out.add("".join(w[0] for w in words) + "L " + m.group(2))
    for m in re.finditer(
            r"LOT\s+(?:NO\.?\s*)?(\d+)\b[^,;]{0,40}?\bIN\s+(?:DEMARCATION\s+DISTRICT|D\.?D\.?)\s*(?:NO\.?)?\s*(\d+)", t):
        out.add(f"DD{m.group(2)} LOT {m.group(1)}")
    for m in re.finditer(r"\b([A-Z]{2,6}L)\s+(\d{2,5})\b", t):
        out.add(f"{m.group(1)} {m.group(2)}")
    return frozenset(out)


def norm_ap(s) -> frozenset:
    s = str(s or "").split(" - ")[0]
    s = re.sub(r"[^a-z ]", " ", s.lower())
    return frozenset(t for t in s.split() if len(t) > 1)


def norm_co(s) -> str:
    s = re.sub(r"\b(limited|ltd|company|co|holdings?|development|developments|investments?)\b", "",
               str(s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


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
    ps = clean_xy(ps)
    ps = ps[(ps.yr >= PRESALE_FROM_YEAR) & (ps.units > 0) & (ps.lat.notna() | (ps.lot.map(canon_lots).map(len) > 0))]
    ps = ps.reset_index(drop=True)
    ps["lots"] = ps.lot.map(canon_lots)
    ps["ap_n"] = ps.ap.map(norm_ap)
    ps["owner"] = ps.vendor.map(owner_of)
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
    b_start = raw["bd_start"].rename(columns={
        "ADDRESS_EN": "address", "NSEARCH02_EN": "btype", "NSEARCH03_EN": "units",
        "NSEARCH08_EN": "ap", "NSEARCH10_EN": "applicant", "LATITUDE": "lat", "LONGITUDE": "lon"})
    for c in ("address", "btype", "ap", "applicant"):
        b_start[c] = b_start[c].fillna("").astype(str)
    b_start["units"] = to_num(b_start.units).fillna(0)
    b_start["ym"] = ym(b_start)
    b_start = clean_xy(b_start)
    # 过渡性房屋不是私人住宅供应
    b_start = b_start[~b_start.btype.astype(str).str.contains("Transitional", case=False)]
    b_start = b_start[(b_start.units > 0) & b_start.lat.notna()].reset_index(drop=True)
    b_start["owner"] = b_start.applicant.map(owner_of)
    b_start["ap_n"] = b_start.ap.map(norm_ap)
    b_start["app_n"] = b_start.applicant.map(norm_co)
    b_start["lots"] = b_start.address.map(canon_lots)
    aps, apps = b_start.ap_n.values, b_start.app_n.values
    b_start["site"] = cluster(b_start, 30, lambda i, j: len(aps[i] & aps[j]) >= 2 or (apps[i] and apps[i] == apps[j]))

    b_plan = raw["bd_plan"].rename(columns={"ADDRESS_EN": "address", "NSEARCH05_EN": "ap",
                                            "NSEARCH03_EN": "dom_gfa", "LATITUDE": "lat", "LONGITUDE": "lon"})
    b_plan["dom_gfa"] = to_num(b_plan.dom_gfa).fillna(0)
    b_plan["ym"] = ym(b_plan)
    b_plan = clean_xy(b_plan)
    b_plan = b_plan[(b_plan.dom_gfa > 0) & b_plan.lat.notna()].reset_index(drop=True)
    b_plan["ap_n"] = b_plan.ap.map(norm_ap)

    b_op = raw["bd_op"].rename(columns={"ADDRESS_EN": "address", "NSEARCH05_EN": "units",
                                        "NSEARCH10_EN": "ap", "LATITUDE": "lat", "LONGITUDE": "lon"})
    b_op["units"] = to_num(b_op.units).fillna(0)
    b_op["ym"] = ym(b_op)
    b_op = clean_xy(b_op)
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
        "address": lambda d: d.address.iloc[0], "applicant": lambda d: d.applicant.iloc[-1],
        "btype": lambda d: d.btype.iloc[-1], "ap": lambda d: d.ap.iloc[-1],
        "owner": lambda d: next(o for o in ("港铁", "市建局", "房協", "房委会", "愉景湾", "私人") if o in set(d.owner)),
        "app_n": lambda d: norm_co(d.applicant.iloc[-1]),
    })

    # ---------- 批则 / 入伙 -> 动工地盘 ----------
    def attach_bd(src, radius, after=None):
        Ds = haversine(BD.lat.values[:, None], BD.lon.values[:, None], src.lat.values[None, :], src.lon.values[None, :])
        res = []
        for j in range(len(BD)):
            idx = [int(k) for k in np.where(Ds[j] <= radius)[0]
                   if (Ds[j, k] <= 30 or len(BD.aps[j] & src.ap_n[k]) >= 2)
                   and (after is None or src.ym[k] >= BD[after][j])]
            res.append(idx)
        return res
    BD["plan_idx"] = attach_bd(b_plan, 120)
    BD["op_idx"] = attach_bd(b_op, 120, after="first_ym")     # 入伙纸不可能早于动工
    BD["plan_ym"] = BD.plan_idx.map(lambda ix: min(b_plan.ym[ix]) if ix else "")
    BD["op_ym"] = BD.op_idx.map(lambda ix: min(b_op.ym[ix]) if ix else "")
    BD["op_last_ym"] = BD.op_idx.map(lambda ix: max(b_op.ym[ix]) if ix else "")
    BD["op_units"] = BD.op_idx.map(lambda ix: int(b_op.units[ix].sum()) if ix else 0)

    # ---------- 土地记录 -> 地盘 ----------
    def attach_land(S, radius=60):
        Dl = haversine(S.lat.values[:, None], S.lon.values[:, None], land.lat.values[None, :], land.lon.values[None, :])
        Dl = np.nan_to_num(Dl, nan=np.inf)
        lot_index: dict[str, list[int]] = {}
        for k, lts in enumerate(land.lots):
            for lt in lts:
                lot_index.setdefault(lt, []).append(k)
        res = []
        for i in range(len(S)):
            by_lot = {k for lt in S.lots[i] for k in lot_index.get(lt, [])}
            near = set(int(k) for k in np.where(Dl[i] <= radius)[0])
            # 地段号对上的不看距离：来源库里有些点的坐标错到几十公里外
            idx = sorted(by_lot | near, key=lambda k: (k not in by_lot, land.date[k] if pd.notna(land.date[k]) else pd.Timestamp.max))
            res.append(idx)
        return res
    PS["land_idx"] = attach_land(PS)
    BD["land_idx"] = attach_land(BD)
    used_land = set(k for ix in PS.land_idx for k in ix) | set(k for ix in BD.land_idx for k in ix)

    # ---------- 预售地盘 -> 动工地盘（一对多）----------
    # A: 30 米内直接算同一地盘
    # B: 30~300 米，认可人士相同且单位数对得上（或单位数几乎相等）
    # C: 1.5 公里内共享同一份土地记录，且认可人士 / 申请人一致 —— 日出康城这种跨一公里的大盘
    D = haversine(PS.lat.values[:, None], PS.lon.values[:, None], BD.lat.values[None, :], BD.lon.values[None, :])
    PS_vendor_n = PS.vendor.map(norm_co)
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
            party_same = (PS_vendor_n[i] and PS_vendor_n[i] == BD.app_n[j]) or (PS.owner[i] == BD.owner[j] != "私人")
            shared_land = bool(my_land & set(BD.land_idx[j]))
            lot_same = bool(PS.lots[i] & BD.lots[j])
            if lot_same or (shared_land and (ap_same or party_same)) or (ap_same and party_same):
                picked.add(j)
        bd_of[i] = sorted(picked, key=lambda j: D[i, j])
    PS["bd"] = bd_of
    BD["presold"] = False
    BD.loc[sorted({j for js in bd_of for j in js}), "presold"] = True

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

    def land_records(ix):
        recs = []
        for k in ix:
            r = land.iloc[k]
            recs.append({
                "kind": r.kind, "date": r.date.strftime("%Y-%m-%d") if pd.notna(r.date) else "",
                "lot": str(r.lot)[:80], "use": r.use[:60],
                "premium_m": None if pd.isna(r.premium_m) else round(float(r.premium_m), 1),
                "area": None if pd.isna(r.area) else int(r.area), "party": r.party[:80],
            })
        return recs

    def source_of(owner, recs):
        kinds = {r["kind"].split("(")[0] for r in recs}
        if owner == "港铁":
            return "港铁上盖"
        if owner in ("市建局", "房協", "房委会", "愉景湾"):
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
    for i, p in PS.iterrows():
        recs = land_records(p.land_idx)
        b = bd_agg(p.bd)
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
            "op_ym": b["op_ym"] if b else "", "op_units": b["op_units"] if b else 0,
            "ap": str(p.ap).split(" - ")[0], "applicant": b["applicant"] if b else "",
        })
    for j, b in BD.iterrows():
        if b.presold or b.first_ym < f"{LAND_FROM_YEAR}-01":
            continue
        recs = land_records(b.land_idx)
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
        sites.append({
            "id": f"l{k}", "lat": round(float(r.lat), 6), "lon": round(float(r.lon), 6),
            "stage": "批地未动工", "name": str(r.address)[:60] or str(r.lot)[:60], "name_en": "", "phases": [],
            "address": str(r.address), "owner": "私人", "vendor": "",
            "source": source_of("私人", [{"kind": r.kind}]), "land": land_records([k]),
            "presale_units": 0, "presale_first": "", "presale_last": "",
            "plan_ym": "", "start_ym": "", "bd_units": None, "op_ym": "", "op_units": 0,
            "ap": "", "applicant": "",
        })

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
    }


# ----------------------------------------------------------------------------
# 销售状态：接 house730 逐盘余货（英文名 / 门牌号+街名两条路对）
# ----------------------------------------------------------------------------
_STREET = re.compile(r"(?:NO\.?\s*)?(\d+[A-Z]?)\s+([A-Z'’ ]+?\s(?:ROAD|STREET|LANE|AVENUE|PATH|TERRACE|DRIVE|WAY|CIRCUIT|CRESCENT|SQUARE|VILLAS?|GARDENS?|PLACE|HILL|BAY))\b")


def addr_key(text) -> str:
    t = str(text or "").upper().replace(",", " ")
    t = re.sub(r"\bRD\b\.?", "ROAD", t); t = re.sub(r"\bST\b\.?", "STREET", t); t = re.sub(r"\bAVE\b\.?", "AVENUE", t)
    m = _STREET.search(t)
    return f"{m.group(1)} {re.sub(r'[^A-Z ]', '', m.group(2)).strip()}" if m else ""


def name_key(text) -> str:
    """'LA MIRABELLE Phase II' / 'Phase XIIIB of LOHAS Park – LA MIRABELLE' 的每一段 -> 'LAMIRABELLE'。"""
    t = str(text or "").upper()
    t = re.sub(r"\bPHASE\s*[\w-]+\b|\bSERIES\b|\bDEVELOPMENT\b|\bPENDING\b|\bTOWERS?\b", " ", t)
    t = re.sub(r"\b(I{1,3}|IV|VI{0,3}|IX|X{1,3}|[0-9]+[A-Z]?)\b$", " ", t.strip())
    return re.sub(r"[^A-Z]", "", t)


def name_keys(text) -> set:
    parts = re.split(r"\s*[-–—:]\s*|\(|\)", str(text or ""))
    return {k for k in (name_key(x) for x in parts) if len(k) >= 4}


def attach_sales(sites: list[dict], house730_csv: Path | None) -> None:
    h = None
    if house730_csv and Path(house730_csv).exists():
        try:
            h = pd.read_csv(house730_csv)
        except Exception as e:      # noqa: BLE001
            print(f"  [land_chain] house730 表读取失败，销售状态按无余货数据处理: {e}")
    by_name: dict[str, set] = {}
    by_addr: dict[str, set] = {}
    if h is not None:
        for i, r in h.iterrows():
            for nm in str(r.get("phase_names") or "").split("|") + [str(r.get("project") or "")]:
                for k in name_keys(nm):
                    by_name.setdefault(k, set()).add(i)
            ak = addr_key(r.get("address"))
            if ak:
                by_addr.setdefault(ak, set()).add(i)
    used = set()
    for s in sites:
        s["sale"] = None
        if not s["stage"].startswith("已预售"):
            continue
        hits = set()
        for ph in s.get("phases", []):
            for k in name_keys(ph.get("name_en") or ""):
                hits |= by_name.get(k, set())
        for k in name_keys(s.get("name_en")):
            hits |= by_name.get(k, set())
        ak = addr_key(s.get("address_en"))
        if ak:
            hits |= by_addr.get(ak, set())
        if hits:
            sub = h.loc[sorted(hits)]
            used |= hits
            s["sale"] = {
                "projects": [str(x) for x in sub.project],
                "total": int(pd.to_numeric(sub.total_units, errors="coerce").fillna(0).sum()),
                "sold": int(pd.to_numeric(sub.sold_units, errors="coerce").fillna(0).sum()),
                "remaining": int(pd.to_numeric(sub.remaining_units, errors="coerce").fillna(0).sum()),
                "first_sales": str(sub.first_sales_date.min()) if "first_sales_date" in sub else "",
            }
    # 销售状态
    recent = (pd.Timestamp.now(tz=HKT) - pd.DateOffset(months=24)).strftime("%Y-%m")
    for s in sites:
        st = s["stage"]
        if st == "动工未预售":
            s["status"] = "已动工·未预售"
        elif st == "批地未动工":
            s["status"] = "已批地·未动工"
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
    if h is not None:
        miss = h.loc[[i for i in range(len(h)) if i not in used]]
        print(f"  [land_chain] house730 {len(h)} 个在售项目，{len(used)} 个接到地盘；未接上: "
              + "; ".join(str(x) for x in miss.project.head(12)) + (" …" if len(miss) > 12 else ""))


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
    attach_sales(data["sites"], Path(args.house730) if args.house730 else None)
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
