import asyncio
import json
import pytest

from apps.gateway.app.mcp_endpoint import (
    external_tool_name,
    mcp_http_app,
    split_external_tool_name,
)


def test_external_tool_name_is_namespaced():
    assert external_tool_name("inventory", "list_inventory") == "inventory__list_inventory"


def test_split_external_tool_name():
    assert split_external_tool_name("support__create_ticket") == ("support", "create_ticket")


@pytest.mark.parametrize(
    "name",
    [
        "list_inventory",
        "__list_inventory",
        "inventory__",
        "unknown__list_inventory",
    ],
)
def test_split_rejects_unqualified_or_invalid_names(name):
    assert split_external_tool_name(name) is None


def _run_asgi(scope):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(mcp_http_app(scope, receive, send))
    return messages


def test_missing_api_key_is_rejected_before_mcp(monkeypatch):
    monkeypatch.setattr("apps.gateway.app.mcp_endpoint.database.record_audit", lambda **kwargs: None)
    messages = _run_asgi({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [],
    })
    assert messages[0]["status"] == 401
    assert b'"detail"' in messages[1]["body"]


def test_revoked_or_unknown_api_key_is_rejected(monkeypatch):
    monkeypatch.setattr("apps.gateway.app.mcp_endpoint.database.verify_api_key", lambda key: None)
    monkeypatch.setattr("apps.gateway.app.mcp_endpoint.database.record_audit", lambda **kwargs: None)
    messages = _run_asgi({
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-api-key", b"mcp_invalid_test_key")],
    })
    assert messages[0]["status"] == 401


def test_revoked_key_is_rechecked_on_the_next_http_request(monkeypatch):
    from apps.gateway.app import mcp_endpoint

    state = {"active": True}
    identity = {"identity_id": 5, "team_id": 2, "key_id": 12, "role": "operator"}
    monkeypatch.setattr(
        mcp_endpoint.database,
        "verify_api_key",
        lambda key: identity if state["active"] else None,
    )
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)

    async def handle_request(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    monkeypatch.setattr(mcp_endpoint._session_manager, "handle_request", handle_request)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(b"x-api-key", b"test-revocable-key")],
    }
    assert _run_asgi(scope)[0]["status"] == 204
    state["active"] = False
    assert _run_asgi(scope)[0]["status"] == 401


