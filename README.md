# 香港一手短期库存（自动抓取 + 回推 + 定时邮件）

自动下载政府预售批出、土地注册处一手成交，回推**即时可售货量**，在 GitHub Actions 上**每天定时运行**；
数据相对上次有变化时**发邮件通知**，并把交互式看板发布到 GitHub Pages。

## 📊 网页版看板

### **<https://wangsy2018.github.io/hk-primary-inventory/>**

固定网址，任何设备打开都是最新数据，无需安装或登录。
每天自动更新四次（港时 05:43 / 09:17 / 15:29 / 21:51），代码 push 到 `main` 时也会立即重建。
GitHub 的定时不保证准时、偶尔整天不触发，所以用多个时段互为冗余。

手机上可「加到主画面 / 添加到主屏幕」，当成 App 用。

> 刚部署完的 10 分钟内可能仍看到旧版 —— GitHub Pages 的 CDN 固定发 `cache-control: max-age=600`。
> 强制刷新用 `Cmd+Shift+R`，或在网址后加 `?v=1`。日常查看碰不到。

## 装到手机（PWA）

用 Android Chrome 打开看板网址，地址栏会提示「安装应用」/「加入主畫面」；
iOS Safari 走「分享 → 加入主畫面」。装完是独立图标、点开全屏无地址栏，
和原生 App 观感一致。

实现：`chart_dashboard.py` 在生成看板时一并产出 `manifest.json`、`sw.js` 和图标，
workflow 把它们和 `index.html` 一起发布。

- **`sw.js` 必须和 `index.html` 同级**，否则作用域覆盖不到整个站点
- 缓存策略：页面**网络优先**（每次打开都是最新数据，断网才回退缓存）；
  1 MB 的 `echarts.min.js` 与图标**缓存优先**，这是第二次秒开的关键
- `sw.js` 里的 `VERSION` 是构建时间戳，每次发布都会换掉旧缓存；
  新版本接管后页面自动刷新一次，不会卡在旧缓存上
- 图标源文件在 `assets/pwa/`，由 `tools` 里的一段 Pillow 脚本画出（深蓝底白色柱状图），
  换图标直接替换这三个 PNG 即可

