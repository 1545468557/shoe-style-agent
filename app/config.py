"""配置：只从环境变量/.env 读取；密钥不回显、不进日志。"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


class Config:
    def __init__(self) -> None:
        _load_env()
        self.model_provider = os.environ.get("MODEL_PROVIDER", "mock")
        self.model_api_key = os.environ.get("MODEL_API_KEY", "")
        self.model_base_url = os.environ.get("MODEL_BASE_URL", "https://api.deepseek.com")
        self.model_id = os.environ.get("MODEL_ID", "deepseek-chat")
        self.image_provider = os.environ.get("IMAGE_PROVIDER", "placeholder")
        self.image_api_key = os.environ.get("IMAGE_API_KEY", "")
        # 火山方舟（即梦底层模型 Seedream 系列）走 OpenAI 兼容接口
        self.image_base_url = os.environ.get("IMAGE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        self.image_model = os.environ.get("IMAGE_MODEL", "")
        self.image_size = os.environ.get("IMAGE_SIZE", "2K")  # 方舟 seedream 要求 ≥3,686,400 像素
        self.port = int(os.environ.get("APP_PORT", "8020"))
        self.db_url = os.environ.get("DATABASE_URL", "sqlite:///data/app.sqlite3")
        self.assets_dir = Path(os.environ.get("ASSETS_DIR", "data/assets"))
        if not self.assets_dir.is_absolute():
            self.assets_dir = ROOT / self.assets_dir
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_mock_model(self) -> bool:
        """没有 Key 或显式指定 mock → 走 mock 模型（不阻塞开发，只阻塞真实验收）。"""
        return self.model_provider == "mock" or not self.model_api_key

    @property
    def is_placeholder_image(self) -> bool:
        """出图是否为占位图（界面必须明示，不得冒充真图）。"""
        return self.image_provider == "placeholder" or not self.image_api_key


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
