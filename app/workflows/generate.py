"""面料与工艺的 AI 生成（**不再从示例库挑选**）。

产品经理定调（2026-09-21）：面料、版型、工艺都让大模型自己生产。
口径红线：生成物是**设计建议**，**不是真实物料、不是采购报价、不是工厂工价**，界面必须如实标注。
"""

from __future__ import annotations

import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..category import is_shoe
from ..gates import require_for_materials, require_for_pattern
from ..models import (
    Brief,
    CraftSuggestion,
    MaterialPick,
    MaterialSuggestion,
    PatternPick,
    StyleDirection,
    TrimPick,
    TrimSuggestion,
)

MATERIAL_SYSTEM = (
    "你是资深面料/鞋材采购与设计师。请依据【品类、风格方向、人群、价格带】给出 3 个可选面料（或鞋材）方案，"
    '只输出 JSON：{"items":[{"name":"","fiber":"","weight_gsm":0,"width_cm":0,'
    '"price_yuan_per_m":[0,0],"hand":[""],"why":""}]}。'
    "规则：① **必须与品类一致**——裙装给面料（真丝/醋酸/雪纺/棉麻等）；羽绒服给防钻绒面料与充绒相关；"
    "裤装给有弹或无弹的梭织/针织；**鞋子给鞋材（鞋面革/PU/网布/内里/大底等）**；"
    "② fiber 写常见可实现成分，不要编造生僻材料；"
    "③ price_yuan_per_m 给合理的元/米（鞋材可按元/尺）估算区间；"
    "④ hand 写手感关键词；⑤ why 一句话说明为什么适合这个方向与价位；⑥ 不得出现品牌名或商标。"
)

CRAFT_SYSTEM = (
    "你是资深服装/鞋类工艺师。请依据【品类、版型要点、面料方案、价格带】给出 3–5 道关键工艺，"
    '只输出 JSON：{"crafts":[{"name":"","how":"","why":"","difficulty":""}]}。'
    "规则：① **必须与品类一致**——鞋类给「鞋面拼接/后跟定型/中底粘合/鞋口包边」这类；"
    "羽绒服给「防钻绒缝合/分格充绒/压胶」这类；裙装才用「隐形拉链/里衬/鱼骨」；"
    "② how 写做法要点；③ why 写为什么这个款需要它；④ difficulty 只能是 低/中/高；⑤ 不得出现品牌名。"
)


TRIM_SYSTEM = (
    "你是资深服装/鞋类开发跟单（辅料与配件）。请依据【品类、风格方向、价格带、已选面料】"
    '给出 3–5 条**该品类**需要的辅料，只输出 JSON：'
    '{"items":[{"name":"","spec":"","use":"","unit":"","price":[0,0],"why":""}]}。'
    "规则：① **严格按品类**——鞋/靴/凉鞋/拖鞋只能给鞋类辅件（内里、鞋垫、鞋带/魔术贴、中底、大底、"
    "鞋口包边、五金扣件、防滑贴等）；服装才能给里布、拉链、细扣、粘合衬、松紧、织带、鱼骨；"
    "**鞋类不得出现细扣/鱼骨/隐形拉链等服装辅料，服装不得出现大底/鞋垫等鞋材**；"
    "② unit 写计价单位（元/双、元/米、元/条、元/个）；③ price 给合理的估算单价区间（不是采购报价）；"
    "④ use 一句话说用在哪里；⑤ why 一句话说为什么这个款需要它；⑥ 不得出现品牌名或商标。"
)


def _mock_trims(category: str) -> list[dict]:
    """离线兜底：**按品类**给辅料，鞋类不会拿到女装辅料。"""
    if is_shoe(category):
        return [
            {"name": "鞋用内里", "spec": "猪皮/网布内里", "use": "贴脚内里", "unit": "元/双",
             "price": [6, 12], "why": "鞋需要贴脚、吸湿的内里（离线兜底）"},
            {"name": "成型鞋垫", "spec": "EVA 成型鞋垫", "use": "缓震与脚感", "unit": "元/双",
             "price": [3, 6], "why": "提升脚感与缓震（离线兜底）"},
            {"name": "橡胶大底", "spec": "耐磨防滑纹路", "use": "外底", "unit": "元/双",
             "price": [12, 22], "why": "通勤需要耐磨防滑（离线兜底）"},
            {"name": "中底/成型衬", "spec": "成型中底", "use": "支撑与定型", "unit": "元/双",
             "price": [5, 9], "why": "决定鞋型轮廓与安定性（离线兜底）"},
        ]
    return [
        {"name": "里布", "spec": "涤纶/醋酸里布", "use": "里衬防透", "unit": "元/米",
         "price": [6, 14], "why": "防透光、提升穿着感（离线兜底）"},
        {"name": "拉链", "spec": "3号尼龙/金属拉链", "use": "侧缝/后背", "unit": "元/条",
         "price": [1.5, 6], "why": "穿脱方便且不破坏线条（离线兜底）"},
        {"name": "粘合衬", "spec": "无纺粘合衬", "use": "门襟/领口定型", "unit": "元/米",
         "price": [2, 5], "why": "领口/门襟需要挺度（离线兜底）"},
        {"name": "缝纫线/包边带", "spec": "同色缝纫线", "use": "合缝与包边", "unit": "元/件",
         "price": [1, 3], "why": "收口与配色完整度（离线兜底）"},
    ]


