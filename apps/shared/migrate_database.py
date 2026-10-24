"""Explicit, one-shot entry point for applying the shared schema migrations."""

from __future__ import annotations

import sys

from apps.shared import database


def main() -> int:
    try:
        # This deliberately bypasses MCPHUB_AUTO_MIGRATE. Only an explicit
        # invocation of this module applies the migrations when auto mode is off.
        database.initialize_schema()
    except database.MigrationDiagnosticError as exc:
        # The exception contains only a fixed step label, exception class, and
        # optional numeric database code / SQLSTATE. Never print driver text.
        print(f"MCPHub schema migration failed; {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Do not print unexpected exception details, connection strings, or SQL.
        print("MCPHub schema migration failed; step=unknown error_class=UnexpectedError", file=sys.stderr)
        return 1
    print("MCPHub schema migrations completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
