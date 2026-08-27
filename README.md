# 香港一手短期库存（自动抓取 + 回推 + 定时邮件）

自动下载政府预售批出、土地注册处一手成交，回推**即时可售货量**，在 GitHub Actions 上**每天定时运行**；
数据相对上次有变化时**发邮件通知**，并把交互式看板发布到 GitHub Pages。

## 📊 网页版看板

### **<https://wangsy2018.github.io/hk-primary-inventory/>**

固定网址，任何设备打开都是最新数据，无需安装或登录。
每天香港时间约 **05:43** 自动更新（UTC 21:43），代码 push 到 `main` 时也会立即重建。
GitHub 的定时不保证准时，偶尔会延后数小时。

手机上可「加到主画面 / 添加到主屏幕」，当成 App 用。

> 刚部署完的 10 分钟内可能仍看到旧版 —— GitHub Pages 的 CDN 固定发 `cache-control: max-age=600`。
> 强制刷新用 `Cmd+Shift+R`，或在网址后加 `?v=1`。日常查看碰不到。

## 本地运行（PyCharm）

```bash
pip install -r requirements.txt
python 一手短期库存.py
```

输出在 `out_inventory/`：四份 CSV + 汇总 Excel。再跑 `python chart_dashboard.py`
生成 `dashboard.html`（双击即可本地预览，ECharts 走本地 `assets/`）。整轮约 7 秒。

> **本地开了代理（Clash / VPN）？** 港府数据源（`portal.csdi.gov.hk`、`www.landreg.gov.hk`、`www.landsd.gov.hk`）经代理常连不通。
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

- 定时：**每天 UTC 21:43**（约香港次日 **05:43**）
  > GitHub 的 schedule 走共享 runner 池、**不保证准时**，整点前后最拥挤。
  > 实测设 `30 1 * * *` 时曾延迟 10 小时才跑。所以挑了零碎分钟数 + 冷门时段。
  > 网页顶部的「更新时间」已换算成香港时间并标注 HKT（runner 系统时区是 UTC）。
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
| `一手短期库存.py` | 主程序：抓四个数据源 + 锚点回推，输出 CSV/Excel |
| `house730_inventory.py` | 抓 house730 逐盘在售货量，输出 `projects_inventory.csv` |
| `chart_dashboard.py` | 生成交互式 HTML 看板（ECharts），本地 / GitHub Pages 共用；**唯一的图表产物** |
| `run_daily.py` | 定时任务入口（对比 + 邮件 + 生成网页看板） |
| `notify_utils.py` | 数据 diff 与 SMTP 发信 |
| `data/baseline/` | 上次确认的数据快照（提交到 Git），用于判断「是否有更新」 |
| `data/history/` | 待批预售楼花逐月历史、house730 日期缓存与上次成功的项目表（均提交到 Git） |
| `assets/echarts.min.js` | 内嵌的 ECharts 库（网页版离线可用） |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务 |

## 数据源与抓取方式

四个序列各有各的坑，这里把「取哪个 URL、怎么解析、为什么这么做」都写清楚，
对应实现全在 [`一手短期库存.py`](一手短期库存.py)。

| 序列 | 来源 | 历史起点 | 频率 |
|------|------|----------|------|
| 预售批出伙数 | CSDI ArcGIS `LAO_PCRD` 图层 | 全历史 | 月 |
| 一手 / 二手成交伙数 | 土地注册处 `t1.json` | 2002-01 | 月 |
| 一手 / 二手成交金额 | 土地注册处 `t2.json`（$ million） | 2002-01 | 月 |
| 待批预售楼花单位数 | 地政总署月报 PDF `t2_YYMM.pdf` | 2013-01 | 月末时点 |
| 中原城市领先指数 CCL | 中原 `CCLChart` 接口 | 1993-12 | 周（取月末） |
| 逐盘在售货量 | house730 `api.house730.com` | 当前快照 | 日 |

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
2. 每个 slug 背后有两份 JSON，字段都自带 `Year` / `Month`：
   - `.../monthly_agt-pri/<slug>/**t1**.json` —— **成交单位数**
     （`Number of Primary Sales ...` / `Number of Secondary Sales ...`）
   - `.../monthly_agt-pri/<slug>/**t2**.json` —— **成交金额**
     （`Consideration of Primary Sales ...` / `Consideration of Secondary Sales ...`），
     单位是**港币百万**（页面表头写明 `Consideration ($ million)`），除以 100 即為億

