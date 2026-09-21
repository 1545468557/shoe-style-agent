"""示例库读取（面料/辅料/版型/工艺/尺码）。

**示例库是仿真实例**（PRD FR-74 / AGENTS.md §2.10）：界面与导出必须标注「示例库」，
不得宣称"真实面料数据"。结构按"可接入企业真实库"的形状设计，未来换真库只替换本模块的实现。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SEED_DIR = Path(__file__).resolve().parents[1] / "seed"

FILES = {
    "materials": "materials.json",
    "trims": "trims.json",
    "patterns": "patterns.json",
    "crafts": "crafts.json",
    "sizes": "sizes.json",
}


class LibraryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@lru_cache(maxsize=1)
def _load() -> dict[str, list[dict]]:
    data: dict[str, list[dict]] = {}
    for key, filename in FILES.items():
        path = SEED_DIR / filename
        if not path.exists():
            raise LibraryError("library_missing", f"示例库缺少文件：{filename}")
        data[key] = json.loads(path.read_text(encoding="utf-8"))
    return data


def all_of(kind: str) -> list[dict]:
    if kind not in FILES:
        raise LibraryError("library_unknown_kind", f"没有这个库：{kind}")
    return list(_load()[kind])


def find(kind: str, item_id: str) -> dict:
    for item in all_of(kind):
        if item.get("id") == item_id:
            return item
    raise LibraryError("library_item_not_found", f"示例库里没有这个条目：{kind}/{item_id}")


def sizes_summary() -> list[dict]:
    return all_of("sizes")


def stats() -> dict[str, int]:
    return {kind: len(items) for kind, items in _load().items()}