def generate_trims(session: Session, project_id: int) -> tuple[list[dict], str]:
    """AI 生成 3–5 条**按品类**的辅料建议（非真实采购数据）。"""
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import TrimGenSet

    require_for_materials(session, project_id)            # 门 1、2、3
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    category = ((brief.parsed if brief else {}) or {}).get("category") or "女装"
    material = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    if get_config().is_mock_model:
        items, provider = _mock_trims(category), "tag-fallback"
    else:
        direction = session.get(StyleDirection, material.direction_id) if material else None
        user = (
            f"{_brief_ctx(session, project_id, direction)}\n"
            f"已选面料：{material.note if material else '未提供'}\n"
            f"品类判定：{'鞋类' if is_shoe(str(category)) else '服装类'}（请严格按此给辅料）"
        )
        try:
            out, provider = complete_json(TRIM_SYSTEM, user, TrimGenSet)
            items = [t.model_dump() for t in out.items]
            if not items:
                raise ValueError("模型没给出辅料建议")
        except (LlmError, ValueError):
            items, provider = _mock_trims(category), "tag-fallback"

    # 模型偶尔跨品类（给凉鞋配细扣）→ 用品类关键词做一次清洗与兜底；顺手清掉品牌名
    items = [_strip_brands(i) for i in items if _trim_allowed(i, str(category))] or _mock_trims(category)

    session.execute(delete(TrimSuggestion).where(TrimSuggestion.project_id == project_id))
    session.commit()
    numbered: list[dict] = []
    for idx, item in enumerate(items[:5], start=1):
        session.add(TrimSuggestion(project_id=project_id, seq=idx, payload=item, provider=provider,
                                   created_at=time.strftime("%Y-%m-%d %H:%M:%S")))
        numbered.append(dict(item, seq=idx))
    session.commit()
    return numbered, provider


#: 跨品类关键词：鞋类不得出现这些；服装不得出现鞋材词
_CLOTHING_ONLY = ("细扣", "鱼骨", "隐形拉链", "门襟", "腰头", "里布")
_SHOE_ONLY = ("大底", "鞋垫", "中底", "跖围", "鞋面", "跟太", "鞋带", "魔术贴")

#: 品牌名不入交付物（模型偶尔会写"YKK 或同等级"）→ 统一改成"同等级配件"
_BRANDS = ("YKK", "ykk", "3M", "Gore-Tex", "gore-tex", "施华洛世奇", "杜邦")


def _strip_brands(item: dict) -> dict:
    cleaned = {}
    for key, value in item.items():
        if isinstance(value, str):
            for brand in _BRANDS:
                value = value.replace(brand, "同等级配件")
        cleaned[key] = value
    return cleaned


def _trim_allowed(item: dict, category: str) -> bool:
    name = f"{item.get('name', '')}{item.get('spec', '')}{item.get('use', '')}"
    if is_shoe(category):
        return not any(word in name for word in _CLOTHING_ONLY)
    return not any(word in name for word in _SHOE_ONLY)


def accept_trims(session: Session, project_id: int, seqs: list[int]) -> dict:
    """采纳 AI 辅料建议（按序号）；采纳后 BOM 用这份，未采纳时用按品类的内置兜底。"""
    require_for_materials(session, project_id)
    rows = session.scalars(
        select(TrimSuggestion).where(TrimSuggestion.project_id == project_id).order_by(TrimSuggestion.seq)
    ).all()
    if not rows:
        raise ValueError("还没有 AI 辅料建议，请先点「AI 生成辅料建议」")
    wanted = set(seqs) if seqs else {r.seq for r in rows}
    items = [dict(r.payload, seq=r.seq) for r in rows if r.seq in wanted]
    if not items:
        raise ValueError("没有勾选任何辅料")
    row = session.scalar(select(TrimPick).where(TrimPick.project_id == project_id))
    if row is None:
        row = TrimPick(project_id=project_id)
        session.add(row)
    row.payload = {"items": items}
    row.note = f"已采纳 {len(items)} 条 AI 辅料建议（非采购数据，价格为估算）"
    row.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    session.commit()
    return {"items": items, "note": row.note}


