"""MariaDB connection handling, schema migrations, and shared repositories."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlsplit

import pymysql
from pymysql.cursors import DictCursor


class StorageUnavailable(RuntimeError):
    """Raised when the shared metadata database cannot be reached."""


class ConflictError(ValueError):
    """Raised for duplicate or inconsistent metadata operations."""


ROLES = {"admin", "operator", "viewer"}
IDENTITY_TYPES = {"user", "service"}
READ_ONLY_TOOLS = {
    "list_products",
    "list_orders",
    "get_order",
    "list_inventory",
    "list_tickets",
    "get_ticket",
}
SERVERS = {"woocommerce", "inventory", "support"}
DEFAULT_UPSTREAMS = {
    "woocommerce": os.getenv("WOOCOMMERCE_MCP_URL", "http://mcphub-woocommerce-mcp.mcphub.svc.cluster.local:8001/mcp"),
    "inventory": os.getenv("INVENTORY_MCP_URL", "http://mcphub-inventory-mcp.mcphub.svc.cluster.local:8002/mcp"),
    "support": os.getenv("SUPPORT_MCP_URL", "http://mcphub-support-mcp.mcphub.svc.cluster.local:8003/mcp"),
}
_schema_initialized = False


def _config() -> dict[str, Any]:
    config = {
        "host": os.environ.get("MCPHUB_DB_HOST", "").strip(),
        "port": int(os.environ.get("MCPHUB_DB_PORT", "3306")),
        "database": os.environ.get("MCPHUB_DB_NAME", "").strip(),
        "user": os.environ.get("MCPHUB_DB_USER", "").strip(),
        "password": os.environ.get("MCPHUB_DB_PASSWORD", ""),
    }
    if not all((config["host"], config["database"], config["user"], config["password"])):
        raise StorageUnavailable("Metadata database configuration is incomplete.")
    return config


def connect():
    try:
        return pymysql.connect(
            **_config(),
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
    except (pymysql.MySQLError, ValueError, StorageUnavailable):
        raise StorageUnavailable("Metadata database is unavailable.") from None


@contextmanager
def connection(*, initialize: bool = True) -> Iterator[Any]:
    if initialize:
        ensure_schema()
    conn = connect()
    try:
        yield conn
        conn.commit()
    except pymysql.MySQLError:
        conn.rollback()
        raise StorageUnavailable("Metadata database operation failed.") from None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS schema_migrations (
        version INT NOT NULL PRIMARY KEY,
        applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS teams (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(64) NOT NULL UNIQUE,
        name VARCHAR(120) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS identities (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        team_id BIGINT UNSIGNED NOT NULL,
        name VARCHAR(120) NOT NULL,
        role VARCHAR(16) NOT NULL,
        identity_type VARCHAR(16) NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_identity_team_name (team_id, name),
        CONSTRAINT fk_identity_team FOREIGN KEY (team_id) REFERENCES teams(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS api_keys (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        identity_id BIGINT UNSIGNED NOT NULL,
        key_prefix VARCHAR(16) NOT NULL,
        key_hash CHAR(64) NOT NULL UNIQUE,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        expires_at DATETIME(6) NULL,
        revoked_at DATETIME(6) NULL,
        last_used_at DATETIME(6) NULL,
        CONSTRAINT fk_api_key_identity FOREIGN KEY (identity_id) REFERENCES identities(id),
        INDEX ix_api_key_identity (identity_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS permissions (
        subject_type VARCHAR(16) NOT NULL,
        subject_id BIGINT UNSIGNED NOT NULL,
        server_name VARCHAR(32) NOT NULL,
        tool_name VARCHAR(128) NOT NULL DEFAULT '*',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (subject_type, subject_id, server_name, tool_name),
        INDEX ix_permission_lookup (subject_type, subject_id, server_name, tool_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS audit_events (
        duration_ms INT UNSIGNED NULL,
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        identity_id BIGINT UNSIGNED NULL,
        team_id BIGINT UNSIGNED NULL,
        api_key_id BIGINT UNSIGNED NULL,
        event_type VARCHAR(32) NOT NULL,
        server_name VARCHAR(32) NULL,
        tool_name VARCHAR(128) NULL,
        outcome VARCHAR(16) NOT NULL,
        error_class VARCHAR(64) NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX ix_audit_created (created_at),
        INDEX ix_audit_team_created (team_id, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS inventory_items (
        sku VARCHAR(64) NOT NULL PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        quantity INT UNSIGNED NOT NULL,
        reserved INT UNSIGNED NOT NULL DEFAULT 0,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        CHECK (reserved <= quantity)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS support_tickets (
        id VARCHAR(32) NOT NULL PRIMARY KEY,
        customer VARCHAR(200) NOT NULL,
        subject VARCHAR(240) NOT NULL,
        description TEXT NOT NULL,
        status VARCHAR(24) NOT NULL,
        priority VARCHAR(16) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        INDEX ix_ticket_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS mcp_upstreams (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(64) NOT NULL UNIQUE,
        endpoint_url VARCHAR(2048) NOT NULL,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def initialize_schema() -> None:
    """Apply idempotent schema setup and seed demo data under a DB advisory lock."""
    global _schema_initialized
    with connection(initialize=False) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 30) AS acquired", ("mcphub-schema-v1",))
            lock = cursor.fetchone()
            if not lock or lock["acquired"] != 1:
                raise StorageUnavailable("Could not acquire metadata schema lock.")
            try:
                for statement in SCHEMA:
                    cursor.execute(statement)
                cursor.execute(
                    "INSERT IGNORE INTO schema_migrations (version) VALUES (1)"
                )
                cursor.execute(
                    """SELECT COUNT(*) AS column_count FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'audit_events'
                         AND COLUMN_NAME = 'duration_ms'"""
                )
                if cursor.fetchone()["column_count"] == 0:
                    cursor.execute(
                        "ALTER TABLE audit_events ADD COLUMN duration_ms INT UNSIGNED NULL"
                    )
                cursor.execute(
                    "INSERT IGNORE INTO schema_migrations (version) VALUES (2)"
                )
                cursor.execute("SELECT version FROM schema_migrations WHERE version = 3")
                if cursor.fetchone() is None:
                    for name, endpoint_url in DEFAULT_UPSTREAMS.items():
                        cursor.execute(
                            """INSERT IGNORE INTO mcp_upstreams (name, endpoint_url, enabled)
                               VALUES (%s, %s, TRUE)""",
                            (name, endpoint_url),
                        )
                    cursor.execute("INSERT INTO schema_migrations (version) VALUES (3)")
                for sku, name, quantity, reserved in (
                    ("SKU-HEADPHONES", "MCPHub Demo Headphones", 25, 2),
                    ("SKU-KEYBOARD", "MCPHub Mechanical Keyboard", 12, 1),
                    ("SKU-MOUSE", "MCPHub Wireless Mouse", 40, 5),
                ):
                    cursor.execute(
                        """INSERT IGNORE INTO inventory_items
                           (sku, name, quantity, reserved) VALUES (%s, %s, %s, %s)""",
                        (sku, name, quantity, reserved),
                    )
                for ticket in (
                    (
                        "TKT-1001",
                        "Demo Customer",
                        "Order delivery status",
                        "Customer wants an update on delivery.",
                        "open",
                        "normal",
                    ),
                    (
                        "TKT-1002",
                        "Demo Customer",
                        "Refund request",
                        "Customer is asking about a refund.",
                        "in_progress",
                        "high",
                    ),
                ):
                    cursor.execute(
                        """INSERT IGNORE INTO support_tickets
                           (id, customer, subject, description, status, priority)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        ticket,
                    )
            finally:
                cursor.execute("SELECT RELEASE_LOCK(%s)", ("mcphub-schema-v1",))
        _schema_initialized = True


