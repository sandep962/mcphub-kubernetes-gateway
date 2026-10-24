from unittest.mock import Mock

from apps.shared import database, diagnose_database


class DiagnosticCursor:
    def __init__(self, *, fail_on=None):
        self.fail_on = fail_on
        self.failed_once = False
        self.statement = ""
        self.statements = []

    def execute(self, statement, params=None):
        self.statement = statement
        self.statements.append((statement, params))
        if self.fail_on and self.fail_on in statement and not self.failed_once:
            self.failed_once = True
            raise DiagnosticDriverError(1045, "private driver message")

    def fetchone(self):
        if "SELECT 1" in self.statement:
            return {"probe": 1}
        if "CURRENT_USER()" in self.statement:
            return {
                "database_name": "mcphub",
                "authenticated_user": "mcphub_app@%",
            }
        if "GET_LOCK" in self.statement:
            return {"acquired": 1}
        if "RELEASE_LOCK" in self.statement:
            return {"released": 1}
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class DiagnosticDriverError(Exception):
    sqlstate = "HY000"


class DiagnosticConnection:
    def __init__(self, cursor):
        self.value = cursor
        self.closed = False

    def cursor(self):
        return self.value

    def close(self):
        self.closed = True


def _test_config():
    return {
        "host": "database.internal",
        "port": 3306,
        "database": "mcphub",
        "user": "mcphub_app",
        "password": "diagnostic-test-password",
    }


def test_read_only_diagnostic_checks_connection_identity_and_advisory_lock(
    monkeypatch, capsys
):
    cursor = DiagnosticCursor()
    connection = DiagnosticConnection(cursor)
    connect = Mock(return_value=connection)
    initialize_schema = Mock(side_effect=AssertionError("must never initialize schema"))
    monkeypatch.setattr(database, "_config", _test_config)
    monkeypatch.setattr(database.pymysql, "connect", connect)
    monkeypatch.setattr(database, "initialize_schema", initialize_schema)

    assert diagnose_database.main() == 0

    output = capsys.readouterr().out
    assert "check=select_1 status=ok" in output
    assert "check=database_identity status=matched" in output
    assert "check=advisory_lock_acquire status=ok" in output
    assert "check=advisory_lock_release status=ok" in output
    assert "mcphub_app" not in output
    assert "database.internal" not in output
    assert "diagnostic-test-password" not in output
    connect.assert_called_once()
    kwargs = connect.call_args.kwargs
    assert kwargs["database"] == "mcphub"
    assert kwargs["user"] == "mcphub_app"
    assert kwargs["password"] == "diagnostic-test-password"
    assert kwargs["autocommit"] is True
    initialize_schema.assert_not_called()
    assert all(statement.lstrip().upper().startswith("SELECT") for statement, _ in cursor.statements)
    lock_call = next(item for item in cursor.statements if "GET_LOCK" in item[0])
    release_call = next(item for item in cursor.statements if "RELEASE_LOCK" in item[0])
    assert lock_call[1] == (diagnose_database.DIAGNOSTIC_LOCK, 3)
    assert release_call[1] == (diagnose_database.DIAGNOSTIC_LOCK,)
    assert connection.closed


def test_connection_failure_reports_only_safe_class_code_and_sqlstate(monkeypatch, capsys):
    error = DiagnosticDriverError(1045, "private host/password/driver details")
    monkeypatch.setattr(database, "_config", _test_config)
    monkeypatch.setattr(database.pymysql, "connect", Mock(side_effect=error))

    assert diagnose_database.main() == 1

    captured = capsys.readouterr()
    assert "step=database_connect" in captured.err
    assert "exception_class=DiagnosticDriverError" in captured.err
    assert "database_code=1045" in captured.err
    assert "sqlstate=HY000" in captured.err
    assert "private host/password/driver details" not in captured.err
    assert "diagnostic-test-password" not in captured.err
    assert "database.internal" not in captured.err


def test_failed_lock_release_is_retried_before_connection_close(monkeypatch, capsys):
    cursor = DiagnosticCursor(fail_on="RELEASE_LOCK")
    connection = DiagnosticConnection(cursor)
    monkeypatch.setattr(database, "_config", _test_config)
    monkeypatch.setattr(database.pymysql, "connect", Mock(return_value=connection))

    assert diagnose_database.main() == 1

    captured = capsys.readouterr()
    assert "step=advisory_lock_release" in captured.err
    assert "private driver message" not in captured.err
    assert sum("RELEASE_LOCK" in statement for statement, _ in cursor.statements) == 2
    assert connection.closed
