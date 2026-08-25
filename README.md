# 香港一手短期库存（自动抓取 + 回推 + 定时邮件）

自动下载政府预售批出、土地注册处一手成交，回推**即时可售货量**，在 GitHub Actions 上**每天定时运行**；
数据相对上次有变化时**发邮件通知**，并把交互式看板发布到 GitHub Pages。

## 📊 网页版看板

### **<https://wangsy2018.github.io/hk-primary-inventory/>**

固定网址，任何设备打开都是最新数据，无需安装或登录。
每天香港时间 **09:30** 自动更新（UTC 01:30），代码 push 到 `main` 时也会立即重建。

手机上可「加到主画面 / 添加到主屏幕」，当成 App 用。

> 刚部署完的 10 分钟内可能仍看到旧版 —— GitHub Pages 的 CDN 固定发 `cache-control: max-age=600`。
> 强制刷新用 `Cmd+Shift+R`，或在网址后加 `?v=1`。日常查看碰不到。

## 本地运行（PyCharm）

```bash
pip install -r requirements.txt
python 一手短期库存.py
```

结果在 `out_inventory/`（含 Excel 与图表），整轮约 3 秒。

> **本地开了代理（Clash / VPN）？** 港府两个数据源（`portal.csdi.gov.hk`、`www.landreg.gov.hk`）经代理常连不通。
> 脚本会在代理失败时自动绕过代理直连重试，无需手动关代理。

## 部署到 GitHub + 每日自动运行

### 1. 创建仓库并推送

在本目录（`PyCharmMiscProject`）：

