#!/usr/bin/env python
"""示例库校验脚本：检查字段是否齐全、ID 是否重复，并打印规模。

**示例库只是仿真实例**（PRD 口径 + AGENTS.md §2.10）：
- 对外必须标注"示例库"，不得宣称"真实面料数据"；
- 结构上按"可接入企业真实库"的字段设计（这里是 JSON 起步，接口形状与未来真库一致）。

用法：cd projects/鞋服agent开发文档 && .venv/bin/python scripts/seed.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = ROOT / "seed"   # 示例库是**源码级数据**，放项目根下的 seed/（data/ 是运行数据、被 gitignore）

REQUIRED = {
    "materials": ("id", "name", "fiber", "weight_gsm", "price_yuan_per_m", "season"),
    "trims": ("id", "name", "spec"),   # 辅料价格按米或按个，两种字段任一即可（见下面单独校验）
    "patterns": ("id", "name", "fit", "waist_ease_cm", "length_options_cm"),
    "crafts": ("id", "name", "note"),
    "sizes": ("id", "system", "bust", "waist", "hip"),
}


def main() -> int:
    total, problems = 0, []
    for name, fields in REQUIRED.items():
        path = SEED / f"{name}.json"
        if not path.exists():
            problems.append(f"{name}.json 不存在")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = [item.get("id") for item in data]
        if len(ids) != len(set(ids)):
            problems.append(f"{name}: 有重复 ID")
        for item in data:
            missing = [f for f in fields if item.get(f) in (None, "", [])]
            if name == "trims" and not any(item.get(k) for k in ("price_yuan_per_m", "price_yuan_per_pc")):
                missing.append("价格（按米 price_yuan_per_m 或按个 price_yuan_per_pc 至少一个）")
            if missing:
                problems.append(f"{name}/{item.get('id')}: 缺字段 {missing}")
        total += len(data)
        print(f"  {name:10s} {len(data):3d} 条")
    print(f"\n示例库合计 {total} 条（仿真实例，界面必须标注「示例库」）")
    if problems:
        print("\n❌ 发现问题：")
        for line in problems:
            print("   -", line)
        return 1
    print("✅ 校验通过：字段齐全、ID 无重复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
