import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from apps.gateway.app import main
from apps.shared import database


class FakeCursor:
    def __init__(self, result=None):
        self.result = result
        self.queries = []
        self.lastrowid = 41

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
    async def test_admin_team_routes_require_an_explicit_admin_role(self):
        verification_team_member = {
            "identity_id": 12,
            "team_id": 99,
            "identity_name": "verification-member",
            "role": "viewer",
            "identity_type": "user",
        }
        main.app.dependency_overrides[main.require_identity] = lambda: verification_team_member
        try:
            with patch.object(database, "record_audit") as audit:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    listed = await client.get("/admin/teams")
                    created = await client.post(
                        "/admin/teams", json={"slug": "verification", "name": "Verification Team"}
                    )
        finally:
            main.app.dependency_overrides.clear()

        self.assertEqual(listed.status_code, 403)
        self.assertEqual(created.status_code, 403)
        self.assertEqual(audit.call_count, 2)
        self.assertTrue(all(call.kwargs["error_class"] == "insufficient_role" for call in audit.call_args_list))

    async def test_admin_team_routes_reject_missing_authentication(self):
        with patch.object(database, "record_audit"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                response = await client.get("/admin/teams")

        self.assertEqual(response.status_code, 401)

    async def test_admin_team_routes_use_persisted_database_functions(self):
        admin = {"identity_id": 1, "team_id": 1, "role": "admin", "identity_type": "user"}
        created_team = {"id": 22, "slug": "verification", "name": "Verification Team"}
        main.app.dependency_overrides[main.require_admin] = lambda: admin
        try:
            with (
                patch.object(database, "create_team", return_value=created_team) as create_team,
                patch.object(database, "list_teams", return_value=[
                    {"id": 1, "slug": "administrators", "name": "Administrators"}, created_team
                ]) as list_teams,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    created = await client.post(
                        "/admin/teams", json={"slug": "verification", "name": "Verification Team"}
                    )
                    listed = await client.get("/admin/teams")
        finally:
            main.app.dependency_overrides.clear()

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json(), created_team)
        create_team.assert_called_once_with("verification", "Verification Team")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([team["name"] for team in listed.json()], ["Administrators", "Verification Team"])
        list_teams.assert_called_once_with()

    async def test_tools_catalog_includes_granted_tools_from_each_discovered_upstream(self):
        viewer = {
            "identity_id": 2,
            "team_id": 7,
            "identity_name": "viewer",
            "role": "viewer",
            "identity_type": "user",
        }
        discovered = [
            {"server": "inventory", "name": "list_inventory"},
            {"server": "inventory", "name": "update_stock"},
            {"server": "support", "name": "list_tickets"},
            {"server": "woocommerce", "name": "list_orders"},
            {"server": "other-enabled-server", "name": "read_status"},
        ]
        permitted = {
            ("inventory", "list_inventory"),
            ("support", "list_tickets"),
            ("woocommerce", "list_orders"),
        }
        main.app.dependency_overrides[main.require_identity] = lambda: viewer
        try:
            with (
                patch.object(main, "list_all_tools", return_value=discovered),
                patch.object(
                    database,
                    "is_authorized",
                    side_effect=lambda identity, server, name: (server, name) in permitted,
                ) as authorize,
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    response = await client.get("/tools")
        finally:
            main.app.dependency_overrides.clear()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {(tool["server"], tool["name"]) for tool in response.json()},
            permitted,
        )
        self.assertEqual(authorize.call_count, len(discovered))

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

    def test_api_key_creation_persists_hash_and_prefix_but_returns_no_secret(self):
        api_key = "mcp_" + "test-only-random-key-material-1234567890"
        cursor = FakeCursor(result={"id": 9})

        @contextmanager
        def fake_connection():
            yield FakeConnection(cursor)

        with (
            patch.object(database, "_schema_initialized", True),
            patch.object(database, "connection", fake_connection),
        ):
            created = database.create_api_key(9, api_key)

        insert_query, values = cursor.queries[1]
        self.assertIn("key_prefix, key_hash", insert_query)
        self.assertNotIn("api_key,", insert_query)
        self.assertEqual(values[0], 9)
        self.assertEqual(values[1], api_key[:12])
        self.assertTrue(values[2] == database.hash_api_key(api_key))
        self.assertNotIn(api_key, repr(values))
        self.assertEqual(set(created), {"id", "identity_id", "key_prefix"})
        self.assertNotIn("key_hash", created)

    async def test_server_generated_api_key_is_returned_once_and_legacy_contract_remains_valid(self):
        generated_key = "mcp_" + "test-server-generated-material-1234567890"
        metadata = {"id": 11, "identity_id": 9, "key_prefix": generated_key[:12]}
        main.app.dependency_overrides[main.require_admin] = lambda: {"role": "admin"}
        try:
            with (
                patch.object(database, "generate_api_key", return_value=generated_key) as generate,
                patch.object(database, "create_api_key", return_value=metadata) as create,
                patch.object(database, "list_api_keys", return_value=[metadata]),
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=main.app), base_url="http://test"
                ) as client:
                    issued = await client.post("/admin/keys", json={"identity_id": 9})
                    legacy_key = "mcp_" + "legacy-client-key-material-1234567890"
                    legacy = await client.post(
                        "/admin/keys", json={"identity_id": 9, "api_key": legacy_key}
                    )
                    listed = await client.get("/admin/keys")
        finally:
            main.app.dependency_overrides.clear()

        self.assertEqual(issued.status_code, 200)
        issued_body = issued.json()
        self.assertTrue(issued_body.get("api_key"))
        self.assertNotIn("key_hash", issued_body)
        self.assertTrue(create.call_args_list[0].args[1] == issued_body["api_key"])
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(legacy.status_code, 200)
        self.assertNotIn("api_key", legacy.json())
        self.assertNotIn("key_hash", legacy.json())
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("api_key", listed.text)
        self.assertNotIn("key_hash", listed.text)

    async def test_revoked_key_is_rejected_on_the_next_rest_request(self):
        state = {"active": True}
        identity = {
            "identity_id": 5, "team_id": 2, "key_id": 12,
            "identity_name": "test-user", "role": "operator", "identity_type": "user",
        }

        def verify(_key):
            return identity if state["active"] else None

        with (
            patch.object(database, "verify_api_key", side_effect=verify),
            patch.object(database, "record_audit"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                active = await client.get("/whoami", headers={"X-API-Key": "test-key-not-a-secret"})
                state["active"] = False
                revoked = await client.get("/whoami", headers={"X-API-Key": "test-key-not-a-secret"})

        self.assertEqual(active.status_code, 200)
        self.assertEqual(revoked.status_code, 401)

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
