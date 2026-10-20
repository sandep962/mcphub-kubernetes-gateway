
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from apps.shared import database


mcp = FastMCP(
    "MCPHub Inventory MCP",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "mcphub-inventory-mcp.mcphub.svc.cluster.local:8002",
            "localhost:8002",
            "127.0.0.1:8002",
        ],
    ),
)

@mcp.tool()
async def list_inventory() -> list[dict[str, Any]]:
    """List inventory items and their available quantities."""
    return database.list_inventory()


@mcp.tool()
async def update_stock(sku: str, quantity: int) -> dict[str, Any]:
    """Set the physical stock quantity for an existing SKU."""
    try:
        return database.update_stock(sku, quantity)
    except (ValueError, LookupError) as exc:
        raise ValueError(str(exc)) from None


async def health_check(_request: Any) -> JSONResponse:
    return JSONResponse(
        {"status": "healthy", "service": "mcphub-inventory-mcp"}
    )


async def readiness_check(_request: Any) -> JSONResponse:
    try:
        database.ping()
        return JSONResponse({"status": "ready", "service": "mcphub-inventory-mcp"})
    except database.StorageUnavailable:
        return JSONResponse({"status": "unavailable"}, status_code=503)


app = mcp.streamable_http_app()
app.routes.append(Route("/health", health_check, methods=["GET"]))
app.routes.append(Route("/ready", readiness_check, methods=["GET"]))
