# 协作说明

欢迎基于本仓库改进财经新闻采集器。

## 改动入口

| 模块 | 主要文件 | 改动重点 |
| --- | --- | --- |
| 数据源适配 | `src/news_ingestion/fetchers.py`、`config/source_registry.v1.json` | 源站入口、列表解析、正文抽取 |
| 数据质量 | `src/news_ingestion/quality.py`、`config/quality_rules.v1.json` | 质量分、复核规则、拒收规则 |
| API 与导出 | `src/news_ingestion/api_server.py` | 查询接口、热点接口、CSV/XLSX/ZIP 导出 |
| 文档与说明 | `README.md`、`QUICKSTART.md`、`DEPLOY.md` | 使用流程、部署流程、维护说明 |

## 提交前必须做

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
```

如果改了真实源抓取，再跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
```

## 不要提交

- `.env`
- `data/daily/`
- `data/samples/`
- `data/realtest/`
- `data/multisource_test/`
- `dist/`
- `__pycache__/`

## 新增数据源流程

1. 在 `config/source_registry.v1.json` 添加 source。
2. 在 `src/news_ingestion/fetchers.py` 添加适配器。
3. 输出必须包含 `title`、`url`、`source`、`crawled_at`。
4. 尽量补 `published_at`、`content`、`hot_features`。
5. 跑 `daily` 并查看 `crawl_report_YYYYMMDD.md`。

## 质量原则

- 缺失值保持 `null`，不要伪造成 0。
- 抓取失败要进入错误记录或报告，不要静默吞掉。
- 不要为了条数牺牲质量。