```bash
git init
git add .
git commit -m "Initial commit: HK primary short-term inventory monitor"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

> 首次推送已包含 `data/baseline/` 快照，用于之后判断「是否有更新」。

### 2. 配置 GitHub Secrets

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | 说明 | 示例 |
|--------|------|------|
| `SMTP_HOST` | 发信 SMTP 服务器 | `smtp.gmail.com` |
| `SMTP_PORT` | 端口 | `587`（TLS）或 `465`（SSL） |
| `SMTP_USER` | 登录账号 | 你的邮箱 |
| `SMTP_PASSWORD` | 密码 / 应用专用密码 | Gmail 需[应用密码](https://support.google.com/accounts/answer/185833) |
| `SMTP_FROM` | 发件人地址 | 同 `SMTP_USER` 或别名 |
| `NOTIFY_EMAIL_TO` | 收件人，多个用英文逗号 | `you@example.com, colleague@example.com` |

### 3. 定时规则

工作流文件：`.github/workflows/daily.yml`

- 定时：**每天 UTC 01:30**（约香港 **09:30**）
- **push 到 `main` 也会触发**，代码一改网页立即重建（机器人用 `GITHUB_TOKEN` 的提交不会再触发，不会自我循环）
- 也可在 GitHub **Actions** 页手动 **Run workflow**

### 4. 运行逻辑（发邮件规则）

| 触发方式 | 数据有变化 | 是否发邮件 |
|----------|------------|------------|
| **每天定时**（cron） | 是 | 发 |
| **每天定时** | 否 | **不发** |
| **手动 / push 触发** | 是 | 发，并更新 baseline |
| **手动 / push 触发** | 否 | **仍发**（正文说明无变化） |

邮件正文只有简短摘要 + 看板链接，**不带任何附件**（CSV / Excel / PDF / PNG 都不附）。
未配置 SMTP 时会跳过发信并继续，不影响数据与网页更新。

### 5. 本地测试「对比 + 邮件」

先改 `out_inventory/` 里某个数字制造差异，再（macOS / Linux）：

```bash
SMTP_HOST=smtp.gmail.com SMTP_PORT=465 SMTP_USER=你的邮箱 \
SMTP_PASSWORD=你的应用密码 SMTP_FROM=你的邮箱 \
NOTIFY_EMAIL_TO=收件人@example.com python run_daily.py
```

代码里用的是 `SMTP_SSL`，Gmail 端口填 **465**（不是 587），密码用
[应用专用密码](https://support.google.com/accounts/answer/185833)。

## 看板发布机制（已启用，无需再配置）

看板共 5 张图：年度对照 / 即时可售货量 + 待批预售楼花 / 批出 vs 成交（月度）/ 批出 vs 成交（季度）/ 二手成交 vs CCL。

`.github/workflows/daily.yml` 每次运行都会：
1. 跑数据 pipeline，生成 `out_inventory/dashboard.html`
2. 包装为 `pages_build/index.html`，连同 `assets/echarts.min.js`（约 1 MB，已提交进仓库，网页完全自包含、不依赖 CDN）
3. 用 `actions/upload-pages-artifact` + `actions/deploy-pages` 部署

若 `dashboard.html` 未生成则**中止发布**，保留线上已有页面，不会被空目录覆盖。

<details>
<summary>换仓库时的一次性配置</summary>

1. **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**
2. **Settings → Actions → General → Workflow permissions** 选 **Read and write**（自动提交 baseline 与待批历史需要）
3. Actions 页手动跑一次，deploy job 日志里会给出 `page_url`

</details>

### 本地预览网页版

```bash
python 一手短期库存.py
python chart_dashboard.py
# 双击 out_inventory/dashboard.html 即可预览（echarts 走本地 assets/）
```

## 文件说明

| 文件 | 作用 |
|------|------|
| `一手短期库存.py` | 主程序（本地完整运行 + 图表）；抓数优先 JSON 通道，失败回退 Selenium |
| `chart_report.py` | 研报图表（季度/逐月横轴，与本地 `Inventory with chart.py` 一致；CI 用 Noto 字体路径加载） |
| `chart_dashboard.py` | 生成交互式 HTML 看板（ECharts），供本地 / GitHub Pages 查看 |
| `run_daily.py` | 定时任务入口（对比 + 邮件 + 生成网页看板） |
| `notify_utils.py` | 数据 diff 与 SMTP 发信 |
| `data/baseline/` | 上次确认的数据快照（提交到 Git），用于判断「是否有更新」 |
| `data/history/` | 待批预售楼花的逐月历史（提交到 Git），避免每天重下 100+ 份 PDF |
| `assets/echarts.min.js` | 内嵌的 ECharts 库（网页版离线可用） |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务 |

## 图表与本地一致

- 图表逻辑集中在 `chart_report.py`，与本地 `Inventory with chart.py` 的数据准备、季度/逐月横轴一致；仅开头连续为 0 的库存月不画线（与本地相同）。
- Linux CI 通过 **字体文件路径** 注册 Noto CJK（`daily.yml` 会 `fc-cache` 并重建 matplotlib 字体缓存）。
- 可选：将 `NotoSansSC-Regular.otf` 放入 `assets/fonts/`，本地与 GitHub 使用同一字体文件。

## 数据源与抓取方式

四个序列各有各的坑，这里把「取哪个 URL、怎么解析、为什么这么做」都写清楚，
对应实现全在 [`一手短期库存.py`](一手短期库存.py)。

| 序列 | 来源 | 历史起点 | 频率 |
|------|------|----------|------|
| 预售批出伙数 | CSDI ArcGIS `LAO_PCRD` 图层 | 全历史 | 月 |
| 一手 / 二手成交伙数 | 土地注册处 `t1.json` | 2002-01 | 月 |
| 待批预售楼花单位数 | 地政总署月报 PDF `t2_YYMM.pdf` | 2013-01 | 月末时点 |
| 中原城市领先指数 CCL | 中原 `CCLChart` 接口 | 1993-12 | 周（取月末） |

### 1. 预售批出伙数 —— CSDI ArcGIS

```
https://portal.csdi.gov.hk/server/rest/services/common/landsd_rcd_1637303511514_65978/FeatureServer/0
```

标准 ArcGIS REST，`/query?where=1=1&outFields=*&f=json`。先读 `?f=pjson` 拿到
`maxRecordCount` 和 `objectIdField`，再按 `resultOffset` / `resultRecordCount` 分页，
`orderByFields=OBJECTID ASC` 保证翻页稳定。

用到三个字段：`SEARCH01_EN`（同意书年份）、`SEARCH02_EN`（同意书月份）、
`NSEARCH13_EN`（住宅单位数目），按年月分组求和。

> 字段名不直观，`--dump-arcgis-fields` 可导出全部字段名与中英别名。

### 2. 一手 / 二手成交 —— 土地注册处 JSON 接口

**不用 Selenium。** 页面上那些年份段按钮和表格都是 JS 渲染的，但数据其实来自静态 JSON：

1. 主页 `https://www.landreg.gov.hk/tc/monthly/agreement.htm` 里内联了一段
   `var pastStatJson=[...]`，正则取出即可，**不必渲染页面**。里面是三类统计，
   取「住宅樓宇買賣合約統計數字:一手及二手買賣」那一类，得到年份段的 slug：
   `agt-primary`（当年）、`agt-pri-1` … `agt-pri-5`（历史五年段）。
2. 每个 slug 背后是 `https://www.landreg.gov.hk/json/monthly_agt-pri/<slug>/t1.json`，
   字段自带 `Year` / `Month`，以及
   `Number of Primary Sales for ASP Residential Building Units`（一手）和
   `Number of Secondary Sales ...`（二手）。

解析注意：
- `Month` 为 `"Total"` 的是**年合计行**，要跳过（只收 1–12）。
- 年份段之间有重叠（`agt-primary` 含 2022–2026，`agt-pri-5` 含 2021–2025），
  **从最老的段开始写、新段覆盖旧段**，重叠月份以最新一版为准。

