"""版型迭代 / BOM / 打样单（离线可跑）。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'b.sqlite3'}")
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    import app.config as cfg
    import app.models as models
    from app import gates
    from app.workflows.direction import generate_directions
    from app.workflows.material import confirm_material, pick_material, pick_pattern

    cfg.get_config.cache_clear()
    models.reset_engine_for_tests()
    models.init_db()
    session = models.session()
    project = models.Project(name="BOM测试")
    session.add(project)
    session.commit()
    session.add(models.Brief(project_id=project.id, raw_text="夏天度假连衣裙，预算 300 元以内"))
    session.commit()
    gates.confirm(session, project.id, "brief")
    directions = generate_directions(session, project.id)
    gates.confirm(session, project.id, "direction", {"direction_id": directions[0].id})
    pick_material(session, project.id, directions[0].id, "M003")
    confirm_material(session, project.id)
    pick_pattern(session, project.id, "P003", ["C001", "C005"])
    gates.confirm(session, project.id, "pattern")
    return session, project


def test_size_spec_is_computed_by_code(confirmed):
    session, project = confirmed
    from app.workflows.bom import compute_size_spec

    spec = compute_size_spec(session, project.id)
    assert spec["skirt_length"] > 0 and spec["waist"] > 0
    assert "估算值" in spec["note"]


def test_iteration_changes_numbers_and_bumps_version(confirmed):
    session, project = confirmed
    from app.workflows.bom import compute_size_spec, iterate_pattern

    before = compute_size_spec(session, project.id)
    result = iterate_pattern(session, project.id, {"skirt_length_cm": 10, "waist_ease_cm": -2})
    after = compute_size_spec(session, project.id)
    assert result["iteration_no"] == 1
    assert after["skirt_length"] == round(before["skirt_length"] + 10, 1)   # 数值确实变了
    assert after["waist"] == round(before["waist"] - 2, 1)
    assert result["effects"]                                              # 有"改了什么、影响什么"
    second = iterate_pattern(session, project.id, {"skirt_length_cm": 5})
    assert second["iteration_no"] == 2                                    # 版本号递增


def test_iteration_rejects_unknown_parameter(confirmed):
    session, project = confirmed
    from app.workflows.bom import iterate_pattern

    with pytest.raises(ValueError):
        iterate_pattern(session, project.id, {"随便改点什么": 1})


def test_bom_is_range_and_flagged_estimate(confirmed):
    session, project = confirmed
    from app.workflows.bom import build_bom

    bom = build_bom(session, project.id)
    assert bom["estimated_cost_yuan"][0] <= bom["estimated_cost_yuan"][1]
    assert "估算值" in bom["note"]
    assert bom["sample_library"] is True


def test_sampling_order_and_gate_five(confirmed):
    session, project = confirmed
    from app import gates
    from app.workflows.bom import advance_sampling, create_sampling_order

    order = create_sampling_order(session, project.id)
    assert "仿真" in order["note"] and "预留标准对接位" in order["note"]
    advance_sampling(session, project.id, "approved")              # 门 5
    assert gates.status(session, project.id)["sampling"] is True
    assert advance_sampling(session, project.id, "sampling")["state"] == "sampling"
