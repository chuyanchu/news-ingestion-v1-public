# Render 公网部署步骤

这个项目已经包含 `Dockerfile` 和 `render.yaml`，推荐用 Render Blueprint 部署。

## 1. 准备 GitHub 仓库

把当前代码提交并推到一个你有权限的 GitHub 仓库。

不要提交：

- `.env`
- `data/daily/`
- `logs/`

## 2. 创建 Render 服务

1. 打开 Render。
2. 选择 `New` -> `Blueprint`。
3. 选择这个 GitHub 仓库。
4. Render 会读取 `render.yaml` 并创建 Docker Web Service。

## 3. 环境变量

至少保留：

```text
NEWS_API_HOST=0.0.0.0
NEWS_REFRESH_INTERVAL_SECONDS=10
NEWS_API_CORS_ORIGIN=*
NEWS_API_TOKEN=Render 自动生成或手动填写一串随机字符
```

可选 AI：

```text
DEEPSEEK_API_KEY=你的 DeepSeek key
OPENAI_API_KEY=你的 OpenAI key
```

有 `NEWS_API_TOKEN` 时，公网前端会隐藏“刷新采集”按钮，避免老师或同学误点导致 401。手动刷新请用带 token 的接口。

## 4. 持久化磁盘

`render.yaml` 已配置：

```text
mountPath=/app/data
sizeGB=1
```

不要删除这个 disk，否则重启后历史新闻会丢失。

## 5. 验证

部署成功后访问：

```text
https://你的服务名.onrender.com/
https://你的服务名.onrender.com/health
https://你的服务名.onrender.com/api/v1/dates
```

手动刷新：

```bash
curl -X POST "https://你的服务名.onrender.com/api/v1/refresh" \
  -H "Authorization: Bearer 你的 NEWS_API_TOKEN"
```

刷新后再看：

```text
https://你的服务名.onrender.com/api/v1/heatmap
```
