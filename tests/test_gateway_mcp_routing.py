import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from apps.gateway.app import main as gateway_main
from apps.gateway.app.services import mcp_client


class GatewayMcpRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_status_reports_connected_unreachable_and_disabled(self):
        rows = [
            {"name": "inventory", "endpoint_url": "http://inventory/mcp", "enabled": True},
            {"name": "support", "endpoint_url": "http://support/mcp", "enabled": True},
            {"name": "woocommerce", "endpoint_url": "http://woocommerce/mcp", "enabled": False},
        ]

        async def discover(server_name, _server_url):
            if server_name == "support":
                raise OSError("connection refused")
            return []

        with (
            patch.object(mcp_client.database, "list_upstreams", return_value=rows),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            result = await mcp_client.list_upstream_statuses()

        self.assertEqual(
            result,
            [
                {"name": "inventory", "status": "Connected"},
                {"name": "support", "status": "Unreachable", "reason": "connection_error"},
                {"name": "woocommerce", "status": "Disabled"},
            ],
        )
        self.assertNotIn("endpoint_url", str(result))

    async def test_upstream_status_times_out_one_server_and_checks_healthy_peer(self):
        rows = [
            {"name": "slow", "endpoint_url": "http://slow/mcp", "enabled": True},
            {"name": "healthy", "endpoint_url": "http://healthy/mcp", "enabled": True},
        ]
        checked = []

        async def discover(server_name, _server_url):
            checked.append(server_name)
            if server_name == "slow":
                await asyncio.sleep(0.2)
            return []

        with (
            patch.object(mcp_client.database, "list_upstreams", return_value=rows),
            patch.object(mcp_client, "_list_server_tools", discover),
            patch.object(mcp_client, "UPSTREAM_DISCOVERY_TIMEOUT_SECONDS", 0.05),
        ):
            result = await mcp_client.list_upstream_statuses()

        self.assertEqual(
            result,
            [
                {"name": "slow", "status": "Unreachable", "reason": "timeout"},
                {"name": "healthy", "status": "Connected"},
            ],
        )
        self.assertCountEqual(checked, ["slow", "healthy"])

    async def test_upstream_status_reports_sanitized_timeout_and_discovery_reasons(self):
        rows = [
            {"name": "slow", "endpoint_url": "http://secret-host/mcp", "enabled": True},
            {"name": "broken", "endpoint_url": "http://secret-host/mcp", "enabled": True},
        ]

        async def discover(server_name, _server_url):
            if server_name == "slow":
                raise TimeoutError("sensitive URL and response details")
            raise RuntimeError("sensitive URL and response details")

        with (
            patch.object(mcp_client.database, "list_upstreams", return_value=rows),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            result = await mcp_client.list_upstream_statuses()

        self.assertEqual(
            result,
            [
                {"name": "slow", "status": "Unreachable", "reason": "timeout"},
                {"name": "broken", "status": "Unreachable", "reason": "discovery_error"},
            ],
        )
        self.assertNotIn("sensitive", repr(result))
        self.assertNotIn("secret-host", repr(result))

    async def test_upstream_status_endpoint_requires_identity_and_redacts_endpoint(self):
        rows = [{"name": "inventory", "endpoint_url": "http://private/mcp", "enabled": False}]
        with patch.object(mcp_client.database, "list_upstreams", return_value=rows):
            gateway_main.app.dependency_overrides[gateway_main.require_identity] = lambda: {
                "identity_id": 7,
                "team_id": 2,
                "key_id": 10,
                "role": "viewer",
            }
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway_main.app),
                    base_url="http://test",
                ) as client:
                    response = await client.get("/upstreams/status")
            finally:
                gateway_main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"name": "inventory", "status": "Disabled"}])
        self.assertNotIn("private", response.text)

    async def test_upstream_status_endpoint_returns_only_fixed_failure_reason(self):
        rows = [{"name": "support", "endpoint_url": "http://private-host/mcp", "enabled": True}]

        async def discover(_server_name, _server_url):
            raise OSError("private-host and sensitive response content")

        with (
            patch.object(mcp_client.database, "list_upstreams", return_value=rows),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            gateway_main.app.dependency_overrides[gateway_main.require_identity] = lambda: {
                "identity_id": 7, "team_id": 2, "key_id": 10, "role": "viewer",
            }
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway_main.app), base_url="http://test"
                ) as client:
                    response = await client.get("/upstreams/status")
            finally:
                gateway_main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            [{"name": "support", "status": "Unreachable", "reason": "connection_error"}],
        )
        self.assertNotIn("private-host", response.text)
        self.assertNotIn("sensitive", response.text)

    async def test_discovers_tools_from_multiple_upstreams(self):
        upstream_tools = [
            [
                {
                    "name": "list_products",
                    "description": "List products",
                    "server": "woocommerce",
                }
            ],
            [
                {
                    "name": "list_inventory",
                    "description": "List inventory",
                    "server": "inventory",
                }
            ],
        ]
        discover = AsyncMock(side_effect=upstream_tools)

        with (
            patch.dict(
                mcp_client.MCP_SERVERS,
                {
                    "woocommerce": "http://woo/mcp",
                    "inventory": "http://inventory/mcp",
                },
                clear=True,
            ),
            patch.object(mcp_client, "_enabled_servers", lambda: dict(mcp_client.MCP_SERVERS)),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            result = await mcp_client.list_all_tools()

        self.assertEqual(result, upstream_tools[0] + upstream_tools[1])
        self.assertEqual(discover.await_count, 2)

    async def test_keeps_other_upstream_tools_when_one_upstream_fails(self):
        inventory_tools = [
            {
                "name": "list_inventory",
                "description": "List inventory",
                "server": "inventory",
            }
        ]
        discover = AsyncMock(
            side_effect=[TimeoutError("WooCommerce unavailable"), inventory_tools]
        )

        with (
            patch.dict(
                mcp_client.MCP_SERVERS,
                {
                    "woocommerce": "http://woo/mcp",
                    "inventory": "http://inventory/mcp",
                },
                clear=True,
            ),
            patch.object(mcp_client, "_enabled_servers", lambda: dict(mcp_client.MCP_SERVERS)),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            result = await mcp_client.list_all_tools()

        self.assertEqual(result, inventory_tools)
        self.assertEqual(discover.await_count, 2)

    async def test_slow_upstream_times_out_without_blocking_healthy_discovery(self):
        inventory_tools = [
            {
                "name": "list_inventory",
                "description": "List inventory",
                "server": "inventory",
            }
        ]

        async def discover(server_name, _server_url):
            if server_name == "woocommerce":
                await asyncio.sleep(0.2)
                return []
            return inventory_tools

        with (
            patch.dict(
                mcp_client.MCP_SERVERS,
                {
                    "woocommerce": "http://woo/mcp",
                    "inventory": "http://inventory/mcp",
                },
                clear=True,
            ),
            patch.object(mcp_client, "_enabled_servers", lambda: dict(mcp_client.MCP_SERVERS)),
            patch.object(mcp_client, "_list_server_tools", discover),
            patch.object(mcp_client, "UPSTREAM_DISCOVERY_TIMEOUT_SECONDS", 0.05),
        ):
            result = await mcp_client.list_all_tools()

        self.assertEqual(result, inventory_tools)

    async def test_disabled_upstream_is_not_discovered_or_callable(self):
        discover = AsyncMock()
        with (
            patch.object(mcp_client, "_enabled_servers", return_value={}),
            patch.object(mcp_client, "_list_server_tools", discover),
        ):
            self.assertEqual(await mcp_client.list_all_tools(), [])
            with self.assertRaises(ValueError):
                await mcp_client.call_server_tool("inventory", "list_inventory", {})
        discover.assert_not_awaited()

    async def test_tool_call_deadline_covers_execution_and_does_not_block_healthy_server(self):
        async def execute(_url, server_name, tool_name, _arguments):
            if server_name == "slow":
                await asyncio.sleep(0.2)
            return {"tool": tool_name, "server": server_name, "is_error": False}

        with (
            patch.object(
                mcp_client,
                "_enabled_servers",
                return_value={"slow": "http://internal-slow/mcp", "healthy": "http://healthy/mcp"},
            ),
            patch.object(mcp_client, "_execute_server_tool", execute),
            patch.object(mcp_client, "UPSTREAM_TOOL_CALL_TIMEOUT_SECONDS", 0.05),
        ):
            slow, healthy = await asyncio.gather(
                mcp_client.call_server_tool("slow", "slow_tool", {}),
                mcp_client.call_server_tool("healthy", "healthy_tool", {}),
                return_exceptions=True,
            )

        self.assertIsInstance(slow, mcp_client.UpstreamToolCallTimeout)
        self.assertEqual(
            healthy,
            {"tool": "healthy_tool", "server": "healthy", "is_error": False},
        )

    async def test_rest_tool_timeout_is_sanitized_and_audited(self):
        identity = {
            "identity_id": 17,
            "team_id": 2,
            "key_id": 23,
            "role": "operator",
        }
        available_tools = [{"name": "list_inventory", "server": "inventory"}]

        async def hang(*_args, **_kwargs):
            await asyncio.sleep(0.2)

        with (
            patch.object(gateway_main, "list_all_tools", AsyncMock(return_value=available_tools)),
            patch.object(gateway_main.database, "is_authorized", return_value=True),
            patch.object(gateway_main.database, "record_audit") as audit,
            patch.object(
                mcp_client,
                "_enabled_servers",
                return_value={"inventory": "http://private.internal/mcp"},
            ),
            patch.object(
                mcp_client,
                "_execute_server_tool",
                hang,
            ),
            patch.object(mcp_client, "UPSTREAM_TOOL_CALL_TIMEOUT_SECONDS", 0.01),
        ):
            gateway_main.app.dependency_overrides[gateway_main.require_identity] = lambda: identity
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=gateway_main.app),
                    base_url="http://test",
                ) as client:
                    response = await client.post(
                        "/call", json={"tool": "list_inventory", "arguments": {}}
                    )
            finally:
                gateway_main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.json(), {"detail": "The upstream MCP tool call timed out."})
        self.assertNotIn("private.internal", response.text)
        audit_row = audit.call_args.kwargs
        self.assertEqual(audit_row["outcome"], "failure")
        self.assertEqual(audit_row["error_class"], "upstream_tool_timeout")
        self.assertEqual(audit_row["identity"], identity)
        self.assertEqual(audit_row["server_name"], "inventory")
        self.assertEqual(audit_row["tool_name"], "list_inventory")
        self.assertGreaterEqual(audit_row["duration_ms"], 0)

    async def test_call_endpoint_routes_inventory_tool_to_inventory_server(self):
        available_tools = [
            {
                "name": "list_inventory",
                "description": "List inventory",
                "server": "inventory",
            }
        ]
        call = AsyncMock(
            return_value={
                "tool": "list_inventory",
                "server": "inventory",
                "is_error": False,
                "content": [],
                "structured_content": [],
            }
        )

        with (
            patch.object(
                gateway_main,
                "list_all_tools",
                AsyncMock(return_value=available_tools),
            ),
            patch.object(gateway_main, "call_server_tool", call),
            patch.object(gateway_main.database, "is_authorized", return_value=True),
            patch.object(gateway_main.database, "record_audit") as audit,
        ):
            gateway_main.app.dependency_overrides[gateway_main.require_identity] = lambda: {
                "identity_id": 7,
                "team_id": 2,
                "key_id": 10,
                "role": "operator",
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway_main.app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/call", json={"tool": "list_inventory", "arguments": {}}
                )
            gateway_main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["server"], "inventory")
        call.assert_awaited_once_with("inventory", "list_inventory", {})
        self.assertIsInstance(audit.call_args.kwargs["duration_ms"], int)

    async def test_call_endpoint_rejects_ambiguous_tool_names(self):
        duplicate_tools = [
            {"name": "list_items", "server": "inventory"},
            {"name": "list_items", "server": "support"},
        ]
        call = AsyncMock()

        with (
            patch.object(
                gateway_main,
                "list_all_tools",
                AsyncMock(return_value=duplicate_tools),
            ),
            patch.object(gateway_main, "call_server_tool", call),
            patch.object(gateway_main.database, "record_audit") as audit,
        ):
            gateway_main.app.dependency_overrides[gateway_main.require_identity] = lambda: {
                "identity_id": 7,
                "team_id": 2,
                "key_id": 10,
                "role": "operator",
            }
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=gateway_main.app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    "/call", json={"tool": "list_items", "arguments": {}}
                )
            gateway_main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(audit.call_args.kwargs["error_class"], "ambiguous_tool")
        call.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
