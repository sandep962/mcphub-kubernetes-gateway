"""Authenticated Streamable HTTP MCP endpoint for the MCPHub gateway."""

from __future__ import annotations

import contextvars
import json
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import unquote

from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from apps.gateway.app.services.mcp_client import call_server_tool, list_all_tools
from apps.shared import database


_current_identity: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar("mcphub_mcp_identity", default=None)
)

_mcp_server = Server("MCPHub Gateway", version="0.4.0")
_session_manager = StreamableHTTPSessionManager(
    app=_mcp_server,
    stateless=True,
    json_response=True,
)


def external_tool_name(server_name: str, tool_name: str) -> str:
    """Return a collision-safe public name for a tool."""
    return f"{server_name}__{tool_name}"


def split_external_tool_name(name: str) -> tuple[str, str] | None:
    """Parse a public name without guessing when the server prefix is missing."""
    if "__" not in name:
        return None
    server_name, tool_name = name.split("__", 1)
    if not server_name or not tool_name:
        return None
    if server_name not in database.SERVERS:
        try:
            if not database.is_registered_upstream(server_name):
                return None
        except database.StorageUnavailable:
            return None
    return server_name, tool_name


def _text_result(message: str, *, is_error: bool = True) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        isError=is_error,
    )


@_mcp_server.list_tools()
async def _list_tools() -> list[types.Tool]:
    identity = _current_identity.get()
    if identity is None:
        # Normally prevented by the ASGI authentication wrapper.
        return []

    try:
        tools = await list_all_tools()
    except Exception:
        # Do not forward transport exceptions, URLs, or upstream response bodies to MCP clients.
        return []
    visible: list[types.Tool] = []
    for tool in tools:
        server_name = tool["server"]
        tool_name = tool["name"]
        try:
            authorized = database.is_authorized(identity, server_name, tool_name)
        except database.StorageUnavailable:
            raise RuntimeError("Authorization storage is unavailable.") from None
        if not authorized:
            continue
        visible.append(
            types.Tool(
                name=external_tool_name(server_name, tool_name),
                description=tool.get("description") or f"{tool_name} on {server_name}",
                inputSchema=tool.get("inputSchema")
                or {"type": "object", "properties": {}},
            )
        )
    return visible


@_mcp_server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
    identity = _current_identity.get()
    if identity is None:
        return _text_result("Authentication required.")

    parsed = split_external_tool_name(name)
    if parsed is None:
        try:
            database.record_audit(
                event_type="tool_call",
                outcome="denied",
                error_class="unknown_or_unqualified_tool",
                identity=identity,
                tool_name=name,
                duration_ms=0,
            )
        except database.StorageUnavailable:
            return _text_result("Audit storage is unavailable.")
        return _text_result(
            "Unknown tool name. Use the server__tool name returned by tools/list."
        )

    server_name, tool_name = parsed
    started = time.perf_counter()
    try:
        discovered = await list_all_tools()
        exists = any(
            item["server"] == server_name and item["name"] == tool_name
            for item in discovered
        )
        if not exists:
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            database.record_audit(
                event_type="tool_call",
                outcome="denied",
                error_class="unknown_tool",
                identity=identity,
                server_name=server_name,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
            )
            return _text_result("Unknown MCP tool.")

        if not database.is_authorized(identity, server_name, tool_name):
            elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
            database.record_audit(
                event_type="tool_call",
                outcome="denied",
                error_class="permission_denied",
                identity=identity,
                server_name=server_name,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
            )
            return _text_result("Permission denied for this MCP tool.")

        result = await call_server_tool(server_name, tool_name, arguments or {})
        elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
        is_error = bool(result.get("is_error"))
        database.record_audit(
            event_type="tool_call",
            outcome="failure" if is_error else "success",
            error_class="mcp_tool_error" if is_error else None,
            identity=identity,
            server_name=server_name,
            tool_name=tool_name,
            duration_ms=elapsed_ms,
        )
        # Keep the MCP response valid and portable across upstream content types.
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(result, ensure_ascii=False, default=str),
                )
            ],
            isError=is_error,
        )
    except database.StorageUnavailable:
        return _text_result("Authorization or audit storage is unavailable.")
    except Exception as exc:
        elapsed_ms = max(0, round((time.perf_counter() - started) * 1000))
        try:
            database.record_audit(
                event_type="tool_call",
                outcome="failure",
                error_class=type(exc).__name__,
                identity=identity,
                server_name=server_name,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
            )
        except database.StorageUnavailable:
            pass
        return _text_result("The MCP tool call failed.")


async def call_tool_as_identity(
    identity: dict[str, Any], name: str, arguments: dict[str, Any] | None
) -> types.CallToolResult:
    """Route a gateway-internal tool call through the same auth and audit path."""
    token = _current_identity.set(identity)
    try:
        return await _call_tool(name, arguments)
    finally:
        _current_identity.reset(token)


async def _send_json(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AuthenticatedMCPApp:
    """ASGI wrapper that authenticates every HTTP request before MCP handling."""

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await _session_manager.handle_request(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_key = headers.get(b"x-api-key", b"")
        api_key = raw_key.decode("utf-8", errors="ignore").strip()

        if not api_key:
            try:
                database.record_audit(
                    event_type="authentication",
                    outcome="denied",
                    error_class="missing_key",
                )
            except database.StorageUnavailable:
                await _send_json(send, 503, "Authentication storage is unavailable.")
                return
            await _send_json(send, 401, "A valid X-API-Key header is required.")
            return

        try:
            identity = database.verify_api_key(api_key)
            if identity is None:
                database.record_audit(
                    event_type="authentication",
                    outcome="denied",
                    error_class="invalid_key",
                )
                await _send_json(send, 401, "A valid X-API-Key header is required.")
                return
            database.record_audit(
                event_type="authentication",
                outcome="success",
                identity=identity,
            )
        except database.StorageUnavailable:
            await _send_json(send, 503, "Authentication storage is unavailable.")
            return

        token = _current_identity.set(identity)
        try:
            await _session_manager.handle_request(scope, receive, send)
        finally:
            _current_identity.reset(token)


mcp_http_app = AuthenticatedMCPApp()


@asynccontextmanager
async def mcp_lifespan():
    """Run the SDK session manager for the lifetime of the FastAPI app."""
    async with _session_manager.run():
        yield