def ensure_schema() -> None:
    """Initialize schema on the first repository use in each process."""
    if not _schema_initialized:
        initialize_schema()


def ping() -> bool:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT 1 AS ready")
        return cursor.fetchone()["ready"] == 1


def _validate_upstream(name: str, endpoint_url: str) -> tuple[str, str]:
    name = name.strip().lower()
    endpoint_url = endpoint_url.strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9_-]{0,30}[a-z0-9])?", name):
        raise ValueError("Upstream name must use lowercase letters, digits, underscores, or hyphens.")
    if len(endpoint_url) > 2048:
        raise ValueError("Upstream URL is too long.")
    parsed = urlsplit(endpoint_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Upstream URL must be HTTP(S) and cannot contain credentials, query parameters, or fragments.")
    return name, endpoint_url.rstrip("/")


def list_upstreams(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        query = "SELECT id, name, endpoint_url, enabled, created_at, updated_at FROM mcp_upstreams"
        if enabled_only:
            query += " WHERE enabled = TRUE"
        cursor.execute(query + " ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]


def is_registered_upstream(name: str, *, enabled_only: bool = True) -> bool:
    with connection() as conn, conn.cursor() as cursor:
        query = "SELECT 1 AS present FROM mcp_upstreams WHERE name = %s"
        if enabled_only:
            query += " AND enabled = TRUE"
        cursor.execute(query, (name,))
        return cursor.fetchone() is not None


def create_upstream(name: str, endpoint_url: str, enabled: bool = True) -> dict[str, Any]:
    name, endpoint_url = _validate_upstream(name, endpoint_url)
    with connection() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO mcp_upstreams (name, endpoint_url, enabled) VALUES (%s, %s, %s)",
                (name, endpoint_url, bool(enabled)),
            )
        except pymysql.IntegrityError:
            raise ConflictError("An upstream with that name already exists.") from None
        cursor.execute(
            "SELECT id, name, endpoint_url, enabled, created_at, updated_at FROM mcp_upstreams WHERE id = %s",
            (cursor.lastrowid,),
        )
        return dict(cursor.fetchone())


def update_upstream(upstream_id: int, name: str, endpoint_url: str) -> dict[str, Any] | None:
    name, endpoint_url = _validate_upstream(name, endpoint_url)
    with connection() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "UPDATE mcp_upstreams SET name = %s, endpoint_url = %s WHERE id = %s",
                (name, endpoint_url, upstream_id),
            )
        except pymysql.IntegrityError:
            raise ConflictError("An upstream with that name already exists.") from None
        if cursor.rowcount == 0:
            cursor.execute("SELECT id FROM mcp_upstreams WHERE id = %s", (upstream_id,))
            if cursor.fetchone() is None:
                return None
        cursor.execute(
            "SELECT id, name, endpoint_url, enabled, created_at, updated_at FROM mcp_upstreams WHERE id = %s",
            (upstream_id,),
        )
        return dict(cursor.fetchone())


def set_upstream_enabled(upstream_id: int, enabled: bool) -> bool:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("UPDATE mcp_upstreams SET enabled = %s WHERE id = %s", (bool(enabled), upstream_id))
        if cursor.rowcount:
            return True
        cursor.execute("SELECT id FROM mcp_upstreams WHERE id = %s", (upstream_id,))
        return cursor.fetchone() is not None


def delete_upstream(upstream_id: int) -> bool:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("DELETE FROM mcp_upstreams WHERE id = %s", (upstream_id,))
        return cursor.rowcount == 1


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "mcp_" + secrets.token_urlsafe(36)


def verify_api_key(api_key: str) -> dict[str, Any] | None:
    if not api_key or len(api_key) > 512:
        return None
    digest = hash_api_key(api_key)
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT k.id AS key_id, k.identity_id, k.key_prefix,
                      i.team_id, i.name AS identity_name, i.role,
                      i.identity_type, i.active
               FROM api_keys k
               JOIN identities i ON i.id = k.identity_id
               WHERE k.key_hash = %s AND k.revoked_at IS NULL
                 AND (k.expires_at IS NULL OR k.expires_at > UTC_TIMESTAMP(6))""",
            (digest,),
        )
        identity = cursor.fetchone()
        if not identity or not identity["active"]:
            return None
        cursor.execute(
            "UPDATE api_keys SET last_used_at = UTC_TIMESTAMP(6) WHERE id = %s",
            (identity["key_id"],),
        )
        return dict(identity)


def record_audit(
    *,
    event_type: str,
    outcome: str,
    error_class: str | None = None,
    identity: dict[str, Any] | None = None,
    server_name: str | None = None,
    tool_name: str | None = None,
    duration_ms: int | None = None,
) -> None:
    identity = identity or {}
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO audit_events
                (identity_id, team_id, api_key_id, event_type, server_name,
                    tool_name, outcome, error_class, duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                identity.get("identity_id"),
                identity.get("team_id"),
                identity.get("key_id"),
                event_type[:32],
                server_name[:32] if server_name else None,
                tool_name[:128] if tool_name else None,
                outcome[:16],
                error_class[:64] if error_class else None,
                max(0, min(int(duration_ms), 4294967295))
                if duration_ms is not None else None,
            ),
        )


def is_authorized(identity: dict[str, Any], server_name: str, tool_name: str) -> bool:
    role = identity.get("role")
    if role == "admin":
        return True
    if server_name not in SERVERS and not is_registered_upstream(server_name):
        return False
    if role == "viewer" and tool_name not in READ_ONLY_TOOLS:
        return False
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT 1 AS allowed FROM permissions
               WHERE ((subject_type = 'identity' AND subject_id = %s)
                   OR (subject_type = 'team' AND subject_id = %s))
                 AND server_name IN (%s, '*')
                 AND tool_name IN (%s, '*')
               LIMIT 1""",
            (identity["identity_id"], identity["team_id"], server_name, tool_name),
        )
        return cursor.fetchone() is not None


