"""企划解析：mock 通道与结构校验（不花钱、离线可跑）。"""

from __future__ import annotations

import pytest

from app.llm import LlmError, complete_json
from app.schemas import BriefParsed, clean_image_prompt, has_brand_risk

SYSTEM = "你是鞋服设计助理，请输出 JSON。"


def test_mock_extracts_what_is_there_and_lists_missing():
    text = "夏天要一条适合海边度假的连衣裙，预算 300 元以内，卖给年轻女性"
    parsed, provider = complete_json(SYSTEM, text, BriefParsed)
    assert provider == "mock"          # 没有 Key → 必须标明是 mock，界面要显示"离线示例"
    assert parsed.category == "连衣裙"
    assert "度假" in parsed.style_keywords
    assert parsed.target_user == "年轻女性"
    assert "300" in (parsed.price_band + parsed.cost_ceiling)
    assert "品类" not in parsed.missing       # 抽到了就不该进 missing


def test_mock_lists_missing_instead_of_inventing():
    parsed, _ = complete_json(SYSTEM, "随便来点东西", BriefParsed)
    assert parsed.category == ""
    for field in ("品类", "风格关键词", "价格带", "目标人群"):
        assert field in parsed.missing


def test_brand_words_are_removed_from_image_prompt():
    assert has_brand_risk("nike 风格的连衣裙 logo 明显")
    cleaned = clean_image_prompt("nike 风格的连衣裙 logo 明显")
    assert "nike" not in cleaned.lower()
    assert "logo" not in cleaned.lower()
    assert "连衣裙" in cleaned            # 只删风险词，不破坏语义


def test_mock_refuses_unsupported_schema():
    with pytest.raises(LlmError):
        complete_json(SYSTEM, "x", type("Other", (BriefParsed,), {}))
