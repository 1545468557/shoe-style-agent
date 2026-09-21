"""面料/版型：只能从示例库取材、门顺序正确（离线可跑）。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def ready(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'m.sqlite3'}")
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
    project = models.Project(name="面料测试")
    session.add(project)
    session.commit()
    session.add(models.Brief(project_id=project.id, raw_text="夏天海边度假连衣裙，预算300元以内"))
    session.commit()
    gates.confirm(session, project.id, "brief")
    directions = generate_directions(session, project.id)
    gates.confirm(session, project.id, "direction", {"direction_id": directions[0].id})
    return session, project, directions[0]


def test_recommend_only_from_sample_library(ready):
    session, project, direction = ready
    from app.workflows.material import recommend_materials

    items = recommend_materials(session, project.id, direction.id, limit=4)
    assert items, "应该有推荐结果"
    assert all(item["is_sample"] for item in items)          # 必须标注示例库
    ids = {item["id"] for item in items}
    from app.library import all_of

    assert ids <= {m["id"] for m in all_of("materials")}     # 只能来自库


def test_pick_material_outside_library_is_rejected(ready):
    session, project, direction = ready
    from app.library import LibraryError
    from app.workflows.material import pick_material

    with pytest.raises(LibraryError):
        pick_material(session, project.id, direction.id, "M999-不存在")


def test_gate_three_flow(ready):
    session, project, direction = ready
    from app import gates
    from app.workflows.material import confirm_material, pick_material

    pick_material(session, project.id, direction.id, "M003")
    confirm_material(session, project.id)
    flags = gates.status(session, project.id)
    assert flags["material"] is True


def test_pattern_requires_material_gate(ready):
    session, project, direction = ready
    from app import gates
    from app.workflows.material import pick_material, recommend_patterns

    pick_material(session, project.id, direction.id, "M003")
    with pytest.raises(gates.GateError):                      # 门 3 未确认 → 不能推荐版型
        recommend_patterns(session, project.id)
