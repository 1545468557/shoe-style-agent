"""Server-only reader for approved CMS requirement packages.

The design Agent never accepts an unapproved package or exposes the CMS key to a browser.
"""

from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from ..config import get_config
from .store import AgentError


class ApprovedPackage(BaseModel):
    package_id: str = Field(min_length=1)
    source_agent: Literal["product"]
    signal_ids: list[str]
    source: str
    dedup_key: str
    dedup_rule: str
    sample_count: dict
    attribution: dict
    constraints: list[str] | None = None
    requirement_desc: str = Field(min_length=1)
    version: str = Field(min_length=1)
    status: Literal["approved"]
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    priority_basis: str | None = None


class CmsBridge:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None, transport=None):
        settings = get_config()
        self.base_url = (base_url if base_url is not None else settings.cms_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.cms_design_api_key
        self.transport = transport

    def _read(self, path: str, params: dict | None = None):
        if not self.base_url or not self.api_key:
            raise AgentError("CMS_NOT_CONFIGURED", "尚未配置 CMS 地址和设计专用密钥", 503)
        try:
            with httpx.Client(
                base_url=self.base_url, transport=self.transport, timeout=10.0, trust_env=False
            ) as client:
                response = client.get(path, params=params, headers={"X-API-Key": self.api_key})
        except httpx.RequestError as error:
            raise AgentError("CMS_UNAVAILABLE", "暂时无法连接 CMS，请稍后重试", 502) from error
        if response.status_code in (401, 403):
            raise AgentError("CMS_FORBIDDEN", "CMS 未授权设计端读取需求包", 502)
        if response.status_code == 404:
            raise AgentError("CMS_PACKAGE_NOT_FOUND", "CMS 中没有这条已批准需求包", 404)
        if response.status_code >= 400:
            raise AgentError("CMS_UNAVAILABLE", "CMS 暂时无法提供需求包", 502)
        try:
            return response.json()
        except ValueError as error:
            raise AgentError("CMS_INVALID_RESPONSE", "CMS 返回内容无法解析", 502) from error

    def list_approved(self, *, limit: int = 50, offset: int = 0) -> list[ApprovedPackage]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be nonnegative")
        payload = self._read("/api/packages", {"status": "approved", "limit": limit, "offset": offset})
        if not isinstance(payload, list):
            raise AgentError("CMS_INVALID_RESPONSE", "CMS 需求包列表格式异常", 502)
        try:
            return [ApprovedPackage.model_validate(item) for item in payload]
        except ValidationError as error:
            raise AgentError("CMS_INVALID_RESPONSE", "CMS 需求包内容与设计端契约不符", 502) from error

    def get_approved(self, package_id: str) -> ApprovedPackage:
        if not package_id or "/" in package_id or ".." in package_id:
            raise ValueError("invalid package_id")
        payload = self._read(f"/api/packages/{package_id}")
        try:
            return ApprovedPackage.model_validate(payload)
        except ValidationError as error:
            raise AgentError("CMS_INVALID_RESPONSE", "需求包尚未批准或字段不完整", 502) from error


if __name__ == "__main__":
    packages = CmsBridge().list_approved()
    print(f"CMS 已批准需求包：{len(packages)} 条")
    for package in packages:
        print(f"{package.package_id}  {package.version}  {package.priority or '未标优先级'}")
