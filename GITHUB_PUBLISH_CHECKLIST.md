# GitHub 发布清单

这份清单用于维护公开 GitHub 仓库，确保源码、配置和文档可以被直接 clone 和运行。

## 1. 发布前检查

在项目目录运行：

```powershell
cd C:\path\to\news-ingestion-v1
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\package_source.ps1
```

确认：

- `doctor_ok=True`
- `dist/news-ingestion-v1-source.zip` 生成成功。
- zip 中没有 `.env`、`data/daily/`、`data/realtest/`、`data/multisource_test/`。

## 2. 建议仓库结构

GitHub 仓库根目录就是本项目目录里的内容，不要把上级工作区、笔记库或本地数据目录一起推上去。

应该推：

```text
config/
schemas/
scripts/
src/
templates/
.dockerignore
.env.example
.gitignore
DEPLOY.md
Dockerfile
GITHUB_PUBLISH_CHECKLIST.md
QUICKSTART.md
README.md
SOURCE_GUIDE.md
requirements.txt
run.ps1
run_api.ps1
run_daily_17.ps1
```

不应该推：

```text
data/daily/
data/samples/
data/realtest/
data/multisource_test/
dist/
.env
__pycache__/
```

## 3. 第一次推送

如果这是新仓库：

```powershell
git init
git add .
git status
git commit -m "Initial news ingestion v1"
git branch -M main
git remote add origin <你的GitHub仓库地址>
git push -u origin main
```

`git status` 时要特别确认没有 `.env` 和 `data/daily/`。

## 4. 使用入口

建议先读：

```text
先读 README.md，再读 QUICKSTART.md。
```

常用命令：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
```

## 5. 协作规则

改新闻源适配器时，优先改：

```text
src/news_ingestion/fetchers.py
config/source_registry.v1.json
```

改质量门时，优先改：

```text
src/news_ingestion/quality.py
config/quality_rules.v1.json
```

不要提交本地生成内容：

```text
data/
.env
dist/
```

## 6. 发布后验收

在新的工作目录 clone 仓库，独立运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
powershell -ExecutionPolicy Bypass -File .\run.ps1 sample
powershell -ExecutionPolicy Bypass -File .\run.ps1 daily
```

如果能生成：

```text
data/daily/YYYYMMDD/articles_YYYYMMDD.jsonl
data/daily/YYYYMMDD/crawl_report_YYYYMMDD.md
```

就说明 GitHub 仓库可以正常使用。
