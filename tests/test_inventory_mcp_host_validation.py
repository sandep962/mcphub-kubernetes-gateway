import importlib.util
import unittest

from starlette.testclient import TestClient
from pathlib import Path


SERVER_PATH = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "inventory-mcp"
    / "server.py"
)
SPEC = importlib.util.spec_from_file_location("inventory_mcp_server", SERVER_PATH)
inventory_server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory_server)


class InventoryMcpHostValidationTests(unittest.TestCase):
    def test_internal_and_local_hosts_are_allowed_with_protection_enabled(self):
        settings = inventory_server.mcp.settings.transport_security

        self.assertTrue(settings.enable_dns_rebinding_protection)
        self.assertTrue(inventory_server.mcp.settings.stateless_http)
        self.assertEqual(
            settings.allowed_hosts,
            [
                "mcphub-inventory-mcp.mcphub.svc.cluster.local:8002",
                "localhost:8002",
                "127.0.0.1:8002",
            ],
        )

    def test_mcp_endpoint_accepts_internal_host_and_rejects_unlisted_host(self):
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        with TestClient(inventory_server.app) as client:
            allowed = client.post(
                "/mcp",
                headers={
                    **headers,
                    "Host": "mcphub-inventory-mcp.mcphub.svc.cluster.local:8002",
                },
                json=init_request,
            )
            listed = client.post(
                "/mcp",
                headers={
                    **headers,
                    "Host": "mcphub-inventory-mcp.mcphub.svc.cluster.local:8002",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
            )
            rejected = client.post(
                "/mcp",
                headers={**headers, "Host": "attacker.invalid:8002"},
                json={**init_request, "id": 2},
            )

        self.assertEqual(allowed.status_code, 200)
        self.assertNotIn("mcp-session-id", allowed.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn("list_inventory", listed.text)
        self.assertEqual(rejected.status_code, 421)


if __name__ == "__main__":
    unittest.main()
