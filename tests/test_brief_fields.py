"""企划解析结果的人工修改：校验与「未提供」清单重算（纯函数，不花钱）。"""

from __future__ import annotations

import pytest

from app.schemas import BriefFieldsIn, merge_brief_fields, split_keywords

BASE = {
    "category": "连衣裙",
    "style_keywords": ["度假风"],
    "target_user": "",
    "price_band": "",
    "missing": ["目标人群", "价格带"],
}


def test_split_keywords_handles_all_separators():
    assert split_keywords("度假风, 显瘦；不易皱、上镜") == ["度假风", "显瘦", "不易皱", "上镜"]
    assert split_keywords("显瘦, 显瘦") == ["显瘦"]            # 去重
    assert split_keywords("") == []


def test_merge_updates_fields_and_clears_missing():
    out = merge_brief_fields(BASE, BriefFieldsIn(target_user="25-35岁都市女性", price_band="400-600元"))
    assert out["target_user"] == "25-35岁都市女性"
    assert out["price_band"] == "400-600元"
    assert out["missing"] == []                                # 补齐后清单为空


def test_merge_adds_back_to_missing_when_field_blank():
    out = merge_brief_fields(BASE, BriefFieldsIn(category="半身裙"))
    assert out["category"] == "半身裙"
    assert "目标人群" in out["missing"] and "价格带" in out["missing"]


def test_merge_rejects_empty_and_overlong():
    with pytest.raises(ValueError):
        merge_brief_fields(BASE, BriefFieldsIn(category="   "))
    with pytest.raises(ValueError):
        merge_brief_fields(BASE, BriefFieldsIn(price_band="4" * 61))


def test_merge_rejects_too_many_keywords():
    with pytest.raises(ValueError):
        merge_brief_fields(BASE, BriefFieldsIn(style_keywords="，".join(f"k{i}" for i in range(13))))


def test_filled_missing_cannot_hide_a_still_empty_field():
    # 诚实优先：字段还是空的，即便显式声明"已补充"，也照样留在"没说"清单里
    out = merge_brief_fields(BASE, BriefFieldsIn(filled_missing=["价格带"]))
    assert "价格带" in out["missing"] and "目标人群" in out["missing"]

    # 字段真的填了，就等于补充完成，清单里消失
    ok = merge_brief_fields(BASE, BriefFieldsIn(price_band="400-600元", filled_missing=["价格带"]))
    assert ok["missing"] == ["目标人群"]


def test_extra_notes_saved_and_limited():
    out = merge_brief_fields(BASE, BriefFieldsIn(extra_notes="  要能配高跟鞋；不要露背  "))
    assert out["extra_notes"] == "要能配高跟鞋；不要露背"        # 去空白后保存
    with pytest.raises(ValueError):
        merge_brief_fields(BASE, BriefFieldsIn(extra_notes="字" * 501))
