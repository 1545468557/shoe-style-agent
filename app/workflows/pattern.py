"""版型设计（AI 生成）：按品类/方向/面料生成版型设计参数。

产品经理定调（2026-09-21）：**版型不建库，由模型生成**；面料先查库、库外由模型给规格建议。
口径红线：输出是**设计参数与结构说明**，不是工厂纸样 / CAD / DXF 文件。
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..gates import require_for_pattern
from ..models import (
    Brief,
    CraftSuggestion,
    MaterialPick,
    PatternDesign,
    PatternPick,
    StyleDirection,
)

PATTERN_SYSTEM = (
    "你是资深服装/鞋类版型师。请依据【品类、风格方向、已选面料】设计这个款式的版型参数，"
    "只输出 JSON：{\"category\":\"\",\"name\":\"\",\"silhouette\":\"\","
    "\"ease\":{\"胸围\":0},\"structure_lines\":[\"\"],\"collar\":\"\",\"sleeve\":\"\","
    "\"hem\":\"\",\"length_cm\":0,\"size_base\":\"\",\"reason\":\"\"}。"
    "规则：① 必须与品类严格一致（连衣裙就给裙装参数；羽绒服给充绒/防钻绒/连帽等；"
    "裤子给腰头/裆部/裤型；**鞋子给鞋型/楦型/跟高/鞋头围等，collar 与 sleeve 留空**）；"
    "② ease 的键名按品类自定，数值单位 cm；③ structure_lines 写 2–4 条结构线或部件；"
    "④ size_base 写放码基准（基础码 + 档差）；⑤ reason 一句话说清为什么这样设计；"
    "⑥ 不得出现任何品牌名、商标或面料牌号。"
)


def _mock_design(category: str, direction: StyleDirection | None) -> dict:
    """离线/兜底：给一份与品类对应的骨架参数（**如实标注 tag-fallback**）。"""
    name = (direction.name if direction else "") or "基础"
    if any(word in category for word in ("鞋", "靴", "凉拖", "高跟", "运动鞋")):
        return {
            "category": category, "name": f"{name}鞋型", "silhouette": "鞋型按脚型包覆",
            "ease": {"跟高": 6.0, "鞋头围": 21.0, "后跟围": 25.0},
            "structure_lines": ["鞋面拼接", "内里与鞋垫", "鞋底与中底"],
            "collar": "", "sleeve": "", "hem": "鞋口收边", "length_cm": None,
            "size_base": "女鞋 235 码为基准，档差 5mm", "reason": "按品类给鞋类基础参数（离线）",
        }
    if "羽绒" in category:
        return {
            "category": category, "name": f"{name}羽绒版型", "silhouette": "H 型微廓",
            "ease": {"胸围": 20.0, "腰围": 16.0, "臀围": 12.0, "肩宽": 1.5},
            "structure_lines": ["防钻绒胆布与面布分离", "横向分格充绒", "下摆抽绳"],
            "collar": "连帽", "sleeve": "插肩袖", "hem": "抽绳下摆", "length_cm": 60,
            "size_base": "160/84A 为基准，档差 4cm", "reason": "羽绒需充绒空间与防钻绒结构（离线）",
        }
    return {
        "category": category, "name": f"{name}版型", "silhouette": "X 型收腰",
        "ease": {"胸围": 6.0, "腰围": 4.0, "臀围": 6.0, "肩宽": 0.5},
        "structure_lines": ["前中分割线", "腰部省道", "侧缝"],
        "collar": "方领", "sleeve": "正肩短袖", "hem": "直下摆", "length_cm": 105,
        "size_base": "160/84A 为基准，档差 4cm", "reason": "按品类给基础版型参数（离线）",
    }


def design_pattern(session: Session, project_id: int, regenerate: bool = True) -> tuple[dict, str]:
    """生成（或读取）版型设计参数。provider ∈ {模型名, "tag-fallback"}。"""
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import PatternDesignOut

    require_for_pattern(session, project_id)          # 门 1、2、3 必须先过
    old = session.scalar(
        select(PatternDesign).where(PatternDesign.project_id == project_id).order_by(PatternDesign.id.desc())
    )
    if old is not None and not regenerate:
        return old.payload, old.provider

    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    parsed = (brief.parsed if brief else {}) or {}
    category = parsed.get("category") or "女装"
    material_pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    direction = session.get(StyleDirection, material_pick.direction_id) if material_pick else None
    material = (material_pick.note if material_pick else "") or "（未指定）"

    if get_config().is_mock_model:
        payload, provider = _mock_design(category, direction), "tag-fallback"
    else:
        user = (
            f"品类：{category}\n"
            f"其他企划要素：风格{'、'.join(parsed.get('style_keywords') or [])}；"
            f"人群{parsed.get('target_user') or '未提供'}；价格带{parsed.get('price_band') or '未提供'}；"
            f"补充说明{parsed.get('extra_notes') or '无'}\n"
            f"风格方向：{direction.name if direction else ''}｜{direction.silhouette if direction else ''}｜"
            f"{direction.inspiration if direction else ''}\n"
            f"已选面料：{material}"
        )
        try:
            out, provider = complete_json(PATTERN_SYSTEM, user, PatternDesignOut)
            payload = out.model_dump()
            if not payload.get("category"):
                payload["category"] = category
        except (LlmError, ValueError):
            payload, provider = _mock_design(category, direction), "tag-fallback"

    row = PatternDesign(
        project_id=project_id,
        payload=payload,
        provider=provider,
        created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    session.add(row)
    session.commit()
    return payload, provider


def accept_design(session: Session, project_id: int, crafts: list[str]) -> dict:
    """采纳 AI 生成的版型（写入版型选择，供门 4 确认）。"""
    require_for_pattern(session, project_id)
    allowed: set[str] = set()
    craft_row = session.scalar(
        select(CraftSuggestion).where(CraftSuggestion.project_id == project_id).order_by(CraftSuggestion.id.desc())
    )
    if craft_row is not None:
        allowed.update(c["name"] for c in (craft_row.payload.get("crafts") or []))
    from ..library import all_of as _all

    allowed.update(c["id"] for c in _all("crafts"))
    design = session.scalar(
        select(PatternDesign).where(PatternDesign.project_id == project_id).order_by(PatternDesign.id.desc())
    )
    if design is None:
        raise ValueError("还没有生成版型设计，请先点「AI 设计版型」")
    for craft_id in crafts:                       # AI 工艺按名字；示例库 id 也兼容
        if craft_id not in allowed:
            raise ValueError(f"不认识这道工艺：{craft_id}")
    name = design.payload.get("name") or "AI 版型设计"

    pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    if pick is None:
        pick = PatternPick(project_id=project_id, pattern_id=name, crafts=list(crafts))
        session.add(pick)
    pick.pattern_id = name
    pick.crafts = list(crafts)
    session.commit()
    return {"pattern_id": name, "crafts": pick.crafts}
