"""五道人工确认门（**代码强制**，不是提示词约定）。

顺序（PRD §13.1）：企划 → 风格方向 → 面料 → 版型/工艺 → 打样审批。
规则：
- 每道门只允许"前一步已完成"才可确认；跳步 → `gate_order_violation`；
- 任何"下一步"动作在门未确认时 → `gate_not_confirmed`（409，带门名与一句人话原因）；
- **这些判断都在后端**：前端把按钮藏起来不算数，直接调接口一样被拒。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Brief, MaterialPick, PatternPick, Project, Sampling, StyleDirection


class GateError(Exception):
    def __init__(self, code: str, message: str, gate: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.gate = code, message, gate


@dataclass(frozen=True)
class GateSpec:
    key: str
    name: str
    next_step: str


GATES: list[GateSpec] = [
    GateSpec("brief", "确认企划", "生成风格方向"),
    GateSpec("direction", "选定风格方向", "匹配面料"),
    GateSpec("material", "确认面料", "确认版型与工艺"),
    GateSpec("pattern", "确认版型与工艺", "导出 BOM 与尺寸表"),
    GateSpec("sampling", "打样审批", "完成本次设计"),
]


def status(session: Session, project_id: int) -> dict[str, bool]:
    """五道门各自是否已确认（界面据此禁用按钮；后端仍会再判一次）。"""
    brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
    directions = session.scalars(select(StyleDirection).where(StyleDirection.project_id == project_id)).all()
    material = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
    pattern = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
    sampling = session.scalar(select(Sampling).where(Sampling.project_id == project_id))
    return {
        "brief": bool(brief and brief.confirmed_at),
        "direction": any(item.selected for item in directions),
        "material": bool(material and material.confirmed_at),
        "pattern": bool(pattern and pattern.confirmed_at),
        "sampling": bool(sampling and sampling.state in {"approved", "pushed", "sampling", "reviewed"}),
    }


def _require(flags: dict[str, bool], keys: list[str]) -> None:
    for key in keys:
        if not flags.get(key):
            spec = next(item for item in GATES if item.key == key)
            raise GateError(
                "gate_not_confirmed",
                f"「{spec.name}」还没有确认，不能进行「{spec.next_step}」。",
                gate=key,
            )


def require_for_directions(session: Session, project_id: int) -> None:
    """生成风格方向前：企划必须已确认（**门 1**）。"""
    _require(status(session, project_id), ["brief"])


def require_for_materials(session: Session, project_id: int) -> None:
    """匹配面料前：企划 + 方向（**门 1、2**）都要过。"""
    _require(status(session, project_id), ["brief", "direction"])


def require_for_pattern(session: Session, project_id: int) -> None:
    """版型/工艺前：门 1、2、3 都要过。"""
    _require(status(session, project_id), ["brief", "direction", "material"])


def require_for_export(session: Session, project_id: int) -> None:
    """导出前：门 1、2、3、4 都要过（打样审批是最后一步，不阻塞导出）。"""
    _require(status(session, project_id), ["brief", "direction", "material", "pattern"])


def require_for_sampling(session: Session, project_id: int) -> None:
    """提交打样前：前四道门都要过。"""
    _require(status(session, project_id), ["brief", "direction", "material", "pattern"])


def confirm(session: Session, project_id: int, gate: str, payload: dict | None = None) -> dict:
    """确认一道门。**顺序不可跳**：前一道门必须先确认（跳步 → gate_order_violation）。"""
    order = [item.key for item in GATES]
    if gate not in order:
        raise GateError("unknown_gate", f"没有这道门：{gate}")
    flags = status(session, project_id)
    index = order.index(gate)
    for earlier in order[:index]:
        if not flags[earlier]:
            spec = next(item for item in GATES if item.key == earlier)
            raise GateError(
                "gate_order_violation",
                f"要按顺序来：请先完成「{spec.name}」。",
                gate=earlier,
            )
    project = session.get(Project, project_id)
    if project is None:
        raise GateError("project_not_found", "找不到这个项目。")

    now = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
    if gate == "brief":
        brief = session.scalar(select(Brief).where(Brief.project_id == project_id))
        if brief is None:
            raise GateError("brief_missing", "还没有提交企划。")
        brief.confirmed_at = brief.confirmed_at or now
        project.status = "brief_confirmed"
    elif gate == "direction":
        direction_id = int((payload or {}).get("direction_id") or 0)
        target = session.get(StyleDirection, direction_id)
        if target is None or target.project_id != project_id:
            raise GateError("direction_not_found", "请选择一个本项目的风格方向。")
        for item in session.scalars(select(StyleDirection).where(StyleDirection.project_id == project_id)):
            item.selected = 1 if item.id == direction_id else 0
        project.status = "direction_selected"
    elif gate == "material":
        pick = session.scalar(select(MaterialPick).where(MaterialPick.project_id == project_id))
        if pick is None:
            raise GateError("material_missing", "还没有选定面料。")
        pick.confirmed_at = pick.confirmed_at or now
        project.status = "material_confirmed"
    elif gate == "pattern":
        pick = session.scalar(select(PatternPick).where(PatternPick.project_id == project_id))
        if pick is None:
            raise GateError("pattern_missing", "还没有选定版型与工艺。")
        pick.confirmed_at = pick.confirmed_at or now
        project.status = "pattern_confirmed"
    elif gate == "sampling":
        row = session.scalar(select(Sampling).where(Sampling.project_id == project_id))
        if row is None:
            row = Sampling(project_id=project_id, state="approved")
            session.add(row)
        row.state = "approved"
        row.updated_at = now
        project.status = "sampling_approved"
    session.commit()
    return status(session, project_id)