def _brief_ctx(session: Session, project_id: int, direction: StyleDirection | None) -> str:
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    parsed = (brief.parsed if brief else {}) or {}
    return (
        f"品类：{parsed.get('category') or '未提供'}\n"
        f"风格关键词：{'、'.join(parsed.get('style_keywords') or []) or '未提供'}\n"
        f"人群：{parsed.get('target_user') or '未提供'}｜价格带：{parsed.get('price_band') or '未提供'}\n"
        f"补充说明：{parsed.get('extra_notes') or '无'}\n"
        f"风格方向：{direction.name if direction else ''}｜{direction.silhouette if direction else ''}"
    )


def _mock_materials(category: str) -> list[dict]:
    if any(w in category for w in ("鞋", "靴", "高跟", "凉")):
        return [
            {"name": "头层牛皮（鞋面）", "fiber": "牛皮", "weight_gsm": None, "width_cm": None,
             "price_yuan_per_m": [45, 70], "hand": ["挺括", "细腻"], "why": "鞋面需要挺度与耐折（离线兜底）"},
            {"name": "羊皮内里", "fiber": "羊皮", "weight_gsm": None, "width_cm": None,
             "price_yuan_per_m": [28, 42], "hand": ["柔软", "吸湿"], "why": "内里要贴脚舒适（离线兜底）"},
            {"name": "橡胶大底", "fiber": "橡胶", "weight_gsm": None, "width_cm": None,
             "price_yuan_per_m": [18, 30], "hand": ["耐磨", "防滑"], "why": "通勤需要耐磨防滑（离线兜底）"},
        ]
    if "羽绒" in category:
        return [
            {"name": "防钻绒尼龙", "fiber": "100%锦纶", "weight_gsm": 45, "width_cm": 145,
             "price_yuan_per_m": [22, 34], "hand": ["轻", "防风"], "why": "羽绒服需要防钻绒面料（离线兜底）"},
            {"name": "胆布", "fiber": "涤纶", "weight_gsm": 30, "width_cm": 150,
             "price_yuan_per_m": [6, 10], "hand": ["薄", "密"], "why": "防绒胆布（离线兜底）"},
            {"name": "涤纶里布", "fiber": "涤纶", "weight_gsm": 60, "width_cm": 150,
             "price_yuan_per_m": [5, 9], "hand": ["顺滑"], "why": "里布顺滑易穿（离线兜底）"},
        ]
    return [
        {"name": "醋酸缎", "fiber": "100%醋酸", "weight_gsm": 120, "width_cm": 140,
         "price_yuan_per_m": [45, 70], "hand": ["垂坠", "微光泽"], "why": "垂坠显瘦、上镜（离线兜底）"},
        {"name": "真丝双绉", "fiber": "100%桑蚕丝", "weight_gsm": 68, "width_cm": 140,
         "price_yuan_per_m": [120, 180], "hand": ["柔滑"], "why": "高级手感（离线兜底）"},
        {"name": "棉麻混纺", "fiber": "棉 55/麻 45", "weight_gsm": 150, "width_cm": 145,
         "price_yuan_per_m": [28, 42], "hand": ["清爽"], "why": "夏天透气不易皱（离线兜底）"},
    ]


def _mock_crafts(category: str) -> list[dict]:
    if any(w in category for w in ("鞋", "靴", "高跟", "凉")):
        return [
            {"name": "鞋面拼接缝", "how": "按样版拼缝并压线", "why": "决定鞋面轮廓", "difficulty": "中"},
            {"name": "后跟定型", "how": "热定型保持后跟弧度", "why": "防止掉跟", "difficulty": "中"},
            {"name": "中底粘合", "how": "中底与鞋面冷粘压合", "why": "决定鞋底牢固度", "difficulty": "高"},
            {"name": "鞋口包边", "how": "包边收口", "why": "提升舒适与精致度", "difficulty": "低"},
        ]
    if "羽绒" in category:
        return [
            {"name": "防钻绒缝合", "how": "细针距+包边缝", "why": "防止羽绒钻出", "difficulty": "高"},
            {"name": "分格充绒", "how": "按格定量充绒", "why": "保暖均匀不跑绒", "difficulty": "中"},
            {"name": "压胶门襟", "how": "门襟压胶防风", "why": "防风保暖", "difficulty": "中"},
        ]
    return [
        {"name": "隐形拉链", "how": "侧缝装隐形拉链", "why": "保持线条干净", "difficulty": "中"},
        {"name": "隐形里衬", "how": "配同色里衬", "why": "防透光、提升垂坠", "difficulty": "低"},
        {"name": "腰部省道", "how": "前后省道收腰", "why": "显瘦", "difficulty": "中"},
    ]


