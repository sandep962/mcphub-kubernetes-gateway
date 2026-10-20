import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from apps.gateway.app import main as gateway_main
from apps.gateway.app.services import mcp_client


class GatewayMcpRoutingTests(unittest.IsolatedAsyncioTestCase):
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
