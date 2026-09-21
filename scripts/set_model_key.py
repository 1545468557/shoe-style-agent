#!/usr/bin/env python
"""交互式填写【文本模型】Key（DeepSeek 或兼容接口）——**输入不回显、不写日志、不进仓库**。

用法：
    cd projects/鞋服agent开发文档
    .venv/bin/python scripts/set_model_key.py

填完之后：企划解析、风格自主规划、面料搭配建议都会从"模板"变成"真模型"，本项目的离线 fallback 仍保留。
"""

from __future__ import annotations

import getpass
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def set_var(text: str, key: str, value: str) -> str:
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        return re.sub(rf"^{key}=.*$", lambda _: f"{key}={value}", text, flags=re.M)
    return text.rstrip() + f"\n{key}={value}\n"


def check_key(key: str) -> str:
    """形状校验，返回一句人话问题（空串=通过）。"""
    if not key:
        return "没有输入 Key。"
    if any(ch.isspace() for ch in key):
        return "里面有空格或换行，请只复制那一串。"
    if re.search(r"[^\x20-\x7e]", key):
        return "里面有中文或全角符号，多半把页面说明也复制进来了。"
    if key.lower().startswith("bearer"):
        return "不要带 Bearer 前缀，只要那串字符。"
    if len(key) > 120:
        return f"太长了（{len(key)} 字符），正常是 30–80 字符。"
    if len(key) < 20:
        return f"太短了（{len(key)} 字符），看起来没复制全。"
    if not key.startswith("sk-"):
        return "DeepSeek 的 Key 一般以 sk- 开头；如果你用的是火山方舟等兼容接口，忽略这条请按 y 继续。"
    return ""


def main() -> None:
    print("填写文本模型 Key（DeepSeek）—— 内容不会回显、不会进仓库")
    key = getpass.getpass("① 粘贴 MODEL_API_KEY（输入时不显示）：").strip()
    problem = check_key(key)
    if problem:
        if problem.startswith("DeepSeek") and key:
            if input("   仍然要保存吗？(y/N) ").strip().lower() != "y":
                print("已取消。")
                return
        else:
            print(f"❌ {problem}\n   请重新运行本命令。")
            return

    base = input("② 服务地址（直接回车用 https://api.deepseek.com）：").strip() or "https://api.deepseek.com"
    model = input("③ 模型名（直接回车用 deepseek-chat）：").strip() or "deepseek-chat"

    text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    text = set_var(text, "MODEL_API_KEY", key)
    text = set_var(text, "MODEL_BASE_URL", base)
    text = set_var(text, "MODEL_ID", model)
    text = set_var(text, "MODEL_PROVIDER", "deepseek")
    ENV.write_text(text, encoding="utf-8")
    ENV.chmod(0o600)

    print("\n已写入 .env（只显示有没有、多长，不显示内容）：")
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(("MODEL_", "IMAGE_PROVIDER")):
            name, _, value = line.partition("=")
            value = re.sub(r"\s+#.*$", "", value).strip()
            print(f"  {name:18s} {'已填（' + str(len(value)) + ' 字符）' if value else '（空）'}")
    print("\n下一步：回一句「填好了」，我会真模型跑一次解析 + 自主规划（约 ¥0.05–0.1）。")


if __name__ == "__main__":
    main()