> 想要能安装、能上架的 **.apk**：把网址丢进 [PWABuilder](https://www.pwabuilder.com/)
> 就能生成签名好的 Android 包（底层是 Chrome 的 TWA，仍然渲染这个网页），
> 不需要 Android Studio。上 Google Play 另需开发者账号。

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

- 定时：**一天四次**，港时 05:43 / 09:17 / 15:29 / 21:51（UTC 21:43 / 01:17 / 07:29 / 13:51）
  > **为什么要四次？** GitHub 的 schedule 走共享 runner 池，官方明说高负载时会
  > 延迟**甚至丢弃**。本仓库实测：设 `30 1 * * *` 时延迟过 **10.5 小时**；
  > 08-01 至 08-26 之间**整整 25 天一次都没触发**。单个 cron 撑不住「每天更新」，
  > 所以设四个错开的时段互为冗余，丢一个还有别的顶上。分钟数都用零碎值避开整点。
  >
  > 数据没变化时不发邮件、也不产生提交，多跑几次几乎零成本（公开仓库 Actions 免费不限量）。
  >
  > 要**准点**的话只能靠外部触发：用任意免费 cron 服务定时调用
  > GitHub 的 `workflow_dispatch` API。代价是要管理一个 token。
  >
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
| `map_section.py` | 「项目地图」独立页 `map.html`（Leaflet + 政府地图瓦片，探测不通自动只用 OpenStreetMap），按销售状态 / 土地来源 / 规模筛选 |
| `land_chain.py` | 项目地图数据：抓 CSDI 九个图层（预售、卖地、换地、契约修订、地段扩展、屋宇署批则/动工同意/上盖动工通知/OP），按坐标 + 地段号串成「地→楼→售」链，再接 house730 余货定在售 / 售罄，输出 `land_chain.json`。只看 2010 年起、只看私人住宅（房協 / 房委会 / 资助 / 简约 / 过渡房屋全剔）；买家与申请人对不上等可疑匹配放 `review` 不进地图 |
| `ura_projects.py` | 市建局重建项目与招标：抓 ura.org.hk 的 88 个项目页（坐标 / 地址 / 楼面 / 规划伙数 / 进展，中英文名取自页面 JSON-LD）和招标新闻稿（中标公司 + 母公司 + 中标价 + 标书数 + 全部落标价），输出 `data/ura/{projects,tenders}.csv`。市建局的地不经地政总署卖地库，不补的话已招标未预售的项目整个不在图上 |
| `presale_consent.py` | 地政总署同意方案月报：下 t1/t2/t3 三张 PDF，按表头 x 坐标切列、按行距分块，取「待批的预售申请」等，输出 `data/consent/{pending,issued,rejected}.csv`。CSDI 只有已批出的同意书，这张表才分得出「没申请」和「在排队」 |
| `srpe_sync.py` | 一手住宅物業銷售資訊網（SRPE）增量同步：每次只问「过去 2 天成交册有更新的盘」，重下并解析这些盘的成交记录册，改写 `data/srpe/` 里对应几行；PDF 和逐单不进 git |
| `run_daily.py` | 定时任务入口（对比 + 邮件 + 生成网页看板） |
| `notify_utils.py` | 数据 diff 与 SMTP 发信 |
| `data/baseline/` | 上次确认的数据快照（提交到 Git），用于判断「是否有更新」 |
| `data/srpe/` | SRPE 汇总：`index.csv`（全部一手盘，含坐标与销售状态）、`summary.csv`（每盘已售 / 撻订 / 均价 / 近 30·90 天）、`monthly.csv`（每盘每月成交）、`daily.csv`（每盘每日成交，用来重算近 30·90 天） |
| `data/history/` | 待批预售楼花逐月历史、house730 日期缓存与上次成功的项目表、上次成功的 `land_chain.json`（均提交到 Git） |
| `assets/echarts.min.js` | 内嵌的 ECharts 库（网页版离线可用） |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务 |

## 还原点

地图和土地来源上线前的稳定版本打了 tag `v1-before-map`（同名备份分支 `backup/v1-before-map`）。
要退回去：

```bash
git checkout -b rollback v1-before-map
```

然后把 `rollback` 合并回 `main` 推上去即可；线上页面会在下次 Actions 跑完后恢复。

## 口径：每个数字从哪来

七个数据源，各管一段，**不互相覆盖**。看到两个数对不上，先看是不是下面两个不同口径：

| 数字 | 来源 | 含义 |
|------|------|------|
| **预售批出** | 地政总署预售同意书（LAO_PCRD） | 这个地盘**累计批了多少伙**可以卖楼花。批了不等于推出 |
| **已推出** | house730 单位表 | 发展商**实际推出发售**的伙数（出了价单、单位表上线的期数） |
| 已售 / 余货 | house730 单位表逐个单位的状态 | 余货 = 已推出 − 已售 |
| 即时可售货量 | 锚点回推（批出 − 成交累计） | 存量概念，和上面三个不是一回事 |
| 本月成交注册 | 中原「土地注册处 12 个月统计」 | 注册宗数，滞后临约约 1 个月 |
| 成交逐单 | SRPE 成交记录册 | 官方逐单，临约 / 正约 / 撤销 / 价格 |

> 举例：維港·灣畔（承豐道 18 号）批了 4 期共 **2,060** 伙，house730 只收录到 3 期的单位表
> **1,655** 伙 —— 差的 405 伙是第 2A 期，批了预售还没推出。两个数都对，是两件事。

**一块地分很多期卖的怎么办。** 港铁上盖、NOVO LAND、峻巒这类盘，所有期共用一个地段号
（日出康城 21 份预售同意书全是 `TKOTL 70 RP`），按地段号聚类必然并成一个地盘 —— 地价、批则
确实属于整块地，但「哪一期在卖、卖了多少」属于每一期。**一手销售资讯网（SRPE）按《条例》
逐个发展项目登记**，日出康城在它那里是 17 个、港島南岸 10 个，每个有自己的坐标、售楼书和成交
纪录册，正好是「期」这一层的官方名册。`land_chain.py` 把它挂到地盘下面，再**按期号归并成「项目」这一层**：SRPE 把日出康城第 XIII 期
登记成 XIIIA、XIIIB 两条，实际是一个项目，所以按期号的数字部分归并（`13A`/`13B` → 第 13 期）。
地图上一个项目一个点，弹窗先给项目层（批出 / 已售 / 余），再列子期，最后是整盘。
卖完超过 18 个月的期会从 SRPE 下架（日出康城第二、三、六、八期），用预售同意书补成「已售罄」的项目，
所以日出康城是 12 个项目、加总 20,483 伙与整盘完全吻合（第 1 期 2008 年预售，在 2010 年口径外）。
62 个多项目地盘里，只有愉景湾的「津堤 / 意堤」没有期号、对不上（差 360 伙）。
挂接按证据强弱排四级，弱的绝不覆盖强的：**① 门牌 + 街名完全一致 → ② 项目名一致 →
③ 期数标签一致且 300 米内 → ④ 同一条街、门牌落在地盘地址的门牌区间内且 120 米内**。
第 ④ 条是给屋宇署地盘用的（它们只有一串地址、没有项目名，门牌还常写成 `62-76 Main Street`
这种区间，所以要判断 SRPE 的门牌落不落在区间里，而不是比首尾号码）。最后再裁一次：
一个地盘上若挂着名字不同的盘，只留证据最硬的那一组 —— 光靠距离和「第1期」这种通用期号，
会把隔壁盘吸进来（親海駅 旁边的擎海、安達臣道上的安峯 / 峻然 / 灝然）。名字长得像地址的
（`15 Gough Hill Road`）不参与名字匹配，否则去掉数字后两个不同门牌会撞成同一个 key。

期与预售同意书的对应靠期数标签（`PHASE IVA` ↔ `第IVA期`↔`第5A期`，罗马数字、中文数字、
子期字母都归一化成 `4A` / `5A` 这种）。

**怎么串起来的。** 以「地盘」为单位：预售同意书按地段号 + 坐标聚成地盘 → 接屋宇署的批则 /
动工 / 入伙 → 接地政总署的卖地 / 换地 / 契约修订拿土地来源和地价 → 再把 house730 的余货挂上去。
house730 挂地盘的顺序是「门牌 + 街名」→「项目名」→ 坐标裁决，一个项目只认一个地盘。

**接不上的怎么办。** 现楼销售（楼建好了才卖）不需要预售同意书，地政总署的预售图层里根本没有，
链上接不到 —— 这类盘（天御、樂啟都匯等 104 个、余货 3,220 伙）用 house730 坐标 + SRPE 官方
名单独落点，弹窗注明「现楼销售」。

**统一的排除口径**：只要私人住宅（房協 / 房委会 / 公屋 / 资助出售 / 简约 / 过渡房屋全剔，
市建局和港铁保留）、只要香港（house730 列表里 441 期海外和内地盘滤掉）、土地与屋宇署记录只看
2010 年起。

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
| 本月至今成交注册宗数 | 中原「土地注册处12个月统计」页 | 当月至今 | 日 |
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

**只要香港盘**：house730 的一手列表 775 期里有 **441 期是海外 / 内地**（英国 123、澳洲 93、
加拿大 78、马来西亚 43、泰国 43、新加坡 34、日本 13、阿联酋 6、越南 4、韩国 2，以及中山、
珠海各一），靠 `regionNameWithCulture` 只留 Hong Kong Island / Kowloon / New Territories East /
New Territories West 四个值，再用坐标落在香港范围内兜一道（海外盘坐标多是 0,0）。
这些盘本来就没有销售进度和单位表、进不到结果里，但先滤掉能把单位表请求从 771 次降到 330 次，
跑完从 2 分 42 秒降到 54 秒，也少一半被限流封禁的机会。

**只放已开售项目**：项目里任何一期有 First Sales Date **或单位表里已有售出** 即算已开售。
只看日期会漏掉一批盘 —— house730 对现楼盘常常不填 First Sales Date，但单位表里早就在卖了
（天御、The Horizon、ST. Barths 这类，33 个项目、3,697 伙货量、2,311 伙已售）。
所以单位表要全部期数都拉，不能只拉「已开售」项目的。

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

### 6. 本月至今成交注册 —— 中原「土地注册处12个月统计」

KPI 第四格「本月成交注册」。

```
https://hk.centanet.com/info/land-registry/12months
```

服务端渲染的静态表格，**不用浏览器**，`pandas.read_html` 直接解析第一张表。

> `read_html` 需要 HTML 解析器，`requirements.txt` 里必须有 **`lxml`**。
> 少了它本地可能仍能跑（被别的包顺带装上），但 CI 的干净环境会静默失败、
> 卡片变成两个破折号 —— 这个坑踩过一次。

表格按地区拆开（香港 / 九龍 / 新界東 / 新界西 / **總數**），只有 `總數` 行是全港数字 ——
取「香港」那行会只拿到港岛。定位方式：第 0 列含 `一手住宅` / `二手住宅`、
第 1 列含 `私人住宅`、第 2 列含 `總數`。

当月那一列的表头形如 `8月 (截至26日)`，用「含月份且含截至、但不含年」来定位，
避开右侧 `2026年總計 截至2026-08-26` 那几列。完整日期从表头的 `截至YYYY-MM-DD` 取。

> 这是**尚未定案的临时数字**，月底才会定稿，卡片上已注明。

#### 注册处滞后时用中原顶替

土地注册处的成交数据一般滞后 1~2 个月（例如 8 月初时 7 月、8 月都还没有）。
缺失月份的一手 / 二手成交改用同一页的**逐月**私人住宅注册宗数顶上，
标注为「暂时」（整月）或「暂至N号」（当月至今），看板上画成浅色虚边柱。

口径差异（近 12 个月实测）：

| | 中原（私人住宅） | 土地注册处 | 差异 |
|---|---|---|---|
| 一手 | 766 | 796 | −30，稳定在 0.5~2% |
| 二手 | 3,133 | 3,666 | −533，稳定在 10~17% |

一手基本同口径（加上中原的「資助房屋」23 宗即 789，与 796 只差 7）；
二手是中原按私人住宅、注册处按 ASP 的口径差。**都顶**，因为标注了是临时数字，
注册处公布后会被真实数字自动换掉。

> 顶替结果只写进 `out_inventory/landreg_display_monthly.csv`（看板专用），
> **不进** `landreg_primary_monthly.csv`，也**不参与库存回推**。
> 因为中原当月数字每天在变，混进被比对的文件会导致天天发「数据已更新」邮件。

#### CSDI 批出滞后时用地政署月报补正

CSDI 的 `LAO_PCRD` 图层比地政署自己的月报慢 1~2 个月（实测 9 月 7 日时，
图层还停在 2026-07，而月报的 8 月号早已发布，当月批出 1,879 伙）。

同一批月报里的 **t1** 就是「当月批出」的清单，末页 Summary 直接给合计：

```
https://www.landsd.gov.hk/doc/en/consent/monthly/t1_YYMM.pdf

Total no. of Pre-sale Consent (Residential) issued : 5
Total no. of residential units involved : 1,879
```

**这是同口径精确替换，不是估算**：2024-12 ~ 2026-07 抽查 10 个月，
PDF 的 Summary 与 CSDI 汇总**逐月完全相等**。月报只在月底之后发布、数值是终稿，
所以不会给变更通知制造噪音。

补的范围：CSDI 覆盖之后的月份，外加它最后覆盖的 2 个月（防图层只收了半个月）。
判断「CSDI 收录到哪个月」用的是图层里出现过的最大月份，**不能用「最后一个非零月」**
—— 有些月份真的是 0 批出（2026-01、2026-04）。

> 由此产生一个连带问题：批出能补到最新月，成交却还滞后 1~2 个月。
> 只有批出没有成交的月份，回推出来的库存会虚高。所以成交那边也用中原顶上
> （见上一节），并在库存表加 `provisional` 列标注；
> `notify_utils` 的变更比对**跳过**这些行 —— 否则中原当月数字每天变，会天天发邮件。
> 看板上这些月份的库存点画成**空心圆**，悬停显示「暂时」或「暂至N号」。

### 7. 项目地图：卖地 → 批则 → 动工 → 预售 → 入伙 —— CSDI 九个图层串链

`land_chain.py` 每次跑都全量重算，产出 `out_inventory/land_chain.json`，地图页 `map.html` 直接读。
图层：地政总署预售同意书（LAO_PCRD）、卖地记录（LAO_LSR）、已签立换地（LAOLEC）、契约修订（LAOLMC）、
地段扩展（LAO_LEE）；屋宇署月报 5.3 批则（BDMD53）、5.4 施工同意书（BDMD54）、5.5 上盖动工通知（BDMD55）、
5.6 佔用许可证（BDMD56）。

**各图层之间没有共同编号，靠两把钥匙对：坐标和地段号。**
同一地盘的记录在预售 / 屋宇署两边坐标几乎重合（中位数 3 米），30 米内直接算同一地盘；
卖地记录的点是地块、屋宇署的点是楼，大地盘差一两百米，半径按 `60 + √面积` 放。
地段号统一成官方短码（`N.K.I.L. 6584` / `New Kowloon Inland Lot No. 6584` → `NKIL 6584`；
`D.D. 92 Lot 2640` → `DD92 LOT 2640`），地段号对上的不看距离、明显不同的一票否决。
公司名（买家 vs 屋宇署申请人，相似度 ≥ 0.85）只作兜底。

**屋宇署几个口径，踩过坑的：**
- 5.4 单独用会漏 6%：大盘的塔楼记录有时不登，5.3 批则最全且地址带地段号，是屋宇署这边的锚。
- 5.4 / 5.5 里单位数 "-" 或 0 的是休憩空间上的凉亭、厕所、花架，不是地库，**不算动工证据**。
- 5.5 比 5.4 晚约一个月，两表互有漏登，动工取并集。
- 房委会公屋不经屋宇署审批，本来就不在月报里。

**口径：只看 2010 年起、只看私人住宅。** 房協 / 房委会 / 建筑署申请的，以及公屋、资助出售、简约公屋、
过渡性房屋、宿舍类型，三边都剔；市建局、港铁保留。批地必须早于动工 / 预售、且不超过 15 年，否则是同址旧楼。
坐标接上但买家 ≠ 申请人、时间倒序、预售伙数与屋宇署伙数差一倍以上的卖地记录，先摘到 JSON 的 `review`
不进地图，核实后再放回。

审计（2011–2020 年 181 幅住宅卖地）：批则或动工覆盖 100%、OP 85%；地段号对上 163、坐标 31。

### 8. 一手住宅物業銷售資訊網（SRPE）—— 官方逐单成交

`srpe_sync.py`，港府站点，直连可用，接口不需要 cookie 或 token：
- 索引 `POST DistrictAreaSearch/getDistrictAreaSearchResult`：全部 527 个盘，含坐标、销售状态、售楼书文件
- 详情 `POST Map/getMapDevResultById {devId}`：成交册 / 价单 / 销售安排的文件 ID 与更新时间
- 下载 `GET download/all_development_map/trx/x/{fileId}/{fileName}/en?devId=…`（token 位置填任意字符）
- 增量 `POST DistrictAreaSearch/getUploadSearchResult`：过去 N 天新售楼书 / 成交册 / 价单 / 销售安排有更新的盘，天数任填

**增量规则**：法例要求卖方临约后 24 小时内更新成交册，所以「过去 2 天成交册有更新的盘」（留一天重叠）
就是近两天有成交的全部盘。每次只对这几十个盘核对册子时间戳，变了才重下、重解析、改写它在
`data/srpe/` 里的行。成交册是累计册，一份就是该盘全史，不用留历史 PDF。

**成交册解析**：`pdfplumber.extract_tables()` 逐页取表，行首是日期（`dd-mm-yyyy` 或 `d/m/yyyy`，
因发展商而异）才算一单；成交价格子里可能夹改价备注和车位价，只取第一个金额。
约 40 份小型豪宅册子格式不同暂解析为 0 单，网站上个别 PDF 损坏会报失败、不影响其他盘。

SRPE 的坐标按街道地址地理编码，同一盘和预售同意书差 11–80 米、大屋苑差几百米；
接链时用「门牌 + 街名」优先、坐标次之。SRPE 还覆盖不需要预售同意书的**现楼**一手销售，
这部分预售数据完全看不到。

### 9. 市建局（URA）重建项目与招标 —— 官网项目页 + 新闻稿

`ura_projects.py`，产出 `data/ura/projects.csv`、`data/ura/tenders.csv`。

**为什么非补不可**：市建局自己收楼、清场，土地复归政府后再批回市建局，
地政总署的卖地 / 换地 / 契约修订三个库里一笔都查不到。land_chain 原先只能靠预售同意书的
卖方认出市建局盘，结果是**已招标、在建但还没批预售的项目整个不在图上**，
已经在图上的也看不到当年招标卖了多少钱、几家投。

- 项目索引 `GET /en/project/redevelopment` → 88 个重建项目（6 个转到房協网站，属合作项目，按口径不要）
- 项目页 `GET /en/project/redevelopment/<slug>`，中文名取 `/tc/...`。页面里两段 JSON-LD：
  `Place` 给坐标和门牌地址，`Article` 给项目进展和工程时间表；总楼面 / 商业 / 住宅楼面和规划伙数在
  「Project Development Information」那张表里。**注意「Residential Flats | About 76」是伙数不是楼面**，
  只有值里写了 square metres 才当面积收。小标题直接黏在上一句句号后（`…2027/28.Proposed Redevelopment The…`），
  按「句号紧跟大写字母、中间没空格」切。
- 招标新闻稿：项目页的「Related News」里，标题含 tender / award / unsuccessful 的抓正文。
  市建局公布的口径和地政总署一样齐全——中标公司 + 母公司 + 中标价 + 收到几份标书，
  签约后另发一篇「公布落标金额」的稿，按金额从高到低匿名列出其余标书的出价。
  两篇稿讲同一次招标，按它们关联的项目页集合合并成一条记录。目前 2018-11 至 2025-05 共 12 次。

**接回地盘的办法**：市建局项目地址多数只写街口（「鴻福街／銀漢街」）没有门牌，门牌区间那套对不上，
所以按**街名集合**对，再用规划伙数校验：

1. 门牌区间重叠（4 分）> 街口两条街都对上（3 分）> 同街且 250 米内（2 分）> 坐标 60 米内且卖方已是市建局（1 分）
2. 两边都写得出街名却一条都不重合 → 一票否决，再近也不认
3. 规划伙数和预售批出伙数差 ≤ 15% 加 2 分，差 > 30% 减 1.5 分
4. 所有「项目 × 地盘」候选算完再按分数全局分配，不能边扫边占——
   市建局在土瓜湾有八个相邻项目，先到先得的话弱证据会抢走强证据该配的地盘
5. 相距 > 150 米且伙数差 > 30% 的，进 `review` 不上图

结果：82 个项目接上 51 个地盘（27 个伙数可比，26 个吻合，差的那个是 H18 卑利街/嘉咸街这种一个计划分几块地的），
5 个只有招标记录的单独落点。屋宇署 / 土地记录落的点没有 `address_en`，门牌只存在 `name` 里，算街名时要带上它。

港铁的上盖项目同理（招标不经地政总署卖地库），官网维护结束后再补。

### 10. 政府卖地的投标明细 —— CSDI 卖地图层里本来就有

`LAO_LSR` 除了成交地价（NSEARCH04）和中标者（NSEARCH06），还有：

| 字段 | 内容 |
|---|---|
| NSEARCH05 | 收到几份标书 |
| NSEARCH07 | 其余投标公司名单（不配对金额） |
| NSEARCH08 | **落标价**，降序、不含中标价，条数 = 投标数 − 1 |
| NSEARCH09 | 备注，如「Tender Cancelled on …」 |

落标金额地政总署 2018/19 财年起才公布，之前只公布成交价；2010 年起的住宅卖地里 54 幅有明细。
弹窗里显示「收到 N 份标书 · 落标价 区间 · 中标高出次高 x%」，展开看全部落标价和其他投标者。

### 11. 预售申请进度 —— 地政总署同意方案月报

`presale_consent.py`，产出 `data/consent/{pending,issued,rejected}.csv`。

CSDI 的 `LAO_PCRD` 只有**已批出**的预售同意书，分不出一个盘是「还没申请」还是「申请了在排队」。
月报补这一段，`https://www.landsd.gov.hk/doc/en/consent/monthly/{t1|t2|t3}_YYMM.pdf`：
t1 当月批出、**t2 截至月底待批的申请（快照）**、t3 当月被拒 / 撤回 / 取消。脚本从本月往回找第一个能下到的月份。

PDF 没有表格线，`extract_tables()` 抽不出东西，按表头的字 x 坐标切列：
- 列宽锁在**第一份表头**，逐页重认会因为换行差异切出不同列数，把跨页的记录对错位
- 一条记录内部行距约 10 点、记录之间 20 点以上，**按行距分块**比「地段号那列有字就是新记录」靠谱
  （`Lot 385 RP` / `in DD 352` / `& Exts` 是同一个地段号折了三行）
- 同一份 PDF 里还有转让同意书、非住宅、汇总和注释，只取「Presale Consent for Residential Development」那一节

接回地盘靠地段号（`canon_lots`），对不上再用门牌地址——申请阶段项目名常常还是 `Pending`（未定名）。

### 12. 地图的四个筛选维度

以前只有一个 `status`，把预售、销售、工程挤在一条轴上（「已批预售·未开售」「政府已卖地·未开上盖」…），
既说不清一个盘到底在哪一步，加一档就得在缝里塞一项。现在拆成四个互不干扰的维度，各自多选：

| 维度 | 取值 | 依据 |
|---|---|---|
| 土地来源 | 公开卖地 / 换地补地价 / 契约修订补地价 / 港铁上盖 / 市建局 / 无批地记录 | 地政总署三个库 + 预售卖方 |
| 预售 | 未申请 / 已申请 / 已批准 | 已申请 = 在月报 t2 待批名单里；已批准 = 有预售同意书 |
| 销售 | 未售 / 在售 / 售罄 | 在售 = house730 有余货；未售 = 还没推出市场；售罄 = 卖过或已落成而现在市面上没有 |
| 建设 | 未开工 / 在建 / 已入伙 | 屋宇署 5.4/5.5 施工同意书、5.6 入伙纸 |

视觉也按维度分工：颜色 = 土地来源，填充深浅 = 销售，边框 = 建设，黑边 = 已批预售还没开卖。

**建设状态有推断**：屋宇署月报 2011-06 起才有，更早开工的盘一条记录都没有；现楼盘建成才卖，
本来就不会出现在「动工未预售」里。这两类按已知事实补（现楼 → 已入伙；批了预售必然已动工，
预售超过 4 年 → 已入伙，否则 → 在建），字段 `f_build_from` 标 `推断`，弹窗里也注明。

### 通用：本机代理会挡掉港府站点

港府站点（CSDI、地政署、屋宇署、土地注册处、SRPE、政府地图瓦片）拒海外代理出口，直连正常。
各抓取函数在走代理失败时**自动绕过代理直连重试一次**；本机代理若支持按域名直连，给 `.gov.hk` 加一条直连规则，
浏览器里的政府地图底图也就通了。CI 上没有代理，不受影响。

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