def create_team(slug: str, name: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", slug):
        raise ValueError("Team slug must contain lowercase letters, digits, or hyphens.")
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("Team name must contain between 1 and 120 characters.")
    with connection() as conn, conn.cursor() as cursor:
        try:
            cursor.execute("INSERT INTO teams (slug, name) VALUES (%s, %s)", (slug, name))
        except pymysql.IntegrityError:
            raise ConflictError("A team with that slug already exists.") from None
        cursor.execute("SELECT id, slug, name, created_at FROM teams WHERE id = %s", (cursor.lastrowid,))
        return dict(cursor.fetchone())


def list_teams() -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id, slug, name, created_at FROM teams ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]


def create_identity(team_id: int, name: str, role: str, identity_type: str) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError("Role must be admin, operator, or viewer.")
    if identity_type not in IDENTITY_TYPES:
        raise ValueError("Identity type must be user or service.")
    name = name.strip()
    if not name or len(name) > 120:
        raise ValueError("Identity name must contain between 1 and 120 characters.")
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM teams WHERE id = %s", (team_id,))
        if not cursor.fetchone():
            raise LookupError("Team not found.")
        try:
            cursor.execute(
                "INSERT INTO identities (team_id, name, role, identity_type) VALUES (%s, %s, %s, %s)",
                (team_id, name, role, identity_type),
            )
        except pymysql.IntegrityError:
            raise ConflictError("An identity with that name already exists in this team.") from None
        cursor.execute(
            """SELECT id, team_id, name, role, identity_type, active, created_at
               FROM identities WHERE id = %s""",
            (cursor.lastrowid,),
        )
        return dict(cursor.fetchone())


