import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException

from apps.gateway.app import main
from apps.shared import database


class FakeCursor:
    def __init__(self, result=None):
        self.result = result
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def cursor(self):
        return self.cursor_value

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class GatewaySecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_or_revoked_key_is_denied_and_audited_without_key_value(self):
        with (
            patch.object(database, "verify_api_key", return_value=None),
            patch.object(database, "record_audit") as audit,
        ):
            with self.assertRaises(HTTPException) as caught:
                await main.require_identity("mcp_invalid_secret_value")

        self.assertEqual(caught.exception.status_code, 401)
        audit.assert_called_once_with(
            event_type="authentication", outcome="denied", error_class="invalid_key"
        )
        self.assertNotIn("mcp_invalid_secret_value", repr(audit.call_args))

    async def test_missing_key_is_audited_and_rejected(self):
        with patch.object(database, "record_audit") as audit:
            with self.assertRaises(HTTPException) as caught:
                await main.require_identity(None)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(audit.call_args.kwargs["error_class"], "missing_key")

    async def test_non_admin_cannot_access_admin_endpoint_and_is_audited(self):
        identity = {"role": "operator", "identity_id": 3, "team_id": 9}
        with patch.object(database, "record_audit") as audit:
            with self.assertRaises(HTTPException) as caught:
                await main.require_admin(identity)

        self.assertEqual(caught.exception.status_code, 403)
        audit.assert_called_once_with(
            event_type="admin_request",
            outcome="denied",
            error_class="insufficient_role",
            identity=identity,
        )

    def test_viewer_cannot_mutate_even_when_permission_exists(self):
        identity = {"role": "viewer", "identity_id": 3, "team_id": 9}
        with patch.object(database, "connection") as open_connection:
            self.assertFalse(database.is_authorized(identity, "inventory", "update_stock"))
        open_connection.assert_not_called()

    def test_permissions_are_scoped_to_identity_or_its_team(self):
        cursor = FakeCursor(result=None)

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        identity = {"role": "operator", "identity_id": 3, "team_id": 9}
        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            allowed = database.is_authorized(identity, "inventory", "list_inventory")

        self.assertFalse(allowed)
        query, params = cursor.queries[0]
        self.assertIn("subject_type = 'identity'", query)
        self.assertIn("subject_type = 'team'", query)
        self.assertEqual(params, (3, 9, "inventory", "list_inventory"))

    def test_key_lookup_excludes_revoked_and_expired_keys(self):
        cursor = FakeCursor(result=None)

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            result = database.verify_api_key("mcp_" + "x" * 40)

        self.assertIsNone(result)
        self.assertIn("k.revoked_at IS NULL", cursor.queries[0][0])
        self.assertIn("k.expires_at > UTC_TIMESTAMP", cursor.queries[0][0])

    def test_multiline_key_file_content_is_not_treated_as_multiple_credentials(self):
        first_key = "mcp_" + "a" * 40
        second_key = "mcp_" + "b" * 40
        expected_identity = {
            "key_id": 4,
            "identity_id": 2,
            "team_id": 1,
            "identity_name": "admin",
            "role": "admin",
            "identity_type": "user",
            "active": 1,
        }

        class HashCursor(FakeCursor):
            def fetchone(self):
                query, params = self.queries[-1]
                if "WHERE k.key_hash = %s" in query and params[0] == database.hash_api_key(first_key):
                    return expected_identity
                return None

        cursor = HashCursor()

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            self.assertEqual(database.verify_api_key(first_key), expected_identity)
            self.assertIsNone(database.verify_api_key(first_key + "\n" + second_key))

        self.assertEqual(cursor.queries[0][1], (database.hash_api_key(first_key),))
        self.assertEqual(
            cursor.queries[2][1],
            (database.hash_api_key(first_key + "\n" + second_key),),
        )

    def test_inventory_updates_use_persistent_row_lock(self):
        cursor = FakeCursor(result={"sku": "SKU-X", "name": "Test", "quantity": 8, "reserved": 2})

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            updated = database.update_stock("SKU-X", 10)

        self.assertEqual(updated["available"], 8)
        self.assertIn("FOR UPDATE", cursor.queries[0][0])
        self.assertIn("UPDATE inventory_items", cursor.queries[1][0])

    def test_support_ticket_creation_is_persisted_with_insert(self):
        cursor = FakeCursor()

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            ticket = database.create_ticket(
                "TKT-TEST", "Customer", "Subject", "Details", "normal"
            )

        self.assertEqual(ticket["id"], "TKT-TEST")
        self.assertIn("INSERT INTO support_tickets", cursor.queries[0][0])


if __name__ == "__main__":
    unittest.main()