def generate_materials(session: Session, project_id: int, direction_id: int) -> tuple[list[dict], str]:
    """AI 生成 3 个面料/鞋材方案（非真实物料）。"""
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import MaterialGenSet

    require_for_materials(session, project_id)            # 门 1、2
    direction = session.get(StyleDirection, direction_id)
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    category = ((brief.parsed if brief else {}) or {}).get("category") or "女装"
    if get_config().is_mock_model:
        items, provider = _mock_materials(category), "tag-fallback"
    else:
        try:
            out, provider = complete_json(MATERIAL_SYSTEM, _brief_ctx(session, project_id, direction), MaterialGenSet)
            items = [m.model_dump() for m in out.items]
            if not items:
                raise ValueError("模型没给出面料方案")
        except (LlmError, ValueError):
            items, provider = _mock_materials(category), "tag-fallback"

    session.execute(delete(MaterialSuggestion).where(MaterialSuggestion.project_id == project_id))
    session.commit()
    for idx, item in enumerate(items[:3], start=1):
        session.add(MaterialSuggestion(project_id=project_id, seq=idx, payload=item, provider=provider,
                                       created_at=time.strftime("%Y-%m-%d %H:%M:%S")))
    session.commit()
    return items[:3], provider


def generate_crafts(session: Session, project_id: int) -> tuple[list[dict], str]:
    """AI 生成 3–5 道工艺建议（非工厂工价）。"""
    from ..config import get_config
    from ..llm import LlmError, complete_json
    from ..schemas import CraftGenSet

    require_for_pattern(session, project_id)              # 门 1、2、3
    pack = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    category = ((brief.parsed if brief else {}) or {}).get("category") or "女装"
    material = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    if get_config().is_mock_model:
        crafts, provider = _mock_crafts(category), "tag-fallback"
    else:
        user = (
            f"{_brief_ctx(session, project_id, None)}\n"
            f"已选面料：{material.note if material else '未提供'}\n"
            f"版型要点：{pack.pattern_id if pack else '未提供'}"
        )
        try:
            out, provider = complete_json(CRAFT_SYSTEM, user, CraftGenSet)
            crafts = [c.model_dump() for c in out.crafts]
            if not crafts:
                raise ValueError("模型没给出工艺")
        except (LlmError, ValueError):
            crafts, provider = _mock_crafts(category), "tag-fallback"

    session.execute(delete(CraftSuggestion).where(CraftSuggestion.project_id == project_id))
    session.commit()
    session.add(CraftSuggestion(project_id=project_id, payload={"crafts": crafts}, provider=provider,
                                created_at=time.strftime("%Y-%m-%d %H:%M:%S")))
    session.commit()
    return crafts, provider


def accept_material(session: Session, project_id: int, direction_id: int, seq: int) -> dict:
    """采纳第 seq 个 AI 面料方案。"""
    require_for_materials(session, project_id)
    row = session.scalar(
        select(MaterialSuggestion).where(
            MaterialSuggestion.project_id == project_id, MaterialSuggestion.seq == seq
        )
    )
    if row is None:
        raise ValueError("找不到这个面料方案，请先点「AI 生成面料」")
    item = row.payload
    note = f"{item.get('name')}（{item.get('fiber')}，{item.get('weight_gsm') or '—'}g/m²）｜AI 生成建议"
    pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    if pick is None:
        pick = MaterialPick(project_id=project_id, direction_id=direction_id, material_id=f"AI-{seq}", note=note)
        session.add(pick)
    pick.direction_id = direction_id
    pick.material_id = f"AI-{seq}"
    pick.note = note
    pick.price_range = list(item.get("price_yuan_per_m") or [])
    session.commit()
    return {"material_id": pick.material_id, "note": pick.note}


def accept_crafts(session: Session, project_id: int, names: list[str]) -> dict:
    """采纳 AI 工艺（按名字列表），写入版型选择供门 4 确认。"""
    require_for_pattern(session, project_id)
    row = session.scalar(
        select(CraftSuggestion)
        .where(CraftSuggestion.project_id == project_id)
        .order_by(CraftSuggestion.id.desc())
    )
    valid = {c["name"] for c in ((row.payload.get("crafts") if row else []) or [])}
    picked = [n for n in names if n in valid] or sorted(valid)
    if not picked:
        raise ValueError("还没有 AI 工艺建议，请先点「AI 生成工艺」")
    pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    if pick is None:
        raise ValueError("请先采纳版型")
    pick.crafts = picked
    session.commit()
    return {"crafts": picked}
