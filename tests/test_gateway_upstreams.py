from datetime import datetime
import asyncio
from unittest.mock import patch

import httpx
import pytest

from apps.gateway.app import main


def row(**overrides):
    result = {"id": 9, "name": "inventory-extra", "endpoint_url": "https://internal.example/mcp", "enabled": True, "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1)}
    result.update(overrides)
    return result


async def _client_request(method, path, *, body=None, role="admin"):
    main.app.dependency_overrides[main.require_identity] = lambda: {"identity_id": 1, "team_id": 1, "role": role}
    try:
        with patch.object(main.database, "record_audit"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                return await client.request(method, path, json=body)
    finally:
        main.app.dependency_overrides.clear()


def test_upstream_admin_crud_and_response_redacts_endpoint_url():
    async def run():
        with (
            patch.object(main.database, "list_upstreams", return_value=[row()]),
            patch.object(main.database, "create_upstream", return_value=row()),
            patch.object(main.database, "update_upstream", return_value=row(name="inventory-renamed")),
            patch.object(main.database, "set_upstream_enabled", return_value=True),
            patch.object(main.database, "delete_upstream", return_value=True),
        ):
            listed = await _client_request("get", "/admin/upstreams")
            created = await _client_request("post", "/admin/upstreams", body={"name": "inventory-extra", "endpoint_url": "https://internal.example/mcp"})
            updated = await _client_request("put", "/admin/upstreams/9", body={"name": "inventory-renamed", "endpoint_url": "https://internal.example/v2/mcp"})
            disabled = await _client_request("patch", "/admin/upstreams/9/enabled", body={"enabled": False})
            removed = await _client_request("delete", "/admin/upstreams/9")
            return listed, created, updated, disabled, removed
    listed, created, updated, disabled, removed = asyncio.run(run())
    assert listed.status_code == 200 and listed.json()[0]["name"] == "inventory-extra"
    assert created.status_code == 201 and updated.json()["name"] == "inventory-renamed"
    assert disabled.json() == {"id": 9, "enabled": False}
    assert removed.json() == {"deleted": True}
    for response in (listed, created, updated):
        assert "endpoint_url" not in response.text
        assert "internal.example" not in response.text


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/admin/upstreams", None),
        ("post", "/admin/upstreams", {"name": "test", "endpoint_url": "https://upstream.example/mcp"}),
        ("put", "/admin/upstreams/9", {"name": "test", "endpoint_url": "https://upstream.example/mcp"}),
        ("patch", "/admin/upstreams/9/enabled", {"enabled": False}),
        ("delete", "/admin/upstreams/9", None),
    ],
)
def test_upstream_management_rejects_non_admin(method, path, body):
    response = asyncio.run(_client_request(method, path, body=body, role="viewer"))
    assert response.status_code == 403


def test_upstream_url_rejects_embedded_credentials_and_query_tokens():
    from apps.shared.database import _validate_upstream
    with pytest.raises(ValueError):
        _validate_upstream("safe", "https://user:password@upstream.example/mcp")
    with pytest.raises(ValueError):
        _validate_upstream("safe", "https://upstream.example/mcp?token=secret")
