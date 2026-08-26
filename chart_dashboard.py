# -*- coding: utf-8 -*-
"""交互式 HTML 看板：读取 out_inventory 下的 CSV，用 ECharts 渲染离线可双击打开的 dashboard.html。

这是唯一的图表产物；早先的 matplotlib 静态研报（report_chart.png/pdf）已移除。
输出: out_inventory/dashboard.html
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = PROJECT_DIR / "out_inventory"

ECHARTS_LOCAL = PROJECT_DIR / "assets" / "echarts.min.js"
ECHARTS_CDN = "https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"


def resolve_echarts_src(dirpath: Path) -> str:
    """优先引用本地 assets/echarts.min.js（离线可用），否则回退到 CDN。"""
    local = dirpath.parent / "assets" / "echarts.min.js"
    if local.exists():
        return "assets/echarts.min.js"
    return ECHARTS_CDN


def _load_inventory_csv(dirpath: Path) -> pd.DataFrame:
    df = pd.read_csv(dirpath / "instant_saleable_inventory_monthly.csv")
    df["date"] = pd.to_datetime(df["month"].astype(str) + "-01")
    return df.sort_values("date").reset_index(drop=True)


def _load_presale_csv(dirpath: Path) -> pd.DataFrame:
    df = pd.read_csv(dirpath / "presale_approvals_monthly.csv")
    df.columns = [c.strip() for c in df.columns]
    return df


def _load_landreg_csv(dirpath: Path) -> pd.DataFrame:
    df = pd.read_csv(dirpath / "landreg_primary_monthly.csv")
    df.columns = [c.strip() for c in df.columns]
    return df


def _load_optional_csv(dirpath: Path, name: str) -> pd.DataFrame | None:
    """补充序列缺失时返回 None，看板照常渲染其余图表。"""
    path = dirpath / name
    if not path.exists():
        print(f"[看板] 缺 {name}，相关序列留空")
        return None
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]
    return df


def _series_by_month(df: pd.DataFrame | None, months: list[str], col: str) -> list:
    """按 months 顺序对齐取值，缺失填 None（ECharts 会断线而不是画到 0）。"""
    if df is None or col not in df.columns or "month" not in df.columns:
        return [None] * len(months)
    m = dict(zip(df["month"].astype(str), df[col]))
    out = []
    for ym in months:
        v = m.get(ym)
        out.append(None if v is None or pd.isna(v) else float(v))
    return out


def _union_months(dfs: list[pd.DataFrame | None], fallback: list[str]) -> list[str]:
    """把若干张表的 month 列并成一条连续的月份轴。"""
    got = [str(m) for df in dfs if df is not None and "month" in df.columns
           for m in df["month"].astype(str)]
    if not got:
        return list(fallback)
    lo, hi = min(min(got), fallback[0]), max(max(got), fallback[-1])
    out, cur = [], pd.Period(lo, freq="M")
    stop = pd.Period(hi, freq="M")
    while cur <= stop:
        out.append(str(cur))
        cur += 1
    return out


def _month_label(df: pd.DataFrame) -> list[str]:
    return df["date"].dt.strftime("%Y-%m").tolist()


def build_dashboard_html(dirpath: Path) -> str:
    inv = _load_inventory_csv(dirpath)
    presale = _load_presale_csv(dirpath)
    landreg = _load_landreg_csv(dirpath)
    pending_df = _load_optional_csv(dirpath, "pending_presale_monthly.csv")
    ccl_df = _load_optional_csv(dirpath, "ccl_monthly.csv")
    landreg_full = _load_optional_csv(dirpath, "landreg_full_monthly.csv")
    projects_df = _load_optional_csv(dirpath, "projects_inventory.csv")
    pending_proj_df = _load_optional_csv(dirpath, "pending_projects.csv")
    echarts_src = resolve_echarts_src(dirpath)

    # 合并月度批出 / 成交，统一用 YYYY-MM 字符串作 key
    monthly = inv[["date"]].copy()
    monthly["ym"] = monthly["date"].dt.strftime("%Y-%m")
    monthly = monthly.merge(
        presale[["month", "presale_approved_units"]].rename(columns={"month": "ym"}),
        on="ym", how="left",
    )
    monthly = monthly.merge(
        landreg[["month", "primary_units"]].rename(columns={"month": "ym"}),
        on="ym", how="left",
    )
    monthly["presale_approved_units"] = monthly["presale_approved_units"].fillna(0)
    monthly["primary_units"] = monthly["primary_units"].fillna(0)

    months = _month_label(inv)
    inv_vals = [None if pd.isna(v) else int(v) for v in inv["instant_saleable_inventory"]]
    pending_vals = [None if v is None else int(v)
                    for v in _series_by_month(pending_df, months, "pending_units")]

    # 底部那张图用自己的长横轴：CCL 从 1994 起、二手成交从 2002 起，
    # 不跟着库存区间（2016 起）截断。柱子在没数据的年份留空即可。
    # 年度成交：伙数与金额（t2.json 的 $ million -> 億）
    yr_labels: list[str] = []
    yr_pri_units: list[int] = []
    yr_sec_units: list[int] = []
    yr_pri_amt: list[float] = []
    yr_sec_amt: list[float] = []
    if landreg_full is not None and "primary_amount_hkm" in landreg_full.columns:
        f = landreg_full.copy()
        f["year"] = f["month"].astype(str).str[:4]
        agg = f.groupby("year", as_index=False).sum(numeric_only=True)
        yr_labels = agg["year"].tolist()
        yr_pri_units = [int(v) for v in agg["primary_units"]]
        yr_sec_units = [int(v) for v in agg["secondary_units"]]
        yr_pri_amt = [round(v / 100, 1) for v in agg["primary_amount_hkm"]]
        yr_sec_amt = [round(v / 100, 1) for v in agg["secondary_amount_hkm"]]

    long_months = _union_months([ccl_df, landreg_full], fallback=months)
    ccl_vals = _series_by_month(ccl_df, long_months, "ccl")
    secondary_vals = [None if v is None else int(v)
                      for v in _series_by_month(landreg_full, long_months, "secondary_units")]
    # 月末官方未发布时 CSV 里补的是 0，画成零高柱会误导
    for i in range(len(secondary_vals) - 1, -1, -1):
        if secondary_vals[i]:
            break
        secondary_vals[i] = None
    presale_vals = [int(v) for v in monthly["presale_approved_units"]]
    primary_vals = [int(v) for v in monthly["primary_units"]]

    # 季度聚合
    qk = monthly["date"].dt.year.astype(str) + "-Q" + monthly["date"].dt.quarter.astype(str)
    q_group = monthly.groupby(qk).agg(
        presale=("presale_approved_units", "sum"),
        primary=("primary_units", "sum"),
    ).reset_index()
    q_group.columns = ["key", "presale", "primary"]
    q_keys = q_group["key"].tolist()
    q_presale = [int(v) for v in q_group["presale"]]
    q_primary = [int(v) for v in q_group["primary"]]

    # ---- 年度汇总 + 标注坐标（月度图用月度 x 索引，季度图用季度 x 索引） ----
    m_annual = []
    for y, sub in monthly.groupby(monthly["date"].dt.year):
        xs = sub.index.tolist()
        m_annual.append({
            "year": int(y), "start": xs[0], "end": xs[-1],
            "mid": (xs[0] + xs[-1]) // 2,
            "presale": int(sub["presale_approved_units"].sum()),
            "primary": int(sub["primary_units"].sum()),
        })

    q_annual = []
    q_years = [k.split("-Q")[0] for k in q_keys]
    for y in sorted(set(q_years)):
        idxs = [i for i, yy in enumerate(q_years) if yy == y]
        q_annual.append({
            "year": int(y), "start": idxs[0], "end": idxs[-1],
            "mid": (idxs[0] + idxs[-1]) // 2,
        })

    # ---- KPI：找「批出与成交都有数据」的最新月份作为有效月，保证数据同步 ----
    eff_index = len(presale_vals) - 1
    while eff_index >= 0:
        if presale_vals[eff_index] > 0 and primary_vals[eff_index] > 0:
            break
        eff_index -= 1
    if eff_index < 0:
        eff_index = len(presale_vals) - 1

    eff_month = months[eff_index]
    eff_inv = inv_vals[eff_index] if inv_vals[eff_index] is not None else 0
    eff_presale = presale_vals[eff_index]
    eff_primary = primary_vals[eff_index]

    # 月末那几个月官方还没发布，CSV 里补的是 0。画成 0 会变成一根假的零高柱、
    # 以及一段假的库存平台，所以画图时截断成 null（CCL / 待批各自有真实覆盖，不动）。
    data_end = len(months) - 1
    while data_end >= 0 and not (presale_vals[data_end] or primary_vals[data_end]):
        data_end -= 1
    for i in range(data_end + 1, len(months)):
        presale_vals[i] = None
        primary_vals[i] = None
        inv_vals[i] = None

    # house730 逐盘在售货量：KPI 第二格 + 点开的项目列表
    projects: list[dict] = []
    market = {"projects": 0, "phases": 0, "total": 0, "sold": 0, "remaining": 0}
    if projects_df is not None and not projects_df.empty:
        pf = projects_df.fillna("")
        for r in pf.to_dict("records"):
            projects.append({
                "name": str(r.get("project", "")),
                "phases": int(r.get("phases") or 0),
                "phase_names": str(r.get("phase_names", "")),
                "developer": str(r.get("main_developer", "")),
                "address": str(r.get("address", "")),
                "total": int(r.get("total_units") or 0),
                "sold": int(r.get("sold_units") or 0),
                "left": int(r.get("remaining_units") or 0),
                "pct": float(r.get("remaining_pct") or 0),
                "first": str(r.get("first_sales_date", "")),
                "months": int(r.get("months_since_launch") or 0),
                "emd": str(r.get("estimated_material_date", "")),
            })
        market = {
            "projects": len(projects),
            "phases": int(pf["phases"].sum()),
            "total": int(pf["total_units"].sum()),
            "sold": int(pf["sold_units"].sum()),
            "remaining": int(pf["remaining_units"].sum()),
        }

    # 待批预售逐项目（CSDI 当前快照）：KPI 第三格 + 点开的列表
    pending_projects: list[dict] = []
    pending_market = {"projects": 0, "applications": 0, "units": 0, "as_of": ""}
    if pending_proj_df is not None and not pending_proj_df.empty:
        pp = pending_proj_df.fillna("")
        for r in pp.to_dict("records"):
            pending_projects.append({
                "name": str(r.get("project", "")),
                "phases": int(r.get("phases") or 0),
                "phase_names": str(r.get("phase_names", "")),
                "lot": str(r.get("lot", "")),
                "address": str(r.get("address", "")),
                "vendor": str(r.get("vendor", "")),
                "units": int(r.get("pending_units") or 0),
                "emd": str(r.get("estimated_material_date", "")),
            })
        pending_market = {
            "projects": len(pending_projects),
            "applications": int(pp["phases"].sum()),
            "units": int(pp["pending_units"].sum()),
            "as_of": str(pp["as_of"].iloc[0]) if "as_of" in pp.columns else "",
        }

    from datetime import datetime
    last_update = datetime.now().strftime("%Y-%m-%d %H:%M")

    data = {
        "months": months, "inv": inv_vals,
        "pending": pending_vals,
        "long_months": long_months, "ccl": ccl_vals, "secondary": secondary_vals,
        "yr_labels": yr_labels, "yr_pri_units": yr_pri_units, "yr_sec_units": yr_sec_units,
        "yr_pri_amt": yr_pri_amt, "yr_sec_amt": yr_sec_amt,
        "projects": projects, "market": market,
        "pending_projects": pending_projects, "pending_market": pending_market,
        "presale": presale_vals, "primary": primary_vals,
        "q_keys": q_keys, "q_presale": q_presale, "q_primary": q_primary,
        "m_annual": m_annual, "q_annual": q_annual,
        "kpi": {
            "effective_month": eff_month, "effective_inv": eff_inv,
            "effective_presale": eff_presale, "effective_primary": eff_primary,
            "last_update": last_update,
        },
    }
    data_json = json.dumps(data, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>香港一手住宅市场 - 即时可售货量看板</title>
<script src="{echarts_src}"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background: #f4f6f9; color: #2c3e50; }}
  .header {{ background: linear-gradient(135deg, #1f3a5f, #2980b9); color: #fff; padding: 20px 30px; }}
  .header h1 {{ font-size: 22px; font-weight: 700; }}
  .header .sub {{ font-size: 12px; opacity: 0.9; margin-top: 6px; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }}
  .kpi {{ background: #fff; border-radius: 12px; padding: 18px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }}
  .kpi .label {{ font-size: 13px; color: #7f8c8d; }}
  .kpi .value {{ font-size: 26px; font-weight: 700; color: #1f3a5f; margin-top: 6px; }}
  .kpi .value sub {{ font-size: 13px; color: #7f8c8d; font-weight: 400; }}
  .kpi .hint {{ font-size: 11px; color: #95a5a6; margin-top: 4px; }}
  .chart-card {{ background: #fff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); margin-bottom: 20px; }}
  .chart-card h2 {{ font-size: 15px; color: #1f3a5f; margin-bottom: 10px; }}
  .chart {{ width: 100%; height: 400px; }}
  .footer {{ text-align: center; color: #95a5a6; font-size: 11px; padding: 16px; }}
  .kpi--click {{ cursor: pointer; transition: box-shadow .15s, transform .15s; }}
  .kpi--click:hover {{ box-shadow: 0 4px 16px rgba(31,111,235,0.22); transform: translateY(-2px); }}
  .kpi--click .hint {{ color: #1f6feb; font-weight: 600; }}

  .ov {{ position: fixed; inset: 0; background: rgba(15,23,42,.55); display: none;
        align-items: center; justify-content: center; padding: 24px; z-index: 999; }}
  .ov.on {{ display: flex; }}
  .ov__box {{ background: #fff; border-radius: 14px; width: min(1180px, 100%); max-height: 88vh;
             display: flex; flex-direction: column; overflow: hidden; }}
  .ov__hd {{ padding: 16px 20px; border-bottom: 1px solid #e8eef5; display: flex;
            align-items: center; gap: 14px; flex-wrap: wrap; }}
  .ov__hd h3 {{ font-size: 17px; color: #1f3a5f; margin: 0; }}
  .ov__sum {{ font-size: 12px; color: #7f8c8d; }}
  .ov__x {{ margin-left: auto; border: 0; background: #f1f5f9; border-radius: 8px;
           width: 30px; height: 30px; font-size: 17px; cursor: pointer; color: #64748b; }}
  .ov__x:hover {{ background: #e2e8f0; }}
  .ov__find {{ border: 1px solid #dbe4ee; border-radius: 8px; padding: 6px 10px; font-size: 13px; width: 210px; }}
  .ov__body {{ overflow: auto; }}
  table.pl {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  table.pl th, table.pl td {{ padding: 8px 12px; border-bottom: 1px solid #eef2f7; white-space: nowrap; }}
  table.pl th {{ position: sticky; top: 0; background: #f8fafc; color: #475569; font-weight: 600;
                cursor: pointer; user-select: none; z-index: 1; }}
  table.pl th:hover {{ background: #eef2f7; }}
  table.pl td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  table.pl td.nm {{ white-space: normal; min-width: 190px; font-weight: 600; color: #1f3a5f; }}
  table.pl tbody tr:hover {{ background: #f8fbff; }}
  .tag {{ display: inline-block; font-size: 10px; padding: 1px 6px; border-radius: 20px;
         background: #eef2ff; color: #4f46e5; margin-left: 6px; font-weight: 600; }}
  .pctcell {{ display: flex; align-items: center; justify-content: flex-end; gap: 8px; }}
  .bar {{ position: relative; height: 6px; width: 64px; flex: none;
         background: #eef2f7; border-radius: 4px; overflow: hidden; }}
  .bar > i {{ position: absolute; left: 0; top: 0; bottom: 0; border-radius: 4px; background: #1f6feb; }}
  .pctnum {{ min-width: 44px; text-align: right; font-variant-numeric: tabular-nums; }}
  @media (max-width: 800px) {{ .kpis {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>
  <div class="header">
    <h1>🏙️ 香港一手住宅市场 · 即时可售货量看板</h1>
    <div class="sub">数据来源：土地注册处一手成交 · 地政总署预售批出 · 锚点回推 · 数据截至 {data['kpi']['effective_month']} · 更新时间 {data['kpi']['last_update']}</div>
  </div>

  <div class="container">
    <div class="kpis">
      <div class="kpi">
        <div class="label">即时可售货量（{data['kpi']['effective_month']}）</div>
        <div class="value">{data['kpi']['effective_inv']:,}<sub> 伙</sub></div>
        <div class="hint">锚点回推所得，即时可售</div>
      </div>
      <div class="kpi kpi--click" id="kpi-market" role="button" tabindex="0">
        <div class="label">当前市场在售货量</div>
        <div class="value">{market['remaining']:,}<sub> 伙</sub></div>
        <div class="hint">{market['projects']} 个在售项目 · 点击查看明细 ▸</div>
      </div>
      <div class="kpi kpi--click" id="kpi-pending" role="button" tabindex="0">
        <div class="label">最新待批预售（截至 {pending_market['as_of'] or '—'}）</div>
        <div class="value">{pending_market['units']:,}<sub> 伙</sub></div>
        <div class="hint">{pending_market['projects']} 个项目 / {pending_market['applications']} 宗申请 · 点击查看明细 ▸</div>
      </div>
      <div class="kpi">
        <div class="label">当月净变化（批出-成交）</div>
        <div class="value">{data['kpi']['effective_presale'] - data['kpi']['effective_primary']:+,}<sub> 伙</sub></div>
        <div class="hint">批出减成交，即时可售增减</div>
      </div>
    </div>

    <div class="chart-card">
      <h2>年度对照：批出楼花 vs 一手成交（每年合计）</h2>
      <div id="chart-annual" class="chart"></div>
    </div>

    <div class="chart-card">
      <h2>即时可售货量 vs 待批预售楼花（月度走势 · 默认显示近 3 年，可拖动查看更早）</h2>
      <div id="chart-inv" class="chart"></div>
    </div>

    <div class="chart-card">
      <h2>批出楼花 vs 一手成交（月度 · 年度合计标注）</h2>
      <div id="chart-flow" class="chart"></div>
    </div>

    <div class="chart-card">
      <h2>批出 vs 成交（季度聚合 · 年度合计标注）</h2>
      <div id="chart-quarter" class="chart"></div>
    </div>

    <div class="chart-card">
      <h2>每年一手 / 二手成交（上下分区共用年份轴 · 上为成交金额，下为成交单位数，自 2002 年；最后一年为年初至今）</h2>
      <div id="chart-year" class="chart"></div>
    </div>

    <div class="chart-card">
      <h2>中原城市领先指数 CCL 与二手成交（上下分区共用横轴 · CCL 自 1994 年、二手成交自 2002 年，默认近 3 年，可拖到最早）</h2>
      <div id="chart-second" class="chart"></div>
    </div>
  </div>

  <div class="ov" id="ov">
    <div class="ov__box">
      <div class="ov__hd">
        <h3>当前市场在售项目</h3>
        <span class="ov__sum" id="ov-sum"></span>
        <input class="ov__find" id="ov-find" placeholder="搜项目 / 发展商 / 地址">
        <button class="ov__x" id="ov-x" aria-label="关闭">×</button>
      </div>
      <div class="ov__body"><table class="pl">
        <thead><tr>
          <th data-k="name">项目</th>
          <th data-k="total" class="num">总货量</th>
          <th data-k="sold" class="num">已售</th>
          <th data-k="left" class="num">余货</th>
          <th data-k="pct" class="num">余货占比</th>
          <th data-k="first">开售日期</th>
          <th data-k="months" class="num">开售月数</th>
          <th data-k="emd">预计关键日期</th>
          <th data-k="developer">发展商</th>
        </tr></thead>
        <tbody id="ov-rows"></tbody>
      </table></div>
    </div>
  </div>

  <div class="ov" id="ov2">
    <div class="ov__box">
      <div class="ov__hd">
        <h3>最新待批预售同意书</h3>
        <span class="ov__sum" id="ov2-sum"></span>
        <input class="ov__find" id="ov2-find" placeholder="搜项目 / 卖方 / 地段 / 地址">
        <button class="ov__x" id="ov2-x" aria-label="关闭">×</button>
      </div>
      <div class="ov__body"><table class="pl">
        <thead><tr>
          <th data-k="name">项目</th>
          <th data-k="units" class="num">待批伙数</th>
          <th data-k="emd">预计关键日期</th>
          <th data-k="lot">地段编号</th>
          <th data-k="vendor">卖方</th>
          <th data-k="address">地址</th>
        </tr></thead>
        <tbody id="ov2-rows"></tbody>
      </table></div>
    </div>
  </div>

  <div class="footer">交互看板（ECharts）— 双击此文件即可本地查看 · 数据由 一手短期库存.py 生成</div>

<script>
  const D = {data_json};

  // ---------- 年度对照图：每年 批出 vs 成交 ----------
  var annualYears = D.m_annual.map(function(a){{ return String(a.year); }});
  var annualPresale = D.m_annual.map(function(a){{ return a.presale; }});
  var annualPrimary = D.m_annual.map(function(a){{ return a.primary; }});
  var annualChart = echarts.init(document.getElementById('chart-annual'));
  annualChart.setOption({{
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
    legend: {{ data: ['批出楼花', '一手成交'], top: 0 }},
    grid: {{ left: 60, right: 30, top: 40, bottom: 40 }},
    xAxis: {{ type: 'category', data: annualYears, axisLabel: {{ rotate: 0 }} }},
    yAxis: {{ type: 'value', name: '伙', nameGap: 16 }},
    series: [
      {{ name: '批出楼花', type: 'bar', data: annualPresale, itemStyle: {{ color: '#2ca02c' }}, barMaxWidth: 26, label: {{ show: true, position: 'top', fontSize: 10 }} }},
      {{ name: '一手成交', type: 'bar', data: annualPrimary, itemStyle: {{ color: '#d62728' }}, barMaxWidth: 26, label: {{ show: true, position: 'top', fontSize: 10 }} }}
    ]
  }});

  // 折线图默认显示近 3 年（36 个月），可拖动查看更早
  var totMonths = D.months.length;
  var zoomStart = Math.max(0, (totMonths - 36) / totMonths * 100);

  // ---------- 图1：可售货量折线 ----------
  var invChart = echarts.init(document.getElementById('chart-inv'));
  invChart.setOption({{
    tooltip: {{
      trigger: 'axis',
      formatter: function(ps) {{
        var s = ps[0].axisValue + '<br/>';
        ps.forEach(function(p){{
          s += p.marker + p.seriesName + ': <b>' +
               (p.value==null ? '—' : Number(p.value).toLocaleString()) + '</b> 伙<br/>';
        }});
        return s;
      }}
    }},
    legend: {{ data: ['即时可售货量', '待批预售楼花'], top: 0 }},
    grid: {{ left: 60, right: 30, top: 40, bottom: 40 }},
    xAxis: {{ type: 'category', data: D.months, boundaryGap: false }},
    yAxis: {{ type: 'value', name: '伙', nameGap: 16 }},
    dataZoom: [
      {{ type: 'inside', start: zoomStart, end: 100 }},
      {{ type: 'slider', height: 18, bottom: 4, start: zoomStart, end: 100 }}
    ],
    series: [
      {{
        name: '即时可售货量', type: 'line', data: D.inv, smooth: true,
        symbol: 'circle', symbolSize: 5,
        lineStyle: {{ width: 3, color: '#1f6feb' }},
        itemStyle: {{ color: '#1f6feb' }},
        areaStyle: {{ color: new echarts.graphic.LinearGradient(0,0,0,1,[
          {{offset:0,color:'rgba(31,111,235,0.25)'}},{{offset:1,color:'rgba(31,111,235,0.02)'}}
        ]) }}
      }},
      {{
        // 该源只有当前快照、无历史，所以这条线从启用之日起逐月长出来
        name: '待批预售楼花', type: 'line', data: D.pending, smooth: true,
        connectNulls: false, symbol: 'circle', symbolSize: 6,
        lineStyle: {{ width: 2, color: '#e8890c', type: 'dashed' }},
        itemStyle: {{ color: '#e8890c' }}
      }}
    ]
  }});

  // ---------- 蝴蝶图（上下轴）工厂函数：上图批出、下图成交，年度合计标注 ----------
  function butterfly(cat, presaleData, primaryData, annualData, xLabelRotate) {{
    var xAxis = {{
      type: 'category', data: cat, axisLabel: xLabelRotate ? {{ rotate: xLabelRotate }} : {{}}
    }};
    // 年度合计标注：图形元素
    var graphics = [];
    annualData.forEach(function(a){{
      // 上图顶部：批出合计
      graphics.push({{
        type: 'text', silent: true,
        left: a.mid, bottom: 38, z: 100,
        style: {{ text: a.year + ' 批出 ' + Number(a.presale).toLocaleString(), fontSize: 9, fontWeight: 'bold', fill: '#1b6e20', textAlign: 'center', textVerticalAlign: 'top', backgroundColor: '#fff', padding: [2,4] }}
      }});
      // 下图底部：成交合计
      graphics.push({{
        type: 'text', silent: true,
        left: a.mid, top: 42, z: 100,
        style: {{ text: a.year + ' 成交 ' + Number(a.primary).toLocaleString(), fontSize: 9, fontWeight: 'bold', fill: '#a02020', textAlign: 'center', textVerticalAlign: 'top', backgroundColor: '#fff', padding: [2,4] }}
      }});
    }});
    return {{
      tooltip: {{
        trigger: 'axis',
        axisPointer: {{ type: 'shadow' }},
        formatter: function(ps) {{
          var s = ps[0].axisValue + '<br/>';
          ps.forEach(function(p){{ s += p.marker + p.seriesName + ': <b>' + Number(Math.abs(p.value)).toLocaleString() + '</b> 伙<br/>'; }});
          return s;
        }}
      }},
      grid: [
        {{ left: 60, right: 40, top: 30, height: '34%' }},
        {{ left: 60, right: 40, top: '52%', height: '34%' }}
      ],
      xAxis: [ xAxis, Object.assign({{}}, xAxis, {{ gridIndex: 1, show: false, boundaryGap: true }}) ],
      yAxis: [
        {{ type: 'value', name: '批出（伙）', nameGap: 16, gridIndex: 0, splitLine: {{ lineStyle: {{ type:'dashed' }} }} }},
        {{ type: 'value', name: '成交（伙）', nameGap: 16, gridIndex: 1, splitLine: {{ lineStyle: {{ type:'dashed' }} }} }}
      ],
      dataZoom: [
        {{ type: 'inside', xAxisIndex: [0,1], start: 0, end: 100 }},
        {{ type: 'slider', xAxisIndex: [0,1], height: 14, bottom: 4 }}
      ],
      graphic: graphics,
      series: [
        {{ name: '批出楼花', type: 'bar', data: presaleData, itemStyle: {{ color: '#2ca02c' }}, barMaxWidth: 14 }},
        {{ name: '一手成交', type: 'bar', data: primaryData, itemStyle: {{ color: '#d62728' }}, barMaxWidth: 14, xAxisIndex: 1, yAxisIndex: 1 }}
      ]
    }};
  }}

  // ---------- 图2：月度蝴蝶图 ----------
  var flowChart = echarts.init(document.getElementById('chart-flow'));
  flowChart.setOption(butterfly(D.months, D.presale, D.primary, D.m_annual, 0));
  flowChart.getZr().on('click', function(){{ /* 占位，后续可做点击联动 */ }});

  // ---------- 图3：季度蝴蝶图 ----------
  var qChart = echarts.init(document.getElementById('chart-quarter'));
  // 季度图年度合计也给出（p/s 与月度图一致，此处只复用年度文字位置）
  var qAnnualText = D.q_annual.map(function(a){{
    var match = D.m_annual.find(function(m){{ return m.year === a.year; }});
    return {{ year: a.year, start: a.start, end: a.end, mid: a.mid, presale: match ? match.presale : 0, primary: match ? match.primary : 0 }};
  }});
  qChart.setOption(butterfly(D.q_keys, D.q_presale, D.q_primary, qAnnualText, 45));

  // ---------- 上下分区：上 CCL 折线、下 二手成交柱，共用月份横轴 ----------
  // 两者量纲差三个数量级（指数 ~150 vs 伙数 ~5000），叠在左右轴上互相压扁，
  // 所以拆成上下两个 grid，只共享 x 轴。
  var secondChart = echarts.init(document.getElementById('chart-second'));
  var secondXAxis = {{ type: 'category', data: D.long_months, boundaryGap: false }};
  var longTot = D.long_months.length;
  var longZoomStart = Math.max(0, (longTot - 36) / longTot * 100);
  secondChart.setOption({{
    tooltip: {{
      trigger: 'axis',
      axisPointer: {{ type: 'cross', link: [{{ xAxisIndex: 'all' }}] }},
      formatter: function(ps) {{
        var s = ps[0].axisValue + '<br/>';
        ps.forEach(function(p){{
          if (p.value == null) return;
          var unit = (p.seriesName === 'CCL') ? '' : ' 伙';
          s += p.marker + p.seriesName + ': <b>' + Number(p.value).toLocaleString() + '</b>' + unit + '<br/>';
        }});
        return s;
      }}
    }},
    legend: {{ data: ['CCL', '二手成交'], top: 0 }},
    axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }},
    grid: [
      {{ left: 60, right: 40, top: 34, height: '34%' }},
      {{ left: 60, right: 40, top: '56%', height: '30%' }}
    ],
    xAxis: [
      Object.assign({{}}, secondXAxis, {{ gridIndex: 0, axisLabel: {{ show: false }}, axisTick: {{ show: false }} }}),
      Object.assign({{}}, secondXAxis, {{ gridIndex: 1, boundaryGap: true, axisLabel: {{ rotate: 45, fontSize: 10 }} }})
    ],
    yAxis: [
      // scale:true —— 指数在 127~190 之间，从 0 起画会压成一条平线
      {{ type: 'value', name: 'CCL', nameGap: 16, gridIndex: 0, scale: true,
         splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }},
      {{ type: 'value', name: '二手成交（伙）', nameGap: 16, gridIndex: 1,
         splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }}
    ],
    dataZoom: [
      {{ type: 'inside', xAxisIndex: [0, 1], start: longZoomStart, end: 100 }},
      {{ type: 'slider', xAxisIndex: [0, 1], height: 16, bottom: 4, start: longZoomStart, end: 100 }}
    ],
    series: [
      {{
        name: 'CCL', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: D.ccl,
        smooth: true, connectNulls: false, symbol: 'none',
        lineStyle: {{ width: 2.5, color: '#c0392b' }}, itemStyle: {{ color: '#c0392b' }},
        areaStyle: {{ color: new echarts.graphic.LinearGradient(0,0,0,1,[
          {{offset:0,color:'rgba(192,57,43,0.18)'}},{{offset:1,color:'rgba(192,57,43,0.01)'}}
        ]) }}
      }},
      {{
        name: '二手成交', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: D.secondary,
        itemStyle: {{ color: '#7f7fd5' }}, barMaxWidth: 14
      }}
    ]
  }});

  // ---------- 年度成交：上金额（億）、下单位数（伙），共用年份横轴 ----------
  var yearChart = echarts.init(document.getElementById('chart-year'));
  var yearXAxis = {{ type: 'category', data: D.yr_labels }};
  yearChart.setOption({{
    tooltip: {{
      trigger: 'axis',
      axisPointer: {{ type: 'shadow', link: [{{ xAxisIndex: 'all' }}] }},
      formatter: function(ps) {{
        var s = ps[0].axisValue + ' 年<br/>';
        ps.forEach(function(p){{
          if (p.value == null) return;
          var unit = (p.seriesName.indexOf('金额') >= 0) ? ' 億' : ' 伙';
          s += p.marker + p.seriesName + ': <b>' + Number(p.value).toLocaleString() + '</b>' + unit + '<br/>';
        }});
        return s;
      }}
    }},
    legend: {{ data: ['一手金额', '二手金额', '一手单位数', '二手单位数'], top: 0 }},
    axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }},
    grid: [
      {{ left: 66, right: 40, top: 34, height: '34%' }},
      {{ left: 66, right: 40, top: '56%', height: '30%' }}
    ],
    xAxis: [
      Object.assign({{}}, yearXAxis, {{ gridIndex: 0, axisLabel: {{ show: false }}, axisTick: {{ show: false }} }}),
      Object.assign({{}}, yearXAxis, {{ gridIndex: 1, axisLabel: {{ rotate: 45, fontSize: 10 }} }})
    ],
    yAxis: [
      {{ type: 'value', name: '成交金额（億）', nameGap: 16, gridIndex: 0, splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }},
      {{ type: 'value', name: '成交单位数（伙）', nameGap: 16, gridIndex: 1, splitLine: {{ lineStyle: {{ type: 'dashed' }} }} }}
    ],
    dataZoom: [
      {{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }},
      {{ type: 'slider', xAxisIndex: [0, 1], height: 14, bottom: 4, start: 0, end: 100 }}
    ],
    series: [
      {{ name: '一手金额', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: D.yr_pri_amt,
         itemStyle: {{ color: '#d62728' }}, barMaxWidth: 18 }},
      {{ name: '二手金额', type: 'bar', xAxisIndex: 0, yAxisIndex: 0, data: D.yr_sec_amt,
         itemStyle: {{ color: '#7f7fd5' }}, barMaxWidth: 18 }},
      {{ name: '一手单位数', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: D.yr_pri_units,
         itemStyle: {{ color: '#d62728' }}, barMaxWidth: 18 }},
      {{ name: '二手单位数', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: D.yr_sec_units,
         itemStyle: {{ color: '#7f7fd5' }}, barMaxWidth: 18 }}
    ]
  }});

  window.addEventListener('resize', function(){{
    annualChart.resize(); invChart.resize(); flowChart.resize();
    qChart.resize(); secondChart.resize(); yearChart.resize();
  }});

  // ---------- 可点 KPI 弹出的明细列表（两处共用）----------
  function esc(v) {{ return String(v == null ? '' : v).replace(/"/g, '&quot;'); }}
  function num(v) {{ return Number(v || 0).toLocaleString(); }}

  function mountOverlay(cfg) {{
    var ov = document.getElementById(cfg.ov);
    var card = document.getElementById(cfg.kpi);
    if (!ov || !card) return;
    var rowsEl = document.getElementById(cfg.ov + '-rows');
    var findEl = document.getElementById(cfg.ov + '-find');
    var data = (cfg.data || []).slice();
    var sortKey = cfg.sortKey, sortAsc = false;

    document.getElementById(cfg.ov + '-sum').textContent = cfg.summary;

    function render() {{
      var q = (findEl.value || '').trim().toLowerCase();
      var rows = data.filter(function (p) {{
        return !q || cfg.searchText(p).toLowerCase().indexOf(q) >= 0;
      }});
      rows.sort(function (a, b) {{
        var x = a[sortKey], y = b[sortKey];
        if (typeof x === 'string' || typeof y === 'string') {{
          x = String(x); y = String(y);
          return sortAsc ? x.localeCompare(y) : y.localeCompare(x);
        }}
        return sortAsc ? x - y : y - x;
      }});
      rowsEl.innerHTML = rows.map(cfg.row).join('')
        || '<tr><td colspan="9" style="padding:22px;color:#94a3b8">没有匹配的项目</td></tr>';
    }}

    [].forEach.call(ov.querySelectorAll('table.pl th'), function (th) {{
      th.addEventListener('click', function () {{
        var k = th.getAttribute('data-k');
        if (sortKey === k) {{ sortAsc = !sortAsc; }}
        else {{ sortKey = k; sortAsc = (cfg.textCols.indexOf(k) >= 0); }}
        render();
      }});
    }});
    findEl.addEventListener('input', render);

    function open() {{ ov.classList.add('on'); render(); findEl.focus(); }}
    function close() {{ ov.classList.remove('on'); }}
    card.addEventListener('click', open);
    card.addEventListener('keydown', function (e) {{
      if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); open(); }}
    }});
    document.getElementById(cfg.ov + '-x').addEventListener('click', close);
    ov.addEventListener('click', function (e) {{ if (e.target === ov) close(); }});
    document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape') close(); }});
  }}

  // 在售项目（house730）
  mountOverlay({{
    ov: 'ov', kpi: 'kpi-market', data: D.projects, sortKey: 'left',
    textCols: ['name', 'first', 'emd', 'developer'],
    summary: D.market.projects + ' 个项目 / ' + D.market.phases + ' 期 · 总货量 '
      + num(D.market.total) + ' · 已售 ' + num(D.market.sold)
      + ' · 余货 ' + num(D.market.remaining) + ' 伙',
    searchText: function (p) {{ return p.name + ' ' + p.developer + ' ' + p.address + ' ' + p.phase_names; }},
    row: function (p) {{
      // 期数标签的 title 放各期原名，便于定期核对合并对不对
      var tag = p.phases > 1
        ? '<span class="tag" title="' + esc(p.phase_names) + '">' + p.phases + ' 期</span>' : '';
      return '<tr>'
        + '<td class="nm" title="' + esc(p.address) + '">' + p.name + tag + '</td>'
        + '<td class="num">' + num(p.total) + '</td>'
        + '<td class="num">' + num(p.sold) + '</td>'
        + '<td class="num"><b>' + num(p.left) + '</b></td>'
        + '<td class="num"><div class="pctcell"><span class="bar"><i style="width:'
            + Math.max(2, Math.min(100, p.pct)) + '%"></i></span>'
            + '<span class="pctnum">' + p.pct + '%</span></div></td>'
        + '<td>' + (p.first || '—') + '</td>'
        + '<td class="num">' + p.months + '</td>'
        + '<td>' + (p.emd || '—') + '</td>'
        + '<td>' + (p.developer || '—') + '</td>'
        + '</tr>';
    }}
  }});

  // 待批预售同意书（地政总署 / CSDI）
  mountOverlay({{
    ov: 'ov2', kpi: 'kpi-pending', data: D.pending_projects, sortKey: 'units',
    textCols: ['name', 'emd', 'lot', 'vendor', 'address'],
    summary: D.pending_market.projects + ' 个项目 / ' + D.pending_market.applications
      + ' 宗申请 · 合计 ' + num(D.pending_market.units) + ' 伙 · 截至 ' + D.pending_market.as_of,
    searchText: function (p) {{ return p.name + ' ' + p.vendor + ' ' + p.lot + ' ' + p.address + ' ' + p.phase_names; }},
    row: function (p) {{
      var tag = p.phases > 1
        ? '<span class="tag" title="' + esc(p.phase_names) + '">' + p.phases + ' 期</span>' : '';
      // 资助出售房屋不属于私人市场货量，标出来
      return '<tr>'
        + '<td class="nm" title="' + esc(p.phase_names) + '">' + p.name + tag + '</td>'
        + '<td class="num"><b>' + num(p.units) + '</b></td>'
        + '<td>' + (p.emd || '—') + '</td>'
        + '<td>' + (p.lot || '—') + '</td>'
        + '<td>' + (p.vendor || '—') + '</td>'
        + '<td class="nm" style="font-weight:400">'
            + ((p.address && p.address !== p.name) ? p.address : '—') + '</td>'
        + '</tr>';
    }}
  }});

  // 标签页在手机上常常一开就是好几天。切回来且距上次加载超过 10 分钟就自己刷新，
  // 免得看到的是几天前的数字。正在看图时不会打断（只在重新可见时触发）。
  var loadedAt = Date.now();
  document.addEventListener('visibilitychange', function(){{
    if (!document.hidden && Date.now() - loadedAt > 10 * 60 * 1000) {{
      location.reload();
    }}
  }});
</script>
</body>
</html>
"""
    return html


def generate(out_dir: Path) -> Path:
    """生成 dashboard.html 到 out_dir（自动复制本地 echarts 保证离线可用）。"""
    import shutil

    out_dir.mkdir(parents=True, exist_ok=True)

    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    if ECHARTS_LOCAL.exists():
        shutil.copy2(ECHARTS_LOCAL, assets_dir / "echarts.min.js")

    html = build_dashboard_html(out_dir)
    target = out_dir / "dashboard.html"
    target.write_text(html, encoding="utf-8")
    print(f"[看板] 已生成: {target}")
    return target


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="生成香港一手市场交互式 HTML 看板")
    ap.add_argument("--out-dir", type=str, default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args()
    generate(Path(args.out_dir))


if __name__ == "__main__":
    main()