from typing import Any
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route

from apps.shared import database

mcp = FastMCP(
    "MCPHub Support MCP",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "mcphub-support-mcp.mcphub.svc.cluster.local:8003",
            "localhost:8003",
            "127.0.0.1:8003",
        ],
    ),
)

VALID_STATUSES = {"open", "in_progress", "resolved", "closed"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


@mcp.tool()
async def list_tickets(status: str | None = None) -> list[dict[str, Any]]:
    """List support tickets, optionally filtered by status."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status. Use one of: {sorted(VALID_STATUSES)}")

    return database.list_tickets(status)


@mcp.tool()
async def get_ticket(ticket_id: str) -> dict[str, Any]:
    """Retrieve a support ticket by its ID."""
    ticket = database.get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Ticket not found: {ticket_id}")
    return dict(ticket)


@mcp.tool()
async def create_ticket(
    customer: str,
    subject: str,
    description: str,
    priority: str = "normal",
) -> dict[str, Any]:
    """Create a support ticket for a customer."""
    customer = customer.strip()
    subject = subject.strip()
    description = description.strip()

    if not customer or not subject or not description:
        raise ValueError("Customer, subject, and description are required.")
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"Invalid priority. Use one of: {sorted(VALID_PRIORITIES)}")

    ticket_id = f"TKT-{uuid4().hex[:8].upper()}"
    return database.create_ticket(ticket_id, customer, subject, description, priority)


@mcp.tool()
async def update_ticket_status(
    ticket_id: str,
    status: str,
) -> dict[str, Any]:
    """Update a support ticket's status."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status. Use one of: {sorted(VALID_STATUSES)}")

    ticket = database.update_ticket_status(ticket_id, status)
    if ticket is None:
        raise ValueError(f"Ticket not found: {ticket_id}")
    return ticket


async def health_check(_request: Any) -> JSONResponse:
    return JSONResponse({
        "status": "healthy",
        "service": "mcphub-support-mcp",
    })


async def readiness_check(_request: Any) -> JSONResponse:
    try:
        database.ping()
        return JSONResponse({"status": "ready", "service": "mcphub-support-mcp"})
    except database.StorageUnavailable:
        return JSONResponse({"status": "unavailable"}, status_code=503)


app = mcp.streamable_http_app()
app.routes.append(Route("/health", health_check, methods=["GET"]))
app.routes.append(Route("/ready", readiness_check, methods=["GET"]))

