import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.routing import Route


def get_config() -> tuple[str, str, str, str]:
    base_url = os.environ.get("WOOCOMMERCE_BASE_URL", "").strip().rstrip("/")
    canonical_url = (
        os.environ.get("WOOCOMMERCE_CANONICAL_URL", "").strip().rstrip("/")
    )
    consumer_key = os.environ.get("WOOCOMMERCE_CONSUMER_KEY", "").strip()
    consumer_secret = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET", "").strip()

    if not all((base_url, consumer_key, consumer_secret)):
        missing = [
            name
            for name, value in (
                ("WOOCOMMERCE_BASE_URL", base_url),
                ("WOOCOMMERCE_CANONICAL_URL", canonical_url),
                ("WOOCOMMERCE_CONSUMER_KEY", consumer_key),
                ("WOOCOMMERCE_CONSUMER_SECRET", consumer_secret),
            )
            if not value
        ]
        raise RuntimeError(
            "Missing required WooCommerce configuration: " + ", ".join(missing)
        )

    for name, value in (
        ("WOOCOMMERCE_BASE_URL", base_url),
        ("WOOCOMMERCE_CANONICAL_URL", canonical_url),
    ):
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(f"{name} must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RuntimeError(
                f"{name} must not include credentials, a query, or a fragment"
            )
    return base_url, canonical_url, consumer_key, consumer_secret


# Fail before serving MCP requests when the process was launched without its
# configuration. Values are held in memory and never included in diagnostics.
BASE_URL, CANONICAL_URL, CONSUMER_KEY, CONSUMER_SECRET = get_config()


def _oauth_header(method: str, url: str, params: dict[str, Any]) -> str:
    """Sign WooCommerce's HTTP-only OAuth 1.0a requests without URL credentials."""
    oauth = {
        "oauth_consumer_key": CONSUMER_KEY,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    parts = urlsplit(url)
    base_url = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", "")
    )
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    parameter_names = [key for key, _ in query_pairs] + list(params)
    if len(parameter_names) != len(set(parameter_names)):
        raise ValueError("OAuth requests cannot contain duplicate query parameter names")
    if any(isinstance(value, (list, tuple, dict, set)) for value in params.values()):
        raise ValueError("OAuth query parameters must have scalar values")
    all_params = query_pairs + list(params.items()) + list(oauth.items())
    encoded = sorted(
        (quote(str(k), safe="~-._"), quote(str(v), safe="~-._"))
        for k, v in all_params
    )
    normalized = "&".join(f"{k}={v}" for k, v in encoded)
    base_string = "&".join(
        (
            method.upper(),
            quote(base_url, safe="~-._"),
            quote(normalized, safe="~-._"),
        )
    )
    # WooCommerce validates with the raw consumer secret followed by '&'.
    signing_key = f"{CONSUMER_SECRET}&"
    oauth["oauth_signature"] = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha256).digest()
    ).decode("ascii")
    return "OAuth " + ", ".join(
        f'{quote(key, safe="~-._")}="{quote(value, safe="~-._")}"'
        for key, value in sorted(oauth.items())
    )


def _safe_response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        return "WooCommerce returned a non-JSON error response."

    if not isinstance(body, dict):
        return "WooCommerce returned an unexpected error response."
    code = str(body.get("code", ""))[:100]
    message = str(body.get("message", ""))[:240]
    for secret in (CONSUMER_KEY, CONSUMER_SECRET):
        code = code.replace(secret, "[redacted]")
        message = message.replace(secret, "[redacted]")
    detail = "; ".join(part for part in (code, message) if part)
    return detail or "WooCommerce returned an API error."


mcp = FastMCP(
    "MCPHub WooCommerce",
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "mcphub-woocommerce-mcp.mcphub.svc.cluster.local:8001",
            "localhost:8001",
            "127.0.0.1:8001",
        ]
    ),
)


def _build_api_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/wp-json/wc/v3/{endpoint.lstrip('/')}"


async def wc_request(
    method: str,
    endpoint: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    query_params = params or {}
    url = _build_api_url(BASE_URL, endpoint)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if urlsplit(BASE_URL).scheme == "https":
                response = await client.request(
                    method,
                    url,
                    params=query_params,
                    json=payload,
                    auth=(CONSUMER_KEY, CONSUMER_SECRET),
                )
            else:
                # Basic Auth is not a safe/reliable authentication method over
                # plain HTTP. WooCommerce requires OAuth 1.0a in this case.
                # Its verifier rebuilds the signature URL from WordPress's home
                # URL and the request path, not the internal Service hostname.
                response = await client.request(
                    method,
                    url,
                    params=query_params,
                    json=payload,
                    headers={
                        "Authorization": _oauth_header(
                            method,
                            _build_api_url(CANONICAL_URL, endpoint),
                            query_params,
                        )
                    },
                )
            if response.is_error:
                raise RuntimeError(
                    f"WooCommerce API returned HTTP {response.status_code} "
                    f"for {method} {endpoint}: {_safe_response_detail(response)}"
                )
            if not response.content:
                return {"success": True}
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError):
                raise RuntimeError(
                    f"WooCommerce returned a non-JSON response for {method} {endpoint}."
                ) from None
    except httpx.RequestError:
        raise RuntimeError(
            f"Could not reach WooCommerce for {method} {endpoint}. Check the base URL and network."
        ) from None


