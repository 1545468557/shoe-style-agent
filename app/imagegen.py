"""出图通道：placeholder（兜底）/ ark（火山方舟 = 即梦底层 Seedream 系列）。

两条硬规矩：
1. **占位图必须可识别**：placeholder 画出的图上有"占位图"字样，且调用方要把 `is_placeholder=True`
   带回界面 —— 绝不允许拿占位图冒充真实出图；
2. 出图失败**如实报错**（不静默换图）；失败的方向可单独重试。
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from .config import get_config


@dataclass
class ImageResult:
    path: Path
    is_placeholder: bool
    provider: str


def _target(project_id: int, seq: int) -> Path:
    directory = get_config().assets_dir / str(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"direction_{seq}.png"


def _placeholder(text: str, path: Path) -> None:
    """画一张带文字的占位图（Pillow 画字，保证一眼能看出是占位）。"""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (768, 1024), (238, 242, 240))
    draw = ImageDraw.Draw(image)
    draw.rectangle([24, 24, 744, 1000], outline=(160, 180, 175), width=3)
    draw.text((56, 80), "占位图 / PLACEHOLDER", fill=(120, 140, 136))
    draw.text((56, 130), "未接入真实出图模型时使用", fill=(150, 165, 162))
    y = 200
    for line in text.split("，")[:8]:
        draw.text((56, y), line[:22], fill=(90, 110, 106))
        y += 36
    image.save(path)


def _ark(prompt: str, path: Path) -> None:
    """火山方舟图像生成（OpenAI 兼容 /images/generations）。"""
    import httpx

    cfg = get_config()
    if not cfg.image_model:
        raise RuntimeError("还没有配置 IMAGE_MODEL（控制台「模型广场」里开通的图像模型 ID）。")
    payload = {"model": cfg.image_model, "prompt": prompt, "size": cfg.image_size, "response_format": "url"}
    headers = {"Authorization": f"Bearer {cfg.image_api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=120) as client:
        response = client.post(f"{cfg.image_base_url.rstrip('/')}/images/generations", json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"出图接口返回 {response.status_code}：{response.text[:160]}")
        data = response.json().get("data") or [{}]
        item = data[0]
        if item.get("b64_json"):
            path.write_bytes(base64.b64decode(item["b64_json"]))
            return
        url = item.get("url")
        if not url:
            raise RuntimeError("出图接口没有返回图片地址。")
        image = client.get(url, timeout=120)
        if image.status_code >= 400:
            raise RuntimeError(f"下载出图结果失败：{image.status_code}")
        path.write_bytes(image.content)


def generate(prompt: str, project_id: int, seq: int) -> ImageResult:
    """按配置出图；返回落盘路径与"是否占位图"。"""
    cfg = get_config()
    path = _target(project_id, seq)
    if cfg.is_placeholder_image:
        _placeholder(prompt or "（无描述）", path)
        return ImageResult(path=path, is_placeholder=True, provider="placeholder")
    if cfg.image_provider == "ark":
        _ark(prompt, path)
        return ImageResult(path=path, is_placeholder=False, provider="ark")
    raise RuntimeError(f"还不支持这个出图通道：{cfg.image_provider}")


def retry_after_seconds(attempt: int) -> int:
    return min(2 ** attempt, 8)


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
