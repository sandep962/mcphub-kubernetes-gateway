
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from apps.shared import database


MCP_SERVERS = {
    name: url
    for name, url in {
        "woocommerce": os.getenv(
            "WOOCOMMERCE_MCP_URL",
            "http://mcphub-woocommerce-mcp.mcphub.svc.cluster.local:8001/mcp",
        ),
        "inventory": os.getenv(
            "INVENTORY_MCP_URL",
            "http://mcphub-inventory-mcp.mcphub.svc.cluster.local:8002/mcp",
        ),
        "support": os.getenv(
            "SUPPORT_MCP_URL",
            "http://mcphub-support-mcp.mcphub.svc.cluster.local:8003/mcp",
        ),
    }.items()
    if url and url.strip()
}

UPSTREAM_DISCOVERY_TIMEOUT_SECONDS = 8.0


def _enabled_servers() -> dict[str, str]:
    """Read active endpoints from metadata so admin changes apply immediately."""
    return {
        row["name"]: row["endpoint_url"]
        for row in database.list_upstreams(enabled_only=True)
    }


async def _list_server_tools(server_name: str, server_url: str) -> list[dict]:
    async with streamablehttp_client(server_url) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

            return [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "server": server_name,
                    "inputSchema": tool.inputSchema,
                }
                for tool in result.tools
            ]


async def list_all_tools() -> list[dict]:
    """Discover tools from all configured MCP servers."""
    tools = []
    errors = []
    servers = _enabled_servers()

    async def discover(server_name: str, server_url: str):
        try:
            return await asyncio.wait_for(
                _list_server_tools(server_name, server_url),
                timeout=UPSTREAM_DISCOVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return exc

    results = await asyncio.gather(
        *(discover(server_name, server_url) for server_name, server_url in servers.items())
    )
    for (server_name, _), result in zip(servers.items(), results):
        if isinstance(result, Exception):
            # Keep diagnostics useful without logging upstream URLs or response bodies.
            errors.append(f"{server_name}: {type(result).__name__}")
        else:
            tools.extend(result)

    if errors and not tools:
        raise RuntimeError("Unable to discover tools from configured upstreams.")

    return tools


async def call_server_tool(
    server_name: str,
    tool_name: str,
    arguments: dict | None = None,
) -> dict:
    """Execute a tool on its owning MCP server."""
    server_url = _enabled_servers().get(server_name)
    if not server_url:
        raise ValueError(f"Unknown MCP server: {server_name}")

    async with streamablehttp_client(server_url) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                tool_name,
                arguments=arguments or {},
            )

            return {
                "tool": tool_name,
                "server": server_name,
                "is_error": result.isError,
                "content": [
                    {
                        "type": item.type,
                        "text": getattr(item, "text", None),
                    }
                    for item in result.content
                ],
                "structured_content": getattr(
                    result, "structuredContent", None
                ),
            }
