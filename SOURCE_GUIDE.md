# 源码使用说明

建议通过 GitHub 仓库 clone 项目，而不是复制整个工作目录。这样能避免把本地数据、缓存和密钥一起带出去。

## 应该包含

- `src/news_ingestion/`
- `config/`
- `schemas/`
- `templates/`
- `run.ps1`
- `run_api.ps1`
- `Dockerfile`
- `render.yaml`
- `railway.json`
- `.env.example`
- `README.md`
- `DEPLOY.md`
- `QUICKSTART.md`
- `CONTRIBUTING.md`
- `requirements.txt`

## 不应该包含

- `.env`
- `data/daily/`
- `data/samples/`
- `data/realtest/`
- `data/multisource_test/`
- `__pycache__/`
- `dist/`

原因：

- `.env` 可能有密钥。
- `data/` 里是真实抓取数据、测试数据和导出包，可能很大，也可能涉及来源网站条款和数据使用边界。
- 缓存和构建产物会让源码包变脏。

## 本地运行

先 clone 到普通工作目录，不要在 `C:\windows\system32` 里操作：

```powershell
cd "$env:USERPROFILE\Desktop"
git clone https://github.com/ZhuJiapei712/news-ingestion-v1.git
cd news-ingestion-v1
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 127.0.0.1 -Port 19080 -RefreshIntervalMinutes 0 -NoRefreshOnStart
```

访问：

```text
http://127.0.0.1:19080/health
http://127.0.0.1:19080/api/v1/export/daily.zip
```

## 公网部署

可以直接用 GitHub 仓库部署到 Render 或 Railway。部署说明见：

```text
DEPLOY.md
```

关键环境变量：

```text
NEWS_REFRESH_INTERVAL_MINUTES=10
NEWS_API_CORS_ORIGIN=*
NEWS_API_TOKEN=一个长随机字符串
```

关键持久化路径：

```text
/app/data
```

## 源码打包

如果不用 GitHub，也可以在当前目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package_source.ps1
```

它会生成：

```text
dist/news-ingestion-v1-source.zip
```

这个 zip 会排除真实数据、缓存和密钥。

## 注意事项

- 新闻源页面结构可能变化，适配器需要定期维护。
- 下载和导出接口可以直接使用；手动刷新接口应使用 `NEWS_API_TOKEN` 保护。
- 真实新闻全文和原始 HTML 的再分发需要遵守来源网站条款和数据使用规范。
