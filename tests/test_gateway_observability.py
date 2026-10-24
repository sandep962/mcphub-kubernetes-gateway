import asyncio
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch

import httpx
import pytest

from apps.gateway.app import main
from apps.shared import database


class MetricsCursor:
    def __init__(self, totals, groups):
        self.totals = totals
        self.groups = groups
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.totals

    def fetchall(self):
        return self.groups


def _metric_row(server, tool, *, calls, success, failure, denied, timeouts, avg, maximum):
    return {
        "server_name": server,
        "tool_name": tool,
        "total_calls": calls,
        "success_count": success,
        "failure_count": failure,
        "denied_count": denied,
        "timeout_count": timeouts,
        "tool_error_count": failure - timeouts,
        "other_failure_count": 0,
        "avg_duration_ms": Decimal(str(avg)) if avg is not None else None,
        "max_duration_ms": maximum,
    }


@pytest.fixture
def fake_metrics_connection():
    totals = _metric_row("ignored", "ignored", calls=4, success=2, failure=1, denied=1, timeouts=1, avg=18.25, maximum=41)
    groups = [
        _metric_row("inventory", "list_inventory", calls=3, success=2, failure=1, denied=0, timeouts=1, avg=18.25, maximum=41),
        _metric_row("support", "list_tickets", calls=1, success=0, failure=0, denied=1, timeouts=0, avg=5, maximum=5),
    ]
    cursor = MetricsCursor(totals, groups)

    @contextmanager
    def connection():
        class FakeConnection:
            def cursor(self):
                return cursor

        yield FakeConnection()

    with patch.object(database, "connection", connection):
        yield cursor


def test_tool_call_metrics_aggregate_counts_rates_latency_and_fixed_failure_categories(fake_metrics_connection):
    result = database.tool_call_metrics(24)

    assert result["window_hours"] == 24
    assert result["totals"] == {
        "total_calls": 4,
        "success_count": 2,
        "failure_count": 1,
        "denied_count": 1,
        "success_rate_percent": 50.0,
        "failure_rate_percent": 25.0,
        "denied_rate_percent": 25.0,
        "timeout_count": 1,
        "tool_error_count": 0,
        "other_failure_count": 0,
        "avg_duration_ms": 18.25,
        "max_duration_ms": 41,
    }
    assert result["tools"][0]["server_name"] == "inventory"
    assert result["tools"][0]["tool_name"] == "list_inventory"
    assert result["tools"][0]["total_calls"] == 3
    assert result["tools"][1]["denied_count"] == 1
    assert result["truncated"] is False
    assert "identity_id" not in repr(result)
    assert "error_class" not in repr(result)


def test_tool_call_metrics_time_bound_and_group_limit_are_parameterized(fake_metrics_connection):
    database.tool_call_metrics(6)

    totals_query, totals_params = fake_metrics_connection.queries[0]
    grouped_query, grouped_params = fake_metrics_connection.queries[1]
    assert "created_at >= DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL %s HOUR)" in totals_query
    assert "created_at >= DATE_SUB(CURRENT_TIMESTAMP(6), INTERVAL %s HOUR)" in grouped_query
    assert "GROUP BY server_name, tool_name" in grouped_query
    assert totals_params == (6,)
    assert grouped_params[0] == totals_params[0]
    assert grouped_params[1] == 201


def test_tool_call_metrics_caps_grouped_output(fake_metrics_connection):
    fake_metrics_connection.groups = [
        _metric_row(f"server-{index}", f"tool-{index}", calls=1, success=1, failure=0, denied=0, timeouts=0, avg=2, maximum=2)
        for index in range(201)
    ]

    result = database.tool_call_metrics()

    assert len(result["tools"]) == 200
    assert result["truncated"] is True


@pytest.mark.parametrize("hours", [0, 169, True, 1.5])
def test_tool_call_metrics_reject_out_of_bounds_windows(hours):
    with patch.object(database, "connection") as connect:
        with pytest.raises(ValueError):
            database.tool_call_metrics(hours)
        connect.assert_not_called()


def test_admin_tool_call_metrics_endpoint_returns_aggregate_only_and_rejects_viewers():
    metrics = {
        "window_hours": 24,
        "totals": {"total_calls": 1, "success_count": 1, "failure_count": 0, "denied_count": 0},
        "tools": [{"server_name": "inventory", "tool_name": "list_inventory", "total_calls": 1}],
        "truncated": False,
    }

    async def run(role):
        main.app.dependency_overrides[main.require_identity] = lambda: {
            "identity_id": 73, "team_id": 9, "key_id": 101, "role": role,
        }
        try:
            with (
                patch.object(database, "tool_call_metrics", return_value=metrics) as query,
                patch.object(database, "record_audit"),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    response = await client.get("/admin/observability/tool-calls?hours=24")
                return response, query
        finally:
            main.app.dependency_overrides.clear()

    admin_response, admin_query = asyncio.run(run("admin"))
    assert admin_response.status_code == 200
    assert admin_response.json() == metrics
    assert "identity_id" not in admin_response.text
    assert "api_key" not in admin_response.text
    assert "endpoint_url" not in admin_response.text
    assert admin_query.call_args.args == (24,)

    viewer_response, viewer_query = asyncio.run(run("viewer"))
    assert viewer_response.status_code == 403
    viewer_query.assert_not_called()


def test_admin_tool_call_metrics_endpoint_enforces_http_window_bounds():
    async def run():
        main.app.dependency_overrides[main.require_identity] = lambda: {"role": "admin"}
        try:
            with patch.object(database, "tool_call_metrics") as query:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    response = await client.get("/admin/observability/tool-calls?hours=169")
            return response, query
        finally:
            main.app.dependency_overrides.clear()

    response, query = asyncio.run(run())
    assert response.status_code == 422
    query.assert_not_called()
