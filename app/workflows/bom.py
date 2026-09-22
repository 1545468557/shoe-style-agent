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

from ..category import is_boot, is_shoe
from ..gates import confirm, require_for_export, require_for_sampling
from ..library import LibraryError, all_of, find
from ..models import (
    Brief,
    MaterialPick,
    PatternDesign,
    PatternIteration,
    PatternPick,
    Project,
    Sampling,
    StyleDirection,
    TrimPick,
)

#: 长度部位的叫法：裙装→裙长；裤装→裤长；其余→衣长（不把"裙长"硬塞给裤子/上衣）
_LENGTH_LABEL = (("裙", "裙长"), ("裤", "裤长"))


def length_label(category_text: str) -> str:
    for word, label in _LENGTH_LABEL:
        if word in category_text:
            return label
    return "衣长"

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


def _shaft_mm(payload: dict) -> float | None:
    """靴类筒高（mm）：优先取版型里显式给的值；否则按 length_cm × 10 换算。

    **都没有就不输出这一行**——不拿一个不相干的数字硬填（凉鞋出现"筒高 28mm"就是这么来的）。
    """
    explicit = payload.get("shaft_height_mm") or payload.get("筒高")
    if explicit not in (None, ""):
        try:
            return round(float(explicit), 1)
        except (TypeError, ValueError):
            return None
    try:
        value = float(payload.get("length_cm"))          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return round(value * 10, 1) if value >= 15 else None   # 小于 15cm 不是筒高，宁可不显示


def pattern_line(category_text: str, spec: dict, payload: dict) -> str:
    """用料清单里"版型"那一行的说明：**按品类**写（鞋类不写"腰围松量"）。"""
    if is_shoe(category_text):
        parts = []
        for row in spec.get("rows", []):
            if row["key"] == "girth":
                parts.append(f"跖围 {row['value']}mm")
            elif row["key"] == "shaft":
                parts.append(f"筒高 {row['value']}mm")
        return "｜".join(parts) or "鞋型参数（估算）"
    ease = payload.get("ease") or {}
    parts = []
    for key, label in (("胸围", "胸围松量"), ("腰围", "腰围松量"), ("臀围", "臀围松量")):
        if ease.get(key) is not None:
            parts.append(f"{label} {float(ease[key]):g}cm")
    return "｜".join(parts) or "按基础版型（估算）"