async def _request_asgi(body, *, api_key="valid-test-key"):
    messages = []
    sent = False
    raw_body = json.dumps(body).encode("utf-8")

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    headers = [
        (b"content-type", b"application/json"),
        (b"accept", b"application/json, text/event-stream"),
    ]
    if api_key is not None:
        headers.append((b"x-api-key", api_key.encode("utf-8")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp/",
        "raw_path": b"/mcp/",
        "root_path": "",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    from apps.gateway.app.main import app

    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    response = next(m for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(response.get("body", b"{}") or b"{}")


def test_streamable_http_initialize_list_and_call(monkeypatch):
    async def scenario():
        await _streamable_http_roundtrip(monkeypatch)

    asyncio.run(scenario())


async def _streamable_http_roundtrip(monkeypatch):
    from apps.gateway.app import main as gateway_main
    from apps.gateway.app import mcp_endpoint

    identity = {
        "identity_id": 5,
        "team_id": 2,
        "key_id": 12,
        "identity_name": "test-operator",
        "role": "operator",
        "identity_type": "user",
    }
    tool = {
        "name": "list_inventory",
        "description": "List inventory",
        "server": "inventory",
        "inputSchema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
        },
    }
    collision_tool = {
        "name": "list_inventory",
        "description": "Inventory view from support server",
        "server": "support",
        "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}}},
    }
    monkeypatch.setattr(mcp_endpoint.database, "verify_api_key", lambda key: identity)
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)
    monkeypatch.setattr(mcp_endpoint.database, "is_authorized", lambda *args: True)

    async def list_tools():
        return [tool, collision_tool]

    calls = []

    async def call_tool(server, name, arguments):
        calls.append((server, name, arguments))
        return {"tool": name, "server": server, "is_error": False, "content": []}

    monkeypatch.setattr(mcp_endpoint, "list_all_tools", list_tools)
    monkeypatch.setattr(mcp_endpoint, "call_server_tool", call_tool)

    async with gateway_main.app.router.lifespan_context(gateway_main.app):
        status, initialized = await _request_asgi(
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        assert status == 200
        assert initialized["result"]["serverInfo"]["name"] == "MCPHub Gateway"

        status, listed = await _request_asgi(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        assert status == 200
        listed_tools = {item["name"]: item for item in listed["result"]["tools"]}
        assert set(listed_tools) == {"inventory__list_inventory", "support__list_inventory"}
        assert listed_tools["inventory__list_inventory"]["inputSchema"] == tool["inputSchema"]

        status, called = await _request_asgi(
            {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "inventory__list_inventory",
                    "arguments": {"sku": "SKU-1"},
                },
            }
        )
        assert status == 200
        assert called["result"]["isError"] is False
        assert calls == [("inventory", "list_inventory", {"sku": "SKU-1"})]


def test_mcp_permissions_and_tool_audit(monkeypatch):
    from apps.gateway.app import mcp_endpoint

    async def scenario():
        identity = {"identity_id": 8, "team_id": 4, "key_id": 19, "role": "operator"}
        token = mcp_endpoint._current_identity.set(identity)
        audit_rows = []
        tool = {"name": "list_inventory", "server": "inventory", "inputSchema": {"type": "object"}}
        update_tool = {"name": "update_stock", "server": "inventory", "inputSchema": {"type": "object"}}
        monkeypatch.setattr(mcp_endpoint, "list_all_tools", lambda: _async_return([tool, update_tool]))
        monkeypatch.setattr(
            mcp_endpoint.database,
            "is_authorized",
            lambda caller, server, name: caller["identity_id"] == 8 and name == "list_inventory",
        )
        monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **row: audit_rows.append(row))
        calls = []

        async def execute(server, name, args):
            calls.append((server, name, args))
            return {"is_error": False, "content": []}

        monkeypatch.setattr(mcp_endpoint, "call_server_tool", execute)
        try:
            visible = await mcp_endpoint._list_tools()
            assert [item.name for item in visible] == ["inventory__list_inventory"]
            assert mcp_endpoint.external_tool_name("inventory", "list_inventory") == "inventory__list_inventory"

            denied = await mcp_endpoint._call_tool("inventory__update_stock", {"sku": "SKU-1"})
            assert denied.isError
            assert calls == []
            assert audit_rows[-1]["outcome"] == "denied"
            assert audit_rows[-1]["error_class"] == "permission_denied"
            assert audit_rows[-1]["identity"] == identity
            assert audit_rows[-1]["server_name"] == "inventory"
            assert audit_rows[-1]["tool_name"] == "update_stock"
            assert audit_rows[-1]["duration_ms"] is not None

            result = await mcp_endpoint._call_tool("inventory__list_inventory", {})
            assert not result.isError
            assert calls == [("inventory", "list_inventory", {})]
            assert audit_rows[-1]["outcome"] == "success"
            assert audit_rows[-1]["duration_ms"] is not None
        finally:
            mcp_endpoint._current_identity.reset(token)

    asyncio.run(scenario())


def test_mcp_tool_execution_failure_is_generic_and_audited(monkeypatch):
    from apps.gateway.app import mcp_endpoint

    async def scenario():
        token = mcp_endpoint._current_identity.set({
            "identity_id": 3, "team_id": 2, "key_id": 7, "role": "operator"
        })
        audits = []
        monkeypatch.setattr(
            mcp_endpoint,
            "list_all_tools",
            lambda: _async_return([{"name": "list_inventory", "server": "inventory"}]),
        )
        monkeypatch.setattr(mcp_endpoint.database, "is_authorized", lambda *args: True)
        monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **row: audits.append(row))

        async def fail(*args):
            raise RuntimeError("upstream secret URL and response data")

        monkeypatch.setattr(mcp_endpoint, "call_server_tool", fail)
        try:
            result = await mcp_endpoint._call_tool("inventory__list_inventory", {})
            assert result.isError
            message = result.content[0].text
            assert "upstream secret URL" not in message
            assert audits[-1]["outcome"] == "failure"
            assert audits[-1]["error_class"] == "RuntimeError"
            assert audits[-1]["server_name"] == "inventory"
            assert audits[-1]["tool_name"] == "list_inventory"
            assert audits[-1]["duration_ms"] is not None
        finally:
            mcp_endpoint._current_identity.reset(token)

    asyncio.run(scenario())


@pytest.mark.parametrize("column_count, expected_alters", [(0, 1), (1, 0)])
def test_audit_duration_migration_is_idempotent_and_non_destructive(
    monkeypatch, column_count, expected_alters
):
    from contextlib import contextmanager
    from apps.shared import database

    class Cursor:
        def __init__(self):
            self.query = ""
            self.executed = []

        def execute(self, query, params=None):
            self.query = query
            self.executed.append(query)

        def fetchone(self):
            if "GET_LOCK" in self.query:
                return {"acquired": 1}
            if "information_schema.COLUMNS" in self.query:
                return {"column_count": column_count}
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    class Connection:
        def __init__(self):
            self.value = Cursor()

        def cursor(self):
            return self.value

    conn = Connection()

    @contextmanager
    def fake_connection(*, initialize=True):
        yield conn

    monkeypatch.setattr(database, "_schema_initialized", False)
    monkeypatch.setattr(database, "connection", fake_connection)
    monkeypatch.setattr(database, "SCHEMA", ())
    database.initialize_schema()
    sql = "\n".join(conn.value.executed)
    assert sql.count("ALTER TABLE audit_events ADD COLUMN duration_ms") == expected_alters
    assert "INSERT IGNORE INTO schema_migrations (version) VALUES (2)" in sql
    assert "DELETE FROM audit_events" not in sql
    assert "DROP TABLE audit_events" not in sql


async def _async_return(value):
    return value
