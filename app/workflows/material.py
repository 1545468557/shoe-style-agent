"""面料搭配（主链路第三步）：从示例库推荐 → 用户确认（门 3）。

硬规矩：
- 推荐结果**只能来自示例库**（返回条目里带 `is_sample=True` 与库内 id）；
- 模型若给出库里不存在的面料 id → **拒绝**（`library_item_not_found`），不允许凭空编面料；
- 确认面料（门 3）后写入 `MaterialPick.confirmed_at`，才算过门。
"""

from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..gates import require_for_materials, require_for_pattern
from ..library import LibraryError, all_of, find
from ..models import MaterialPick, PatternPick, Project, StyleDirection

SEASON_HINTS = {"春": "spring", "夏": "summer", "秋": "autumn", "冬": "winter"}


SMART_SYSTEM = (
    "你是女装面料搭配师。只能从给定的候选面料里挑，挑 3 条最合适的并说明理由（每条一句话，"
    '说清为什么适合这个方向：克重/成分/手感/价格/场景）。只输出 JSON：'
    '{"choices":[{"material_id":"候选里的 id","reason":""}]}。禁止编造候选之外的面料。'
)


def recommend_materials_smart(session: Session, project_id: int, direction_id: int) -> tuple[list[dict], str]:
    """**模型搭配**：把候选清单给模型，它挑 3 条并给理由；库外 id 一律拒。

    返回 (条目列表, provider)。provider ∈ {deepseek 模型名, "tag-fallback"}：
    模型失败时回退到"按标签排序"，并如实标注，**不假装是模型搭配**。
    """
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import MaterialChoiceSet

    candidates = recommend_materials(session, project_id, direction_id, limit=12)   # 先按标签粗筛 12 条
    direction = session.get(StyleDirection, direction_id)
    if get_config().is_mock_model:
        for item in candidates[:3]:
            item["reason"] = f"（离线示例）按方向「{direction.name if direction else ''}」的风格标签匹配"
        return candidates[:3], "tag-fallback"

    lines = [
        f'- id={c["id"]}｜{c["name"]}｜{c["fiber"]}｜{c["weight_gsm"]}g/m²｜'
        f'{c["price_yuan_per_m"][0]}~{c["price_yuan_per_m"][1]} 元/米｜季节{"/".join(c["season"])}｜'
        f'手感{"/".join(c["hand"])}｜标签{"/".join(c["tags"])}'
        for c in candidates
    ]
    head = (
        f"方向：{direction.name if direction else ''}｜"
        f"廓形：{direction.silhouette if direction else ''}\n候选面料：\n"
    )
    user = head + "\n".join(lines)
    try:
        chosen, provider = complete_json(SMART_SYSTEM, user, MaterialChoiceSet)
        index = {c["id"]: c for c in candidates}
        picked: list[dict] = []
        for choice in chosen.choices:
            if choice.material_id not in index:      # 模型编了库外面料 → 拒绝
                raise LibraryError("library_item_not_found", f"模型挑了库外面料：{choice.material_id}")
            picked.append(dict(index[choice.material_id], is_sample=True, reason=choice.reason))
        if not picked:
            raise LibraryError("library_item_not_found", "模型没有给出可用面料")
        return picked, provider
    except (LlmError, LibraryError):
        for item in candidates[:3]:
            item["reason"] = "（模型搭配失败，已回退为按风格标签匹配）"
        return candidates[:3], "tag-fallback"


def recommend_materials(session: Session, project_id: int, direction_id: int, limit: int = 4) -> list[dict]:
    """按方向推荐面料：**只是从示例库里排序挑选**，不生成新材料。"""
    require_for_materials(session, project_id)              # 门 1、2 必须先过
    direction = session.get(StyleDirection, direction_id)
    if direction is None or direction.project_id != project_id:
        raise ValueError("这个风格方向不属于当前项目")

    text = f"{direction.name}{direction.inspiration}{direction.silhouette}"
    scored: list[tuple[int, dict]] = []
    for material in all_of("materials"):
        score = 0
        for tag in material.get("tags", []):
            if tag in text:
                score += 3
        for season in material.get("season", []):
            if season in text:
                score += 2
        for hand in material.get("hand", []):
            if hand in text:
                score += 1
        scored.append((score, material))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [dict(material, is_sample=True) for _, material in scored[:limit]]


