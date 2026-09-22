"""鞋服智能设计 Agent · HTTP 入口（阶段 2 最小验收形态）。

设计口径：
- 五道人工门由后端强制（`app/gates.py`）；前端只是把按钮灰掉，**绕过前端一样被拒**；
- 示例库内容一律带 `is_sample=true`，界面据此标注「示例库」；
- 没有真模型/真出图 Key 时走 mock + placeholder，并在返回值里标明来源（不许冒充）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .gates import GateError, confirm
from .gates import status as gate_status
from .library import LibraryError, all_of
from .library import stats as library_stats
from .llm import LlmError, complete_json
from .models import (
    Brief,
    MaterialPick,
    PatternIteration,
    PatternPick,
    Project,
    Sampling,
    StyleDirection,
    init_db,
    session,
)
from .schemas import BriefFieldsIn, BriefParsed
from .workflows.bom import (
    advance_sampling,
    build_bom,
    compute_size_spec,
    create_sampling_order,
    iterate_pattern,
)
from .workflows.direction import generate_directions
from .workflows.material import (
    confirm_material,
    pick_material,
    pick_pattern,
)

app = FastAPI(title="鞋服智能设计 Agent", version="0.1.0")

# 正式前端（Next.js，端口 5180）在开发期需要跨域访问本后端。
# **只放行本机开发端口**，不用 "*"，也不影响其他项目。
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5180",
        "http://127.0.0.1:5180",
        "http://localhost:8020",
        "http://127.0.0.1:8020",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

BRIEF_SYSTEM = (
    "你是鞋服企划助理。只输出 JSON，字段："
    "category/style_keywords/target_user/price_band/cost_ceiling/missing。"
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _fail(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.get("/api/health")
def health() -> dict[str, Any]:
    from .config import get_config

    cfg = get_config()
    return {
        "ok": True,
        "model_provider": "mock" if cfg.is_mock_model else cfg.model_id,
        "image_provider": "placeholder" if cfg.is_placeholder_image else cfg.image_provider,
        "library": library_stats(),
        "note": "示例库为仿真实例，界面必须标注「示例库」",
    }


class ProjectIn(BaseModel):
    name: str = "未命名项目"


class BriefIn(BaseModel):
    text: str


class GateIn(BaseModel):
    direction_id: int | None = None


class MaterialIn(BaseModel):
    direction_id: int
    material_id: str
    note: str = ""


class PatternIn(BaseModel):
    pattern_id: str
    crafts: list[str] = []


class IterateIn(BaseModel):
    changes: dict[str, float]


class SamplingIn(BaseModel):
    to_state: str


@app.post("/api/project")
def create_project(payload: ProjectIn) -> dict[str, Any]:
    with session() as s:
        row = Project(name=payload.name)
        s.add(row)
        s.commit()
        return {"id": row.id, "name": row.name, "status": row.status}


@app.post("/api/project/{project_id}/brief")
def submit_brief(project_id: int, payload: BriefIn) -> Any:
    try:
        with session() as s:
            project = s.get(Project, project_id)
            if project is None:
                return _fail("project_not_found", "找不到这个项目。", 404)
            parsed, provider = complete_json(BRIEF_SYSTEM, payload.text, BriefParsed)
            from sqlalchemy import select

            row = s.scalar(select(Brief).where(Brief.project_id == project_id))
            if row is None:
                row = Brief(project_id=project_id, raw_text=payload.text)
                s.add(row)
            row.raw_text = payload.text
            row.parsed = parsed.model_dump()
            row.confirmed_at = None
            s.commit()
            return {"parsed": parsed.model_dump(), "provider": provider, "message": "请确认企划（门 1）"}
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.post("/api/project/{project_id}/brief/fields")
def edit_brief_fields(project_id: int, payload: BriefFieldsIn) -> Any:
    """人工修改企划解析结果。**企划门一旦确认就锁定**，绕过前端也拒（审计要求）。"""
    from sqlalchemy import select

    from .schemas import merge_brief_fields

    try:
        data = payload
        with session() as s:
            if s.get(Project, project_id) is None:
                return _fail("project_not_found", "找不到这个项目。", 404)
            row = s.scalar(select(Brief).where(Brief.project_id == project_id))
            if row is None:
                return _fail("brief_not_found", "请先点「开始理解」，再来修改。", 409)
            if row.confirmed_at:
                return _fail(
                    "gate_not_confirmed",
                    "企划已确认，不能再改。如需修改请点「重新编辑」。",
                    409,
                )
            row.parsed = merge_brief_fields(row.parsed, data)
            s.commit()
            return {
                "brief": {"text": row.raw_text, "parsed": row.parsed},
                "edited": True,
                "message": "已保存你的修改（未经模型重新解析）",
            }
    except ValueError as exc:
        return _fail("invalid_request", str(exc), 409)


@app.post("/api/project/{project_id}/brief/reopen")
def reopen_brief(project_id: int) -> Any:
    """解锁企划门（供人工改完再确认）。下游已推进（选了面料/版型）时拒绝，避免数据自相矛盾。"""
    from sqlalchemy import select

    from .models import MaterialPick, PatternPick

    with session() as s:
        if s.get(Project, project_id) is None:
            return _fail("project_not_found", "找不到这个项目。", 404)
        row = s.scalar(select(Brief).where(Brief.project_id == project_id))
        if row is None:
            return _fail("brief_not_found", "还没理解过企划。", 409)
        busy = s.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id)) or s.scalar(
            select(PatternPick).where(PatternPick.project_id == project_id)
        )
        if busy:
            return _fail("downstream_confirmed", "已经选了面料或版型，不能再退回改企划。", 409)
        row.confirmed_at = None
        s.commit()
        return {"status": gate_status(s, project_id), "message": "已解锁企划，修改后请重新确认"}


@app.post("/api/project/{project_id}/gate/{gate}")
def confirm_gate(project_id: int, gate: str, payload: GateIn | None = None) -> Any:
    body = payload or GateIn()
    try:
        with session() as s:
            flags = confirm(s, project_id, gate, {"direction_id": body.direction_id})
            return {"status": flags}
    except GateError as exc:
        return _fail(exc.code, exc.message, 409)


@app.get("/api/projects")
def list_projects(limit: int = 30) -> Any:
    """我的设计：方案列表（按 id 倒序，**过滤掉什么都没做的空方案**）。"""
    from sqlalchemy import select

    from .models import Brief, StyleDirection

    with session() as s:
        rows = s.scalars(select(Project).order_by(Project.id.desc()).limit(300)).all()
        items: list[dict[str, Any]] = []
        for project in rows:
            brief = s.scalar(select(Brief).where(Brief.project_id == project.id))
            first_img = s.scalar(
                select(StyleDirection)
                .where(StyleDirection.project_id == project.id, StyleDirection.image_path.is_not(None))
                .order_by(StyleDirection.seq)
            )
            if brief is None and first_img is None:       # 空方案不返回，免得历史被空壳塞满
                continue
            gates = gate_status(s, project.id)
            items.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "status": project.status,
                    "category": ((brief.parsed or {}).get("category") if brief else "") or "",
                    "progress": gates,
                    "gate_count": sum(1 for v in gates.values() if v),
                    "gate_total": len(gates),
                    "thumbnail": (
                        f"/assets/{project.id}/{first_img.image_path.split('/')[-1]}"
                        if first_img and first_img.image_path
                        else None
                    ),
                    "is_sample": True,
                }
            )
            if len(items) >= limit:
                break
        s.expunge_all()
        return {"items": items, "total_returned": len(items), "is_sample": True}


@app.get("/api/library")
def library_dump() -> Any:
    """素材库（只读浏览）：全部来自示例库，均标 is_sample。"""

    return {
        "materials": all_of("materials"),
        "trims": all_of("trims"),
        "patterns": all_of("patterns"),
        "crafts": all_of("crafts"),
        "sizes": all_of("sizes"),
        "is_sample": True,
        "note": "示例数据 · 仿真实例：接入企业物料库后自动替换",
    }


class MaterialAcceptIn(BaseModel):
    """采纳 AI 面料方案。"""

    direction_id: int
    seq: int


class CraftAcceptIn(BaseModel):
    """采纳 AI 工艺（按名字）。"""

    names: list[str] = []


@app.post("/api/project/{project_id}/materials/generate")
def make_materials_ai(project_id: int, direction_id: int) -> Any:
    """AI 生成 3 个面料/鞋材方案（**非真实物料，价格为估算**）。"""
    try:
        with session() as s:
            from .workflows.generate import generate_materials

            items, provider = generate_materials(s, project_id, direction_id)
            note = (
                "AI 生成的面料方案（非真实物料，价格为估算值）"
                if provider != "tag-fallback"
                else "模型未成功，已回退为按品类的基础面料骨架（离线）"
            )
            return {"items": items, "provider": provider, "is_ai_generated": True, "note": note}
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.get("/api/project/{project_id}/materials/generate")
def read_materials_ai(project_id: int) -> Any:
    """读取已生成的 AI 面料方案（零费用）。"""
    from sqlalchemy import select

    from .models import MaterialSuggestion

    with session() as s:
        rows = s.scalars(
            select(MaterialSuggestion)
            .where(MaterialSuggestion.project_id == project_id)
            .order_by(MaterialSuggestion.seq)
        ).all()
        return {
            "items": [dict(r.payload, seq=r.seq) for r in rows],
            "provider": rows[0].provider if rows else None,
            "is_ai_generated": True,
        }


@app.post("/api/project/{project_id}/material/accept")
def accept_material_ai(project_id: int, payload: MaterialAcceptIn) -> Any:
    """采纳第 seq 个 AI 面料方案。"""
    try:
        with session() as s:
            from .workflows.generate import accept_material

            return accept_material(s, project_id, payload.direction_id, payload.seq)
    except (GateError, LibraryError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


@app.post("/api/project/{project_id}/crafts/generate")
def make_crafts_ai(project_id: int) -> Any:
    """AI 生成 3–5 道工艺建议（**非工厂工价**）。"""
    try:
        with session() as s:
            from .workflows.generate import generate_crafts

            crafts, provider = generate_crafts(s, project_id)
            return {
                "crafts": crafts,
                "provider": provider,
                "is_ai_generated": True,
                "note": "AI 生成的工艺建议（非工厂工价/工时）"
                if provider != "tag-fallback"
                else "模型未成功，已回退为按品类的基础工艺骨架（离线）"
            }
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.post("/api/project/{project_id}/crafts/accept")
def accept_crafts_ai(project_id: int, payload: CraftAcceptIn) -> Any:
    """采纳 AI 工艺（按名字列表）。"""
    try:
        with session() as s:
            from .workflows.generate import accept_crafts

            return accept_crafts(s, project_id, payload.names)
    except (GateError, LibraryError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


class TrimAcceptIn(BaseModel):
    """采纳 AI 辅料建议（按序号）。"""

    seqs: list[int] = []


@app.post("/api/project/{project_id}/trims/generate")
def make_trims_ai(project_id: int) -> Any:
    """AI 生成 3–5 条**按品类**的辅料建议（**非真实采购数据，价格为估算**）。"""
    try:
        with session() as s:
            from .workflows.generate import generate_trims

            items, provider = generate_trims(s, project_id)
            return {
                "items": items,
                "provider": provider,
                "is_ai_generated": True,
                "note": "AI 生成的辅料建议（非真实采购数据，价格为估算值）"
                if provider != "tag-fallback"
                else "模型未成功，已回退为按品类的内置辅料兜底（离线）",
            }
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.get("/api/project/{project_id}/trims/generate")
def read_trims_ai(project_id: int) -> Any:
    """读取已生成的 AI 辅料建议（零费用，刷新回显）。"""
    from sqlalchemy import select as _select

    from .models import TrimPick, TrimSuggestion

    with session() as s:
        rows = s.scalars(
            _select(TrimSuggestion)
            .where(TrimSuggestion.project_id == project_id)
            .order_by(TrimSuggestion.seq)
        ).all()
        pick = s.scalar(_select(TrimPick).where(TrimPick.project_id == project_id))
        accepted = [i.get("seq") for i in ((pick.payload or {}).get("items") or [])] if pick else []
        return {
            "items": [dict(r.payload, seq=r.seq) for r in rows],
            "accepted": accepted,
            "provider": rows[0].provider if rows else None,
            "is_ai_generated": True,
            "note": "AI 生成的辅料建议（非真实采购数据，价格为估算值）",
        }


@app.post("/api/project/{project_id}/trims/accept")
def accept_trims_ai(project_id: int, payload: TrimAcceptIn) -> Any:
    """采纳 AI 辅料建议（按序号列表）。"""
    try:
        with session() as s:
            from .workflows.generate import accept_trims

            return accept_trims(s, project_id, payload.seqs)
    except (GateError, LibraryError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


class PatternAcceptIn(BaseModel):
    """采纳 AI 版型时的工艺选择。"""

    crafts: list[str] = []


@app.post("/api/project/{project_id}/pattern-design")
def make_pattern_design(project_id: int) -> Any:
    """AI 生成版型设计参数（**非工厂纸样**）；每次约 ¥0.01。"""
    try:
        with session() as s:
            from .workflows.pattern import design_pattern

            payload, provider = design_pattern(s, project_id, regenerate=True)
            return {
                "design": payload,
                "provider": provider,
                "note": "AI 生成的版型设计参数（非工厂纸样 / 非打版文件）"
                if provider != "tag-fallback"
                else "模型未成功，已回退为按品类的基础参数（离线骨架）",
            }
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.get("/api/project/{project_id}/pattern-design")
def read_pattern_design(project_id: int) -> Any:
    """读取已生成的版型设计（**不调用模型、零费用**）。"""
    from sqlalchemy import select

    from .models import PatternDesign

    with session() as s:
        row = s.scalar(
            select(PatternDesign)
            .where(PatternDesign.project_id == project_id)
            .order_by(PatternDesign.id.desc())
        )
        if row is None:
            return {"design": None, "provider": None, "is_sample": True}
        return {"design": row.payload, "provider": row.provider, "created_at": row.created_at, "is_sample": True}


@app.post("/api/project/{project_id}/pattern/accept")
def accept_pattern_design(project_id: int, payload: PatternAcceptIn) -> Any:
    """采纳 AI 生成的版型（工艺仍来自示例库），随后请确认门 4。"""
    try:
        with session() as s:
            from .workflows.pattern import accept_design

            return accept_design(s, project_id, payload.crafts)
    except (GateError, LibraryError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


class ScoreIn(BaseModel):
    """人工打分（1–3 分）。"""

    score: int
    note: str = ""


@app.get("/api/project/{project_id}/directions")
def list_directions(project_id: int) -> Any:
    """只读列出已生成的方向（**不重新出图、不花钱**），带人工打分。"""
    from sqlalchemy import select

    from .models import DirectionScore

    with session() as s:
        rows = s.scalars(
            select(StyleDirection).where(StyleDirection.project_id == project_id).order_by(StyleDirection.seq)
        ).all()
        ids = [r.id for r in rows]
        scores: dict[int, int] = {}
        if ids:
            for sc in s.scalars(select(DirectionScore).where(DirectionScore.direction_id.in_(ids))).all():
                scores[sc.direction_id] = sc.score
        return {
            "items": [
                {
                    "id": r.id,
                    "seq": r.seq,
                    "name": r.name,
                    "inspiration": r.inspiration,
                    "palette": r.palette,
                    "silhouette": r.silhouette,
                    "image_url": f"/assets/{project_id}/{r.image_path.split('/')[-1]}" if r.image_path else None,
                    "is_placeholder": bool(r.is_placeholder),
                    "score": scores.get(r.id),
                }
                for r in rows
            ],
            "is_sample": True,
        }


@app.post("/api/direction/{direction_id}/score")
def score_direction(direction_id: int, payload: ScoreIn) -> Any:
    """给某个方向图打 1–3 分（人工评分，用于校准出图质量；可重复打分，后者覆盖）。"""
    from sqlalchemy import select

    from .models import DirectionScore

    if payload.score not in (1, 2, 3):
        return _fail("invalid_request", "打分只能是 1、2 或 3 分。", 409)
    with session() as s:
        direction = s.get(StyleDirection, direction_id)
        if direction is None:
            return _fail("not_found", "找不到这个方向。", 404)
        row = s.scalar(select(DirectionScore).where(DirectionScore.direction_id == direction_id))
        if row is None:
            row = DirectionScore(
                direction_id=direction_id, created_at=time.strftime("%Y-%m-%d %H:%M:%S")
            )
            s.add(row)
        row.score = payload.score
        row.note = (payload.note or "")[:160]
        s.commit()
        return {"direction_id": direction_id, "score": row.score, "message": "已记录你的打分"}


@app.get("/api/scores")
def scores_summary() -> Any:
    """打分汇总（给设置页看）：评了几张、平均分。"""
    from sqlalchemy import func, select

    from .models import DirectionScore

    with session() as s:
        rows = s.scalars(select(DirectionScore)).all()
        avg = round(sum(r.score for r in rows) / len(rows), 2) if rows else None
        counts = dict(s.execute(select(DirectionScore.score, func.count()).group_by(DirectionScore.score)).all())
        return {
            "scored": len(rows),
            "average": avg,
            "by_score": {str(k): v for k, v in counts.items()},
            "is_sample": True,
        }


class DirectionsIn(BaseModel):
    """要生成几个方向（默认 4 个；花钱随数量增加）。"""

    count: int = 4


@app.post("/api/project/{project_id}/directions")
def make_directions(project_id: int, payload: DirectionsIn | None = None) -> Any:
    try:
        with session() as s:
            rows = generate_directions(s, project_id, (payload.count if payload else 4))
            return {
                "items": [
                    {
                        "id": row.id,
                        "seq": row.seq,
                        "name": row.name,
                        "inspiration": row.inspiration,
                        "palette": row.palette,
                        "silhouette": row.silhouette,
                        "image_url": (
                            f"/assets/{project_id}/{row.image_path.split('/')[-1]}"
                            if row.image_path
                            else None
                        ),
                        "image_prompt": row.image_prompt,
                        "image_status": row.image_status,
                        "is_placeholder": bool(row.is_placeholder),
                    }
                    for row in rows
                ],
                "count": len(rows),
                "provider": "mock" if rows and rows[0].image_status == "done" else "unknown",
            }
    except GateError as exc:
        return _fail(exc.code, exc.message, 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.get("/api/project/{project_id}/materials")
def list_materials(project_id: int, direction_id: int) -> Any:
    """**AI 生成**面料方案（不再是示例库挑选）。字段形状与前端兼容：id 用 AI-<seq>。"""
    try:
        with session() as s:
            from .workflows.generate import generate_materials

            items, provider = generate_materials(s, project_id, direction_id)
            shaped = [
                {
                    "id": f"AI-{idx}",
                    "name": it.get("name", ""),
                    "fiber": it.get("fiber", ""),
                    "weight_gsm": it.get("weight_gsm") or 0,
                    "width_cm": it.get("width_cm") or 0,
                    "price_yuan_per_m": it.get("price_yuan_per_m") or [0, 0],
                    "hand": it.get("hand") or [],
                    "season": [],
                    "reason": it.get("why", ""),
                    "is_sample": False,
                    "is_ai_generated": True,
                }
                for idx, it in enumerate(items, start=1)
            ]
            note = (
                "AI 生成的面料方案（非真实物料，价格为估算值）"
                if provider != "tag-fallback"
                else "模型未成功，已回退为按品类的基础面料骨架（离线）"
            )
            return {"items": shaped, "provider": provider, "is_ai_generated": True, "note": note}
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)
    except LlmError as exc:
        return _fail(exc.code, exc.message, 502)


@app.post("/api/project/{project_id}/material")
def choose_material(project_id: int, payload: MaterialIn) -> Any:
    """采纳 AI 面料方案（id 形如 AI-1）；老库 id 仍兼容。"""
    try:
        with session() as s:
            if str(payload.material_id).startswith("AI-"):
                from .workflows.generate import accept_material

                seq = int(str(payload.material_id).split("-")[-1])
                return accept_material(s, project_id, payload.direction_id, seq)
            row = pick_material(s, project_id, payload.direction_id, payload.material_id)
            return {"material_id": row.material_id, "note": row.note}
    except (GateError, LibraryError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


@app.post("/api/project/{project_id}/gate/material")
def gate_material(project_id: int) -> Any:
    try:
        with session() as s:
            confirm_material(s, project_id)
            return {"status": gate_status(s, project_id)}
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.get("/api/project/{project_id}/patterns")
def list_patterns(project_id: int) -> Any:
    """工艺由 AI 生成（id=工艺名）；版型本身已由 /pattern-design 生成，这里 items 仅占位。"""
    try:
        with session() as s:
            from .workflows.generate import generate_crafts

            crafts, provider = generate_crafts(s, project_id)
            shaped = [
                {"id": c.get("name", ""), "name": c.get("name", ""),
                 "note": f'{c.get("how", "")}｜{c.get("why", "")}｜难度{c.get("difficulty", "")}'}
                for c in crafts
            ]
            return {"items": [], "crafts": shaped, "is_sample": False, "is_ai_generated": True,
                    "provider": provider,
                    "note": "AI 生成的工艺建议（非工厂工价/工时）" if provider != "tag-fallback"
                            else "模型未成功，已回退为按品类的基础工艺骨架（离线）"}
    except (GateError, ValueError) as exc:
        return _fail(getattr(exc, "code", "invalid_request"), str(exc), 409)


@app.get("/api/project/{project_id}/craft-suggestions")
def craft_suggestions(project_id: int, direction_id: int) -> Any:
    """AI 建议工艺（3 条，可改）。模型失败时回退并如实标注 provider。"""
    try:
        with session() as s:
            from .workflows.material import suggest_crafts

            items, provider = suggest_crafts(s, project_id, direction_id)
            return {
                "items": items,
                "is_sample": True,
                "provider": provider,
                "note": "AI 建议工艺（可改）" if provider != "tag-fallback"
                        else "模型建议未成功，已回退为按方向标签匹配",
            }
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.post("/api/project/{project_id}/pattern")
def choose_pattern(project_id: int, payload: PatternIn) -> Any:
    try:
        with session() as s:
            row = pick_pattern(s, project_id, payload.pattern_id, payload.crafts)
            return {"pattern_id": row.pattern_id, "crafts": row.crafts, "message": "请确认版型与工艺（门 4）"}
    except (GateError, LibraryError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.post("/api/project/{project_id}/iterate")
def iterate(project_id: int, payload: IterateIn) -> Any:
    try:
        with session() as s:
            return iterate_pattern(s, project_id, payload.changes)
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.post("/api/project/{project_id}/bom")
def bom(project_id: int) -> Any:
    try:
        with session() as s:
            return {"bom": build_bom(s, project_id), "size_spec": compute_size_spec(s, project_id)}
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.post("/api/project/{project_id}/sampling")
def sampling_order(project_id: int) -> Any:
    try:
        with session() as s:
            return create_sampling_order(s, project_id)
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.post("/api/project/{project_id}/sampling/advance")
def sampling_advance(project_id: int, payload: SamplingIn) -> Any:
    try:
        with session() as s:
            return advance_sampling(s, project_id, payload.to_state)
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.get("/api/project/{project_id}/state")
def snapshot(project_id: int) -> Any:
    from sqlalchemy import select

    from .models import StyleDirection

    with session() as s:
        project = s.get(Project, project_id)
        if project is None:
            return _fail("project_not_found", "找不到这个项目。", 404)
        brief = s.scalar(select(Brief).where(Brief.project_id == project_id))
        picks = s.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
        pattern = s.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
        sampling = s.scalar(select(Sampling).where(Sampling.project_id == project_id))
        iterations = s.scalars(
            select(PatternIteration).where(PatternIteration.project_id == project_id).order_by(PatternIteration.id)
        ).all()
        from sqlalchemy import select

        _sel = s.scalar(
            select(StyleDirection).where(
                StyleDirection.project_id == project_id, StyleDirection.selected == 1
            )
        )
        return {
            "direction_id": _sel.id if _sel else None,
            "project": {"id": project.id, "name": project.name, "status": project.status},
            "gates": gate_status(s, project_id),
            "brief": {"text": brief.raw_text, "parsed": brief.parsed, "confirmed": bool(brief.confirmed_at)}
            if brief
            else None,
            "material": {"id": picks.material_id, "note": picks.note} if picks else None,
            "pattern": {"id": pattern.pattern_id, "crafts": pattern.crafts} if pattern else None,
            "iterations": [{"no": row.iteration_no, "changes": row.changes} for row in iterations],
            "sampling": {"state": sampling.state} if sampling else None,
        }


@app.get("/assets/{project_id}/{name}")
def asset(project_id: int, name: str) -> Any:
    from .config import get_config

    path = get_config().assets_dir / str(project_id) / name
    if not path.exists() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return _fail("asset_not_found", "找不到这张图。", 404)
    return FileResponse(path)


class ExportOut(BaseModel):
    word: str
    excel: str


@app.post("/api/project/{project_id}/export")
def export_files(project_id: int) -> Any:
    """导出 Word 方案 + Excel（BOM/尺寸表/打样单）。文件里带上示例库与 AI 生成口径。"""
    from .export import export_excel, export_word

    try:
        with session() as s:
            word = export_word(s, project_id)
            excel = export_excel(s, project_id)
            return {"word": word.name, "excel": excel.name,
                    "download": {"word": f"/files/{word.name}", "excel": f"/files/{excel.name}"}}
    except (GateError, ValueError) as exc:
        code = getattr(exc, "code", "invalid_request")
        return _fail(code, str(exc), 409)


@app.get("/files/{name}")
def download(name: str) -> Any:
    from .config import get_config

    path = get_config().assets_dir.parent / "exports" / Path(name).name
    if not path.exists():
        return _fail("file_not_found", "找不到这个文件。", 404)
    return FileResponse(path)


@app.get("/ui")
def ui() -> FileResponse:
    """最小验收页面（单页，原生 JS）。界面上常驻"示例库"标识。"""
    path = Path(__file__).resolve().parents[1] / "static" / "index.html"
    return FileResponse(path)


@app.get("/")
def index() -> FileResponse:
    """根路径直接给可操作界面（与 /ui 同一份页面）。"""
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "index.html")
