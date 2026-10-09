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

## 知需与设计团队直接交接

知需产品负责人批准需求后，知需保存不可变的需求版本；设计负责人在 `/handoffs` 点击“同步知需已批准需求”，确认接收并分派。设计人员只看到分派给自己的项目。设计负责人退回澄清或审核通过时，意见和设计效果图直接回到知需审批记录。澄清后产品负责人可把需求交给产品人员补充，重新审批会产生下一版本，旧版本仍保留在设计端。

两个服务端分别配置相同的 `DESIGN_HANDOFF_KEY` 和 `PRODUCT_HANDOFF_KEY`，设计端另配 `PRODUCT_HANDOFF_URL`；密钥不进入浏览器。本机首次使用可运行 `python scripts/setup_design_accounts.py` 创建设计总监和初始设计人员账号。初始账号信息保存在上一级目录 `.local-agent-keys/design-accounts.txt`，不要上传或分享密码。此后设计人员可在 `/register` 自行注册，角色固定为设计人员；设计总监在 `/settings` 查看并管理已注册成员，可停用、恢复或重置密码。所有成员都可在设置页修改自己的密码，修改或重置密码会使旧会话失效。成员存在进行中任务时不能停用。效果图链接指向本机服务，正式部署时需要可稳定访问的图片存储地址。

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