解析注意：
- `Month` 为 `"Total"` 的是**年合计行**，要跳过（只收 1–12）。
- 年份段之间有重叠（`agt-primary` 含 2022–2026，`agt-pri-5` 含 2021–2025），
  **从最老的段开始写、新段覆盖旧段**，重叠月份以最新一版为准。
- 成交金额只写进 `out_inventory/landreg_full_monthly.csv`（2002-01 起的全量历史，供看板用），
  **不进** `landreg_primary_monthly.csv` —— 后者参与变更比对与库存回推，多加列只会制造噪音。

> **为什么不解析渲染后的表格。** 早期版本用 Selenium + 写死的 `nth-child` 选择器找年份段链接，
> 结果只抓到部分年份段，`2016-2019`、`2021-2024` 整整八年在 CSV 里是 0，而且**静默补 0**、
> 从结果上看不出来。现在改成 JSON，年份直接来自字段，不必再从 DOM id `t1Y{n}r{m}_td{k}` 反推。
>
> 保留了 Selenium 回退（`build_landreg_primary_monthly_via_selenium`），
> 但只在 JSON 通道失败时才启用；`--no-landreg-selenium` 可彻底禁用。
> 另有 `_assert_landreg_coverage()`：区间内缺月就直接报错触发回退，
> **绝不静默补 0** —— 补 0 会直接污染回推出来的库存曲线。

### 3. 待批预售楼花 —— 月度合计走月报 PDF，逐项目明细走 CSDI

索引页列出每个月的归档（回溯到 2013-01）：

```
https://www.landsd.gov.hk/en/resources/land-info-stat/dev-control-compliance/consent/presale.html
```

每月三份 PDF，要的是 **t2**（待批）：

```
https://www.landsd.gov.hk/doc/en/consent/monthly/t2_YYMM.pdf     # 例: t2_2607.pdf = 2026-07
```

**月度合计只读末页的 Summary，不解析明细表。** 明细表跨十几页、单元格还会换行，解析极易出错；
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

#### 逐项目明细 —— 用 CSDI，别去解析 PDF

看板 KPI 第三格「最新待批预售」和点开的列表要的是**逐条申请**。月报明细表跨十几页、
单元格换行、每页重复表头，解析很脆；而 CSDI 的 `LAO_PCRDP` 数据集就是**同一张表的
结构化版本**：

```
https://static.csdi.gov.hk/csdi-webpage/download/c84ee393122e5442985d6ce1cdddd162/csv
```

32 条记录、13,734 伙，与月报末页 Summary 逐条 `(地段编号, 单位数)` 完全一致。
字段映射见 `_PENDING_FIELD_MAP`；比 PDF 还多一个 `NSEARCH12_EN`
（`Subsidised Sale Flats`），用它把**资助出售房屋直接剔掉**（当前 3 宗申请、
全部房協、3,768 伙），卖方名单 `PENDING_EXCLUDED_VENDORS` 再兜一道防漏标。
注意**市建局（URA）不带这个标记**，是公开市场发售，不剔。
剔除后 29 宗申请 / 18 个项目 / 9,966 伙 —— 月报 Summary 的 13,734 伙是含资助的口径。

> 这份只有**当前快照**没有历史 —— 所以月度那条线仍然只能靠 PDF。两者分工：
> **PDF 给历史合计，CSDI 给当前明细。**

**期数合并用地段编号，不是名称也不是地址。** 32 条里 **25 条项目名是 `Pending`**
（尚未定名）、6 条连地址都是 `Pending`，`NKIL 6458` 的三期全叫 "Pending (Phase N)" ——
只有地政署自己的地段编号能可靠对上。归一时去掉 `RP`（余段）、`& Exts`、`Section A`
等后缀但保留编号本身（所以 `KIL 11275` 与 `KIL 11276` 不会误并），
再以卖方作第二重判据（实测 7 个多期组卖方全部一致，加上只会更稳）。
**32 宗申请 → 剔除资助后 29 宗 → 18 个项目。**