def pick_material(
    session: Session, project_id: int, direction_id: int, material_id: str, note: str = ""
) -> MaterialPick:
    """选定面料（**必须来自示例库**）。"""
    require_for_materials(session, project_id)
    material = find("materials", material_id)               # 库外 id → 抛错，不编
    pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    if pick is None:
        pick = MaterialPick(project_id=project_id, direction_id=direction_id, material_id=material["id"])
        session.add(pick)
    else:
        pick.direction_id = direction_id
        pick.material_id = material["id"]
    pick.note = note or f"{material['name']}（{material['fiber']}，{material['weight_gsm']}g/m²）"
    pick.confirmed_at = None                                 # 换了面料要重新确认
    session.commit()
    return pick


def confirm_material(session: Session, project_id: int) -> MaterialPick:
    """确认面料（**门 3**）。"""
    pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    if pick is None:
        raise ValueError("还没有选定面料")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    from ..gates import confirm

    confirm(session, project_id, "material")                 # 门顺序与状态由 gates 统一管
    pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    assert pick is not None
    pick.confirmed_at = pick.confirmed_at or now
    project = session.get(Project, project_id)
    if project is not None:
        project.status = "material_confirmed"
    session.commit()
    return pick


def recommend_patterns(session: Session, project_id: int, limit: int = 3) -> list[dict]:
    """版型推荐：必须已过门 1/2/3。"""
    require_for_pattern(session, project_id)
    pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    direction = session.get(StyleDirection, pick.direction_id) if pick else None
    text = f"{direction.name if direction else ''}{direction.silhouette if direction else ''}"
    scored: list[tuple[int, dict]] = []
    for pattern in all_of("patterns"):
        score = sum(3 for tag in pattern.get("tags", []) if tag in text)
        scored.append((score, pattern))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return [dict(pattern, is_sample=True) for _, pattern in scored[:limit]]


def pick_pattern(session: Session, project_id: int, pattern_id: str, crafts: list[str]) -> PatternPick:
    """选定版型与工艺（版型/工艺都必须来自示例库）。"""
    require_for_pattern(session, project_id)
    pattern = find("patterns", pattern_id)
    for craft_id in crafts:
        find("crafts", craft_id)                             # 库外工艺 id → 抛错
    row = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    if row is None:
        row = PatternPick(project_id=project_id, pattern_id=pattern["id"], crafts=list(crafts))
        session.add(row)
    else:
        row.pattern_id = pattern["id"]
        row.crafts = list(crafts)
    row.confirmed_at = None
    session.commit()
    return row


CRAFT_SYSTEM = (
    "你是女装工艺师。只能从给定候选工艺里挑，挑 3 条最值得做的并说明理由（每条一句话，"
    '说清为什么适合这个方向：廓形/面料/穿着场景）。只输出 JSON：'
    '{"crafts":[{"craft_id":"候选里的 id","reason":""}]}。禁止编造候选之外的工艺。'
)


def suggest_crafts(
    session: Session, project_id: int, direction_id: int, limit: int = 3
) -> tuple[list[dict], str]:
    """**模型建议工艺**：从示例库候选里挑并给理由，供界面默认勾选（可改）。

    provider ∈ {模型名, "tag-fallback"}；模型失败时回退并按标签排序，**如实标注**不假装。
    """
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import CraftChoiceSet

    require_for_pattern(session, project_id)          # 门 1、2 必须先过
    direction = session.get(StyleDirection, direction_id)
    if direction is None or direction.project_id != project_id:
        raise LibraryError("library_item_not_found", "方向不存在")
    candidates = [dict(c, is_sample=True) for c in all_of("crafts")]
    if get_config().is_mock_model:
        for item in candidates[:limit]:
            item["reason"] = f"（离线示例）按方向「{direction.name}」的风格标签匹配"
        return candidates[:limit], "tag-fallback"

    lines = [f'- id={c["id"]}｜{c["name"]}｜{c.get("note", "")}' for c in candidates]
    user = f"方向：{direction.name}｜廓形：{direction.silhouette}\n候选工艺：\n" + "\n".join(lines)
    try:
        chosen, provider = complete_json(CRAFT_SYSTEM, user, CraftChoiceSet)
        index = {c["id"]: c for c in candidates}
        picked: list[dict] = []
        for choice in chosen.crafts:
            if choice.craft_id not in index:           # 模型编了库外工艺 → 拒绝
                raise LibraryError("library_item_not_found", f"模型挑了库外工艺：{choice.craft_id}")
            picked.append(dict(index[choice.craft_id], reason=choice.reason))
        if not picked:
            raise LibraryError("library_item_not_found", "模型没有给出可用工艺")
        return picked[:limit], provider
    except (LlmError, LibraryError):
        for item in candidates[:limit]:
            item["reason"] = "（模型建议失败，已回退为按方向标签匹配）"
        return candidates[:limit], "tag-fallback"
