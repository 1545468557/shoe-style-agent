# 款式工场 · 鞋服设计 Agent

从自然语言和参考图开始，与设计助手整理需求、规划不同风格方向、生成并修改鞋服款式。选定满意图片后，可在独立方案页查看设计依据，下载图片与首版打样沟通资料。3D 预览需单独接通服务。

## 本地启动

需要 Python 3.12+、Node.js 和 npm。密钥只写在本项目忽略提交的 `.env` 中；可从 `.env.example` 复制配置项。

```bash
uv sync --dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8020
```

另开终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

打开 [http://localhost:5180/](http://localhost:5180/)。后端健康检查为 `GET /api/health`。如果采用生产模式运行前端，修改代码后须重新 `npm run build` 并重启 `npm run start`。

## 当前主流程

1. 对话描述款式，可上传草图、面料照片或参考图；助手整理设计要求。
2. 确认要求，查看并筛选风格方向，再生成候选图片。
3. 比较候选款，对单款提出修改并生成新版本；图片及逐项检查结果保存在项目中。
4. 确认一款后进入方案页，查看设计依据并下载图片与打样沟通资料。未确定的尺寸、面料和工艺会标明待核对，示意结构图不能直接当纸样使用。

顶部“历史对话”可打开现有项目。旧 `/workbench` 地址会转到当前入口。

## CMS 需求包连接（只读基础）

在后端 `.env` 配置 `CMS_BASE_URL` 和 `CMS_DESIGN_API_KEY`（CMS 中绑定 `design` 身份的专用 Key），然后运行：

```bash
.venv/bin/python -m app.agent.cms_bridge
```

这只会列出 CMS 已批准的需求包编号与版本，不会自动创建设计项目或触发出图。设计负责人接收、分派、设计审核与回传的实施顺序见 [CMS 设计交接实施方案](docs/CMS设计交接实施方案.md)。密钥仅在后端使用，不要写入前端或提交到 Git。

## 代码与数据

- `app/main.py`：项目创建、健康检查和 Agent 路由入口。
- `app/agent/`：需求记录、风格规划、生成、检查、版本、素材、打样资料与可选 3D。
- `frontend/components/design/`：对话、方向、版本和方案页界面。
- `frontend/lib/agent-api.ts`：当前前端 API 客户端。
- `data/app.sqlite3`、`data/assets/`：已有项目及生成素材。旧工作台的历史表仍保存在数据库中，清理代码时不会删除或迁移这些数据。

旧“五道门”工作台、示例库和静态验收页已从运行代码中移除。

## 验证

```bash
uv run pytest tests -q
uv run ruff check app tests scripts
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
```
