"""模型输出的结构定义与校验（Pydantic）。

口径：**模型给什么就校验什么，缺的进 missing，绝不由代码替它编一个**。
校验失败 → 有限重试 → 仍失败则**如实报错**（不降级成假数据）。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator


class BriefParsed(BaseModel):
    """企划解析结果（第一步的产物）。"""

    category: str = ""
    style_keywords: list[str] = Field(default_factory=list)
    target_user: str = ""
    price_band: str = ""
    cost_ceiling: str = ""
    #: 企划里**没说**的项，原样列出（界面显示"未提供"，不编）
    missing: list[str] = Field(default_factory=list)

    @field_validator("style_keywords", mode="before")
    @classmethod
    def _split_keywords(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
        return value


class DirectionPlan(BaseModel):
    """一个风格方向（第二步的产物，3–4 条为一组）。"""

    seq: int
    name: str
    inspiration: str = ""
    palette: list[dict] = Field(default_factory=list)
    silhouette: str = ""
    #: 出图用的提示词；**不得包含品牌名或商标**（代码层再过滤一次）
    image_prompt: str = ""


BANNED_IN_PROMPT = ("logo", "商标", "品牌", "nike", "adidas", "zara", "uniqlo", "优衣库", "香奈儿", "chanel", "gucci")


def clean_image_prompt(prompt: str) -> str:
    """去掉提示词里的品牌/商标风险词（不静默改写语义，只删这些词并记录）。"""
    cleaned = prompt
    for word in BANNED_IN_PROMPT:
        cleaned = cleaned.replace(word, "").replace(word.upper(), "")
    return " ".join(cleaned.split())


def has_brand_risk(prompt: str) -> bool:
    lower = prompt.lower()
    return any(word in lower for word in BANNED_IN_PROMPT)


class DirectionSet(BaseModel):
    """一组风格方向（**数量由调用方指定**，允许 3–6 个）。"""

    directions: list[DirectionPlan] = Field(default_factory=list)

    @field_validator("directions")
    @classmethod
    def _between_three_and_six(cls, value: list[DirectionPlan]) -> list[DirectionPlan]:
        if len(value) not in (3, 4, 5, 6):
            raise ValueError(f"风格方向必须是 3 个，收到 {len(value)} 个")
        return value


class MaterialChoice(BaseModel):
    """模型挑中的一块面料（`material_id` 必须在示例库内，否则整组结果作废）。"""

    material_id: str
    reason: str = ""


class MaterialChoiceSet(BaseModel):
    """面料搭配：2–4 条，且必须都来自候选清单。"""

    choices: list[MaterialChoice] = Field(default_factory=list)

    @field_validator("choices")
    @classmethod
    def _count(cls, value: list[MaterialChoice]) -> list[MaterialChoice]:
        if not 1 <= len(value) <= 4:
            raise ValueError(f"面料搭配要给 1–4 条，收到 {len(value)} 条")
        return value


class CraftChoice(BaseModel):
    """模型建议的单条工艺。"""

    craft_id: str
    reason: str = ""


class CraftChoiceSet(BaseModel):
    """模型建议工艺的返回结构（最多 3 条，必须来自候选库）。"""

    crafts: list[CraftChoice] = []


def split_keywords(raw: str) -> list[str]:
    """把「度假风, 显瘦；不易皱、上镜」这类输入统一切成关键词列表（去重、去空、每个 ≤20 字）。"""
    parts = re.split(r"[,，、;；/|\s]+", (raw or "").strip())
    out: list[str] = []
    for part in parts:
        word = part.strip()
        if word and word not in out:
            out.append(word[:20])
    return out


class BriefFieldsIn(BaseModel):
    """人工修改企划解析结果的入参（都不传＝不改该项）。"""

    category: str | None = None
    style_keywords: list[str] | str | None = None
    target_user: str | None = None
    price_band: str | None = None
    filled_missing: list[str] = []
    extra_notes: str | None = None            # 「还有什么要补充的？」自由文本


FIELD_LABELS = {
    "category": "品类",
    "style_keywords": "风格关键词",
    "target_user": "目标人群",
    "price_band": "价格带",
}


def merge_brief_fields(parsed: dict, payload: BriefFieldsIn) -> dict:
    """把人工修改合并进解析结果；重新计算「未提供」清单（填了就移出，清空就加回）。"""
    out = dict(parsed or {})
    for key, value in (
        ("category", payload.category),
        ("target_user", payload.target_user),
        ("price_band", payload.price_band),
    ):
        if value is None:
            continue
        text = value.strip()
        if not text:
            raise ValueError(f"{FIELD_LABELS[key]}不能为空（要清空请重新点「开始理解」）")
        if len(text) > 60:
            raise ValueError(f"{FIELD_LABELS[key]}最多 60 个字")
        out[key] = text
    if payload.style_keywords is not None:
        raw = payload.style_keywords
        keywords = split_keywords(raw if isinstance(raw, str) else "，".join(raw))
        if len(keywords) > 12:
            raise ValueError("风格关键词最多 12 个")
        out["style_keywords"] = keywords

    if payload.extra_notes is not None:
        notes = payload.extra_notes.strip()
        if len(notes) > 500:
            raise ValueError("补充说明最多 500 个字")
        out["extra_notes"] = notes

    missing = [m for m in (out.get("missing") or []) if m not in set(payload.filled_missing or [])]
    for key, label in FIELD_LABELS.items():
        filled = bool(out.get(key))
        if filled and label in missing:
            missing.remove(label)
        if not filled and label not in missing:
            missing.append(label)
    out["missing"] = missing
    return out


class PatternDesignOut(BaseModel):
    """AI 生成的版型设计参数（**不是工厂纸样/CAD 文件**）。"""

    category: str = ""
    name: str = ""
    silhouette: str = ""
    ease: dict[str, float] = {}          # 胸围/腰围/臀围/肩宽；鞋类则填 跟高/跖围 等
    structure_lines: list[str] = []
    collar: str = ""
    sleeve: str = ""
    hem: str = ""
    length_cm: int | None = None
    size_base: str = ""
    reason: str = ""


class MaterialGen(BaseModel):
    """AI 生成的一条面料方案（非真实物料）。"""

    name: str = ""
    fiber: str = ""
    weight_gsm: int | None = None
    width_cm: int | None = None
    price_yuan_per_m: list[float] = []
    hand: list[str] = []
    why: str = ""


class MaterialGenSet(BaseModel):
    items: list[MaterialGen] = []


class CraftGen(BaseModel):
    """AI 生成的一道工艺建议。"""

    name: str = ""
    how: str = ""
    why: str = ""
    difficulty: str = ""


class CraftGenSet(BaseModel):
    crafts: list[CraftGen] = []


class TrimGen(BaseModel):
    """AI 生成的一条辅料/鞋材辅件建议（非真实采购数据）。"""

    name: str = ""
    spec: str = ""
    use: str = ""
    unit: str = ""                                  # 元/双、元/米、元/条、元/个
    price: list[float] = []                         # 估算单价区间
    why: str = ""


class TrimGenSet(BaseModel):
    items: list[TrimGen] = []
