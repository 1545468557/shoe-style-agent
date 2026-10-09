import httpx
import pytest

from app.agent.cms_bridge import CmsBridge
from app.agent.store import AgentError

PACKAGE = {
    "package_id": "PKG-1",
    "source_agent": "product",
    "signal_ids": ["SIG-1"],
    "source": "穿搭信号",
    "dedup_key": "通勤-裤装",
    "dedup_rule": "相同场景合并",
    "sample_count": {"intent": 3, "simulated_cart": 1, "real_purchase": 0},
    "attribution": {"supply_gap": False, "image_gap": False, "style_demand": True},
    "constraints": ["通勤场景"],
    "requirement_desc": "改善通勤西裤的裤长适配",
    "version": "v1",
    "status": "approved",
}


def test_lists_only_approved_packages_with_server_key():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[PACKAGE])

    bridge = CmsBridge(
        base_url="https://cms.example", api_key="private-design-key", transport=httpx.MockTransport(respond)
    )
    result = bridge.list_approved(limit=5)
    assert result[0].package_id == "PKG-1"
    assert requests[0].url.path == "/api/packages"
    assert requests[0].url.params["status"] == "approved"
    assert requests[0].headers["X-API-Key"] == "private-design-key"


def test_rejects_unapproved_data_even_if_cms_returns_it():
    bridge = CmsBridge(
        base_url="https://cms.example", api_key="key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=[{**PACKAGE, "status": "pending"}])),
    )
    with pytest.raises(AgentError, match="契约"):
        bridge.list_approved()


def test_missing_key_and_unauthorized_response_fail_closed():
    with pytest.raises(AgentError) as missing:
        CmsBridge(base_url="https://cms.example", api_key="").list_approved()
    assert missing.value.code == "CMS_NOT_CONFIGURED"

    bridge = CmsBridge(
        base_url="https://cms.example", api_key="wrong",
        transport=httpx.MockTransport(lambda _: httpx.Response(403)),
    )
    with pytest.raises(AgentError) as forbidden:
        bridge.list_approved()
    assert forbidden.value.code == "CMS_FORBIDDEN"
