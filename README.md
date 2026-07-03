# News Ingestion V1

财经新闻数据 Agent 重构版。项目聚焦“真实财经新闻抓取 + 板块热度看板 + 相关新闻查看 + AI 辅助分析”，用于展示每日市场热点、持续活跃板块和可进入交易池观察的新闻催化方向。

公开访问地址：

- GitHub Pages 看板：https://chuyanchu.github.io/news-ingestion-v1-public/
- GitHub 仓库：https://github.com/chuyanchu/news-ingestion-v1-public

## 项目定位

原模块包含抓取、去重、聚类、情绪分析、热度排序、时间线等较多功能，整体复杂度较高。重构后先保留最核心的展示和分析链路：

```text
真实财经新闻源
-> 抓取与标准化
-> 去重和质量校验
-> 板块关键词匹配
-> 热度评分和排序
-> AI 摘要/影响方向/重要性分析
-> 每日热力图、实时消息、交易池
```

第一阶段不引入 BERTopic 等重模型，优先保证数据真实、结构清晰、界面可展示、部署后其他同学可以访问。

## 核心功能

- 真实新闻抓取：接入新浪财经、东方财富、证券时报、央视财经等财经新闻源。
- 新闻去重：按 `article_id`、URL、内容哈希等字段减少重复新闻。
- 质量校验：过滤标题缺失、来源缺失、URL 无效等不可用数据。
- 板块热力图：按板块关键词聚合新闻，计算热度分、新闻数、重要新闻数。
- 点击查看资讯：点击具体板块后展示相关新闻、来源、时间、摘要和影响方向。
- 实时消息流：展示最新抓取到的重要财经消息。
- AI 分析：使用 DeepSeek/OpenAI 兼容接口生成摘要、影响方向、重要性、催化逻辑和关联个股。
- 交易池：基于过去 1/3/5/10 天的持续热度，筛选值得继续观察的板块。
- 静态公网访问：通过 GitHub Actions 采集和预计算数据，再导出为 GitHub Pages 静态页面。
- 本地实时服务：支持启动本地 API 服务，进行手动刷新、实时抓取和导出。

## 数据说明

项目使用真实财经新闻数据，不使用示例新闻作为公开看板数据。当前接入源包括：

- 新浪财经滚动新闻：https://finance.sina.com.cn/roll/
- 东方财富新闻 API：https://np-listapi.eastmoney.com/comm/web/getNewsByColumns
- 证券时报：https://www.stcn.com/article/index.html
- 央视财经：https://finance.cctv.com/

历史数据会保存在：

```text
data/daily/YYYYMMDD/
seed-data/daily/YYYYMMDD/
```

其中 `seed-data/daily/` 用于 GitHub Actions 构建时恢复历史数据，避免静态站点每次部署后只剩当天新闻。

## AI 分析说明

静态网页不能安全地直接调用 AI API，因为前端代码和请求会暴露 API Key。当前采用更适合公开部署的方案：

```text
GitHub Actions
-> 读取 GitHub Secret 中的 DEEPSEEK_API_KEY 或 OPENAI_API_KEY
-> 对当天重要新闻做 AI 预计算
-> 输出 static-data/gpt_analysis_YYYYMMDD.json
-> GitHub Pages 前端读取预计算结果
```

这样其他人可以直接访问分析结果，但 API Key 不会写入仓库或暴露到浏览器端。

本地 `.env` 可配置：

```text
DEEPSEEK_API_KEY=你的 key
OPENAI_API_KEY=你的 key
```

公开仓库中不要提交 `.env`。

## 快速运行

安装依赖：

```bash
pip install -r requirements.txt
```

运行每日采集：

```bash
PYTHONPATH=src python -m news_ingestion.cli daily
```

运行采集并做 AI 预计算：

```bash
PYTHONPATH=src python -m news_ingestion.cli daily --analyze --analyze-max 30
```

启动本地实时 API 和看板：

```bash
PYTHONPATH=src python -m news_ingestion.api_server
```

浏览器访问：

```text
http://127.0.0.1:8080/
```

导出静态站点：

```bash
PYTHONPATH=src python -m news_ingestion.static_export --out-dir public --max-dates 10
```

## 常用接口

本地 API 服务启动后可访问：

