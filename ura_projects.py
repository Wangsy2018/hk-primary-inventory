"""
市建局（URA）重建项目与招标记录。

地政总署的卖地 / 换地 / 契约修订库里没有市建局的项目：市建局自己收地、自己招标，
土地是「复归政府后重批」，在 CSDI 上看不到一笔「卖地」。所以 land_chain 只能靠
预售同意书的卖方认出市建局盘，结果是已招标、在建但还没批预售的项目整个不在图上。

这里补官方来源：
  https://www.ura.org.hk/en/project/redevelopment   88 个重建项目，每页带
      JSON-LD 的坐标 / 地址、总楼面 / 商业 / 住宅楼面、项目进展、工程时间表、关联新闻
  https://www.ura.org.hk/en/news-centre/press-releases/<日期>   招标新闻稿

市建局公布的口径和地政总署一样齐全：中标公司 + 母公司 + 中标价 + 收到多少份标书，
签约后再补一篇「公布落标金额」的稿，按金额从高到低匿名列出其余标书的出价。
两篇稿讲同一次招标，按它们关联的项目页集合合并成一条记录。

产出 data/ura/projects.csv、data/ura/tenders.csv。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://www.ura.org.hk"
INDEX = "/en/project/redevelopment"
PR_PREFIX = "/en/news-centre/press-releases/"

# 招标相关的新闻稿标题；正文里再判有没有真事实，标题只用来缩小抓取范围
TENDER_KW = re.compile(r"tender|award|expression of interest|unsuccessful", re.I)

WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
            "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
            "twenty": 20, "twenty-one": 21, "twenty-two": 22, "twenty-three": 23, "twenty-four": 24}


# ----------------------------------------------------------------------------
# 抓取
# ----------------------------------------------------------------------------
def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False          # 走本机代理会被 ura.org.hk 拒，直连
    s.headers["User-Agent"] = "Mozilla/5.0 hk-primary-inventory"
    return s


def _get(s: requests.Session, path: str, tries: int = 3) -> str:
    last = None
    for i in range(tries):
        try:
            r = s.get(BASE + path, timeout=60)
            r.raise_for_status()
            if len(r.content) > 5000:
                return r.text
            last = RuntimeError(f"内容过短 {len(r.content)}")
        except Exception as e:      # noqa: BLE001
            last = e
        time.sleep(1 + i)
    raise RuntimeError(f"URA 请求失败 {path}: {last}")


def _cached(s: requests.Session, cache: Path | None, path: str, forever: bool = False) -> str:
    if cache is None:
        return _get(s, path)
    f = cache / (path.strip("/").replace("/", "_") + ".html")
    if f.exists() and f.stat().st_size > 5000 and (forever or time.time() - f.stat().st_mtime < 20 * 3600):
        return f.read_text(encoding="utf-8", errors="replace")
    t = _get(s, path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(t, encoding="utf-8")
    return t


# ----------------------------------------------------------------------------
# 解析
# ----------------------------------------------------------------------------
def _unescape(t: str) -> str:
    for a, b in (("&nbsp;", " "), ("\xa0", " "), ("&amp;", "&"), ("&rsquo;", "'"), ("&lsquo;", "'"),
                 ("&quot;", '"'), ("&ldquo;", '"'), ("&rdquo;", '"'), ("&#39;", "'"), ("&ndash;", "-")):
        t = t.replace(a, b)
    return t


def _text(html: str) -> str:
    """整页转纯文本，块级标签换行。"""
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>", "\n", html)
    html = re.sub(r"(?i)</(p|div|tr|li|h[1-6]|td|th|table)>", "\n", html)
    t = _unescape(re.sub(r"<[^>]+>", " ", html))
    return "\n".join(l for l in (re.sub(r"\s+", " ", x).strip() for x in t.split("\n")) if l)


def _ldjson(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r'(?s)<script type="application/ld\+json">(.*?)</script>', html):
        try:
            d = json.loads(m.group(1))
        except Exception:       # noqa: BLE001
            continue
        out.extend(d if isinstance(d, list) else [d])
    return out


def parse_project(html_en: str, html_tc: str, slug: str) -> dict:
    place = next((o for o in _ldjson(html_en) if o.get("@type") == "Place"), {})
    art_en = next((o for o in _ldjson(html_en) if o.get("@type") == "Article"), {})
    art_tc = next((o for o in _ldjson(html_tc) if o.get("@type") == "Article"), {})
    geo, addr = place.get("geo") or {}, place.get("address") or {}

    name_en = _unescape(art_en.get("headline") or place.get("name") or "").strip()
    name_zh = _unescape(art_tc.get("headline") or "").strip()
    if not name_en:
        return {}                # 少数 slug 重定向到房協网站（市建局 / 房協 合作项目），按口径不要
    # 项目编号写在名字末尾的括号里：KC-013、DL-2:SSP、CBS-1: KC、C&W-006
    m = re.search(r"\(([A-Z][A-Z&]{0,4}[-–][\w:()（） ]{1,14})\)\s*$", name_en)
    code = re.sub(r"\s+", "", m.group(1).replace("–", "-")).rstrip(")") if m else ""

    # 「Project Development Information」是个两列表，键名各页不完全一样
    gfa = {}
    body = html_en[html_en.find("Project Development Information"):] if "Project Development Information" in html_en else ""
    for k, v in re.findall(r"(?is)<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>", body[:4000]):
        k = re.sub(r"\s+", " ", _unescape(re.sub(r"<[^>]+>", "", k))).strip().lower()
        v = re.sub(r"\s+", " ", _unescape(re.sub(r"<[^>]+>", "", v))).strip()
        n = re.search(r"([\d,]+(?:\.\d+)?)", v)
        if not n:
            continue
        val = float(n.group(1).replace(",", ""))
        lv = v.lower()
        is_area = bool(re.search(r"square metre|sq\.?\s?m|hectare|㎡|m2", lv))
        if "hectare" in lv:
            val *= 10000
        if not is_area:
            # 「Residential Flats | About 76」这种是伙数不是楼面，按楼面收会把 76 当成 76 ㎡
            if re.search(r"flat|unit", k):
                gfa["units"] = val
            continue
        if k.startswith("total gfa") or k == "gfa":
            gfa["gfa_total"] = val
        elif "commercial" in k or "non-domestic" in k or "retail" in k:
            gfa["gfa_comm"] = val
        elif "residential" in k or "domestic" in k:
            gfa["gfa_resi"] = val
        elif "site area" in k:
            gfa["site_area"] = val

    # articleBody 的小标题没有前导换行，是直接黏在上一段句号后面的，按标题名切
    txt = _unescape(art_en.get("articleBody") or "").replace("\r", "")
    HEADS = ("Project Status", "Project Programme", "More Information",
             "Project Development Information", "Related Documents")
    parts, cur, buf = {}, "", []
    for tok in re.split(r"(" + "|".join(HEADS) + r")\n", txt):
        if tok in HEADS:
            if cur:
                parts[cur] = "".join(buf)
            cur, buf = tok, []
        else:
            buf.append(tok)
    if cur:
        parts[cur] = "".join(buf)
    def _sect(name):
        t = re.sub(r"\s*\n\s*", " ", parts.get(name, "")).strip()
        # 小标题黏在上一句句号后面（「…2027/28.Proposed Redevelopment The …」），
        # 页面里除了这几个固定标题还有项目自己加的小标题，按「句号紧跟大写字母、中间没空格」切
        return re.split(r"(?<=[.。])(?=[A-Z])", t)[0].strip()
    status, prog = _sect("Project Status"), _sect("Project Programme")

    m = re.search(r"(?i)gazetted on (\d{1,2} \w+ \d{4})", status)
    return {
        "slug": slug, "code": code, "name_en": name_en, "name_zh": name_zh,
        "address": _unescape(addr.get("streetAddress") or ""),
        "district": _unescape(addr.get("addressLocality") or ""),
        "lat": geo.get("latitude"), "lon": geo.get("longitude"),
        **{k: gfa.get(k) for k in ("site_area", "gfa_total", "gfa_comm", "gfa_resi", "units")},
        "gazette": pd.to_datetime(m.group(1), errors="coerce").strftime("%Y-%m-%d") if m else "",
        "status": status[:400], "programme": prog[:200],
        "url": BASE + f"/en/project/redevelopment/{slug}",
    }


def related_news(html: str) -> list[tuple[str, str, str]]:
    pat = re.compile(r'href="(' + PR_PREFIX + r'[^"#]+)">\s*<div class="version-box__body">\s*'
                     r'<span class="version-box__body-date">([^<]*)</span>\s*'
                     r'<div class="version-box__body-desc">(.*?)</div>', re.S)
    out = []
    for u, d, t in pat.findall(html):
        out.append((u, d.strip(), re.sub(r"\s+", " ", _unescape(re.sub(r"<[^>]+>", "", t))).strip()))
    return out


def _pr_body(html: str) -> str:
    """新闻稿正文：导航后的 Back 到页尾的 Back/Top 之间。"""
    lines = _text(html).split("\n")
    i = next((k for k, l in enumerate(lines) if l == "Back"), -1)
    lines = lines[i + 1:]
    j = next((k for k, l in enumerate(lines) if l in ("Back", "Top")), len(lines))
    return "\n".join(lines[:j])


def parse_tender_pr(html: str, pr_id: str) -> dict:
    t = _pr_body(html)
    lines = t.split("\n")
    flat = re.sub(r"\s+", " ", t)
    d = {"pr": pr_id, "pr_date": lines[0] if lines else "", "pr_title": lines[1] if len(lines) > 1 else ""}

    m = re.search(r"(?i)award(?:ed)?\s+(?:the\s+)?(?:tender|development contract|contract)?[^.]{0,60}?\bto\s+"
                  r"(?:a consortium,\s*)?([A-Z][\w'’.\- ]*?(?:Limited|Ltd\.?|Company))"
                  r"(?:\s*,\s*(?:a\s+wholly[\s-]?owned subsidiary of|consisting of|a subsidiary of)\s+([^.,][^.]*?))?(?=[.,])",
                  flat)
    if m:
        d["winner"] = m.group(1).strip()
        d["parent"] = re.sub(r"\s+", " ", m.group(2) or "").strip(" .,")

    m = (re.search(r"(?i)tender amount (?:at|of|offered by [^.]*? is)\s*HK\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion)", flat)
         or re.search(r"(?i)successful tender amount of\s*HK\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion)", flat))
    if m:
        d["amount_m"] = round(float(m.group(1).replace(",", "")) * (1000 if m.group(2).lower() == "billion" else 1), 1)

    m = (re.search(r"(?i)received a total of ([\w-]+) tenders", flat)
         or re.search(r"(?i)a total of ([\w-]+) tenders? (?:were|was) received", flat)
         or re.search(r"(?i)received ([\w-]+) tenders", flat))
    if m:
        v = m.group(1).replace(",", "").lower()
        d["n_tender"] = int(v) if v.isdigit() else WORD_NUM.get(v)

    i = flat.lower().find("descending order")
    if i > 0:
        tail = flat[i:i + 2500]
        cut = re.search(r"(?i)(the project|the development|\(ENDS\)|as a combined)", tail[40:])
        if cut:
            tail = tail[:40 + cut.start()]
        v = sorted({int(x.replace(",", "")) for x in re.findall(r"\b(\d{1,3}(?:,\d{3}){2,})\b", tail)
                    if int(x.replace(",", "")) >= 10_000_000}, reverse=True)
        if v:
            d["underbids_m"] = [round(x / 1e6, 1) for x in v]

    m = re.search(r"(?i)site area of (?:about |approximately )?([\d,]+(?:\.\d+)?) square metres", flat)
    if m:
        d["site_area"] = float(m.group(1).replace(",", ""))
    m = re.search(r"(?i)(?:maximum )?(?:total )?gross floor areas? of (?:about |approximately )?([\d,]+(?:\.\d+)?) square metres", flat)
    if m:
        d["gfa"] = float(m.group(1).replace(",", ""))
    return d


# ----------------------------------------------------------------------------
def build(cache: Path | None, workers: int = 6) -> tuple[pd.DataFrame, pd.DataFrame]:
    s = _session()
    idx = _cached(s, cache, INDEX)
    slugs = sorted({m.group(1) for m in re.finditer(
        r'href="/en/project/redevelopment/([^"#/]+)"', idx)})
    if len(slugs) < 50:
        raise RuntimeError(f"URA 项目索引只解析出 {len(slugs)} 个，页面结构可能变了")
    print(f"[ura] 重建项目 {len(slugs)} 个", file=sys.stderr)

    def one(slug):
        en = _cached(s, cache, f"/en/project/redevelopment/{slug}")
        tc = _cached(s, cache, f"/tc/project/redevelopment/{slug}")
        return slug, parse_project(en, tc, slug), related_news(en)

    with cf.ThreadPoolExecutor(workers) as ex:
        got = list(ex.map(one, slugs))

    skipped = [slug for slug, p, _ in got if not p]
    if skipped:
        print(f"[ura] 跳过 {len(skipped)} 个（页面转到房協，属合作项目）: {', '.join(skipped)}", file=sys.stderr)
    projects = pd.DataFrame([p for _, p, _ in got if p])

    # 新闻稿 -> 关联到哪些项目
    news: dict[str, set[str]] = {}
    titles: dict[str, tuple[str, str]] = {}
    for slug, p, rel in got:
        if not p:
            continue
        for u, dt, ti in rel:
            news.setdefault(u, set()).add(slug)
            titles[u] = (dt, ti)
    want = [u for u in news if TENDER_KW.search(titles[u][1])]
    print(f"[ura] 招标相关新闻稿 {len(want)} 篇", file=sys.stderr)

    def pr(u):
        return parse_tender_pr(_cached(s, cache, u, forever=True), u.rsplit("/", 1)[-1])

    with cf.ThreadPoolExecutor(workers) as ex:
        prs = list(ex.map(pr, want))
    for u, d in zip(want, prs):
        d["projects"] = ";".join(sorted(news[u]))
        d["url"] = BASE + u

    # 中标稿 + 落标稿讲的是同一次招标：按关联项目集合合并
    keep = [d for d in prs if d.get("winner") or d.get("amount_m") or d.get("underbids_m")]
    merged: dict[str, dict] = {}
    for d in sorted(keep, key=lambda x: x["pr"]):
        k = d["projects"]
        cur = merged.setdefault(k, {"projects": k})
        for f in ("winner", "parent", "amount_m", "n_tender", "site_area", "gfa"):
            if cur.get(f) in (None, "", float("nan")) and d.get(f) not in (None, ""):
                cur[f] = d[f]
        if d.get("underbids_m") and not cur.get("underbids_m"):
            cur["underbids_m"] = d["underbids_m"]
            cur["underbid_pr"], cur["underbid_url"] = d["pr"], d["url"]
        if d.get("winner") and not cur.get("award_pr"):
            cur["award_pr"], cur["award_url"], cur["award_date"] = d["pr"], d["url"], d["pr_date"]
        cur.setdefault("award_date", d["pr_date"])
    tenders = pd.DataFrame(sorted(merged.values(), key=lambda x: x.get("award_pr") or ""))
    if "underbids_m" in tenders:
        tenders["underbids_m"] = tenders.underbids_m.map(
            lambda v: json.dumps(v) if isinstance(v, list) else "")
    tenders["award_date"] = pd.to_datetime(tenders.award_date, errors="coerce").dt.strftime("%Y-%m-%d")
    return projects, tenders.sort_values("award_date")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/ura")
    ap.add_argument("--cache", default="")
    ap.add_argument("--max-age-hours", type=float, default=0,
                    help="产出比这个新就跳过（给 run_daily 用，避免一天抓 4 次）")
    a = ap.parse_args()
    out = Path(a.out)
    if a.max_age_hours and (out / "projects.csv").exists():
        age = (time.time() - (out / "projects.csv").stat().st_mtime) / 3600
        if age < a.max_age_hours:
            print(f"[ura] {age:.1f} 小时前跑过，跳过", file=sys.stderr)
            return
    proj, tend = build(Path(a.cache) if a.cache else None)
    out.mkdir(parents=True, exist_ok=True)
    proj.to_csv(out / "projects.csv", index=False)
    tend.to_csv(out / "tenders.csv", index=False)
    print(f"[ura] 项目 {len(proj)} 个 / 招标 {len(tend)} 次 -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
