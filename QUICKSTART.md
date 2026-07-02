# 快速开始

这份说明用于快速跑通项目：从 GitHub clone 后，在本地抓取财经新闻，并导出 Excel/CSV/JSONL。

## 你需要准备

- Python 3.11 或更高版本。
- Windows PowerShell。
- 可以访问外网新闻源。

当前项目不需要额外安装 Python 包，`requirements.txt` 只是用于说明依赖状态。

## 1. Clone 仓库

```powershell
cd "$env:USERPROFILE\Desktop"
git clone https://github.com/ZhuJiapei712/news-ingestion-v1.git
cd news-ingestion-v1
```

不要在 `C:\windows\system32` 里 clone。看到 PowerShell 提示符是 `PS C:\windows\system32>` 时，先切到桌面或自己的工作目录。

如果 clone 成功，才继续执行后面的 `cd`、`doctor.ps1` 和 `run.ps1`。

## 2. 自检

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

看到 `doctor_ok=True` 就可以继续。

## 3. 跑样例

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
```

输出位置：

```text
data/samples/
```

这一步不抓真实新闻，只验证本地工具链能跑通。

## 4. 抓真实新闻

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
```

输出位置：

```text
data/daily/YYYYMMDD/
```

核心文件：

- `articles_YYYYMMDD.jsonl`
- `rejected_YYYYMMDD.jsonl`
- `crawl_report_YYYYMMDD.md`
- `run_metadata_YYYYMMDD.jsonl`

`articles_YYYYMMDD.jsonl` 使用 `ArticleRecord 2.0` 结构。优先看这些分区：

- `source_info`：来源和栏目。
- `time_info`：发布时间、抓取时间和时延。
- `quality`：质量等级、复核标记和下游可用性。
- `hotness`：榜单位置和稳定热度分。
- `engagement`：阅读、评论、点赞、收藏、分享、转发等可用互动指标。
- `extraction`：入口 URL 和列表位置，方便复查。

## 5. 导出数据

启动本地 API：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 127.0.0.1 -Port 19080 -RefreshIntervalMinutes 0 -NoRefreshOnStart
```

浏览器打开：

```text
http://127.0.0.1:19080/health
```

打开重构后的板块热力图看板：

```text
http://127.0.0.1:19080/
```

看板包含：

- 每日板块热力图：点击板块查看相关新闻。
- 实时消息：按刷新间隔采集后，页面每分钟更新。
- 交易池：可选择过去 1/3/5/10 天。
- GPT 状态：配置 `OPENAI_API_KEY` 后可调用单篇新闻分析接口。

下载当天全套数据包：

```text
http://127.0.0.1:19080/api/v1/export/daily.zip
```

下载热点 Excel：

```text
http://127.0.0.1:19080/api/v1/export/hot.xlsx?limit=50
```

下载全量新闻 Excel：

```text
http://127.0.0.1:19080/api/v1/export/articles.xlsx?limit=500
```

## 6. 常见问题

### `git clone` 后出现 TLS connect error

常见原因是 Git 代理配置失效，例如本机配置了 `127.0.0.1:7890`，但代理软件没有启动或连接不稳定。

先检查远端是否可访问：

```powershell
git ls-remote https://github.com/ZhuJiapei712/news-ingestion-v1.git HEAD
```

如果报 TLS 错误，检查代理：

```powershell
git config --global --get http.proxy
git config --global --get https.proxy
```

如果显示的是本地代理地址，比如 `http://127.0.0.1:7890`，有两种处理方式：

```powershell
# 方式一：启动你的代理软件，然后重试 clone
git clone https://github.com/ZhuJiapei712/news-ingestion-v1.git
```

```powershell
# 方式二：不用代理访问 GitHub 时，清掉 Git 代理后重试
git config --global --unset http.proxy
git config --global --unset https.proxy
git clone https://github.com/ZhuJiapei712/news-ingestion-v1.git
```

如果 `git ls-remote` 能返回一串 commit hash，但 clone 仍失败，通常是网络中断，换一个网络或稍后重试。

### `cd news-ingestion-v1` 找不到路径

说明前面的 `git clone` 没成功。先解决 clone 报错，不要继续执行后面的命令。

### PowerShell 不让执行脚本

使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
```

### `python` 找不到

先安装 Python 3.11+，然后重新打开 PowerShell。

### 抓取条数为 0

检查：

- 电脑是否能访问新闻源网页。
- 是否被代理、防火墙或校园网拦截。
- `config/source_registry.v1.json` 是否被改坏。

### API 端口被占用

换一个端口：

```powershell
powershell -ExecutionPolicy Bypass -File .\run_api.ps1 -HostName 127.0.0.1 -Port 19100 -RefreshIntervalMinutes 0 -NoRefreshOnStart
```

### 数据在哪里

默认在项目目录下：

```text
data/daily/
data/samples/
```

这些数据不会提交到 GitHub，因为 `.gitignore` 已经排除。

## 7. 排查信息

如果抓取失败，先保留这些信息，方便定位问题：

- 运行的命令。
- `data/daily/YYYYMMDD/crawl_report_YYYYMMDD.md`。
- 报错截图或终端输出。
- 系统版本和 Python 版本。
