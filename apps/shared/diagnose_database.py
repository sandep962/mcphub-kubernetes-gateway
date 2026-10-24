"""Read-only metadata database connectivity diagnostic; never initializes schema."""

from __future__ import annotations

import re
import sys

import pymysql
from pymysql.cursors import DictCursor

from apps.shared import database


DIAGNOSTIC_LOCK = "mcphub-readonly-diagnostic-v1"
LOCK_TIMEOUT_SECONDS = 3


class DiagnosticCheckError(RuntimeError):
    """A failed safe check, without the underlying driver message or query values."""


def _safe_error_summary(step: str, exc: Exception) -> str:
    error_class = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", error_class):
        error_class = "DatabaseError"
    fields = [f"step={step}", f"exception_class={error_class}"]
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        fields.append(f"database_code={args[0]}")
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        fields.append(f"sqlstate={sqlstate}")
    return " ".join(fields)


def _require(condition: bool, step: str, error_class: str) -> None:
    if not condition:
        raise DiagnosticCheckError(f"step={step} exception_class={error_class}")


def run_diagnostics() -> int:
    """Connect using the app DB configuration and perform read-only checks only."""
    step = "database_config"
    connection = None
    lock_acquired = False
    primary_error: Exception | None = None
    result = 0
    try:
        config = database._config()
        print("check=database_config status=ok")

        step = "database_connect"
        connection = pymysql.connect(
            **config,
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
        )
        print("check=database_connect status=ok")

        with connection.cursor() as cursor:
            step = "select_1"
            cursor.execute("SELECT 1 AS probe")
            row = cursor.fetchone()
            _require(bool(row and row.get("probe") == 1), step, "ProbeMismatch")
            print("check=select_1 status=ok")

            step = "database_identity"
            cursor.execute(
                "SELECT DATABASE() AS database_name, CURRENT_USER() AS authenticated_user"
            )
            identity = cursor.fetchone() or {}
            current_user = identity.get("authenticated_user")
            authenticated_user = (
                current_user.split("@", 1)[0] if isinstance(current_user, str) else ""
            )
            _require(identity.get("database_name") == config["database"], step, "IdentityMismatch")
            _require(authenticated_user == config["user"], step, "IdentityMismatch")
            print("check=database_identity status=matched")

            step = "advisory_lock_acquire"
            cursor.execute(
                "SELECT GET_LOCK(%s, %s) AS acquired",
                (DIAGNOSTIC_LOCK, LOCK_TIMEOUT_SECONDS),
            )
            lock_acquired = bool((cursor.fetchone() or {}).get("acquired") == 1)
            _require(lock_acquired, step, "AdvisoryLockUnavailable")
            print("check=advisory_lock_acquire status=ok")

            step = "advisory_lock_release"
            cursor.execute("SELECT RELEASE_LOCK(%s) AS released", (DIAGNOSTIC_LOCK,))
            released = (cursor.fetchone() or {}).get("released") == 1
            lock_acquired = not released
            _require(released, step, "AdvisoryLockReleaseFailed")
            print("check=advisory_lock_release status=ok")

        print("MCPHub read-only database diagnostics completed.")
    except Exception as exc:
        primary_error = exc
        result = 1
        print(
            "MCPHub read-only database diagnostics failed; "
            + _safe_error_summary(step, exc),
            file=sys.stderr,
        )
    finally:
        if connection is not None:
            if lock_acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT RELEASE_LOCK(%s)", (DIAGNOSTIC_LOCK,))
                        cursor.fetchone()
                except Exception as release_error:
                    if primary_error is None:
                        result = 1
                        print(
                            "MCPHub read-only database diagnostics failed; "
                            + _safe_error_summary("advisory_lock_release", release_error),
                            file=sys.stderr,
                        )
            try:
                connection.close()
            except Exception as close_error:
                if primary_error is None:
                    result = 1
                    print(
                        "MCPHub read-only database diagnostics failed; "
                        + _safe_error_summary("database_close", close_error),
                        file=sys.stderr,
                    )
    return result


def main() -> int:
    return run_diagnostics()


if __name__ == "__main__":
    raise SystemExit(main())
