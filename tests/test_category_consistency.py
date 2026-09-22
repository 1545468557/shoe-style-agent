"""按品类一致性：鞋/靴/服装的尺寸表、用料、辅料、导出都必须对得上（离线可跑）。

对应《阶段 3 第 6 子阶段技术开发文档》的修复项 F1–F9——
凉鞋不得出现「腰围/裙长/筒高/鱼骨/纽扣」，导出不得报 500。
"""

from __future__ import annotations

import pytest


def _project(tmp_path, monkeypatch, category: str, *, length_cm: int, ease: dict, is_boot: bool = False):
    """造一个已过门 1–4 的项目：面料与版型都走 AI 生成（复现真实主路径）。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'c.sqlite3'}")
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    import app.config as cfg
    import app.models as models
    from app import gates
    from app.workflows.direction import generate_directions

    cfg.get_config.cache_clear()
    models.reset_engine_for_tests()
    models.init_db()
    session = models.session()
    project = models.Project(name=f"{category}测试")
    session.add(project)
    session.commit()
    session.add(models.Brief(project_id=project.id, raw_text=f"{category} 企划", parsed={"category": category}))
    session.commit()
    gates.confirm(session, project.id, "brief")
    directions = generate_directions(session, project.id)
    gates.confirm(session, project.id, "direction", {"direction_id": directions[0].id})

    is_dress = category == "连衣裙"
    session.add(models.MaterialSuggestion(
        project_id=project.id, seq=1, provider="tag-fallback",
        payload={"name": "醋酸缎" if is_dress else "PU合成革",
                 "fiber": "醋酸" if is_dress else "PU",
                 "price_yuan_per_m": [8, 12]},
    ))
    session.add(models.MaterialPick(project_id=project.id, direction_id=directions[0].id,
                                    material_id="AI-1", note="AI 生成面料（非真实物料）"))
    session.commit()
    gates.confirm(session, project.id, "material")

    session.add(models.PatternDesign(
        project_id=project.id, provider="tag-fallback",
        payload={"category": category, "name": "AI 版型", "ease": ease, "length_cm": length_cm},
    ))
    session.add(models.PatternPick(project_id=project.id, pattern_id="AI 版型", crafts=["主工艺 A", "主工艺 B"]))
    session.commit()
    gates.confirm(session, project.id, "pattern")
    return session, project


@pytest.fixture()
def sandal(tmp_path, monkeypatch):
    return _project(tmp_path, monkeypatch, "凉鞋", length_cm=28, ease={"跖围": 220})


@pytest.fixture()
def boot(tmp_path, monkeypatch):
    return _project(tmp_path, monkeypatch, "女鞋/长筒靴", length_cm=45, ease={"跖围": 222}, is_boot=True)


@pytest.fixture()
def dress(tmp_path, monkeypatch):
    return _project(tmp_path, monkeypatch, "连衣裙", length_cm=110,
                    ease={"胸围": 96, "腰围": 74, "臀围": 98, "袖长": 56})


def test_sandal_size_spec_has_no_clothing_parts(sandal):
    """凉鞋：只有鞋码/跖围，**不出现筒高、腰围、裙长**。"""
    from app.workflows.bom import compute_size_spec

    session, project = sandal
    spec = compute_size_spec(session, project.id)
    labels = [row["label"] for row in spec["rows"]]
    assert labels == ["鞋码（＝脚长）", "跖围"]
    assert spec["unit"] == "mm"
    assert "筒高" not in "".join(labels)
    assert "腰围" not in "".join(labels) and "裙长" not in "".join(labels)
    assert len(spec["tiers"]) == 5
    first, last = spec["tiers"][0], spec["tiers"][-1]
    assert first["size"] == "225" and last["size"] == "245"
    assert last["跖围"] - first["跖围"] == 16.0         # 4 个码差 × 每码 4mm（估算档差）


def test_boot_size_spec_has_shaft_only_for_boots(boot):
    """靴类：有筒高（mm），仍不出现腰围/裙长。"""
    from app.workflows.bom import compute_size_spec

    session, project = boot
    spec = compute_size_spec(session, project.id)
    labels = [row["label"] for row in spec["rows"]]
    assert "筒高" in labels
    assert "腰围" not in "".join(labels) and "裙长" not in "".join(labels)
    shaft = next(row for row in spec["rows"] if row["label"] == "筒高")
    assert shaft["value"] == 450.0                      # 45cm → 450mm


def test_dress_size_spec_uses_cm_and_dress_length(dress):
    """服装：胸腰臀 + 裙长（连衣裙），cm 计量，4 档。"""
    from app.workflows.bom import compute_size_spec

    session, project = dress
    spec = compute_size_spec(session, project.id)
    labels = [row["label"] for row in spec["rows"]]
    assert labels[:3] == ["胸围", "腰围", "臀围"]
    assert "裙长" in labels and "袖长" in labels
    assert spec["unit"] == "cm"
    assert [t["size"] for t in spec["tiers"]] == ["S", "M", "L", "XL"]


def test_pants_length_label_is_not_skirt(tmp_path, monkeypatch):
    """裤装叫「裤长」，不把「裙长」硬塞给裤子。"""
    session, project = _project(tmp_path, monkeypatch, "牛仔裤", length_cm=100,
                                ease={"胸围": 96, "腰围": 74, "臀围": 98})
    from app.workflows.bom import compute_size_spec

    labels = [row["label"] for row in compute_size_spec(session, project.id)["rows"]]
    assert "裤长" in labels and "裙长" not in labels


def test_sandal_bom_has_no_womens_trims(sandal):
    """用料清单：凉鞋不出现女装部位与女装辅料（鱼骨/纽扣/隐形拉链）。"""
    from app.workflows.bom import build_bom

    session, project = sandal
    bom = build_bom(session, project.id)
    pattern_row = next(i for i in bom["items"] if i["kind"] == "版型")
    assert "腰围" not in pattern_row["qty"] and "裙长" not in pattern_row["qty"]
    assert pattern_row["no_cost"] is True
    trim_names = " ".join(i["name"] + i["qty"] for i in bom["items"] if i["kind"] == "辅料")
    for word in ("鱼骨", "纽扣", "隐形拉链", "里布"):
        assert word not in trim_names
    assert any(word in trim_names for word in ("大底", "鞋垫", "内里", "中底"))


def test_dress_bom_has_no_shoe_trims(dress):
    """服装辅料里不出现鞋材（大底/鞋垫）。"""
    from app.workflows.bom import build_bom

    session, project = dress
    trim_names = " ".join(i["name"] for i in build_bom(session, project.id)["items"] if i["kind"] == "辅料")
    assert "大底" not in trim_names and "鞋垫" not in trim_names


def test_ai_trims_generation_and_accept(sandal):
    """AI 辅料建议：按品类生成；采纳后 BOM 用采纳的那份。"""
    from app.workflows.bom import build_bom
    from app.workflows.generate import accept_trims, generate_trims

    session, project = sandal
    items, _provider = generate_trims(session, project.id)
    assert 3 <= len(items) <= 5
    names = " ".join(i["name"] + str(i.get("spec", "")) for i in items)
    assert "鱼骨" not in names and "纽扣" not in names
    accept_trims(session, project.id, [items[0]["seq"]])
    bom = build_bom(session, project.id)
    trims = [i for i in bom["items"] if i["kind"] == "辅料"]
    assert len(trims) == 1 and trims[0]["name"] == items[0]["name"]
    assert "AI 生成建议" in bom["trims_source"]


def test_accept_trims_rejects_when_gate_not_passed(tmp_path, monkeypatch, sandal):
    """门没过就不允许采纳辅料（不会绕过人工确认）。"""
    from app.gates import GateError

    session, project = sandal
    from app.workflows.generate import accept_trims

    with pytest.raises((GateError, ValueError)):
        accept_trims(session, project.id, [999])


def test_trim_sanitizer_strips_brands_and_cross_category():
    """辅料清洗：品牌名清掉；跨品类条目拦下（鞋类不给里布/细扣）。"""
    from app.workflows.generate import _strip_brands, _trim_allowed

    cleaned = _strip_brands({"name": "YKK 拉链", "spec": "3M 反光条"})
    assert "YKK" not in cleaned["name"] and "3M" not in cleaned["spec"]
    assert _trim_allowed({"name": "曼鱼骨支撑条"}, "凉鞋") is False
    assert _trim_allowed({"name": "橡胶大底"}, "凉鞋") is True
    assert _trim_allowed({"name": "橡胶大底"}, "连衣裙") is False


def test_export_word_and_excel_do_not_fail_on_ai_material(sandal, dress):
    """AI 面料 + AI 版型也能导出（原先 Word 直接 500）；部位名随品类。"""
    from pathlib import Path

    from docx import Document

    from app.export import export_excel, export_word

    for session, project, forbidden, expected in (
        (sandal[0], sandal[1], "裙长", "跖围"),
        (dress[0], dress[1], "跖围", "裙长"),
    ):
        word = export_word(session, project.id)
        excel = export_excel(session, project.id)
        assert Path(word).exists() and Path(excel).exists()
        text = "\n".join(p.text for p in Document(word).paragraphs)
        assert "尺寸表" in text
        assert expected in text
        assert forbidden not in text