```text
GET /health
GET /
GET /api/v1/dates
GET /api/v1/articles?date=YYYYMMDD&limit=50
GET /api/v1/realtime?date=YYYYMMDD&limit=20
GET /api/v1/heatmap?date=YYYYMMDD&limit=30
GET /api/v1/sector/{板块名}/news?date=YYYYMMDD
GET /api/v1/trade-pool?days=3&limit=20
GET /api/v1/gpt/status
GET /api/v1/gpt/analyze?date=YYYYMMDD&article_id=ARTICLE_ID
GET /api/v1/export/articles.xlsx?date=YYYYMMDD
GET /api/v1/export/daily.zip?date=YYYYMMDD
POST /api/v1/refresh?date=YYYYMMDD
```

公网部署时建议设置 `NEWS_API_TOKEN`，保护刷新接口。

## 代码结构

```text
.
├── .github/workflows/pages.yml       # GitHub Pages 自动采集、预计算、静态导出和部署
├── config/
│   ├── source_registry.v1.json       # 新闻源注册表
│   ├── sector_keywords.v1.json       # 板块关键词词典
│   └── quality_rules.v1.json         # 新闻质量校验规则
├── data/
│   ├── daily/                        # 本地运行生成的每日新闻数据
│   └── inbox/                        # 可手动放入待校验新闻
├── seed-data/daily/                  # GitHub Pages 构建时使用的历史真实数据
├── schemas/
│   └── article_record.schema.json    # 标准新闻记录结构
├── src/news_ingestion/
│   ├── api_server.py                 # 本地 HTTP API、看板接口、刷新接口、导出接口
│   ├── cli.py                        # 命令行入口：采集、校验、日报生成
│   ├── fetchers.py                   # 新浪、东方财富、证券时报、央视财经等抓取逻辑
│   ├── quality.py                    # 新闻质量评分和有效性判断
│   ├── rebucket.py                   # 按真实发布时间重新分配到对应日期
│   ├── sector_heat.py                # 板块匹配、热度评分、交易池计算
│   ├── gpt_analyzer.py               # DeepSeek/OpenAI 新闻分析
│   ├── static_export.py              # 导出 GitHub Pages 可用的静态 JSON 和页面
│   ├── events.py                     # 事件/消息结构辅助逻辑
│   ├── backtest.py                   # 回测相关原型逻辑
│   ├── report.py                     # 采集健康报告生成
│   └── io_utils.py                   # JSON/JSONL/文本读写工具
├── static/dashboard.html             # 前端看板：热力图、消息流、交易池、AI 结果
├── Dockerfile                        # 容器部署入口
├── render.yaml                       # Render 部署配置
├── railway.json                      # Railway 部署配置
├── DEPLOY.md                         # 公网 API 部署说明
├── QUICKSTART.md                     # 快速开始
└── SOURCE_GUIDE.md                   # 源码使用说明
```

## 部署方式

### GitHub Pages 静态部署

适合课堂展示和给其他同学访问。流程为：

```text
push 到 main
-> GitHub Actions 定时或手动运行
-> 抓取真实新闻
-> 恢复历史数据
-> 预计算 AI 分析
-> 导出 public/
-> 部署到 GitHub Pages
```

优点是免费、访问稳定、电脑关机后仍可访问。限制是网页本身不能实时主动抓取，只能展示 Actions 最近一次构建出的静态数据。

### Render/Railway API 部署

适合需要真正实时刷新、后台常驻抓取和手动刷新接口的场景。项目已包含：

- `Dockerfile`
- `render.yaml`
- `railway.json`
- `.env.example`

部署 API 服务时需要挂载持久化目录到 `/app/data`，否则服务重启后历史数据可能丢失。

## 输出数据

每日采集后会生成：

```text
data/daily/YYYYMMDD/articles_YYYYMMDD.jsonl
data/daily/YYYYMMDD/articles_valid.jsonl
data/daily/YYYYMMDD/articles_rejected.jsonl
data/daily/YYYYMMDD/ai_analysis.json
data/daily/YYYYMMDD/crawl_report_YYYYMMDD.md
```

静态导出后会生成：

```text
public/index.html
public/static-data/dates.json
public/static-data/articles_YYYYMMDD.json
public/static-data/heatmap_YYYYMMDD.json
public/static-data/realtime_YYYYMMDD.json
public/static-data/trade_pool.json
public/static-data/gpt_status.json
public/static-data/gpt_analysis_YYYYMMDD.json
```

## 当前取舍

- 先用板块词典和热度规则实现可解释的 MVP，不优先上复杂聚类模型。
- GitHub Pages 采用 AI 预计算，不在前端暴露 API Key。
- 公开站点以真实数据展示为主，避免使用假新闻或 `example.com` 数据。
- 回测功能保留原型代码，后续可结合交易池和板块热度继续完善。

