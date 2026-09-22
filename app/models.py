"""数据表（SQLAlchemy 2.0）。一个项目 = 一次设计任务；五道门的状态都落在项目上。"""

from __future__ import annotations

import time

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import ROOT, get_config


class Base(DeclarativeBase):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), default="连衣裙")
    status: Mapped[str] = mapped_column(String(40), default="draft")
    created_at: Mapped[str] = mapped_column(String(32), default=_now)

    brief: Mapped[Brief | None] = relationship(back_populates="project", uselist=False)
    directions: Mapped[list[StyleDirection]] = relationship(back_populates="project")


class Brief(Base):
    __tablename__ = "brief"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    project: Mapped[Project] = relationship(back_populates="brief")


class StyleDirection(Base):
    __tablename__ = "style_direction"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    inspiration: Mapped[str] = mapped_column(Text)
    palette: Mapped[list] = mapped_column(JSON, default=list)
    silhouette: Mapped[str] = mapped_column(String(160))
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str | None] = mapped_column(String(240), nullable=True)
    image_status: Mapped[str] = mapped_column(String(24), default="pending")
    is_placeholder: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship(back_populates="directions")


class MaterialPick(Base):
    __tablename__ = "material_pick"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    direction_id: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    confirmed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PatternPick(Base):
    __tablename__ = "pattern_pick"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    pattern_id: Mapped[str] = mapped_column(String(40))
    crafts: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class PatternIteration(Base):
    """版型迭代记录（官方要求里有"版型迭代"，每一版都要能回溯改了什么、尺寸变成多少）。"""

    __tablename__ = "pattern_iteration"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    iteration_no: Mapped[int] = mapped_column(Integer, default=1)
    changes: Mapped[dict] = mapped_column(JSON, default=dict)
    size_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(32), default=_now)


class Sampling(Base):
    __tablename__ = "sampling"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    state: Mapped[str] = mapped_column(String(24), default="draft")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(32), default=_now)


_engine = None
_Session = None


def engine():
    global _engine, _Session
    url = get_config().db_url
    if url.startswith("sqlite:///"):
        path = Path(url.replace("sqlite:///", ""))
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{path}"
    if _engine is None:
        _engine = create_engine(url, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session() -> Session:
    engine()
    assert _Session is not None
    return _Session()


def reset_engine_for_tests() -> None:
    """测试用：切库后重建引擎（避免复用旧连接）。"""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def init_db() -> None:
    Base.metadata.create_all(engine())


from pathlib import Path  # noqa: E402  （放在末尾避免与上面 create_engine 的参数顺序混淆）


class DirectionScore(Base):
    """人工给方向图的打分（1–3 分）：用于校准出图质量（台账 D2/D3）。"""

    __tablename__ = "direction_score"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    direction_id: Mapped[int] = mapped_column(ForeignKey("style_direction.id"))
    score: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class PatternDesign(Base):
    """AI 生成的版型设计参数（每次生成存一条，便于回显与审计）。**非工厂纸样**。"""

    __tablename__ = "pattern_design"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class MaterialSuggestion(Base):
    """AI 生成的面料方案（**非真实物料**，价格为估算）。"""

    __tablename__ = "material_suggestion"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class CraftSuggestion(Base):
    """AI 生成的工艺建议（**非工厂工价/工时**）。"""

    __tablename__ = "craft_suggestion"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class TrimSuggestion(Base):
    """AI 生成的辅料/鞋材辅件建议（**非真实采购数据**，价格为估算）。"""

    __tablename__ = "trim_suggestion"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")


class TrimPick(Base):
    """采纳的辅料清单（采纳后 BOM 用这份；未采纳时用**按品类的内置兜底**）。"""

    __tablename__ = "trim_pick"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[str] = mapped_column(String(32), default="")
