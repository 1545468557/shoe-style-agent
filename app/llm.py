"""模型调用（mock / DeepSeek 双通道）。

- **mock**：没有 Key 时使用，纯确定性解析（正则抽取价格带、季节、人群等），
  返回结构与真模型一致，**并在返回值里标明 provider="mock"**，界面据此显示"离线示例"；
- **deepseek**：OpenAI 兼容接口，要求模型输出 JSON，用 Pydantic 校验；
  校验失败重试（≤2 次），仍失败**抛错**（不编数据）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from .config import get_config


class LlmError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _mock_brief(text: str) -> dict[str, Any]:
    """确定性 mock：从企划文本里抽能抽到的，抽不到的写进 missing（不编）。"""
    price = re.search(r"(\d{2,5})\s*(?:元|块)?\s*(?:以内|以下|左右|以内)?", text)
    band = re.search(r"(?:预算|价格|售价|零售价)[^\d]{0,6}(\d{2,5})[^\d]{0,4}(\d{2,5})?", text)
    seasons = [s for s in ("春", "夏", "秋", "冬") if s in text]
    style_words = ("法式", "通勤", "度假", "复古", "简约", "优雅", "甜美", "干练", "国风", "运动")
    keywords = [w for w in style_words if w in text]
    category = "连衣裙" if "连衣裙" in text else ("半裙" if "半裙" in text or "裙子" in text else "")
    target = ""
    for who in ("白领", "学生", "宝妈", "职场女性", "年轻女性", "中年女性"):
        if who in text:
            target = who
            break
    missing: list[str] = []
    if not category:
        missing.append("品类")
    if not keywords:
        missing.append("风格关键词")
    if not band and not price:
        missing.append("价格带")
    if not target:
        missing.append("目标人群")
    if not seasons:
        missing.append("适用季节")
    return {
        "category": category,
        "style_keywords": keywords,
        "target_user": target,
        "price_band": (
            f"{band.group(1)}-{band.group(2)}元"
            if band and band.group(2)
            else (band.group(1) + "元左右" if band else "")
        ),
        "cost_ceiling": (price.group(1) + "元以内") if price and not band else "",
        "missing": missing,
    }


def _deepseek_json(system: str, user: str) -> str:
    from openai import OpenAI

    cfg = get_config()
    client = OpenAI(api_key=cfg.model_api_key, base_url=cfg.model_base_url, timeout=90, max_retries=0)
    response = client.chat.completions.create(
        model=cfg.model_id,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3,
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def complete_json[T: BaseModel](system: str, user: str, schema: type[T]) -> tuple[T, str]:
    """返回 (校验通过的对象, provider)。provider 为 "mock" 时界面必须标注离线示例。"""
    cfg = get_config()
    if cfg.is_mock_model:
        if schema.__name__ == "BriefParsed":
            return schema.model_validate(_mock_brief(user)), "mock"
        raise LlmError("mock_unsupported", f"mock 通道还不支持 {schema.__name__}")

    last_error = ""
    for _ in range(3):
        raw = _deepseek_json(system, user)
        try:
            return schema.model_validate(json.loads(raw)), cfg.model_id
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)[:200]
            user = (
                user
                + f"\n\n上一次输出不符合要求（{last_error}），请只输出符合结构的 JSON。"
            )
    raise LlmError("schema_invalid", f"模型连续 3 次没有给出符合结构的结果：{last_error}")
