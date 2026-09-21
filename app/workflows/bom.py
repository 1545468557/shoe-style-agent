"""版型迭代 + BOM/尺寸表 + 打样单（主链路第五步及收尾）。

口径：
- **尺寸表由代码算**（不交给模型编数字）：基础尺码（示例库）+ 版型松量 + 本次迭代改动；
- **成本只给区间并标注"估算值"**（示例库价格区间 × 估算用量 + 辅料 + 工艺加成），不报"准价"；
- **打样单**是"仿真流程"的交付物：写明"预留标准对接位"，不得宣称已对接真实工厂系统。
"""

from __future__ import annotations

import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..gates import confirm, require_for_export, require_for_sampling
from ..library import LibraryError, all_of, find
from ..models import (
    MaterialPick,
    PatternDesign,
    PatternIteration,
    PatternPick,
    Project,
    Sampling,
    StyleDirection,
)

#: 允许迭代的参数及其单位（**白名单**：不认识的参数一律拒绝，避免模型乱改）
ITERABLE = {
    "skirt_length_cm": ("裙长", "cm"),
    "waist_ease_cm": ("腰围放松量", "cm"),
    "hip_ease_cm": ("臀围放松量", "cm"),
    "sleeve_length_cm": ("袖长", "cm"),
}


def _base_sizes() -> dict:
    """基础尺码（示例库第一条作基准模特，真实库接入后按选码规则替换）。"""
    return all_of("sizes")[0]


def compute_size_spec(session: Session, project_id: int) -> dict:
    """尺寸表：基础尺码 + 版型松量 + 累计迭代改动（**代码计算，可复算**）。"""
    pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    if pick is None:
        raise ValueError("还没有选定版型")
    design = session.scalar(
        select(PatternDesign).where(PatternDesign.project_id == project_id).order_by(PatternDesign.id.desc())
    )
    payload = (design.payload if design else {}) or {}
    ease = payload.get("ease") or {}
    category = str(payload.get("category") or "")
    try:
        pattern = find("patterns", pick.pattern_id)
        base = _base_sizes()
        is_ai = False
    except LibraryError:                                  # AI 生成的版型名不在示例库里
        is_ai = True
        base = {}
        pattern = {
            "name": pick.pattern_id or "AI 版型",
            "waist_ease_cm": float(ease.get("腰围", 4) or 4),
            "hip_ease_cm": float(ease.get("臀围", 6) or 6),
            "length_options_cm": [int(payload.get("length_cm") or 100)],
        }
    iterations = session.scalars(
        select(PatternIteration)
        .where(PatternIteration.project_id == project_id)
        .order_by(PatternIteration.iteration_no)
    ).all()
    delta: dict[str, float] = {}
    for row in iterations:
        for key, value in (row.changes or {}).items():
            delta[key] = delta.get(key, 0) + float(value)

    # 鞋类：按鞋码/围度给表；服装：按胸腰臀/长度给表（**都由代码算，估算值**）
    if any(word in category for word in ("鞋", "靴", "跟", "凉", "拖")):
        ball = round(float(ease.get("跖围", 220) or 220) + delta.get("waist_ease_cm", 0), 1)
        shaft = round(float(payload.get("length_cm") or 250) + delta.get("skirt_length_cm", 0), 1)
        return {
            "system": "鞋码（基础码 230，档差 5mm）",
            "unit": "mm",
            "tiers": [
                {"size": f"{code}", "脚长": code, "跖围": round(ball + (code - 235) * 0.4, 1), "筒高/鞋帮高": shaft}
                for code in (225, 230, 235, 240, 245)
            ],
            "labels": {"bust": "鞋码", "waist": "脚长", "hip": "跖围", "skirt_length": "筒高/鞋帮高"},
            "bust": 235,
            "waist": 235,
            "hip": round(float(ease.get("跖围", 220) or 220) + delta.get("waist_ease_cm", 0), 1),
            "skirt_length": round(float(payload.get("length_cm") or 250) + delta.get("skirt_length_cm", 0), 1),
            "sleeve_length": 0,
            "pattern": pattern["name"],
            "iterations": len(iterations),
            "note": "尺寸由代码按「鞋码 + 版型围度参数 + 迭代改动」计算，**估算值**，打样前请版师复核",
        }

    if is_ai:
        bust = float(ease.get("胸围", 96) or 96)
        waist = float(ease.get("腰围", 74) or 74) + delta.get("waist_ease_cm", 0)
        hip = float(ease.get("臀围", 98) or 98)
        length = float(payload.get("length_cm") or 100) + delta.get("skirt_length_cm", 0)
        sleeve = float(ease.get("袖长", 0) or 0)
        system = "AI 生成版型（160/84A 基准）"
    else:
        bust = float(base["bust"])
        waist = float(base["waist"]) + float(pattern["waist_ease_cm"]) + delta.get("waist_ease_cm", 0)
        hip = float(base["hip"]) + float(pattern["hip_ease_cm"]) + delta.get("hip_ease_cm", 0)
        length = float(pattern["length_options_cm"][-1]) + delta.get("skirt_length_cm", 0)
        sleeve = float(base["sleeve"]) + delta.get("sleeve_length_cm", 0)
        system = base["system"]
    tiers = []
    for idx, code in enumerate(("S", "M", "L", "XL")):
        step = idx - 1
        tiers.append({
            "size": code,
            "胸围": round(bust + step * 4, 1),
            "腰围": round(waist + step * 4, 1),
            "臀围": round(hip + step * 4, 1),
            "衣长/裙长": round(length + step * 2, 1),
        })
    return {
        "system": system,
        "unit": "cm",
        "tiers": tiers,
        "labels": {"bust": "胸围", "waist": "腰围", "hip": "臀围", "skirt_length": "衣长/裙长"},
        "bust": round(bust, 1),
        "waist": round(waist, 1),
        "hip": round(hip, 1),
        "skirt_length": round(length, 1),
        "sleeve_length": round(sleeve, 1),
        "pattern": pattern["name"],
        "iterations": len(iterations),
        "note": "尺寸由代码按「基础尺码 + 版型松量 + 迭代改动」计算，**估算值**，打样前请版师复核",
    }


