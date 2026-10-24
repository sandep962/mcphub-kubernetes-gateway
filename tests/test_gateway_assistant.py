from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from apps.gateway.app import main, mcp_endpoint

TOOLS = [
    {"name": "list_products", "server": "woocommerce", "description": "List products", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_orders", "server": "woocommerce", "description": "List orders", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_inventory", "server": "inventory", "description": "List inventory", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_tickets", "server": "support", "description": "List support tickets", "inputSchema": {"type": "object", "properties": {}}},
]
IDENTITY = {"identity_id": 17, "team_id": 4, "key_id": 29, "identity_name": "test-user", "role": "operator", "identity_type": "user"}


def completion(content: str = "", calls: list[dict] | None = None) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": calls or []}}]}


def tool_call(name: str, arguments: str = "{}", call_id: str = "call-1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


class GatewayAssistantTests(unittest.IsolatedAsyncioTestCase):
    async def request_assistant(self, model_responses, *, authorized_servers=None, configured=True, tool_result=None, auth_override=True, client_identity=None):
        env = {
            "MCPHUB_LLM_BASE_URL": "https://llm.example/v1",
            "MCPHUB_LLM_API_KEY": "test-provider-key",
            "MCPHUB_LLM_MODEL": "test-tool-model",
        }
        if not configured:
            env = {key: "" for key in env}
        authorize = lambda identity, server, name: authorized_servers is None or server in authorized_servers
        provider = AsyncMock(side_effect=model_responses)
        if tool_result is None:
            tool_result = {"tool": "test", "server": "test", "is_error": False, "content": [], "structured_content": {"ok": True}}
        upstream = AsyncMock(return_value=tool_result)
        main.app.dependency_overrides.clear()
        if auth_override:
            main.app.dependency_overrides[main.require_identity] = lambda: IDENTITY
        try:
            with (
                patch.dict(os.environ, env, clear=False),
                patch.object(main, "_request_chat_completion", provider),
                patch.object(main, "list_all_tools", AsyncMock(return_value=TOOLS)),
                patch.object(mcp_endpoint, "list_all_tools", AsyncMock(return_value=TOOLS)),
                patch.object(main.database, "is_authorized", side_effect=authorize),
                patch.object(mcp_endpoint.database, "is_authorized", side_effect=authorize),
                patch.object(main.database, "record_audit"),
                patch.object(mcp_endpoint.database, "record_audit") as mcp_audit,
                patch.object(mcp_endpoint, "call_server_tool", upstream),
            ):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                    request_body = {"messages": [{"role": "user", "content": "Help me"}]}
                    if client_identity is not None:
                        request_body["identity"] = client_identity
                    response = await client.post("/assistant", json=request_body)
                return response, provider, upstream, mcp_audit
        finally:
            main.app.dependency_overrides.clear()

    async def test_provider_http_failure_reports_status_without_exposing_body(self):
        request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
        response = httpx.Response(
            429,
            request=request,
            json={"error": {"message": "sensitive provider response body"}},
        )
        transport = httpx.MockTransport(lambda _: response)
        real_client = httpx.AsyncClient
        with patch("httpx.AsyncClient", side_effect=lambda *args, **kwargs: real_client(transport=transport, **kwargs)):
            with self.assertRaises(main.HTTPException) as raised:
                await main._request_chat_completion(
                    "https://llm.example/v1",
                    "test-provider-key",
                    {"model": "test-tool-model", "messages": []},
                )
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("HTTP 429", raised.exception.detail)
        self.assertNotIn("sensitive provider response body", raised.exception.detail)

    async def test_help_request_gets_model_answer_with_available_tool_examples(self):
        response, provider, upstream, _ = await self.request_assistant([completion("Ask me to list products, inspect inventory, or review support tickets.")])
        self.assertEqual(response.status_code, 200, response.text)
        payload = provider.await_args.args[2]
        names = [item["function"]["name"] for item in payload["tools"]]
        self.assertIn("inventory__list_inventory", names)
        self.assertIn("support__list_tickets", names)
        self.assertIn("woocommerce__list_products", names)
        self.assertIn("Available tools", payload["messages"][0]["content"])
        self.assertEqual(response.json()["tool_calls"], [])
        upstream.assert_not_awaited()

    async def test_natural_language_request_selects_tool_from_available_servers(self):
        response, provider, upstream, mcp_audit = await self.request_assistant([completion(calls=[tool_call("woocommerce__list_orders")]), completion("There are two orders.")])
        self.assertEqual(response.status_code, 200, response.text)
        upstream.assert_awaited_once_with("woocommerce", "list_orders", {})
        self.assertEqual(response.json()["tool_calls"], [{"name": "woocommerce__list_orders", "outcome": "succeeded"}])
        self.assertEqual(provider.await_count, 2)
        success = next(call.kwargs for call in mcp_audit.call_args_list if call.kwargs.get("outcome") == "success")
        self.assertEqual(success["identity"], IDENTITY)

    async def test_chat_call_uses_authenticated_identity_not_client_identity(self):
        response, _, upstream, mcp_audit = await self.request_assistant(
            [completion(calls=[tool_call("inventory__list_inventory")]), completion("Inventory is ready.")],
            client_identity={"identity_id": 999, "role": "admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        upstream.assert_awaited_once_with("inventory", "list_inventory", {})
        audit = next(
            call.kwargs for call in mcp_audit.call_args_list
            if call.kwargs.get("event_type") == "tool_call"
        )
        self.assertEqual(audit["identity"]["identity_id"], IDENTITY["identity_id"])
        self.assertNotEqual(audit["identity"]["identity_id"], 999)

    async def test_invalid_model_arguments_are_audited_with_validated_tool_and_duration(self):
        response, _, _, mcp_audit = await self.request_assistant(
            [
                completion(calls=[tool_call("inventory__list_inventory", arguments='["not-an-object"]')]),
                completion("The request arguments were invalid."),
            ]
        )
        self.assertEqual(response.status_code, 200, response.text)
        audit = next(
            call.kwargs for call in mcp_audit.call_args_list
            if call.kwargs.get("error_class") == "invalid_assistant_arguments"
        )
        self.assertEqual(audit["identity"], IDENTITY)
        self.assertEqual(audit["server_name"], "inventory")
        self.assertEqual(audit["tool_name"], "list_inventory")
        self.assertEqual(audit["outcome"], "denied")
        self.assertGreaterEqual(audit["duration_ms"], 0)
        self.assertNotIn("not-an-object", repr(audit))

    async def test_agent_supports_multiple_rounds_across_upstream_servers(self):
        response, provider, upstream, _ = await self.request_assistant([
            completion(calls=[tool_call("inventory__list_inventory", call_id="call-1")]),
            completion(calls=[tool_call("support__list_tickets", call_id="call-2")]),
            completion("Inventory and support summaries are ready."),
        ])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(upstream.await_count, 2)
        self.assertEqual(provider.await_count, 3)
        final_messages = provider.await_args_list[2].args[2]["messages"]
        self.assertEqual([item["role"] for item in final_messages].count("tool"), 2)
        self.assertEqual([item["outcome"] for item in response.json()["tool_calls"]], ["succeeded", "succeeded"])

    async def test_tool_failure_is_returned_to_model_and_shown_in_outcome(self):
        response, _, upstream, _ = await self.request_assistant(
            [completion(calls=[tool_call("inventory__list_inventory")]), completion("Inventory lookup failed; please retry later.")],
            tool_result={"tool": "list_inventory", "server": "inventory", "is_error": True, "content": [], "structured_content": None},
        )
        self.assertEqual(response.status_code, 200, response.text)
        upstream.assert_awaited_once()
        self.assertEqual(response.json()["tool_calls"][0]["outcome"], "failed")
        self.assertIn("Inventory lookup failed", response.json()["answer"])

    async def test_unauthorized_tool_from_model_is_denied_and_audited(self):
        response, _, upstream, mcp_audit = await self.request_assistant(
            [completion(calls=[tool_call("support__list_tickets")]), completion("I do not have permission to access support tickets.")],
            authorized_servers={"inventory"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tool_calls"][0]["outcome"], "failed")
        upstream.assert_not_awaited()
        denial = next(call.kwargs for call in mcp_audit.call_args_list if call.kwargs.get("error_class") == "permission_denied")
        self.assertEqual(denial["identity"], IDENTITY)

    async def test_missing_provider_configuration_returns_clear_setup_error(self):
        response, provider, _, _ = await self.request_assistant([completion("unused")], configured=False)
        self.assertEqual(response.status_code, 503)
        self.assertIn("MCPHUB_LLM_API_KEY", response.json()["detail"])
        provider.assert_not_awaited()

    async def test_invalid_api_key_is_rejected_before_agent_execution(self):
        provider = AsyncMock()
        main.app.dependency_overrides.clear()
        try:
            with patch.object(main.database, "verify_api_key", return_value=None), patch.object(main.database, "record_audit"), patch.object(main, "_request_chat_completion", provider):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                    response = await client.post("/assistant", headers={"X-API-Key": "invalid"}, json={"messages": [{"role": "user", "content": "Show my orders"}]})
        finally:
            main.app.dependency_overrides.clear()
        self.assertEqual(response.status_code, 401)
        provider.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