def list_identities(team_id: int | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        sql = """SELECT id, team_id, name, role, identity_type, active, created_at
                 FROM identities"""
        params: tuple[Any, ...] = ()
        if team_id is not None:
            sql += " WHERE team_id = %s"
            params = (team_id,)
        cursor.execute(sql + " ORDER BY id", params)
        return [dict(row) for row in cursor.fetchall()]


def create_api_key(identity_id: int, api_key: str) -> dict[str, Any]:
    if len(api_key) < 32 or len(api_key) > 256:
        raise ValueError("API key must be between 32 and 256 characters.")
    digest = hash_api_key(api_key)
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM identities WHERE id = %s AND active = TRUE", (identity_id,))
        if not cursor.fetchone():
            raise LookupError("Active identity not found.")
        try:
            cursor.execute(
                "INSERT INTO api_keys (identity_id, key_prefix, key_hash) VALUES (%s, %s, %s)",
                (identity_id, api_key[:12], digest),
            )
        except pymysql.IntegrityError:
            raise ConflictError("That API key is already registered.") from None
        return {"id": cursor.lastrowid, "identity_id": identity_id, "key_prefix": api_key[:12]}


def list_api_keys(team_id: int | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        sql = """SELECT k.id, k.identity_id, i.team_id, i.name AS identity_name,
                        k.key_prefix, k.created_at, k.expires_at, k.revoked_at,
                        k.last_used_at
                 FROM api_keys k JOIN identities i ON i.id = k.identity_id"""
        params: tuple[Any, ...] = ()
        if team_id is not None:
            sql += " WHERE i.team_id = %s"
            params = (team_id,)
        cursor.execute(sql + " ORDER BY k.id", params)
        return [dict(row) for row in cursor.fetchall()]


def revoke_api_key(key_id: int) -> bool:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE api_keys SET revoked_at = UTC_TIMESTAMP(6) WHERE id = %s AND revoked_at IS NULL",
            (key_id,),
        )
        return cursor.rowcount == 1


def set_permission(
    *, subject_type: str, subject_id: int, server_name: str, tool_name: str
) -> dict[str, Any]:
    if subject_type not in {"team", "identity"}:
        raise ValueError("Permission subject must be a team or identity.")
    if server_name not in SERVERS | {"*"} and not is_registered_upstream(server_name):
        raise ValueError("Unknown MCP server.")
    if tool_name != "*" and not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", tool_name):
        raise ValueError("Invalid tool name.")
    table = "teams" if subject_type == "team" else "identities"
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(f"SELECT id FROM {table} WHERE id = %s", (subject_id,))
        if not cursor.fetchone():
            raise LookupError("Permission subject not found.")
        cursor.execute(
            """INSERT IGNORE INTO permissions
               (subject_type, subject_id, server_name, tool_name)
               VALUES (%s, %s, %s, %s)""",
            (subject_type, subject_id, server_name, tool_name),
        )
    return {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "server_name": server_name,
        "tool_name": tool_name,
    }


def list_permissions() -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT subject_type, subject_id, server_name, tool_name, created_at
               FROM permissions ORDER BY subject_type, subject_id, server_name, tool_name"""
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_permission(
    *, subject_type: str, subject_id: int, server_name: str, tool_name: str
) -> bool:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """DELETE FROM permissions WHERE subject_type = %s AND subject_id = %s
               AND server_name = %s AND tool_name = %s""",
            (subject_type, subject_id, server_name, tool_name),
        )
        return cursor.rowcount == 1


def list_audit_events(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT id, identity_id, team_id, api_key_id, event_type,
                      server_name, tool_name, outcome, error_class, duration_ms, created_at
               FROM audit_events ORDER BY id DESC LIMIT %s OFFSET %s""",
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]