def iterate_pattern(session: Session, project_id: int, changes: dict[str, float]) -> dict:
    """版型迭代：改白名单参数 → 记录版本 → 重算尺寸表（返回"改了什么、影响什么"）。"""
    require_for_sampling(session, project_id)          # 前四道门都必须已过
    if not changes:
        raise ValueError("没有给出要改的参数")
    unknown = [key for key in changes if key not in ITERABLE]
    if unknown:
        raise ValueError(f"不支持改这些参数：{unknown}（只允许 {list(ITERABLE)}）")

    before = compute_size_spec(session, project_id)
    last = session.scalars(
        select(PatternIteration)
        .where(PatternIteration.project_id == project_id)
        .order_by(PatternIteration.iteration_no.desc())
    ).first()
    number = (last.iteration_no if last else 0) + 1
    row = PatternIteration(project_id=project_id, iteration_no=number, changes=dict(changes))
    session.add(row)
    session.commit()
    after = compute_size_spec(session, project_id)
    row.size_spec = after
    session.commit()

    effects = []
    for key, value in changes.items():
        label, unit = ITERABLE[key]
        if key in before and key in after:
            effects.append(f"{label} {value:+g}{unit} → 成品 {after[key]}{unit}（原 {before[key]}{unit}）")
        else:
            effects.append(f"{label} {value:+g}{unit}")
    return {"iteration_no": number, "changes": dict(changes), "effects": effects, "size_spec": after}


def _cost_note(session: Session, project_id: int, low: float, high: float) -> str:
    """成本说明：AI 估算；若明显高于企划售价，明确提示不匹配（避免出现"售价 40、成本 120"这种荒唐结果）。"""
    from ..models import Brief

    base = "**估算值**：用量按版型简化估算，价格为 AI 生成的面料估算区间；正式核价请由采购确认"
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    band = str(((brief.parsed if brief else {}) or {}).get("price_band") or "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", band)]
    if nums and low > max(nums):
        return base + f"｜⚠️ 成本估算（{low:.0f}–{high:.0f} 元）**高于企划售价（{band}）**：可能是 AI 面料单价的计价单位（元/米 vs 元/尺或元/双）与实际不符，请核对"
    return base


