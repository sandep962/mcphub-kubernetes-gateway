"""MariaDB connection handling, schema migrations, and shared repositories."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import urlsplit

import pymysql
from pymysql.cursors import DictCursor


class StorageUnavailable(RuntimeError):
    """Raised when the shared metadata database cannot be reached."""


class MigrationDiagnosticError(StorageUnavailable):
    """Safe migration failure details; deliberately excludes driver messages and SQL values."""

    def __init__(
        self,
        step: str,
        error_class: str,
        error_code: int | None = None,
        sqlstate: str | None = None,
    ) -> None:
        self.step = step
        self.error_class = error_class
        self.error_code = error_code
        self.sqlstate = sqlstate
        details = [f"step={step}", f"error_class={error_class}"]
        if error_code is not None:
            details.append(f"error_code={error_code}")
        if sqlstate is not None:
            details.append(f"sqlstate={sqlstate}")
        super().__init__(" ".join(details))


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


def _migration_error(step: str, exc: Exception) -> MigrationDiagnosticError:
    error_code = None
    if getattr(exc, "args", None) and isinstance(exc.args[0], int):
        error_code = exc.args[0]
    sqlstate = getattr(exc, "sqlstate", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        sqlstate = None
    error_class = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", error_class):
        error_class = "DatabaseError"
    return MigrationDiagnosticError(step, error_class, error_code, sqlstate)


def _migration_execute(cursor: Any, step: str, statement: str, params: Any = None) -> None:
    try:
        if params is None:
            cursor.execute(statement)
        else:
            cursor.execute(statement, params)
    except MigrationDiagnosticError:
        raise
    except Exception as exc:
        raise _migration_error(step, exc) from None


@contextmanager
def connection(*, initialize: bool = True) -> Iterator[Any]:
    auto_migrate = os.environ.get("MCPHUB_AUTO_MIGRATE", "true").strip().lower()
    if initialize and auto_migrate not in {"0", "false", "no", "off"}:
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
    """CREATE TABLE IF NOT EXISTS oauth_credentials (
        identity_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
        username VARCHAR(120) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        CONSTRAINT fk_oauth_credential_identity FOREIGN KEY (identity_id) REFERENCES identities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_clients (
        client_id VARCHAR(64) NOT NULL PRIMARY KEY,
        client_name VARCHAR(120) NOT NULL,
        redirect_uris TEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_authorization_requests (
        request_hash CHAR(64) NOT NULL PRIMARY KEY,
        client_id VARCHAR(64) NOT NULL,
        redirect_uri VARCHAR(2048) NOT NULL,
        state_value VARCHAR(512) NOT NULL,
        code_challenge CHAR(43) NOT NULL,
        scope_value VARCHAR(512) NOT NULL,
        resource VARCHAR(2048) NOT NULL,
        csrf_hash CHAR(64) NOT NULL,
        expires_at DATETIME(6) NOT NULL,
        INDEX ix_oauth_request_expiry (expires_at),
        CONSTRAINT fk_oauth_request_client FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
        code_hash CHAR(64) NOT NULL PRIMARY KEY,
        client_id VARCHAR(64) NOT NULL,
        identity_id BIGINT UNSIGNED NOT NULL,
        redirect_uri VARCHAR(2048) NOT NULL,
        code_challenge CHAR(43) NOT NULL,
        scope_value VARCHAR(512) NOT NULL,
        resource VARCHAR(2048) NOT NULL,
        expires_at DATETIME(6) NOT NULL,
        consumed_at DATETIME(6) NULL,
        INDEX ix_oauth_code_expiry (expires_at),
        CONSTRAINT fk_oauth_code_client FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id),
        CONSTRAINT fk_oauth_code_identity FOREIGN KEY (identity_id) REFERENCES identities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_access_tokens (
        token_hash CHAR(64) NOT NULL PRIMARY KEY,
        client_id VARCHAR(64) NOT NULL,
        identity_id BIGINT UNSIGNED NOT NULL,
        scope_value VARCHAR(512) NOT NULL,
        resource VARCHAR(2048) NOT NULL,
        expires_at DATETIME(6) NOT NULL,
        revoked_at DATETIME(6) NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        INDEX ix_oauth_token_identity (identity_id, expires_at),
        CONSTRAINT fk_oauth_token_client FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id),
        CONSTRAINT fk_oauth_token_identity FOREIGN KEY (identity_id) REFERENCES identities(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_consents (
        identity_id BIGINT UNSIGNED NOT NULL,
        client_id VARCHAR(64) NOT NULL,
        resource VARCHAR(2048) NOT NULL,
        resource_hash BINARY(32) NOT NULL,
        scope_value VARCHAR(512) NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (identity_id, client_id, resource_hash),
        CONSTRAINT fk_oauth_consent_identity FOREIGN KEY (identity_id) REFERENCES identities(id),
        CONSTRAINT fk_oauth_consent_client FOREIGN KEY (client_id) REFERENCES oauth_clients(client_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS oauth_login_attempts (
        attempt_hash CHAR(64) NOT NULL PRIMARY KEY,
        failures INT UNSIGNED NOT NULL DEFAULT 0,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        INDEX ix_oauth_login_updated (updated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def _migrate_oauth_consent_resource_hash(cursor: Any) -> None:
    """Replace the oversized consent resource key while preserving existing rows."""
    _migration_execute(
        cursor,
        "inspect_oauth_consent_resource_hash",
        """SELECT COUNT(*) AS column_count FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'oauth_consents'
             AND COLUMN_NAME = 'resource_hash'""",
    )
    if cursor.fetchone()["column_count"] == 0:
        _migration_execute(
            cursor,
            "add_oauth_consent_resource_hash",
            "ALTER TABLE oauth_consents ADD COLUMN resource_hash BINARY(32) NULL",
        )

    _migration_execute(
        cursor,
        "backfill_oauth_consent_resource_hash",
        "UPDATE oauth_consents SET resource_hash = UNHEX(SHA2(resource, 256)) "
        "WHERE resource_hash IS NULL",
    )
    _migration_execute(
        cursor,
        "inspect_oauth_consent_primary_key",
        """SELECT COLUMN_NAME AS column_name FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'oauth_consents'
             AND INDEX_NAME = 'PRIMARY' ORDER BY SEQ_IN_INDEX""",
    )
    primary_key = tuple(row["column_name"] for row in cursor.fetchall())
    expected_primary_key = ("identity_id", "client_id", "resource_hash")
    if primary_key != expected_primary_key:
        drop_primary_key = "DROP PRIMARY KEY, " if primary_key else ""
        _migration_execute(
            cursor,
            "replace_oauth_consent_primary_key",
            "ALTER TABLE oauth_consents "
            f"{drop_primary_key}MODIFY resource_hash BINARY(32) NOT NULL, "
            "ADD PRIMARY KEY (identity_id, client_id, resource_hash)",
        )


def initialize_schema() -> None:
    """Apply idempotent schema setup and seed demo data under a DB advisory lock."""
    global _schema_initialized
    step = "database_config"
    conn = None
    lock_acquired = False
    try:
        config = _config()
        step = "database_connect"
        conn = pymysql.connect(
            **config,
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
        step = "open_database_cursor"
        with conn.cursor() as cursor:
            step = "acquire_schema_lock"
            _migration_execute(
                cursor, "acquire_schema_lock", "SELECT GET_LOCK(%s, 30) AS acquired",
                ("mcphub-schema-v1",),
            )
            lock = cursor.fetchone()
            if not lock or lock["acquired"] != 1:
                raise StorageUnavailable("Could not acquire metadata schema lock.")
            lock_acquired = True
            try:
                schema_tables = (
                    "schema_migrations", "teams", "identities", "api_keys", "permissions",
                    "audit_events", "inventory_items", "support_tickets", "mcp_upstreams",
                    "oauth_credentials", "oauth_clients", "oauth_authorization_requests",
                    "oauth_authorization_codes", "oauth_access_tokens", "oauth_consents",
                    "oauth_login_attempts",
                )
                for table_name, statement in zip(schema_tables, SCHEMA):
                    step = f"create_table_{table_name}"
                    _migration_execute(cursor, step, statement)
                step = "record_schema_version_1"
                _migration_execute(
                    cursor, step, "INSERT IGNORE INTO schema_migrations (version) VALUES (1)"
                )
                step = "inspect_audit_duration_column"
                _migration_execute(
                    cursor, step,
                    """SELECT COUNT(*) AS column_count FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'audit_events'
                         AND COLUMN_NAME = 'duration_ms'"""
                )
                if cursor.fetchone()["column_count"] == 0:
                    step = "add_audit_duration_column"
                    _migration_execute(
                        cursor, step,
                        "ALTER TABLE audit_events ADD COLUMN duration_ms INT UNSIGNED NULL"
                    )
                step = "record_schema_version_2"
                _migration_execute(
                    cursor, step, "INSERT IGNORE INTO schema_migrations (version) VALUES (2)"
                )
                step = "check_schema_version_3"
                _migration_execute(
                    cursor, step, "SELECT version FROM schema_migrations WHERE version = 3"
                )
                if cursor.fetchone() is None:
                    for name, endpoint_url in DEFAULT_UPSTREAMS.items():
                        step = "seed_default_upstream"
                        _migration_execute(
                            cursor, step,
                            """INSERT IGNORE INTO mcp_upstreams (name, endpoint_url, enabled)
                               VALUES (%s, %s, TRUE)""",
                            (name, endpoint_url),
                        )
                    step = "record_schema_version_3"
                    _migration_execute(
                        cursor, step, "INSERT INTO schema_migrations (version) VALUES (3)"
                    )
                step = "record_schema_version_4"
                _migration_execute(
                    cursor, step, "INSERT IGNORE INTO schema_migrations (version) VALUES (4)"
                )
                step = "migrate_oauth_consent_resource_index"
                _migrate_oauth_consent_resource_hash(cursor)
                step = "record_schema_version_5"
                _migration_execute(
                    cursor, step, "INSERT IGNORE INTO schema_migrations (version) VALUES (5)"
                )
                for sku, name, quantity, reserved in (
                    ("SKU-HEADPHONES", "MCPHub Demo Headphones", 25, 2),
                    ("SKU-KEYBOARD", "MCPHub Mechanical Keyboard", 12, 1),
                    ("SKU-MOUSE", "MCPHub Wireless Mouse", 40, 5),
                ):
                    step = "seed_demo_inventory"
                    _migration_execute(
                        cursor, step,
                        """INSERT IGNORE INTO inventory_items
                           (sku, name, quantity, reserved) VALUES (%s, %s, %s, %s)""",
                        (sku, name, quantity, reserved),
                    )
                for ticket in (
                    (
                        "TKT-1001", "Demo Customer", "Order delivery status",
                        "Customer wants an update on delivery.", "open", "normal",
                    ),
                    (
                        "TKT-1002", "Demo Customer", "Refund request",
                        "Customer is asking about a refund.", "in_progress", "high",
                    ),
                ):
                    step = "seed_demo_support_ticket"
                    _migration_execute(
                        cursor, step,
                        """INSERT IGNORE INTO support_tickets
                           (id, customer, subject, description, status, priority)
                           VALUES (%s, %s, %s, %s, %s, %s)""",
                        ticket,
                    )
            finally:
                if lock_acquired:
                    original_error_active = sys.exc_info()[0] is not None
                    step = "release_schema_lock"
                    try:
                        _migration_execute(
                            cursor, step, "SELECT RELEASE_LOCK(%s)", ("mcphub-schema-v1",)
                        )
                    except MigrationDiagnosticError:
                        if not original_error_active:
                            raise
        step = "commit_schema_changes"
        conn.commit()
        _schema_initialized = True
    except MigrationDiagnosticError:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise _migration_error(step, exc) from None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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


def _oauth_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Hash a password using scrypt; the encoded value contains salt and parameters."""
    import base64

    if not isinstance(password, str) or len(password) < 12 or len(password) > 1024:
        raise ValueError("Password must contain between 12 and 1024 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(salt).decode() + "$" + base64.urlsafe_b64encode(digest).decode()


def _password_matches(password: str, encoded: str) -> bool:
    import base64

    try:
        algorithm, n, r, p, salt_b64, digest_b64 = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64.encode())
        expected = base64.urlsafe_b64decode(digest_b64.encode())
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, MemoryError):
        return False


_DUMMY_PASSWORD_HASH = hash_password("MCPHub dummy password not usable")


def set_oauth_password(identity_id: int, username: str, password: str) -> None:
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._@+-]{2,119}", username):
        raise ValueError("Username must be 3-120 characters and use letters, digits, or ._@+-.")
    encoded = hash_password(password)
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM identities WHERE id = %s AND active = TRUE", (identity_id,))
        if not cursor.fetchone():
            raise LookupError("Active identity not found.")
        cursor.execute(
            "INSERT INTO oauth_credentials (identity_id, username, password_hash) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE username = VALUES(username), password_hash = VALUES(password_hash)",
            (identity_id, username, encoded),
        )


def authenticate_oauth_user(username: str, password: str) -> dict[str, Any] | None:
    username = username.strip().lower()[:120]
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT c.password_hash, i.id AS identity_id, i.team_id,
                      i.name AS identity_name, i.role, i.identity_type, i.active
               FROM oauth_credentials c JOIN identities i ON i.id = c.identity_id
               WHERE c.username = %s""",
            (username,),
        )
        row = cursor.fetchone()
    if not row:
        # Keep unknown usernames on the same scrypt path as known usernames.
        _password_matches(password, _DUMMY_PASSWORD_HASH)
        return None
    password_matches = _password_matches(password, row["password_hash"])
    if not row.get("active") or not password_matches:
        return None
    identity = dict(row)
    identity.pop("password_hash", None)
    identity["key_id"] = None
    identity["auth_method"] = "oauth"
    return identity


def register_oauth_client(client_id: str, client_name: str, redirect_uris: list[str]) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (%s, %s, %s)",
            (client_id, client_name[:120], json.dumps(redirect_uris)),
        )


def get_oauth_client(client_id: str) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT client_id, client_name, redirect_uris FROM oauth_clients WHERE client_id = %s", (client_id,))
        row = cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    result["redirect_uris"] = json.loads(result["redirect_uris"])
    return result


def create_oauth_request(request_id: str, fields: dict[str, str], csrf_token: str) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("DELETE FROM oauth_authorization_requests WHERE expires_at <= UTC_TIMESTAMP(6)")
        cursor.execute("DELETE FROM oauth_authorization_codes WHERE expires_at <= UTC_TIMESTAMP(6)")
        cursor.execute("DELETE FROM oauth_access_tokens WHERE expires_at <= UTC_TIMESTAMP(6)")
        cursor.execute(
            "DELETE FROM oauth_login_attempts WHERE updated_at < DATE_SUB(UTC_TIMESTAMP(6), INTERVAL 1 DAY) "
        )
        cursor.execute(
            """INSERT INTO oauth_authorization_requests
               (request_hash, client_id, redirect_uri, state_value, code_challenge,
                scope_value, resource, csrf_hash, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 10 MINUTE))""",
            (_oauth_hash(request_id), fields["client_id"], fields["redirect_uri"], fields["state"],
             fields["code_challenge"], fields["scope"], fields["resource"], _oauth_hash(csrf_token)),
        )


def get_oauth_request(request_id: str, csrf_token: str) -> dict[str, str] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM oauth_authorization_requests WHERE request_hash = %s "
            "AND csrf_hash = %s AND expires_at > UTC_TIMESTAMP(6)",
            (_oauth_hash(request_id), _oauth_hash(csrf_token)),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return {key: row[key] for key in ("client_id", "redirect_uri", "state_value", "code_challenge", "scope_value", "resource")}


def consume_oauth_request(request_id: str, csrf_token: str) -> dict[str, str] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM oauth_authorization_requests WHERE request_hash = %s "
            "AND csrf_hash = %s AND expires_at > UTC_TIMESTAMP(6) FOR UPDATE",
            (_oauth_hash(request_id), _oauth_hash(csrf_token)),
        )
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute("DELETE FROM oauth_authorization_requests WHERE request_hash = %s", (_oauth_hash(request_id),))
        return {key: row[key] for key in ("client_id", "redirect_uri", "state_value", "code_challenge", "scope_value", "resource")}


def create_oauth_code(code: str, fields: dict[str, Any], identity_id: int) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO oauth_authorization_codes
               (code_hash, client_id, identity_id, redirect_uri, code_challenge, scope_value, resource, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 90 SECOND))""",
            (_oauth_hash(code), fields["client_id"], identity_id, fields["redirect_uri"],
             fields["code_challenge"], fields["scope"], fields["resource"]),
        )


def exchange_oauth_code(code: str, verifier: str, client_id: str, redirect_uri: str, resource: str, access_token: str) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT * FROM oauth_authorization_codes WHERE code_hash = %s AND client_id = %s
               AND redirect_uri = %s AND resource = %s AND consumed_at IS NULL
               AND expires_at > UTC_TIMESTAMP(6) FOR UPDATE""",
            (_oauth_hash(code), client_id, redirect_uri, resource),
        )
        row = cursor.fetchone()
        if not row:
            return None
        expected = row["code_challenge"]
        actual = __import__("base64").urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not hmac.compare_digest(actual, expected):
            return None
        cursor.execute("SELECT active FROM identities WHERE id = %s FOR UPDATE", (row["identity_id"],))
        identity_state = cursor.fetchone()
        if not identity_state or not identity_state["active"]:
            return None
        cursor.execute(
            "UPDATE oauth_authorization_codes SET consumed_at = UTC_TIMESTAMP(6) WHERE code_hash = %s AND consumed_at IS NULL",
            (_oauth_hash(code),),
        )
        if cursor.rowcount != 1:
            return None
        cursor.execute(
            "INSERT INTO oauth_access_tokens (token_hash, client_id, identity_id, scope_value, resource, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,DATE_ADD(UTC_TIMESTAMP(6), INTERVAL 1 HOUR))",
            (_oauth_hash(access_token), client_id, row["identity_id"], row["scope_value"], resource),
        )
        cursor.execute(
            "INSERT INTO oauth_consents "
            "(identity_id, client_id, resource, resource_hash, scope_value) VALUES (%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE scope_value = VALUES(scope_value)",
            (row["identity_id"], client_id, resource, hashlib.sha256(resource.encode("utf-8")).digest(),
             row["scope_value"]),
        )
        return {"identity_id": row["identity_id"], "client_id": client_id, "scope": row["scope_value"], "resource": resource}


def verify_oauth_access_token(token: str, resource: str) -> dict[str, Any] | None:
    if not token or len(token) > 512:
        return None
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT t.identity_id, t.client_id, t.scope_value, t.resource, t.expires_at,
                      i.team_id, i.name AS identity_name, i.role, i.identity_type, i.active
               FROM oauth_access_tokens t JOIN identities i ON i.id = t.identity_id
               WHERE t.token_hash = %s AND t.resource = %s AND t.revoked_at IS NULL
                 AND t.expires_at > UTC_TIMESTAMP(6)""",
            (_oauth_hash(token), resource),
        )
        row = cursor.fetchone()
    if not row or not row["active"]:
        return None
    identity = dict(row)
    identity["key_id"] = None
    identity["auth_method"] = "oauth"
    identity["oauth_client_id"] = identity.pop("client_id")
    identity["oauth_scopes"] = identity.pop("scope_value").split()
    return identity


def revoke_oauth_access_token(token: str, client_id: str) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE oauth_access_tokens SET revoked_at = UTC_TIMESTAMP(6) WHERE token_hash = %s AND client_id = %s AND revoked_at IS NULL",
            (_oauth_hash(token), client_id),
        )


def _login_attempt_key(username: str, client_fingerprint: str) -> str:
    return _oauth_hash(username.strip().lower()[:120] + "\0" + client_fingerprint[:128])


def oauth_login_delay_seconds(username: str, client_fingerprint: str) -> float:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT failures FROM oauth_login_attempts WHERE attempt_hash = %s",
            (_login_attempt_key(username, client_fingerprint),),
        )
        row = cursor.fetchone()
    if not row:
        return 0.0
    failures = min(int(row["failures"]), 4)
    return min(2.0, 0.25 * (2 ** (failures - 1)))


def record_oauth_login_failure(username: str, client_fingerprint: str) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO oauth_login_attempts (attempt_hash, failures)
               VALUES (%s, 1) ON DUPLICATE KEY UPDATE failures = failures + 1""",
            (_login_attempt_key(username, client_fingerprint),),
        )


def clear_oauth_login_failures(username: str, client_fingerprint: str) -> None:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute("DELETE FROM oauth_login_attempts WHERE attempt_hash = %s", (_login_attempt_key(username, client_fingerprint),))


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


def tool_call_metrics(hours: int = 24) -> dict[str, Any]:
    """Aggregate attributed tool-call audit rows over a bounded time window."""
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 168:
        raise ValueError("Metrics window must be between 1 and 168 hours.")

    group_limit = 200
    where = "event_type = 'tool_call' AND server_name IS NOT NULL AND tool_name IS NOT NULL AND created_at >= DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL %s HOUR)"
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"""SELECT COUNT(*) AS total_calls,
                       COALESCE(SUM(outcome = 'success'), 0) AS success_count,
                       COALESCE(SUM(outcome = 'failure'), 0) AS failure_count,
                       COALESCE(SUM(outcome = 'denied'), 0) AS denied_count,
                       COALESCE(SUM(error_class = 'upstream_tool_timeout'), 0) AS timeout_count,
                       COALESCE(SUM(error_class = 'mcp_tool_error'), 0) AS tool_error_count,
                       COALESCE(SUM(outcome = 'failure' AND
                           (error_class IS NULL OR error_class NOT IN
                               ('upstream_tool_timeout', 'mcp_tool_error'))), 0) AS other_failure_count,
                       AVG(duration_ms) AS avg_duration_ms,
                       MAX(duration_ms) AS max_duration_ms
                FROM audit_events WHERE {where}""",
            (hours,),
        )
        total_row = cursor.fetchone() or {}
        cursor.execute(
            f"""SELECT server_name, tool_name, COUNT(*) AS total_calls,
                       COALESCE(SUM(outcome = 'success'), 0) AS success_count,
                       COALESCE(SUM(outcome = 'failure'), 0) AS failure_count,
                       COALESCE(SUM(outcome = 'denied'), 0) AS denied_count,
                       COALESCE(SUM(error_class = 'upstream_tool_timeout'), 0) AS timeout_count,
                       COALESCE(SUM(error_class = 'mcp_tool_error'), 0) AS tool_error_count,
                       COALESCE(SUM(outcome = 'failure' AND
                           (error_class IS NULL OR error_class NOT IN
                               ('upstream_tool_timeout', 'mcp_tool_error'))), 0) AS other_failure_count,
                       AVG(duration_ms) AS avg_duration_ms,
                       MAX(duration_ms) AS max_duration_ms
                FROM audit_events WHERE {where}
                GROUP BY server_name, tool_name
                ORDER BY total_calls DESC, server_name, tool_name
                LIMIT %s""",
            (hours, group_limit + 1),
        )
        grouped_rows = [dict(row) for row in cursor.fetchall()]

    def count(row: dict[str, Any], field: str) -> int:
        return int(row.get(field) or 0)

    def duration(row: dict[str, Any], field: str) -> float | None:
        value = row.get(field)
        return round(float(value), 2) if value is not None else None

    def rate(part: int, total: int) -> float:
        return round(part * 100 / total, 2) if total else 0.0

    def format_row(row: dict[str, Any], include_names: bool = False) -> dict[str, Any]:
        total = count(row, "total_calls")
        success = count(row, "success_count")
        failure = count(row, "failure_count")
        denied = count(row, "denied_count")
        result = {
            "total_calls": total,
            "success_count": success,
            "failure_count": failure,
            "denied_count": denied,
            "success_rate_percent": rate(success, total),
            "failure_rate_percent": rate(failure, total),
            "denied_rate_percent": rate(denied, total),
            "timeout_count": count(row, "timeout_count"),
            "tool_error_count": count(row, "tool_error_count"),
            "other_failure_count": count(row, "other_failure_count"),
            "avg_duration_ms": duration(row, "avg_duration_ms"),
            "max_duration_ms": count(row, "max_duration_ms") if row.get("max_duration_ms") is not None else None,
        }
        if include_names:
            result = {"server_name": row["server_name"], "tool_name": row["tool_name"], **result}
        return result

    truncated = len(grouped_rows) > group_limit
    totals = format_row(total_row)
    return {
        "window_hours": hours,
        "totals": totals,
        "tools": [format_row(row, include_names=True) for row in grouped_rows[:group_limit]],
        "truncated": truncated,
    }


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