@mcp.tool()
async def list_products(
    search: str = "",
    per_page: int = 10,
) -> list[dict[str, Any]]:
    """List published WooCommerce products, optionally filtered by search text."""
    if not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")

    products = await wc_request(
        "GET",
        "products",
        params={"search": search, "per_page": per_page, "status": "publish"},
    )
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "price": p["price"],
            "regular_price": p["regular_price"],
            "status": p["status"],
            "stock_status": p.get("stock_status"),
        }
        for p in products
    ]


@mcp.tool()
async def create_product(
    name: str,
    regular_price: str,
    description: str = "",
) -> dict[str, Any]:
    """Create and publish a simple WooCommerce product."""
    if not name.strip():
        raise ValueError("Product name cannot be empty")

    try:
        price = float(regular_price)
        if price < 0:
            raise ValueError
    except ValueError:
        raise ValueError("regular_price must be a non-negative number") from None

    product = await wc_request(
        "POST",
        "products",
        payload={
            "name": name.strip(),
            "type": "simple",
            "regular_price": str(regular_price),
            "description": description,
            "status": "publish",
        },
    )
    return {
        "id": product["id"],
        "name": product["name"],
        "price": product["price"],
        "status": product["status"],
        "permalink": product["permalink"],
    }


@mcp.tool()
async def list_orders(
    after: str = "",
    per_page: int = 10,
) -> list[dict[str, Any]]:
    """List WooCommerce orders, optionally created after an ISO-8601 timestamp."""
    if not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")

    params: dict[str, Any] = {
        "per_page": per_page,
        "orderby": "date",
        "order": "desc",
    }
    if after:
        datetime.fromisoformat(after.replace("Z", "+00:00"))
        params["after"] = after

    orders = await wc_request("GET", "orders", params=params)
    return [
        {
            "id": o["id"],
            "number": o["number"],
            "status": o["status"],
            "payment_method": o.get("payment_method", ""),
            "payment_method_title": o.get("payment_method_title", ""),
            "total": o["total"],
            "currency": o["currency"],
            "date_created": o["date_created"],
            "customer": {
                "first_name": o["billing"].get("first_name", ""),
                "last_name": o["billing"].get("last_name", ""),
            },
            "line_items": [
                {"name": item["name"], "quantity": item["quantity"]}
                for item in o.get("line_items", [])
            ],
        }
        for o in orders
    ]


@mcp.tool()
async def get_order(order_id: int) -> dict[str, Any]:
    """Retrieve a WooCommerce order by its numeric ID."""
    if order_id < 1:
        raise ValueError("order_id must be a positive integer")

    order = await wc_request("GET", f"orders/{order_id}")
    return {
        "id": order["id"],
        "number": order["number"],
        "status": order["status"],
        "payment_method": order.get("payment_method", ""),
        "total": order["total"],
        "currency": order["currency"],
        "date_created": order["date_created"],
        "billing": {
            "first_name": order["billing"].get("first_name", ""),
            "last_name": order["billing"].get("last_name", ""),
        },
        "line_items": [
            {
                "product_id": item["product_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "total": item["total"],
            }
            for item in order.get("line_items", [])
        ],
    }


@mcp.tool()
async def update_order_status(
    order_id: int,
    status: str,
) -> dict[str, Any]:
    """Update an order status using a WooCommerce-supported status."""
    allowed_statuses = {
        "pending",
        "processing",
        "on-hold",
        "completed",
        "cancelled",
        "refunded",
        "failed",
    }
    if order_id < 1:
        raise ValueError("order_id must be a positive integer")
    if status not in allowed_statuses:
        raise ValueError(
            f"Unsupported status. Choose one of: {', '.join(sorted(allowed_statuses))}"
        )

    order = await wc_request(
        "PUT",
        f"orders/{order_id}",
        payload={"status": status},
    )
    return {
        "id": order["id"],
        "number": order["number"],
        "status": order["status"],
    }


async def health_check(_request: Any) -> JSONResponse:
    return JSONResponse({"status": "healthy", "service": "mcphub-woocommerce-mcp"})


app = mcp.streamable_http_app()
app.routes.append(Route("/health", health_check, methods=["GET"]))