def bootstrap_admin() -> str:
    """Create the one-time initial administrator and return its key once."""
    api_key = generate_api_key()
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM identities WHERE name = %s AND role = 'admin' LIMIT 1", ("initial-admin",))
        if cursor.fetchone():
            raise ConflictError("An initial administrator already exists.")
        cursor.execute(
            "INSERT IGNORE INTO teams (slug, name) VALUES (%s, %s)",
            ("administrators", "Administrators"),
        )
        cursor.execute("SELECT id FROM teams WHERE slug = %s", ("administrators",))
        team_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO identities (team_id, name, role, identity_type) VALUES (%s, %s, 'admin', 'user')",
            (team_id, "initial-admin"),
        )
        identity_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO api_keys (identity_id, key_prefix, key_hash) VALUES (%s, %s, %s)",
            (identity_id, api_key[:12], hash_api_key(api_key)),
        )
    return api_key


def list_inventory() -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT sku, name, quantity, reserved FROM inventory_items ORDER BY sku"""
        )
        return [
            {**row, "available": row["quantity"] - row["reserved"]}
            for row in cursor.fetchall()
        ]


def update_stock(sku: str, quantity: int) -> dict[str, Any]:
    if quantity < 0:
        raise ValueError("Quantity must be a non-negative integer.")
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT sku, name, quantity, reserved FROM inventory_items WHERE sku = %s FOR UPDATE",
            (sku,),
        )
        item = cursor.fetchone()
        if item is None:
            raise LookupError(f"Unknown SKU: {sku}")
        if quantity < item["reserved"]:
            raise ValueError("Quantity cannot be less than the reserved stock amount.")
        cursor.execute(
            "UPDATE inventory_items SET quantity = %s WHERE sku = %s",
            (quantity, sku),
        )
        item["quantity"] = quantity
        item["available"] = quantity - item["reserved"]
        return dict(item)


VALID_TICKET_STATUSES = {"open", "in_progress", "resolved", "closed"}
VALID_TICKET_PRIORITIES = {"low", "normal", "high", "urgent"}


def list_tickets(status: str | None = None) -> list[dict[str, Any]]:
    if status is not None and status not in VALID_TICKET_STATUSES:
        raise ValueError("Invalid ticket status.")
    with connection() as conn, conn.cursor() as cursor:
        if status is None:
            cursor.execute(
                """SELECT id, customer, subject, description, status, priority
                   FROM support_tickets ORDER BY created_at, id"""
            )
        else:
            cursor.execute(
                """SELECT id, customer, subject, description, status, priority
                   FROM support_tickets WHERE status = %s ORDER BY created_at, id""",
                (status,),
            )
        return [dict(row) for row in cursor.fetchall()]


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT id, customer, subject, description, status, priority
               FROM support_tickets WHERE id = %s""",
            (ticket_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def create_ticket(
    ticket_id: str,
    customer: str,
    subject: str,
    description: str,
    priority: str,
) -> dict[str, Any]:
    if not customer.strip() or not subject.strip() or not description.strip():
        raise ValueError("Customer, subject, and description are required.")
    if len(ticket_id) > 32 or len(customer.strip()) > 200 or len(subject.strip()) > 240:
        raise ValueError("Ticket ID, customer, or subject exceeds its allowed length.")
    if priority not in VALID_TICKET_PRIORITIES:
        raise ValueError("Invalid ticket priority.")
    row = {
        "id": ticket_id,
        "customer": customer.strip(),
        "subject": subject.strip(),
        "description": description.strip(),
        "status": "open",
        "priority": priority,
    }
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO support_tickets
               (id, customer, subject, description, status, priority)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            tuple(row.values()),
        )
    return row


def update_ticket_status(ticket_id: str, status: str) -> dict[str, Any] | None:
    if status not in VALID_TICKET_STATUSES:
        raise ValueError("Invalid ticket status.")
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, customer, subject, description, status, priority FROM support_tickets WHERE id = %s FOR UPDATE",
            (ticket_id,),
        )
        ticket = cursor.fetchone()
        if ticket is None:
            return None
        cursor.execute(
            "UPDATE support_tickets SET status = %s WHERE id = %s",
            (status, ticket_id),
        )
        ticket["status"] = status
        return dict(ticket)
