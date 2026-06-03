---
created: 2026-06-02
type: product
tags:
  - 财经新闻数据Agent
  - 数据抓取
  - V1
---

# News Ingestion V1

这是财经新闻数据 Agent 的第一版采集产品包。它的目标不是一次性抓完所有新闻源，而是先建立一套稳定、可追溯、可校验、可结构化输出的数据入口。

第一次使用建议先读：

- `QUICKSTART.md`
- `SOURCE_GUIDE.md`
- `CONTRIBUTING.md`

## 项目内容

- `config/source_registry.v1.json`：数据源注册表。
- `schemas/article_record.schema.json`：标准新闻记录 Schema。
- `templates/crawl_report_template.md`：每日采集健康报告模板。
- `src/news_ingestion/`：本地可运行的校验、质量门和报告生成工具。
- `run_api.ps1`：本地实时 API 启动脚本。
- `Dockerfile`：公网容器部署入口。
- `render.yaml`：Render Blueprint 部署配置。
- `railway.json`：Railway 部署配置。
- `data/`：运行样例后生成的标准化新闻、拒收新闻和日报。

## 第一版定位

V1 负责把外部新闻源转成统一的 `ArticleRecord`。后续的去重、热点聚类、事件抽取、情绪分析和热度排序都应该从这个标准格式开始。

```text
新闻源
-> 抓取/正文抽取
-> ArticleRecord
-> hot_features
-> 质量门
-> articles_YYYYMMDD.jsonl
-> 热点聚类与事件库
```

## 快速使用

在本目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

自检通过后运行：

```powershell
.\run.ps1 sample
```

如果 Windows 提示脚本执行策略限制，使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
```

生成样例数据和采集报告。

校验一批新闻：

```powershell
.\run.ps1 validate --input .\data\samples\articles_input.sample.jsonl --out-dir .\data\validated
```

基于已有输出生成日报：

```powershell
.\run.ps1 report --articles .\data\validated\articles_valid.jsonl --rejected .\data\validated\articles_rejected.jsonl --out .\data\validated\crawl_report.md
```

运行每日采集：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
```

每日采集会读取 `data/inbox/` 下的当天输入，并输出到 `data/daily/YYYYMMDD/`。

启动实时 API：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 0.0.0.0 -Port 8080 -RefreshIntervalMinutes 10
```

本地测试时可避免启动即刷新：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 127.0.0.1 -Port 19080 -RefreshIntervalMinutes 0 -NoRefreshOnStart
```

常用接口：

```text
GET /health
GET /api/v1/dates
GET /api/v1/articles?date=20260603&limit=50
GET /api/v1/hot?date=20260603&limit=30
GET /api/v1/sources?date=20260603
GET /api/v1/export/articles.csv?date=20260603
GET /api/v1/export/articles.xlsx?date=20260603
GET /api/v1/export/articles.jsonl?date=20260603
GET /api/v1/export/hot.xlsx?date=20260603&limit=50
GET /api/v1/export/daily.zip?date=20260603
POST /api/v1/refresh?date=20260603
```

公网部署时设置 `NEWS_API_TOKEN`，保护刷新接口。

常用导出方式：

- Excel/WPS/飞书表格：下载 `.xlsx` 或 `.csv`。
- Python/Agent：下载 `.jsonl`。
- 一次性拿全套材料：下载 `/api/v1/export/daily.zip?date=YYYYMMDD`。

## 公网部署

如果你没有服务器，推荐用 Render 或 Railway 从 GitHub 部署。项目已经包含：

- `Dockerfile`
- `render.yaml`
- `railway.json`
- `.env.example`
- `DEPLOY.md`

关键要求：给服务挂载持久化磁盘到 `/app/data`，否则重启后历史采集数据可能丢失。

## 源码使用

推荐把本目录作为单独 GitHub 仓库使用，或者运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\package_source.ps1
```

生成干净源码包：

```text
dist/news-ingestion-v1-source.zip
```

源码使用说明见 `SOURCE_GUIDE.md`。不要把 `.env`、`data/daily/`、`data/realtest/`、`data/multisource_test/` 一起提交或打包。

推 GitHub 前请读 `GITHUB_PUBLISH_CHECKLIST.md`。

当前已接入真实源：

- 新浪财经滚动新闻：`https://finance.sina.com.cn/roll/`
- 东方财富新闻 API：`https://np-listapi.eastmoney.com/comm/web/getNewsByColumns`
- 证券时报：`https://www.stcn.com/article/index.html`
- 央视财经：`https://finance.cctv.com/`

当前默认抓取上限：

- 新浪财经：30 条
- 东方财富：20 条
- 证券时报：20 条
- 央视财经：20 条

## 热点字段

每条输出都会包含 `hot_features`：

- `source_prominence`：源站列表排名、列表长度、源优先级、来源层级。
- `engagement_metrics`：阅读、浏览、评论、点赞、收藏、分享、转发等互动字段。

缺失值按真实语义保留：源站未暴露的字段为 `null`，真实返回 0 才写 0。当前新浪财经可通过评论计数接口记录 `comment_count`；东方财富、证券时报、央视财经当前未稳定暴露阅读、收藏、分享、转发等字段。

2026-06-02 真实采集验证：

```text
records=90
rank_coverage=90/90
metric_coverage=30/90
comment_count=30/90
```

## 第一版验收标准

- 每条有效新闻都有 `title`、`url`、`source`、`crawled_at`。
- 每条有效新闻都有 `article_id`、`content_hash`、`quality_score`。
- 每条有效新闻都有 `hot_features.source_prominence`。
- 互动指标必须区分真实 0 和缺失 `null`。
- 无法进入下游的数据进入 `rejected_YYYYMMDD.jsonl`。
- 每日生成 `crawl_report_YYYYMMDD.md`。
- 所有数据保留发布时间和抓取时间，便于检查时点一致性与前视偏差。
