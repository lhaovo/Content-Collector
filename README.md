# Content Collector

本地社媒内容采集 MVP：扫描输入文件夹，将帖子、素材、抽取任务和结构化结果写入 SQLite，并提供本地 Web 管理台。

## 启动

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
copy .env.example .env
content-collector init-db
content-collector serve
```

打开：`http://127.0.0.1:8000`

## 导入

在 Web 页面输入本地文件夹路径，或使用命令：

```bash
content-collector ingest "E:\path\to\input"
```

## 环境变量

- `ZHIPU_API_KEY`：智谱 API Key。
- `CONTENT_COLLECTOR_DB`：SQLite 文件路径。
- `CONTENT_COLLECTOR_MODEL`：默认 `glm-4.6v-flash`。
- `CONTENT_COLLECTOR_ENABLE_AI_GROUPING`：是否对低置信度目录启用 AI 分组。

## 当前能力

- 文件夹扫描
- 规则分组
- AI 分组接口预留
- 素材类型识别
- SQLite 入库
- GLM-4.6V-Flash 抽取封装
- Web Dashboard / Posts / Jobs