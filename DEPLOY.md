# News Ingestion API 部署指南

推荐顺序：

1. Render：最适合第一次部署，`render.yaml` 已包含 Docker 和持久化磁盘配置。
2. Railway：部署也很快，需要在平台里给服务添加 Volume 并挂载到 `/app/data`。
3. VPS/Fly.io：更灵活，但对新手多一点命令行成本。

## 为什么不推荐纯 serverless

本服务需要：

- HTTP API 常驻在线。
- 后台每隔一段时间自动刷新真实新闻源。
- 抓取结果持久保存到 `data/daily/YYYYMMDD/`。

普通 Vercel/Netlify/GitHub Pages 这类部署更适合短函数或静态页面，不适合第一版这种常驻抓取服务。除非后续把存储迁移到数据库，并用独立 Cron 调度。

## Render 快速部署

1. 把本项目根目录作为一个单独仓库推到 GitHub。
2. 打开 Render，选择 `New` -> `Blueprint`。
3. 连接这个 GitHub 仓库。
4. Render 会读取 `render.yaml`。
5. 确认服务、环境变量和 disk 配置。
6. 部署完成后访问：

```text
https://你的服务名.onrender.com/health
```

`render.yaml` 已设置：

```text
NEWS_API_HOST=0.0.0.0
NEWS_REFRESH_INTERVAL_SECONDS=10
NEWS_API_TOKEN=自动生成
disk mountPath=/app/data
```

Render 的 persistent disk 必须保留，否则服务重启后历史 `data/daily/` 可能丢失。

## Railway 快速部署

1. 把本项目根目录作为一个单独仓库推到 GitHub。
2. 打开 Railway，选择从 GitHub 部署。
3. Railway 会读取 `railway.json` 并用 Dockerfile 构建。
4. 在 Variables 里设置：

```text
NEWS_API_HOST=0.0.0.0
NEWS_REFRESH_INTERVAL_SECONDS=10
NEWS_API_CORS_ORIGIN=*
NEWS_API_TOKEN=自己生成的长随机字符串
```

5. 在 Railway 项目里添加 Volume，挂载路径设置为：

```text
/app/data
```

6. 部署完成后访问：

```text
https://你的域名/health
```

## 下载地址

部署完成后，把 `{BASE_URL}` 换成你的公网地址：

```text
{BASE_URL}/api/v1/export/daily.zip?date=20260603
{BASE_URL}/api/v1/export/hot.xlsx?date=20260603&limit=50
{BASE_URL}/api/v1/export/articles.xlsx?date=20260603&limit=500
{BASE_URL}/api/v1/export/articles.jsonl?date=20260603
```

## 手动刷新

公网部署后，手动刷新要带 token：

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "https://你的域名/api/v1/refresh?date=20260603" `
  -Headers @{ Authorization = "Bearer 你的NEWS_API_TOKEN" }
```

下载接口不需要 token；刷新接口需要 token。

## 本地模拟公网服务

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 127.0.0.1 -Port 19080 -RefreshIntervalSeconds 0 -NoRefreshOnStart
```

测试：

```text
http://127.0.0.1:19080/health
http://127.0.0.1:19080/api/v1/export/daily.zip?date=20260603
```
