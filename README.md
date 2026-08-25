# 香港一手短期库存（自动抓取 + 回推 + 定时邮件）

自动下载政府预售批出、土地注册处一手成交，回推**即时可售货量**，并可在 GitHub Actions 上**每天定时运行**；数据相对上次有变化时**发邮件通知**。

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

- 默认：**每天 UTC 01:30**（约香港 **09:30**）
- 可在 GitHub **Actions** 页手动 **Run workflow** 测试

### 4. 运行逻辑（发邮件规则）

| 触发方式 | 数据有变化 | 是否发邮件 | 邮件内容 |
|----------|------------|------------|----------|
| **每天定时**（cron） | 是 | 发 | 附件：`report_chart.pdf` + `report_chart.png` |
| **每天定时** | 否 | **不发** | — |
| **手动 Run workflow** | 是 | 发 | 同上 + 更新 baseline |
| **手动 Run workflow** | 否 | **仍发** | 仅附最新 PDF/PNG（正文说明无变化） |

说明：邮件**不再**在正文里罗列 CSV 文件名，也**不附** CSV/Excel；只附生成的研报 **PDF 和 PNG**。

### 5. 本地测试「对比 + 邮件」（不跑 Selenium）

先改 `out_inventory` 里某个数字，再：

```bash
set SMTP_HOST=smtp.gmail.com
set SMTP_PORT=587
set SMTP_USER=你的邮箱
set SMTP_PASSWORD=你的应用密码
set SMTP_FROM=你的邮箱
set NOTIFY_EMAIL_TO=收件人@example.com
python run_daily.py
```

## 网页版看板（GitHub Pages）

除定时邮件外，可把交互式 **HTML 看板**（ECharts：年度对照 / 可售货量走势 / 月度上下轴 / 季度上下轴）发布到 GitHub Pages，随时在浏览器查看，并每日自动更新。

### 网页版会自动发布

`.github/workflows/daily.yml` 每次运行（每日定时或手动 Run workflow 都会）：
1. 跑数据 pipeline 并生成 `out_inventory/dashboard.html`
2. 包装为 `pages_build/index.html`，连同 `assets/echarts.min.js`
3. 用 `actions/upload-pages-artifact` + `actions/deploy-pages` 部署到 GitHub Pages

因此**只需一次性开启 GitHub Pages**，之后每次运行都会自动刷新网页版。

### 一次性开启步骤

1. 确认 `assets/echarts.min.js`（约 1 MB）已提交进仓库 —— 网页版完全自包含，无需联网加载 CDN。若尚未提交则一起 `git add assets/echarts.min.js`。
2. 仓库 → **Settings** → **Pages**
3. **Build and deployment** → **Source** 选择 **GitHub Actions**
4. 在 **Actions** 页手动运行一次 **Daily inventory check & deploy dashboard**
5. 部署成功后，deploy job 日志里有 `page_url`，通常为：
   `https://<你的用户名>.github.io/<仓库名>/`

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
| `data/baseline/` | 上次确认的数据快照（提交到 Git） |
| `assets/echarts.min.js` | 内嵌的 ECharts 库（网页版离线可用） |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务 |

## 图表与本地一致

- 图表逻辑集中在 `chart_report.py`，与本地 `Inventory with chart.py` 的数据准备、季度/逐月横轴一致；仅开头连续为 0 的库存月不画线（与本地相同）。
- Linux CI 通过 **字体文件路径** 注册 Noto CJK（`daily.yml` 会 `fc-cache` 并重建 matplotlib 字体缓存）。
- 可选：将 `NotoSansSC-Regular.otf` 放入 `assets/fonts/`，本地与 GitHub 使用同一字体文件。

## 注意事项

- 土地注册处数据走其页面背后的 **JSON 接口**（`/json/monthly_agt-pri/<年份段>/t1.json`，字段自带 `Year` / `Month`），不需要浏览器。
  workflow 里仍装 Chrome，只作为 JSON 接口失效时 **Selenium 回退**的保底；加 `--no-landreg-selenium` 可禁用回退。
- 首次 push 后第一次 Action 只会**建立 baseline**，一般**不发邮件**；从第二次起才会在数据变化时通知。
- 不要把 SMTP 密码写进代码，只用 GitHub Secrets。