def _fallback_trims(category_text: str) -> list[dict]:
    """按品类的**内置兜底辅料**（不调模型、零费用）：鞋类不会拿到细扣/鱼骨这类女装辅料。"""
    if is_shoe(category_text):
        items = [
            {"id": "XT1", "name": "鞋用内里", "spec": "猪皮/网布内里", "use": "贴脚内里",
             "unit": "元/双", "price": [6, 12]},
            {"id": "XT2", "name": "成型鞋垫", "spec": "EVA 成型鞋垫", "use": "缓震与脚感",
             "unit": "元/双", "price": [3, 6]},
            {"id": "XT3", "name": "橡胶大底", "spec": "耐磨防滑纹路", "use": "外底",
             "unit": "元/双", "price": [12, 22]},
            {"id": "XT4", "name": "中底/成型衬", "spec": "成型中底", "use": "支撑与定型",
             "unit": "元/双", "price": [5, 9]},
        ]
        if is_boot(category_text):
            items.append({"id": "XT5", "name": "靴筒拉链", "spec": "5号尼龙拉链", "use": "靴筒穿脱",
                          "unit": "元/双", "price": [3, 6]})
        return items
    return [
        {"id": "XT1", "name": "里布", "spec": "涤纶/醋酸里布", "use": "里衬防透",
         "unit": "元/米", "price": [6, 14]},
        {"id": "XT2", "name": "拉链", "spec": "3号尼龙/金属拉链", "use": "侧缝/后背",
         "unit": "元/条", "price": [1.5, 6]},
        {"id": "XT3", "name": "粘合衬", "spec": "无纺粘合衬", "use": "门襟/领口定型",
         "unit": "元/米", "price": [2, 5]},
        {"id": "XT4", "name": "缝纫线/包边带", "spec": "同色缝纫线", "use": "合缝与包边",
         "unit": "元/件", "price": [1, 3]},
    ]


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
    # 品类判定：鞋类给鞋码/跖围（**只有靴类才有筒高**），服装给胸腰臀/长度
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    brief_category = str(((brief.parsed if brief else {}) or {}).get("category") or "")
    category_text = f"{category} {brief_category} {pattern.get('name', '')}"

    if is_shoe(category_text):
        boot = is_boot(category_text)
        girth = round(float(ease.get("跖围") or 220) + delta.get("waist_ease_cm", 0), 1)
        # **只有靴类才有筒高**；凉鞋/拖鞋/单鞋不输出这一行（也不编数字）
        shaft = _shaft_mm(payload) if boot else None
        if shaft is not None:
            shaft = round(shaft + delta.get("skirt_length_cm", 0) * 10, 1)      # 迭代参数单位是 cm
        base_code = 235
        tiers = []
        for code in (225, 230, 235, 240, 245):
            item = {
                "size": f"{code}",
                "鞋码（＝脚长）": code,
                # 跖围档差：每码（5mm）围度 ±4mm（估算；原先 0.4 系数偏小）
                "跖围": round(girth + (code - base_code) * 0.8, 1),
            }
            if shaft is not None:
                item["筒高"] = shaft
            tiers.append(item)
        rows = [
            {"key": "shoe_size", "label": "鞋码（＝脚长）", "value": base_code, "unit": "mm"},
            {"key": "girth", "label": "跖围", "value": girth, "unit": "mm"},
        ]
        if shaft is not None:
            rows.append({"key": "shaft", "label": "筒高", "value": shaft, "unit": "mm"})
        return {
            "system": f"鞋码（基础码 {base_code}，档差 5mm）",
            "unit": "mm",
            "tiers": tiers,
            "rows": rows,
            "labels": {
                "bust": "鞋码（＝脚长）",
                "waist": "",
                "hip": "跖围",
                "skirt_length": "筒高" if shaft is not None else "",
                "sleeve_length": "",
            },
            "bust": base_code,
            "waist": base_code,
            "hip": girth,
            "skirt_length": shaft or 0,
            "sleeve_length": 0,
            "pattern": pattern["name"],
            "iterations": len(iterations),
            "note": "尺寸由代码按「鞋码 + 跖围（估算档差）+ 迭代改动」计算，**估算值**，打样前请版师复核",
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
    len_label = length_label(category_text)
    tiers_out = []
    for idx, code in enumerate(("S", "M", "L", "XL")):
        step = idx - 1
        tiers_out.append({
            "size": code,
            "胸围": round(bust + step * 4, 1),
            "腰围": round(waist + step * 4, 1),
            "臀围": round(hip + step * 4, 1),
            len_label: round(length + step * 2, 1),
        })
    rows_out = [
        {"key": "bust", "label": "胸围", "value": round(bust, 1), "unit": "cm"},
        {"key": "waist", "label": "腰围", "value": round(waist, 1), "unit": "cm"},
        {"key": "hip", "label": "臀围", "value": round(hip, 1), "unit": "cm"},
        {"key": "skirt_length", "label": len_label, "value": round(length, 1), "unit": "cm"},
    ]
    if sleeve:
        rows_out.append({"key": "sleeve_length", "label": "袖长", "value": round(sleeve, 1), "unit": "cm"})
    return {
        "system": system,
        "unit": "cm",
        "tiers": tiers_out,
        "rows": rows_out,
        "labels": {"bust": "胸围", "waist": "腰围", "hip": "臀围", "skirt_length": len_label,
                   "sleeve_length": "袖长"},
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

    # 迭代提示文案也按品类：鞋类改的是"筒高/跖围"，服装是"裙长/腰围松量"（不把女装叫法套到鞋上）
    shoe_spec = "鞋码" in str(before.get("system") or "")
    labels = before.get("labels") or {}
    label_map = {
        "skirt_length_cm": "筒高" if shoe_spec else str(labels.get("skirt_length") or "长度"),
        "waist_ease_cm": "跖围" if shoe_spec else "腰围放松量",
        "hip_ease_cm": "臀围放松量",
        "sleeve_length_cm": "袖长",
    }
    unit_map = {key: ("mm" if shoe_spec else ITERABLE[key][1]) for key in ("skirt_length_cm", "waist_ease_cm")}
    effects = []
    for key, value in changes.items():
        label = label_map.get(key) or ITERABLE[key][0]
        unit = unit_map.get(key) or ITERABLE[key][1]
        if key in before and key in after:
            effects.append(f"{label} {value:+g}{unit} → 成品 {after[key]}{unit}（原 {before[key]}{unit}）")
        else:
            effects.append(f"{label} {value:+g}{unit}")
    return {"iteration_no": number, "changes": dict(changes), "effects": effects, "size_spec": after}


def _cost_note(session: Session, project_id: int, low: float, high: float) -> str:
    """成本说明：AI 估算；若明显高于企划售价，明确提示不匹配（避免出现"售价 40、成本 120"这种荒唐结果）。"""
    from ..models import Brief

    base = "**估算值**：用量按版型简化估算；面料、辅料、工艺价格均为 AI 生成的估算区间；正式核价请由采购确认"
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    band = str(((brief.parsed if brief else {}) or {}).get("price_band") or "")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", band)]
    if nums and low > max(nums):
        return (
            base
            + f"｜⚠️ 成本估算（{low:.0f}–{high:.0f} 元）**高于企划售价（{band}）**："
            + "可能是 AI 给出的计价单位（元/米 vs 元/尺、元/双）与企划价格带有出入，请核对"
        )
    return base


def build_bom(session: Session, project_id: int) -> dict:
    """BOM 和成本估算：面料用量按版型估算，价格取示例库区间（**明确标注估算**）。"""
    require_for_export(session, project_id)            # 门 1–4
    material_pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    pattern_pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    assert material_pick and pattern_pick
    d: dict = {}                                  # AI 版型的设计参数（示例库版型时为空）
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

    brief_row = session.scalar(select(Brief).where(Brief.project_id == project_id))
    category_text = (
        f"{((brief_row.parsed if brief_row else {}) or {}).get('category') or ''} "
        f"{d.get('category') or ''} {pattern.get('name', '')} {material.get('name', '')}"
    )

    # 简化用量估算：**鞋类按每双的鞋面/内里用量估**；服装按长度 × 幅宽系数（估算值）
    if is_shoe(category_text):
        fabric_meters = 0.35
    else:
        fabric_meters = round((float(spec["skirt_length"]) / 100) * 1.6, 2)
    low, high = material["price_yuan_per_m"]
    fabric_cost = (round(low * fabric_meters), round(high * fabric_meters))

    # 辅料：优先用**已采纳的 AI 辅料建议**；没采纳则用**按品类的内置兜底**（零费用）。
    # 两者都按品类——不会再出现"凉鞋配鱼骨支撑条/贝壳细扣"。
    pick_row = session.scalar(select(TrimPick).where(TrimPick.project_id == project_id))
    if pick_row is not None and (pick_row.payload or {}).get("items"):
        trims = list(pick_row.payload["items"])
        trims_source = "AI 生成建议（已采纳，非采购数据）"
    else:
        trims = _fallback_trims(category_text)
        trims_source = "按品类内置兜底（未生成辅料建议时使用，价格为粗略估）"

    trims_cost = 0.0
    trims_cost_high = 0.0
    for trim in trims:
        low_p, high_p = (trim.get("price") or [0, 0])[:2]
        trims_cost += float(low_p)
        trims_cost_high += float(high_p)

    # 版型行的松量说明：AI 版型用它的 ease；示例库版型用库里的松量（不让服装显示"按基础版型"这种空话）
    ease_for_line = dict(d.get("ease") or {})
    if not ease_for_line and not is_shoe(category_text):
        ease_for_line = {"腰围": pattern.get("waist_ease_cm"), "臀围": pattern.get("hip_ease_cm")}

    craft_fee = (8 if len(pattern_pick.crafts) >= 3 else 5) * len(pattern_pick.crafts)
    low_total = round(fabric_cost[0] + trims_cost + craft_fee)
    high_total = round(fabric_cost[1] + trims_cost_high + craft_fee)
    return {
        "material": {"id": material["id"], "name": material["name"], "fiber": material["fiber"],
                     "is_sample": "AI-" not in str(material["id"]),
                     "price_range": [low, high], "unit": "元/米"},
        "fabric_meters": fabric_meters,
        "trims_source": trims_source,
        "is_ai_generated": True,
        "items": [
            {"kind": "版型", "name": pattern["name"],
             "qty": pattern_line(category_text, spec, {"ease": ease_for_line}),
             "price_range": [0, 0], "subtotal": [0, 0], "no_cost": True},
            {"kind": "面料", "name": material["name"], "qty": f"{fabric_meters} 米",
             "price_range": [low, high], "subtotal": list(fabric_cost)},
            *[{"kind": "辅料", "name": t["name"],
               "qty": f'{t.get("spec", "")}｜{t.get("unit", "")}'.strip("｜"),
               "use": t.get("use", ""),
               "price_range": list(t.get("price") or [0, 0]),
               "subtotal": list((t.get("price") or [0, 0])[:2]),
               "source": trims_source} for t in trims],
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