def build_bom(session: Session, project_id: int) -> dict:
    """BOM 和成本估算：面料用量按版型估算，价格取示例库区间（**明确标注估算**）。"""
    require_for_export(session, project_id)            # 门 1–4
    material_pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    pattern_pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    assert material_pick and pattern_pick
    try:

        material = find("materials", material_pick.material_id)

    except LibraryError:                     # AI 生成的面料（id 形如 AI-1）

        from sqlalchemy import select as _select

        from ..models import MaterialSuggestion


        seq = int(str(material_pick.material_id).split("-")[-1])

        sug = session.scalar(

            _select(MaterialSuggestion).where(

                MaterialSuggestion.project_id == project_id, MaterialSuggestion.seq == seq

            )

        )

        payload = (sug.payload if sug else {}) or {}

        material = {

            "id": material_pick.material_id,

            "name": payload.get("name") or "AI 面料",

            "fiber": payload.get("fiber") or "",

            "price_yuan_per_m": payload.get("price_yuan_per_m") or [0, 0],

        }
    try:
        pattern = find("patterns", pattern_pick.pattern_id)
    except LibraryError:                          # AI 生成的版型（名字不在示例库）
        design_row = session.scalar(
            select(PatternDesign).where(PatternDesign.project_id == project_id).order_by(PatternDesign.id.desc())
        )
        d = (design_row.payload if design_row else {}) or {}
        ease = d.get("ease") or {}
        pattern = {
            "id": pattern_pick.pattern_id,
            "name": pattern_pick.pattern_id or "AI 版型",
            "waist_ease_cm": float(ease.get("腰围", 4) or 4),
            "hip_ease_cm": float(ease.get("臀围", 6) or 6),
            "length_options_cm": [int(d.get("length_cm") or 100)],
        }
    spec = compute_size_spec(session, project_id)

    # 简化用量估算：裙长 × 幅宽系数（示例算法，标注估算）
    # 用料：服装按长度估算；**鞋类按每双的鞋面/内里用量估**（不再用"长度×1.6"这套女装算法）
    if any(w in str(spec.get("system", "")) + str(material.get("name", "")) for w in ("鞋", "靴", "跟", "凉", "拖")):
        fabric_meters = 0.35
    else:
        fabric_meters = round((float(spec["skirt_length"]) / 100) * 1.6, 2)
    low, high = material["price_yuan_per_m"]
    fabric_cost = (round(low * fabric_meters), round(high * fabric_meters))

    trims = []
    trims_cost = 0.0
    trims_cost_high = 0.0
    for trim in all_of("trims"):
        if "price_yuan_per_m" in trim:
            low_p, high_p = trim["price_yuan_per_m"]
        else:
            low_p, high_p = trim["price_yuan_per_pc"]
        trims.append({"id": trim["id"], "name": trim["name"], "spec": trim["spec"], "price": [low_p, high_p]})
        trims_cost += low_p
        trims_cost_high += high_p

    craft_fee = (8 if len(pattern_pick.crafts) >= 3 else 5) * len(pattern_pick.crafts)
    low_total = round(fabric_cost[0] + trims_cost + craft_fee)
    high_total = round(fabric_cost[1] + trims_cost_high + craft_fee)
    return {
        "material": {"id": material["id"], "name": material["name"], "fiber": material["fiber"],
                     "is_sample": True},
        "fabric_meters": fabric_meters,
        "items": [
            {"kind": "版型", "name": pattern["name"], "qty": f"腰围松量 {pattern['waist_ease_cm']}cm",
             "price_range": [0, 0], "subtotal": [0, 0]},
            {"kind": "面料", "name": material["name"], "qty": f"{fabric_meters} 米",
             "price_range": [low, high], "subtotal": list(fabric_cost)},
            *[{"kind": "辅料", "name": t["name"], "qty": t["spec"], "price_range": t["price"],
               "subtotal": [t["price"][0] * 1, t["price"][1] * 1]} for t in trims],
            {"kind": "工艺", "name": f"{len(pattern_pick.crafts)} 道工艺", "qty": "按道计",
             "price_range": [craft_fee, craft_fee], "subtotal": [craft_fee, craft_fee]},
        ],
        "estimated_cost_yuan": [low_total, high_total],
        "note": _cost_note(session, project_id, low_total, high_total),
        "sample_library": True,
    }


def create_sampling_order(session: Session, project_id: int) -> dict:
    """生成打样单（仿真流程 + 预留标准对接位）。"""
    require_for_sampling(session, project_id)
    material_pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    pattern_pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    direction = session.get(StyleDirection, material_pick.direction_id) if material_pick else None
    order = {
        "project_id": project_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "direction": {"name": direction.name if direction else "", "image": direction.image_path if direction else ""},
        "material": material_pick.note if material_pick else "",
        "pattern": pattern_pick.pattern_id if pattern_pick else "",
        "crafts": pattern_pick.crafts if pattern_pick else [],
        "size_spec": compute_size_spec(session, project_id),
        "bom": build_bom(session, project_id),
        "state": "draft",
        "note": (
            "**仿真打样流程**：本单不接入真实工厂/PLM 系统，"
            "已在数据层预留标准对接位（订单号/状态/回传字段）"
        ),
    }
    row = session.scalar(select(Sampling).where(Sampling.project_id == project_id))
    if row is None:
        row = Sampling(project_id=project_id, state="draft")
        session.add(row)
    row.state = "draft"
    row.note = order["note"]
    row.updated_at = order["created_at"]
    session.commit()
    return order


def advance_sampling(session: Session, project_id: int, to_state: str) -> dict:
    """推进打样状态（草稿 → 审批 → 已推送 → 打样中 → 评审）。**approve 走门 5**。"""
    allowed = {"approved", "pushed", "sampling", "reviewed"}
    if to_state not in allowed:
        raise ValueError(f"不支持的状态：{to_state}")
    if to_state == "approved":
        confirm(session, project_id, "sampling")        # 门 5
    require_for_sampling(session, project_id)
    row = session.scalar(select(Sampling).where(Sampling.project_id == project_id))
    if row is None:
        raise ValueError("还没有生成打样单")
    row.state = to_state
    row.updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    project = session.get(Project, project_id)
    if project is not None:
        project.status = f"sampling_{to_state}"
    session.commit()
    return {"state": row.state, "updated_at": row.updated_at}
