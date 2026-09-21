"""风格方向生成（主链路第二步）：4 个方向 + 每个方向 1 张效果图。

规矩：
- 方向数量**必须是 3**（schema 层强制）；
- 出图提示词里的品牌/商标词先被清洗（app.schemas.clean_image_prompt）；
- 出图结果带 `is_placeholder`，**占位图不允许被当成真图**（界面必须标注）；
- 无 Key 时走 mock（方向内容）+ placeholder（图），并如实标注 provider。
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..gates import require_for_directions
from ..imagegen import generate
from ..llm import complete_json
from ..models import Brief, Project, StyleDirection
from ..schemas import DirectionPlan, DirectionSet, clean_image_prompt

SYSTEM = (
    "你是资深女装/鞋类设计师。请依据企划给出 %N% 个风格方向，只输出 JSON："
    '{"directions":[{"seq":1,"name":"","inspiration":"","palette":[{"name":"","hex":""}],'
    '"silhouette":"","image_prompt":""}]}。'
    "image_prompt 用中文描述款式效果图，禁止出现任何品牌名、商标或 logo 字样。"
    # 2026-09-21 产品经理定调：**放开写死的四类模板**，改为软性偏好，让每次方向与话术都不一样
    "要求：① %N% 个方向必须彼此**明显不同**——廓形、材质/图案、关键细节、穿着场景里至少有两项不一样，"
    "**不要给出换汤不换药的近似方案**；② 名字要有画面感、互相不重复，不要用「方向一/方案A」这类占位叫法；"
    "③ inspiration 写清这个方向为谁、在什么场景、解决什么问题（一到两句，具体一点，别写套话）。"
    "软性偏好（不是硬性规定，可灵活取舍）：偏向纯色、克制的配色与干净的廓形；若用印花或纹理，"
    "请低饱和、图案小、面积有限（避免热带大花与大面积撞色）；不要主动使用波西米亚要素"
    "（流苏、拼贴、繁复层叠）；确有必要时才少量使用，且不作主特征。"
    "三条 image_prompt 必须统一拍摄口径：正面全身、人物居中、浅灰或纯白棚拍背景、柔和均匀光线、竖构图 3:4，"
    "不要文字、水印、logo、品牌标识。"
)
MOCK_TEMPLATES = [
    ("法式优雅", "法式方领，收腰A字，七分袖，米白色真丝，白色背景，正面全身"),
    ("通勤简约", "衬衫领直筒，及膝长度，藏青色醋酸，长袖，白色背景，正面全身"),
    ("度假浪漫", "吊带大摆长裙，浅色雪纺印花，细肩带，白色背景，正面全身"),
    ("通勤实用", "衬衫式连衣裙，系带收腰，中长，卡其色棉麻，白色背景，正面全身"),
]


def _mock_directions(brief: Brief) -> dict:
    parsed = brief.parsed or {}
    category = parsed.get("category") or "连衣裙"
    style = "、".join(parsed.get("style_keywords") or []) or "简约"
    target = parsed.get("target_user") or "通用"
    directions = []
    for index, (name, shape) in enumerate(MOCK_TEMPLATES, start=1):
        directions.append(
            {
                "seq": index,
                "name": name,
                "inspiration": f"围绕企划的「{style}」基调，面向{target}的{category}方向（示例规划）",
                "palette": [
                    {"name": "主色", "hex": ["#F2EDE4", "#22303C", "#E8D7C3", "#D9CFC0"][index - 1]},
                    {"name": "点缀色", "hex": ["#B9A88F", "#6E6E6E", "#C98F7A", "#7C8B7A"][index - 1]},
                ],
                "silhouette": shape.split("，")[1] if "，" in shape else shape,
                "image_prompt": f"{shape}，{category}款式设计图，无品牌标识，无水印",
            }
        )
    return {"directions": directions}


def _system_for(count: int) -> str:
    """按要几个方向拼提示词（模板已放开，只做数字替换与多样化要求）。"""
    return SYSTEM.replace("%N%", str(count))


def generate_directions(session: Session, project_id: int, count: int = 4) -> list[StyleDirection]:
    """生成本项目的 4 个风格方向（**必须先过门 1**），每个方向出 1 张图。"""
    require_for_directions(session, project_id)          # 门 1：企划已确认
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("找不到项目")
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    if brief is None:
        raise ValueError("还没有提交企划")

    # mock 通道：直接给结构；真模型通道：走 complete_json（含结构校验与重试）
    from ..config import get_config

    if get_config().is_mock_model:
        mock = _mock_directions(brief)
        presets = mock["directions"]
        while len(presets) < count:                      # 兜底预设只有 4 条，需要更多时循环补
            extra = dict(presets[len(presets) % 4])
            extra["seq"] = len(presets) + 1
            extra["name"] = f"{extra['name']}（变体{len(presets) + 1}）"
            presets.append(extra)
        mock["directions"] = presets[:count]
        plan, provider = DirectionSet.model_validate(mock), "mock"
    else:
        user = f"企划原文：{brief.raw_text}\n解析结果：{brief.parsed}"
        plan, provider = complete_json(_system_for(count), user, DirectionSet)

    session.execute(delete(StyleDirection).where(StyleDirection.project_id == project_id))
    session.commit()

    created: list[StyleDirection] = []
    category = (brief.parsed or {}).get("category") or "连衣裙"
    for item in plan.directions[:count]:
        assert isinstance(item, DirectionPlan)
        # 2026-09-21 实测：真模型有时**不填 image_prompt**（字段留空）→ 出图会拿到空提示词。
        # 兜底：用方向自己的名字/廓形/配色/灵感拼一个，保证出图有内容；prompt 一律再过一遍品牌词清洗。
        raw_prompt = item.image_prompt.strip()
        if not raw_prompt:
            palette = "、".join(str(p.get("name", "")) for p in item.palette if isinstance(p, dict)) or "简洁配色"
            raw_prompt = (
                f"{category}款式设计图，{item.name}风格，{item.silhouette}，配色：{palette}，"
                f"{item.inspiration[:60]}，白色背景，正面全身，时装设计稿风格"
            )
        safe_prompt = clean_image_prompt(raw_prompt)
        row = StyleDirection(
            project_id=project_id,
            seq=item.seq,
            name=item.name,
            inspiration=item.inspiration,
            palette=item.palette,
            silhouette=item.silhouette,
            image_prompt=safe_prompt,
            image_status="pending",
        )
        session.add(row)
        session.commit()
        try:
            result = generate(safe_prompt, project_id, item.seq)
            row.image_path = str(result.path)
            row.is_placeholder = 1 if result.is_placeholder else 0
            row.image_status = "done"
        except Exception as exc:      # 出图失败**如实记录**，不静默换图
            row.image_status = "failed"
            row.inspiration = f"{row.inspiration}\n[出图失败：{str(exc)[:120]}]"
        session.commit()
        created.append(row)
    project.status = f"directions_ready({provider})"
    session.commit()
    return created
