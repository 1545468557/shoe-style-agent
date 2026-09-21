"""风格方向生成：3 个方向的强制、门 1 拦截、占位图标记（离线可跑）。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'t.sqlite3'}")
    monkeypatch.setenv("ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    import app.config as cfg
    import app.models as models

    cfg.get_config.cache_clear()
    models.reset_engine_for_tests()
    models.init_db()
    session = models.session()
    row = models.Project(name="测试")
    session.add(row)
    session.commit()
    session.add(models.Brief(project_id=row.id, raw_text="夏天海边度假连衣裙，预算300元以内，年轻女性"))
    session.commit()
    return session, row


def test_directions_require_gate_one(project):
    session, row = project
    from app import gates
    from app.workflows.direction import generate_directions

    with pytest.raises(gates.GateError) as exc:
        generate_directions(session, row.id)
    assert exc.value.code == "gate_not_confirmed"        # 门 1 没过 → 直接拒


def test_generates_four_directions_with_placeholder_images(project):
    session, row = project
    from app import gates
    from app.models import StyleDirection
    from app.workflows.direction import generate_directions

    gates.confirm(session, row.id, "brief")               # 过门 1
    created = generate_directions(session, row.id)

    assert len(created) == 4
    assert [item.seq for item in created] == [1, 2, 3, 4]
    assert len({item.name for item in created}) == 4      # 四个方向名字不重复
    for item in created:
        assert item.image_status == "done"
        assert item.is_placeholder == 1                   # 无 Key → 必须是占位图，且被标记
        assert item.image_path and item.image_path.endswith(f"direction_{item.seq}.png")
    # 重跑要幂等：不累积重复方向
    assert len(session.query(StyleDirection).filter_by(project_id=row.id).all()) == 4


def test_brand_words_cleaned_in_image_prompt(project):
    session, row = project
    from app import gates
    from app.workflows.direction import generate_directions

    gates.confirm(session, row.id, "brief")
    created = generate_directions(session, row.id)
    for item in created:
        assert "nike" not in item.image_prompt.lower()
        assert "logo" not in item.image_prompt.lower()
