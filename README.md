# 鞋服智能设计 Agent（款式工场）

鞋服设计师用的 AI 设计工作台：**企划 → 3 个风格方向（真实 AI 出图）→ 选 1（人工确认）→ 面料搭配 → 版型与工艺 → 用料与尺寸 → 打样单审批 → 导出 Word/Excel**。
五道人工确认由**后端代码强制**，绕过前端直调接口同样被拒（HTTP 409）。

> 对外口径（务必遵守）：**示例数据/仿真实例**、**AI 生成示意图（非实物照片）**、**面料/辅料/工艺均为 AI 生成建议（非真实物料、非采购报价、非工厂工价）**、成本为**估算值**、打样为**演示流程**（未对接真实工厂/PLM）。

> **按品类一致**（2026-09-22 起）：鞋类只输出「鞋码（＝脚长）/ 跖围 /（靴类才有）筒高」，**不得出现腰围/裙长**；服装输出「胸围/腰围/臀围/衣长（裙装=裙长、裤装=裤长）/袖长」。品类判定统一走 `app/category.py`。

## 一、启动

```bash
cd projects/鞋服agent开发文档

# 后端（8020）
.venv/bin/python -m uvicorn app.main:app --port 8020        # 真实出图
IMAGE_PROVIDER=placeholder .venv/bin/python -m uvicorn app.main:app --port 8020   # 不出图、零成本

# 前端（5180，另开一个终端）
cd frontend && npm install && npm run dev
```

打开 **http://localhost:5180/**（正式前端）或 **http://127.0.0.1:8020/**（后端自带验收页）。

## 二、密钥（只写进 .env，绝不进对话/仓库/日志）

```bash
.venv/bin/python scripts/set_model_key.py   # 文本模型（默认 DeepSeek：https://api.deepseek.com / deepseek-chat）
.venv/bin/python scripts/set_image_key.py   # 出图（火山方舟：doubao-seedream-5-0-lite，IMAGE_SIZE=2K）
```

`.env` 已被 `.gitignore` 覆盖（`chmod 600`）。**注意**：出图尺寸小于 2K（约 368 万像素）会被服务端拒绝。

## 三、验证（照着点）

1. 首页 → 在输入框写一段企划（默认已填示例）→ 点「解析企划」→ 看"我理解到的"四项 + "你没说"清单
2. 点「确认，开始出效果图」→ 顶部第一道确认变 ✓（**未解析就点会被拒**，这就是代码强制的确认门）
3. 点「生成 3 个方向」→ 出 3 张 3:4 真图（约 30 秒/张）→ 点图放大 / 勾两张「加入对比」
4. 点「就用这个方向」→ 进入面料：点「让模型挑面料并说明理由」→ 看模型的**搭配理由** → 「用这个」→「面料就它，下一步」
5. 版型页：点「看推荐版型」→「选这个版型」→ 勾工艺 →「版型定好了，下一步」
6. 「用料与尺寸」→ 先点 **「AI 生成辅料建议」**（约 ¥0.01）→ 勾选后点「采纳选中」→ 再点「算用料与尺寸」→ 看用料清单（**版型行不计入成本**）、成本估算块、**尺寸表（部位与档位按品类给）** → 「导出 Word 与 Excel」→ 下载
   > 不生成辅料也能算料：会用**按品类的内置兜底**（鞋类→内里/鞋垫/大底；服装→里布/拉链/衬布），不会给凉鞋配鱼骨/细扣。
7. 「生成打样单并审批」→ 门状态"打样"变 ✓

> 换个品类试试：企划里分别写「凉鞋」「长筒靴」「牛仔裤」各跑一遍，尺寸表部位应与品类对得上（凉鞋无筒高、裤子显示「裤长」）。

## 四、自检

```bash
.venv/bin/python -m pytest tests -q   # 33 passed（注意：直接用 pytest 命令会报 No module named 'app'）
.venv/bin/ruff check app tests        # All checks passed
cd frontend && npx tsc --noEmit      # 前端类型检查
cd frontend && npm test               # 3 passed（vitest）
cd frontend && npm run build          # 生产构建
```

按品类一致性的回归测试在 `tests/test_category_consistency.py`（凉鞋/靴/连衣裙/牛仔裤 + 辅料清洗 + 导出不报错）。

## 五、目录

```
app/            后端（FastAPI）：main.py 路由、category.py 品类判定、gates.py 五道确认门、workflows/ 流程、export.py 导出、imagegen.py 出图
frontend/       正式前端（Next.js，5180）：app/page.tsx 工作台、lib/api.ts 接口客户端、public/looks.html 六风格样板
seed/           示例库（仿真实例）：面料 40 / 辅料 6 / 版型 8 / 工艺 12 / 尺码 4（**不再参与生成**，只作素材库参考）
tests/          mock 自动化测试
scripts/        seed.py 灌示例库、set_model_key.py / set_image_key.py 写密钥
static/         后端自带验收页
```

主要接口（阶段 3 第 6 子阶段新增）：

| 接口 | 说明 |
| --- | --- |
| `POST /api/project/{id}/trims/generate` | AI 生成 3–5 条**本品类**辅料建议（约 ¥0.01） |
| `GET /api/project/{id}/trims/generate` | 读取已生成辅料建议（零费用，刷新回显） |
| `POST /api/project/{id}/trims/accept` | 采纳选中的辅料（`{"seqs":[1,2]}`） |
| `POST /api/project/{id}/bom` | 用料与尺寸：`size_spec.rows`（按品类的部位）+ `size_spec.tiers`（档位）+ `bom.trims_source`（辅料来源） |

## 六、已知坑

- 出图尺寸必须 ≥2K，`IMAGE_SIZE=1024*1024` 会 400。
- Next 的 `rewrites` 代理在本组合下不生效 → 用浏览器直连 + 后端 CORS（只放行本机端口）。
- 改完后端代码**必须重启 8020**（未开 --reload）；前端在生产模式（`next start`）下改了代码要**重新 `npm run build` 再重启**。
- 新增数据表（`trim_suggestion` / `trim_pick`）由启动时的 `init_db()`（`create_all`）自动建，不需手动迁移。

## 七、启动方式（产品经理自用）

**推荐：Dock / Spotlight 图标**（桌面不放东西）

已在 `~/Applications` 里装好两个图标（由 `bash scripts/install_launcher_apps.sh` 生成，可重复执行）：

| 图标 | 作用 |
| --- | --- |
| **鞋服-启动工作台** | 生产模式启动后端（8020）+ 前端（5180），就绪后自动打开浏览器；已在运行则只打开浏览器 |
| **鞋服-停止服务** | 关掉两个服务，释放 8020 / 5180 |

打开方式（任选）：
1. **Spotlight**：按 `Cmd + 空格`，输入「鞋服」，回车；
2. 打开「应用程序」文件夹（`~/Applications`），把图标拖到 **Dock** 上，以后点一下即可。

> 为什么用生产模式：开发模式偶发"JS 加载不到 → 按钮全死"，生产模式不会有这个问题。
> **改了前端代码**要先 `cd frontend && npm run build`（或删 `.next` 让启动图标自己构建），否则页面还是旧的。

**备用：命令行脚本**（同目录下，可直接双击或用终端跑）

```bash
./启动鞋服.command     # 同上：起后端 + 前端 + 开浏览器
./停止鞋服.command     # 关服务
```

> 各服务日志：`/tmp/shoe-8020.log`、`/tmp/shoe-next.log`、`/tmp/shoe-build.log`。
