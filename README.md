# 款式工场 · 鞋服智能设计 Agent

一个**服装/鞋类设计 agent**：把一段大白话企划，变成一份**能打样、能落地**的设计方案。

```
企划 → 4 个风格方向（AI 出图）→ 选一个（人工确认）
     → 面料方案（AI 生成）→ 版型设计参数（AI 生成）→ 工艺建议（AI 生成）
     → 用料与成本（代码估算）→ 尺寸表（按品类）→ 导出 Word/Excel → 打样单与审批
```

## 特点

| 特性 | 说明 |
| --- | --- |
| **按品类生成** | 输入连衣裙 / 羽绒服 / 长筒靴 / T恤衫…，面料、版型、工艺、尺寸表**都按品类给**（鞋给鞋材与鞋码，衣给面料与胸腰围） |
| **五道人工确认（代码强制）** | 企划 / 方向 / 面料 / 版型 / 打样。未确认直接调接口 → **HTTP 409**，绕过前端也拒 |
| **企划可人工修改** | AI 理解错了能当场改；**确认后锁定**，要改必须显式「重新编辑」 |
| **出图质量可打分** | 每张方向图可打 1–3 分，分数入库，用于持续校准 |
| **历史记录** | 「我的设计」列出所有做过内容的方案，可「继续做」（不重新出图、不花钱） |

## 快速开始

```bash
cd projects/鞋服agent开发文档      # 本仓库内的路径
python -m venv .venv && .venv/bin/pip install -e .   # 或 uv sync
.venv/bin/python scripts/set_model_key.py   # 文本模型 Key（默认 DeepSeek）
.venv/bin/python scripts/set_image_key.py   # 出图 Key（默认火山方舟）
.venv/bin/python -m uvicorn app.main:app --port 8020
cd frontend && npm install && npm run build && npx next start -p 5180
```

- 后端：<http://127.0.0.1:8020>（自带验收页）
- 前端：<http://localhost:5180>

**不想花钱跑出图**：启动时加 `IMAGE_PROVIDER=placeholder`（本地生成占位图，界面会明确标注）。

## 口径与边界（请务必遵守）

- 本仓库内的面料/辅料/版型/工艺/尺码示例为 **示例数据 · 仿真实例**，不是真实企业物料库
- 效果图是 **AI 生成示意图（非实物照片）**，模特为虚构
- 成本为 **估算值**，正式核价须由采购确认；生成的面料/工艺为 **AI 建议（非真实物料、非工厂工价）**
- 版型输出的是 **设计参数与结构说明**，不是工厂纸样 / CAD / DXF 文件
- 打样为 **演示流程**，未对接真实工厂 / PLM（接口位已预留）
- 密钥只放在本地 `.env`（已在 `.gitignore` 内），**不进仓库、不进前端产物**

## 技术栈

FastAPI + SQLAlchemy（SQLite）+ Pydantic · Next.js 15 + React 19 + TypeScript · 文本模型 DeepSeek · 出图 火山方舟 doubao-seedream（2K）

## 目录

```
app/          后端：main.py 路由 / gates.py 五道确认门 / workflows/ 各流程 / export.py 导出 / imagegen.py 出图
frontend/     正式前端（Next.js）：app/page.tsx 工作台、lib/api.ts 客户端
seed/         示例库（仿真实例）：面料/辅料/版型/工艺/尺码
tests/        后端测试（离线可跑，不花模型钱）
docs/         阶段文档、证据包、项目状态、PRD
```

## 许可证

MIT，见 [LICENSE](LICENSE)。
