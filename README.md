# 香港一手短期库存（自动抓取 + 回推 + 定时邮件）

自动下载政府预售批出、土地注册处一手成交，回推**即时可售货量**，并可在 GitHub Actions 上**每天定时运行**；数据相对上次有变化时**发邮件通知**。

## 本地运行（PyCharm）

```bash
pip install -r requirements.txt
python 一手短期库存.py
```

结果在 `out_inventory/`（含 Excel 与图表）。

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

## 文件说明

| 文件 | 作用 |
|------|------|
| `一手短期库存.py` | 主程序（本地完整运行 + 图表） |
| `run_daily.py` | 定时任务入口（对比 + 邮件） |
| `notify_utils.py` | 数据 diff 与 SMTP 发信 |
| `data/baseline/` | 上次确认的数据快照（提交到 Git） |
| `.github/workflows/daily.yml` | GitHub Actions 定时任务 |

## 注意事项

- GitHub Actions 需 **Chrome** 抓土地注册处，workflow 已配置。
- 首次 push 后第一次 Action 只会**建立 baseline**，一般**不发邮件**；从第二次起才会在数据变化时通知。
- 不要把 SMTP 密码写进代码，只用 GitHub Secrets。
