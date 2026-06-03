# 每日采集健康报告

日期：`YYYY-MM-DD`

## 总览

| 指标 | 数值 |
| --- | --- |
| 输入新闻数 |  |
| 有效新闻数 |  |
| 待复核新闻数 |  |
| 拒收新闻数 |  |
| 数据源数量 |  |
| 平均质量分 |  |
| 列表排名覆盖 |  |
| 互动指标覆盖 |  |

## 数据源表现

| source | tier | valid | review | rejected | avg_quality | rank_coverage | metric_coverage | top_flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 热点指标覆盖

| metric | records_with_metric |
| --- | --- |

## 质量问题

| flag | count | 处理建议 |
| --- | --- | --- |

## 今日输出文件

- `articles_YYYYMMDD.jsonl`
- `rejected_YYYYMMDD.jsonl`
- `crawl_report_YYYYMMDD.md`

## 人工复核清单

- 时间戳缺失或异常的新闻。
- 正文过短但标题疑似重要的新闻。
- 新增数据源中质量分低于 0.75 的新闻。
- T2 社区源中可能引发舆情但证据不足的文本。