项目名三级降级：**真实项目名 → 地址 → 地段编号**，因为大多数还没定名。

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

按 `realContractEndDate` 归月，**取每月最后一个观测**作为该月的月末时点数，
输出 `ccl_monthly.csv` 时**不截断**（1994-01 起全量），看板底部两张图用自己的长横轴。

### 5. 逐盘在售货量 —— house730

看板 KPI 第二格「当前市场在售货量」和点开的项目列表，数据来自 house730。

**不要爬 `www.house730.com`**：那个站在 Cloudflare 后面，首次请求 403、跑完 JS 挑战才 200，
所以只能上 Selenium 加长延迟。但它是个 Nuxt 应用，数据来自 `api.house730.com`，
且 URL 上那个 `appsignature` **服务端根本不校验**（填错、不填都照样返回）。三个接口：

| 接口 | 用途 |
|------|------|
| `POST /NewEstate/SearchNewEstate`（body `{pageIndex, pageCount}`） | 全部期数，一页 100 条；已带英文地址与 main developer |
| `GET /NewEstate/GetNewEstateSaleProcess?estateId=` | 首次出售日期、Estimated Material Date |
| `GET /NewEstate/GetNewEstateRoomById?estateId=` | 逐单位状态，`status=="2"` 即已售 |

公共参数 `language=en-us&platform=pc&cityen=hk&appkey=730responsive`；
`language=zh-hk` 可拿到中文名与中文地址（合并时两边互补）。

> **API 会限流，而且惩罚很重。** 它也在 Cloudflare 后面，只是只做速率限制：
> 突发几十次就返回 `Just a moment...` 挑战页（HTTP 429）。开发期间被封过两次，
> 第二次**只发了 16 次请求就触发**，且持续 10 分钟以上。
> 所以代码里有全局熔断闸（任何线程吃到 429 就把所有线程一起按住 —— 各自退避没用，
> 退避期间别的线程还在把令牌桶打空）、0.12 秒基础间隔，失败重试到底仍失败**直接抛错**。

**期数合并成项目**：`build_projects()` 用并查集，判据是「main developer 词集合重合度 ≥ 0.6
且中英文地址任一能对上」。地址有三种脏法，都踩过：

- 英文地址栏里存的是中文（Victoria Voyage Phase 1B 是「承豐道18號」，同项目其余三期是 "18 Shing Fung Road"）
- 带区名后缀（`19 shing fung road` vs `19 shing fung road kai tak`）
- 门牌写法不一（`No. 1 Wetland Park` vs `1 Wetland Park Road`），以及录入错字（`O1 Lohas Park Road`）

发展商必须按词比而不是整串比：Villa Garda 两期一个写 `MTR，SINO LAND，K.WAH & CHINA MERCHANTS`、
另一个写 `MTR，SINO，K.WAH & CHINA MERCHANTS`，差一个 LAND。阈值 0.6 是为了扛住康城路1號 ——
那一个地址底下有 5 家不同发展商的盘，放宽到「同地址即合并」会全糊在一起。

**只放已开售项目**：项目里任何一期有 First Sales Date 即算已开售；市场余货加总也只算这些项目。

**只统计私人住宅市场**：资助出售房屋不算市场货量，按 main developer 剔除，
名单在 `EXCLUDED_DEVELOPERS`（目前只有房協 `HONG KONG HOUSING SOCIETY`，
剔除 4 期 / 1 个已开售项目 SIERRA TERRACE，余货 18,640 → 18,099）。要加就往那个元组里加。

**日期落盘缓存**：已开售期数的首次出售日期不会再变，抓过就存进
`data/history/house730_sale_process.csv`；每天只补新增期数和**还没开售的期数**
（它们随时可能开售）。当前 769 期里 243 期已有日期、526 期待开售，
所以次日请求量约 **16（列表）+ 526（待开售复查）+ 256（单位状态）≈ 800 次**，
比全量的 1040 次省下已开售那一截，且这一截会随着开售项目增多而继续变大。
按 0.12 秒基础间隔算整轮约 100 秒。`--refresh-dates` 可强制全量重抓。

`run_daily.py` 里这一步失败会回退到 `data/history/house730_projects_inventory.csv`
（上次成功的结果），不让整块看板消失。

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
