from unittest.mock import Mock

import pymysql
import pytest

from apps.shared import database, migrate_database


class DummyConnection:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_auto_migration_can_be_disabled_for_normal_database_requests(monkeypatch):
    monkeypatch.setenv("MCPHUB_AUTO_MIGRATE", "false")
    ensure = Mock(side_effect=AssertionError("automatic schema initialization was called"))
    monkeypatch.setattr(database, "ensure_schema", ensure)
    monkeypatch.setattr(database, "connect", DummyConnection)

    with database.connection():
        pass

    ensure.assert_not_called()


def test_auto_migration_remains_enabled_by_default_for_compatibility(monkeypatch):
    monkeypatch.delenv("MCPHUB_AUTO_MIGRATE", raising=False)
    ensure = Mock()
    monkeypatch.setattr(database, "ensure_schema", ensure)
    monkeypatch.setattr(database, "connect", DummyConnection)

    with database.connection():
        pass

    ensure.assert_called_once_with()


def test_explicit_migration_command_calls_versioned_initializer(monkeypatch, capsys):
    initialize = Mock()
    monkeypatch.setattr(database, "initialize_schema", initialize)

    assert migrate_database.main() == 0
    initialize.assert_called_once_with()
    assert "completed successfully" in capsys.readouterr().out


def test_explicit_migration_command_sanitizes_failure(monkeypatch, capsys):
    monkeypatch.setattr(database, "initialize_schema", Mock(side_effect=RuntimeError("secret DSN detail")))

    assert migrate_database.main() == 1
    captured = capsys.readouterr()
    assert "migration failed" in captured.err
    assert "secret DSN detail" not in captured.err


def test_explicit_migration_command_reports_safe_database_diagnostics(monkeypatch, capsys):
    failure = database.MigrationDiagnosticError(
        "create_table_teams", "OperationalError", 1071, "42000"
    )
    monkeypatch.setattr(database, "initialize_schema", Mock(side_effect=failure))

    assert migrate_database.main() == 1
    error = capsys.readouterr().err
    assert "step=create_table_teams" in error
    assert "error_class=OperationalError" in error
    assert "error_code=1071" in error
    assert "sqlstate=42000" in error


def test_migration_error_reports_failed_schema_statement_without_raw_sql(monkeypatch):
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = {"acquired": 1}
    cursor.execute.side_effect = [
        None,
        pymysql.err.OperationalError(1071, "sensitive SQL parameter"),
    ]
    conn = Mock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(database.pymysql, "connect", Mock(return_value=conn))
    monkeypatch.setenv("MCPHUB_DB_HOST", "db.internal")
    monkeypatch.setenv("MCPHUB_DB_PORT", "3306")
    monkeypatch.setenv("MCPHUB_DB_NAME", "mcphub")
    monkeypatch.setenv("MCPHUB_DB_USER", "mcphub_app")
    monkeypatch.setenv("MCPHUB_DB_PASSWORD", "not-for-output")

    with pytest.raises(database.MigrationDiagnosticError) as caught:
        database.initialize_schema()

    assert caught.value.step == "create_table_schema_migrations"
    assert caught.value.error_class == "OperationalError"
    assert caught.value.error_code == 1071
    assert "sensitive SQL parameter" not in str(caught.value)
    assert "not-for-output" not in str(caught.value)


def test_migration_connect_failure_is_classified_without_driver_message(monkeypatch):
    monkeypatch.setenv("MCPHUB_DB_HOST", "db.internal")
    monkeypatch.setenv("MCPHUB_DB_PORT", "3306")
    monkeypatch.setenv("MCPHUB_DB_NAME", "mcphub")
    monkeypatch.setenv("MCPHUB_DB_USER", "mcphub_app")
    monkeypatch.setenv("MCPHUB_DB_PASSWORD", "not-for-output")
    monkeypatch.setattr(
        database.pymysql,
        "connect",
        Mock(side_effect=pymysql.err.OperationalError(1045, "password is private")),
    )

    with pytest.raises(database.MigrationDiagnosticError) as caught:
        database.initialize_schema()

    assert caught.value.step == "database_connect"
    assert caught.value.error_class == "OperationalError"
    assert caught.value.error_code == 1045
    assert "password is private" not in str(caught.value)