> **为什么不解析渲染后的表格。** 早期版本用 Selenium + 写死的 `nth-child` 选择器找年份段链接，
> 结果只抓到部分年份段，`2016-2019`、`2021-2024` 整整八年在 CSV 里是 0，而且**静默补 0**、
> 从结果上看不出来。现在改成 JSON，年份直接来自字段，不必再从 DOM id `t1Y{n}r{m}_td{k}` 反推。
>
> 保留了 Selenium 回退（`build_landreg_primary_monthly_via_selenium`），
> 但只在 JSON 通道失败时才启用；`--no-landreg-selenium` 可彻底禁用。
> 另有 `_assert_landreg_coverage()`：区间内缺月就直接报错触发回退，
> **绝不静默补 0** —— 补 0 会直接污染回推出来的库存曲线。

### 3. 待批预售楼花 —— 地政总署月报 PDF

索引页列出每个月的归档（回溯到 2013-01）：

```
https://www.landsd.gov.hk/en/resources/land-info-stat/dev-control-compliance/consent/presale.html
```

每月三份 PDF，要的是 **t2**（待批）：

```
https://www.landsd.gov.hk/doc/en/consent/monthly/t2_YYMM.pdf     # 例: t2_2607.pdf = 2026-07
```

**只读末页的 Summary，不解析表格。** 明细表跨十几页、单元格还会换行，解析极易出错；
而末页有现成的合计：

```
Total no. of Pre-sale Consent (Residential) applications pending approval : 32
Total no. of residential units involved : 13,734
```

用**英文版**：中文版措辞变过（2016/2017 是「预售楼花同意书(住宅)待批数目」，
现在是「待批预售楼花同意书(住宅)申请数目」），英文版十年只把
`units pending approval` 换成过 `units involved`，一个正则兼容两种。

抓过的月份落盘 `data/history/pending_presale_monthly.csv` 并提交进仓库，
日常运行**只补缺失月份 + 重抓最近 2 个月**（防事后修订），不会每天下一百多份 PDF。
重建全部历史：

```bash
python 一手短期库存.py --pending-backfill
```

> **为什么不用 CSDI 的 `LAO_PCRDP` 下载包**（`static.csdi.gov.hk/csdi-webpage/download/.../csv`）：
> 那份**只有当前快照，没有任何历史**。ArcGIS 图层自报 `supportsQueryWithHistoricMoment: false`、
> `startArchivingMoment: -1`，URL 挂日期参数一律被忽略，data.gov.hk 的历史存档也未收录（0 个版本），
> 而且图层里没有「申请日期」字段，无法反推过去时点的存量。
> 两者口径一致（2026-07 都是 32 个申请 / 13,734 伙），但只有 PDF 有历史。

### 4. 中原城市领先指数 CCL

```
https://hk.centanet.com/CCI/api/Index/CCLChart
```

> **注意那个 `/CCI` 前缀。** 页面 JS 里写的是 `$axios.get("/api/Index/CCLChart")`，
> 但直接请求 `https://hk.centanet.com/api/Index/CCLChart` 是 **404**，要带 `/CCI/` 才对。

GET 即可，返回 `rawData`，其中 `ccl` 是周度指数值，`realContractEndDate` 是对应的
合约期结束日（与网站图表 x 轴一致），两个等长数组。1993-12 至今一千七百多个点。

按 `realContractEndDate` 归月，**取每月最后一个观测**作为该月的月末时点数。

### 通用：本机代理会挡掉港府站点

CSDI 和土地注册处经本机全局代理（Clash / VPN 之类）访问常常连不通
（Chrome 报 `ERR_TUNNEL_CONNECTION_FAILED`，curl 报 56），直连则正常。
`_http_get()` 会在请求失败时**自动绕过代理直连重试一次**，本地不用手动关代理。

### 变更通知的取舍

变更比对只看 `data/baseline/` 里的三个核心文件（批出 / 一手二手成交 / 回推库存）。
**CCL 和待批不纳入比对** —— CCL 每周都动，计入的话「数据已更新」的邮件几乎天天发。
两者只进看板。

## 注意事项

- 正常路径**不需要浏览器**，四个数据源都是 HTTP + JSON/PDF。workflow 里仍装 Chrome，
  只为土地注册处 JSON 接口失效时的 Selenium 回退保底 —— 细节见 [数据源与抓取方式](#数据源与抓取方式)。
- GitHub 会在仓库**连续 60 天无活动**后自动停用定时工作流（会发邮件提醒），到 Actions 页点一下即可恢复。
- 首次 push 后第一次 Action 只会**建立 baseline**，一般**不发邮件**；从第二次起才会在数据变化时通知。
- 不要把 SMTP 密码写进代码，只用 GitHub Secrets。
