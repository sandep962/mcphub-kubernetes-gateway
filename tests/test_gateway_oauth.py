import asyncio
import base64
from contextlib import contextmanager
import hashlib
import re
from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from apps.gateway.app import main, oauth, mcp_endpoint
from apps.shared import database


ISSUER = "https://mcp.example.test"
RESOURCE = "https://mcp.example.test/mcp"
REDIRECT = "http://127.0.0.1:4567/callback"
CLIENT_ID = "public-test-client"
VERIFIER = "a" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
IDENTITY = {"identity_id": 19, "team_id": 4, "identity_name": "Demo User", "role": "viewer",
            "identity_type": "user", "active": True, "key_id": None, "auth_method": "oauth"}


def run(coro):
    return asyncio.run(coro)


async def request(method, path, **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url=ISSUER) as client:
        return await client.request(method, path, **kwargs)


class HiddenFieldParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "input" and values.get("type") == "hidden":
            self.values[values["name"]] = values["value"]


@pytest.fixture(autouse=True)
def configured_oauth(monkeypatch):
    monkeypatch.setenv("MCPHUB_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("MCPHUB_MCP_RESOURCE", RESOURCE)


def test_password_hashing_uses_salted_scrypt_and_checks_password():
    first = database.hash_password("correct horse battery staple")
    second = database.hash_password("correct horse battery staple")
    assert first.startswith("scrypt$16384$8$1$")
    assert first != second
    assert "correct horse battery staple" not in first
    assert database._password_matches("correct horse battery staple", first)
    assert not database._password_matches("wrong password", first)
    with pytest.raises(ValueError):
        database.hash_password("short")


def test_login_failure_delay_is_progressive_bounded_and_shared_state_backed(monkeypatch):
    cursor = FakeCursor([None, {"failures": 1}, {"failures": 3}, {"failures": 20}])
    fake_connection(monkeypatch, cursor)
    assert database.oauth_login_delay_seconds("demo", "client") == 0.0
    assert database.oauth_login_delay_seconds("demo", "client") == 0.25
    assert database.oauth_login_delay_seconds("demo", "client") == 1.0
    assert database.oauth_login_delay_seconds("demo", "client") == 2.0
    assert all("attempt_hash" in query for query, _ in cursor.queries)


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.queries = []
        self.rowcount = 1

    def execute(self, query, params=()):
        self.queries.append((query, params))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows.pop(0) if self.rows else []


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def fake_connection(monkeypatch, cursor):
    @contextmanager
    def connection():
        yield FakeConnection(cursor)
    monkeypatch.setattr(database, "connection", connection)


def test_password_enrollment_stores_only_hash_for_existing_identity(monkeypatch):
    cursor = FakeCursor([{"id": 19}])
    fake_connection(monkeypatch, cursor)
    database.set_oauth_password(19, "Demo.User", "correct horse battery staple")
    query, params = cursor.queries[1]
    assert "INSERT INTO oauth_credentials" in query
    assert params[0] == 19 and params[1] == "demo.user"
    assert params[2].startswith("scrypt$")
    assert params[2] != "correct horse battery staple"


def test_password_login_returns_database_identity_not_client_supplied_identity(monkeypatch):
    password_hash = database.hash_password("correct horse battery staple")
    row = {"identity_id": 19, "team_id": 4, "identity_name": "Database User", "role": "viewer",
           "identity_type": "user", "active": True, "password_hash": password_hash}
    cursor = FakeCursor([row, row])
    fake_connection(monkeypatch, cursor)
    identity = database.authenticate_oauth_user("db.user", "correct horse battery staple")
    assert identity["identity_id"] == 19 and identity["role"] == "viewer"
    assert "password_hash" not in identity
    assert identity["auth_method"] == "oauth"
    # A wrong password never returns an identity or lets a request body choose a role/id.
    assert database.authenticate_oauth_user("db.user", "incorrect password") is None


def test_oauth_schema_is_additive_and_versioned():
    schema_text = "\n".join(database.SCHEMA)
    for table in ("oauth_credentials", "oauth_clients", "oauth_authorization_requests",
                  "oauth_authorization_codes", "oauth_access_tokens", "oauth_consents",
                  "oauth_login_attempts"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in schema_text
    # Version 4 is recorded by initialize_schema, but never executed by this test.
    import inspect
    initializer = inspect.getsource(database.initialize_schema)
    assert 'VALUES (4)' in initializer
    assert 'VALUES (5)' in initializer


def test_oauth_consent_primary_key_fits_mariadb_innodb_limit():
    consent_ddl = next(statement for statement in database.SCHEMA if "CREATE TABLE IF NOT EXISTS oauth_consents" in statement)
    # The previous PK was identity_id + client_id + resource: 8 + (64 * 4) + (2048 * 4) bytes.
    previous_key_bytes = 8 + (64 * 4) + (2048 * 4)
    # The replacement is identity_id + client_id + a fixed 32-byte SHA-256 digest.
    replacement_key_bytes = 8 + (64 * 4) + 32
    assert previous_key_bytes > 3072
    assert replacement_key_bytes <= 3072
    assert "resource_hash BINARY(32) NOT NULL" in consent_ddl
    assert "PRIMARY KEY (identity_id, client_id, resource_hash)" in consent_ddl
    assert "PRIMARY KEY (identity_id, client_id, resource)" not in consent_ddl


def test_oauth_consent_index_migration_backfills_legacy_rows_and_replaces_key():
    cursor = FakeCursor([
        {"column_count": 0},
        [
            {"column_name": "identity_id"},
            {"column_name": "client_id"},
            {"column_name": "resource"},
        ],
    ])
    database._migrate_oauth_consent_resource_hash(cursor)
    statements = [query for query, _ in cursor.queries]
    assert any("ADD COLUMN resource_hash BINARY(32) NULL" in query for query in statements)
    assert any("UNHEX(SHA2(resource, 256))" in query for query in statements)
    backfill = next(query for query in statements if "UNHEX(SHA2(resource, 256))" in query)
    assert backfill.startswith("UPDATE oauth_consents SET resource_hash =")
    assert "SET scope_value" not in backfill and "SET resource =" not in backfill
    assert any(
        "DROP PRIMARY KEY" in query
        and "MODIFY resource_hash BINARY(32) NOT NULL" in query
        and "ADD PRIMARY KEY (identity_id, client_id, resource_hash)" in query
        for query in statements
    )
    assert not any("DROP TABLE" in query or "TRUNCATE" in query or "DELETE FROM oauth_consents" in query for query in statements)


def test_oauth_consent_index_migration_is_idempotent_after_key_replacement():
    cursor = FakeCursor([
        {"column_count": 1},
        [
            {"column_name": "identity_id"},
            {"column_name": "client_id"},
            {"column_name": "resource_hash"},
        ],
    ])
    database._migrate_oauth_consent_resource_hash(cursor)
    statements = [query for query, _ in cursor.queries]
    assert not any(query.startswith("ALTER TABLE oauth_consents") for query in statements)
    assert any("WHERE resource_hash IS NULL" in query for query in statements)


def test_authorization_code_is_pkce_bound_one_use_and_only_hashes_are_stored(monkeypatch):
    code_row = {"identity_id": 19, "code_challenge": CHALLENGE, "scope_value": "mcp:tools"}
    cursor = FakeCursor([code_row, {"active": True}])
    fake_connection(monkeypatch, cursor)
    result = database.exchange_oauth_code("one-time-code", VERIFIER, CLIENT_ID, REDIRECT, RESOURCE, "new-access-token")
    assert result == {"identity_id": 19, "client_id": CLIENT_ID, "scope": "mcp:tools", "resource": RESOURCE}
    assert cursor.queries[0][1][0] == database._oauth_hash("one-time-code")
    assert "consumed_at IS NULL" in cursor.queries[0][0]
    assert "expires_at > UTC_TIMESTAMP" in cursor.queries[0][0]
    token_insert = next(query for query in cursor.queries if "INSERT INTO oauth_access_tokens" in query[0])
    assert token_insert[1][0] == database._oauth_hash("new-access-token")
    assert "new-access-token" not in str(token_insert[1])
    consent_insert = next(query for query in cursor.queries if "INSERT INTO oauth_consents" in query[0])
    assert consent_insert[1][3] == hashlib.sha256(RESOURCE.encode("utf-8")).digest()
    assert "ON DUPLICATE KEY UPDATE scope_value = VALUES(scope_value)" in consent_insert[0]


def test_authorization_code_rejects_wrong_pkce_verifier(monkeypatch):
    cursor = FakeCursor([{"identity_id": 19, "code_challenge": CHALLENGE, "scope_value": "mcp:tools"}])
    fake_connection(monkeypatch, cursor)
    assert database.exchange_oauth_code("one-time-code", "wrong-verifier-012345678901234567890123456789012", CLIENT_ID,
                                        REDIRECT, RESOURCE, "new-access-token") is None
    assert not any("INSERT INTO oauth_access_tokens" in query for query, _ in cursor.queries)


def test_token_validation_binds_audience_expiry_and_revocation_and_hashes_presented_token(monkeypatch):
    stored = {"identity_id": 19, "client_id": CLIENT_ID, "scope_value": "mcp:tools", "resource": RESOURCE,
              "expires_at": "future", "team_id": 4, "identity_name": "Demo User", "role": "viewer",
              "identity_type": "user", "active": True}
    cursor = FakeCursor([stored])
    fake_connection(monkeypatch, cursor)
    identity = database.verify_oauth_access_token("opaque-bearer-token", RESOURCE)
    assert identity["identity_id"] == 19 and identity["oauth_scopes"] == ["mcp:tools"]
    query, params = cursor.queries[0]
    assert "t.revoked_at IS NULL" in query and "t.expires_at > UTC_TIMESTAMP" in query
    assert params == (database._oauth_hash("opaque-bearer-token"), RESOURCE)
    assert "opaque-bearer-token" not in str(params)


def test_token_revocation_uses_hash_and_client_binding(monkeypatch):
    cursor = FakeCursor()
    fake_connection(monkeypatch, cursor)
    database.revoke_oauth_access_token("opaque-bearer-token", CLIENT_ID)
    query, params = cursor.queries[0]
    assert "revoked_at IS NULL" in query
    assert params == (database._oauth_hash("opaque-bearer-token"), CLIENT_ID)


def test_revocation_endpoint_returns_no_token_material_and_revokes_for_client():
    async def check():
        with patch.object(main.database, "revoke_oauth_access_token") as revoke:
            response = await request("POST", "/oauth/revoke", data={
                "token": "do-not-return-token", "client_id": CLIENT_ID, "token_type_hint": "access_token"
            })
            return response, revoke
    response, revoke = run(check())
    assert response.status_code == 200 and response.json() == {}
    assert "do-not-return-token" not in response.text
    revoke.assert_called_once_with("do-not-return-token", CLIENT_ID)


def test_metadata_advertises_https_issuer_resource_pkce_and_registration():
    async def check():
        resource = await request("GET", "/.well-known/oauth-protected-resource/mcp")
        auth_server = await request("GET", "/.well-known/oauth-authorization-server")
        return resource, auth_server

    resource, auth_server = run(check())
    assert resource.status_code == 200
    assert resource.json() == {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "scopes_supported": ["mcp:tools"],
        "bearer_methods_supported": ["header"],
    }
    metadata = auth_server.json()
    assert metadata["issuer"] == ISSUER
    assert metadata["code_challenge_methods_supported"] == ["S256"]
    assert metadata["authorization_response_iss_parameter_supported"] is True
    assert metadata["registration_endpoint"] == ISSUER + "/oauth/register"


def test_redirect_validation_allows_https_and_loopback_only():
    assert oauth.validate_redirect_uri("https://client.example/callback")
    assert oauth.validate_redirect_uri("http://localhost:49000/callback")
    assert oauth.validate_redirect_uri("http://[::1]:49000/callback")
    assert not oauth.validate_redirect_uri("http://client.example/callback")
    assert not oauth.validate_redirect_uri("https://user:pass@client.example/callback")
    assert not oauth.validate_redirect_uri("https://client.example/callback#fragment")
    assert not oauth.validate_redirect_uri("javascript:alert(1)")
    assert not oauth.validate_redirect_uri("http://127.0.0.1:99999/callback")


def test_registration_rejects_non_loopback_http_and_registers_public_client():
    async def check():
        with patch.object(main.database, "register_oauth_client") as register:
            rejected = await request("POST", "/oauth/register", json={
                "client_name": "Test", "redirect_uris": ["http://attacker.example/callback"]
            })
            accepted = await request("POST", "/oauth/register", json={
                "client_name": "Test MCP Client", "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code"], "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            })
            return rejected, accepted, register

    rejected, accepted, register = run(check())
    assert rejected.status_code == 400
    assert rejected.json()["error"] == "invalid_client_metadata"
    assert accepted.status_code == 201
    assert accepted.json()["token_endpoint_auth_method"] == "none"
    register.assert_called_once()
    assert register.call_args.args[1:] == ("Test MCP Client", [REDIRECT])


def test_authorization_pkce_state_csrf_consent_and_token_issuance():
    request_fields = {}

    def create_request(request_id, fields, csrf):
        request_fields.update(request_id=request_id, csrf=csrf, **fields)

    def get_request(request_id, csrf):
        if request_id != request_fields.get("request_id") or csrf != request_fields.get("csrf"):
            return None
        return {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "state_value": "state123",
                "code_challenge": CHALLENGE, "scope_value": "mcp:tools", "resource": RESOURCE}

    def consume_request(request_id, csrf):
        return get_request(request_id, csrf)

    async def run_flow():
        params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                  "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": RESOURCE,
                  "state": "state123", "scope": "mcp:tools"}
        issued = {}
        with patch.object(main.database, "get_oauth_client", return_value={
                "client_id": CLIENT_ID, "client_name": "Test MCP", "redirect_uris": [REDIRECT]}), \
             patch.object(main.database, "create_oauth_request", side_effect=create_request), \
             patch.object(main.database, "get_oauth_request", side_effect=get_request), \
             patch.object(main.database, "consume_oauth_request", side_effect=consume_request), \
             patch.object(main.database, "authenticate_oauth_user", return_value=IDENTITY), \
             patch.object(main.database, "oauth_login_delay_seconds", return_value=0.0), \
             patch.object(main.database, "clear_oauth_login_failures"), \
             patch.object(main.database, "create_oauth_code", side_effect=lambda code, fields, identity_id: issued.update(code=code, fields=fields, identity_id=identity_id)), \
             patch.object(main.database, "record_audit"):
            page = await request("GET", "/oauth/authorize", params=params)
            hidden = HiddenFieldParser()
            hidden.feed(page.text)
            result = await request("POST", "/oauth/authorize", data={
                **hidden.values, "username": "demo", "password": "not-logged-password", "consent": "allow",
                "identity_id": "1", "role": "admin",
            })
        return page, hidden, result, issued

    page, hidden, result, issued = run(run_flow())
    assert page.status_code == 200
    assert "not-logged-password" not in page.text
    assert set(hidden.values) == {"request_id", "csrf_token"}
    assert result.status_code == 303
    query = parse_qs(urlsplit(result.headers["location"]).query)
    assert query["state"] == ["state123"]
    assert query["iss"] == [ISSUER]
    assert query["code"] == [issued["code"]]
    assert issued["identity_id"] == IDENTITY["identity_id"]

    async def exchange():
        with patch.object(main.database, "exchange_oauth_code", return_value={"scope": "mcp:tools"}) as token_exchange:
            response = await request("POST", "/oauth/token", data={
                "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": issued["code"],
                "redirect_uri": REDIRECT, "code_verifier": VERIFIER, "resource": RESOURCE,
            })
            return response, token_exchange

    token_response, token_exchange = run(exchange())
    assert token_response.status_code == 200
    assert token_response.json()["token_type"] == "Bearer"
    assert token_response.json()["expires_in"] == 3600
    assert token_response.headers["cache-control"] == "no-store"
    assert token_exchange.call_args.args[1:5] == (VERIFIER, CLIENT_ID, REDIRECT, RESOURCE)


def test_authorization_rejects_wrong_resource_and_duplicate_parameters():
    async def check():
        params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT,
                  "code_challenge": CHALLENGE, "code_challenge_method": "S256", "resource": "https://elsewhere/mcp",
                  "state": "s"}
        wrong_resource = await request("GET", "/oauth/authorize", params=params)
        params["resource"] = RESOURCE
        params_list = list(params.items()) + [("state", "duplicate")]
        duplicate = await request("GET", "/oauth/authorize", params=params_list)
        return wrong_resource, duplicate

    wrong_resource, duplicate = run(check())
    assert wrong_resource.status_code == 400
    assert duplicate.status_code == 400


def test_token_rejects_wrong_resource_and_duplicate_form_fields():
    async def check():
        base = {"grant_type": "authorization_code", "client_id": CLIENT_ID, "code": "one-use",
                "redirect_uri": REDIRECT, "code_verifier": VERIFIER, "resource": RESOURCE}
        wrong = {**base, "resource": "https://elsewhere/mcp"}
        response_wrong = await request("POST", "/oauth/token", data=wrong)
        duplicate = list(base.items()) + [("client_id", "spoofed")]
        response_duplicate = await request("POST", "/oauth/token", content=urlencode(duplicate),
                                            headers={"Content-Type": "application/x-www-form-urlencoded"})
        return response_wrong, response_duplicate

    wrong, duplicate = run(check())
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    assert duplicate.status_code == 400 and duplicate.json()["error"] == "invalid_request"


def test_failed_login_is_generic_and_never_echoes_password():
    async def check():
        fields = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "state_value": "state123",
                  "code_challenge": CHALLENGE, "scope_value": "mcp:tools", "resource": RESOURCE}
        with patch.object(main.database, "get_oauth_request", return_value=fields), \
             patch.object(main.database, "oauth_login_delay_seconds", return_value=0.0), \
             patch.object(main.database, "authenticate_oauth_user", return_value=None), \
             patch.object(main.database, "record_oauth_login_failure"), \
             patch.object(main.database, "record_audit"):
            return await request("POST", "/oauth/authorize", data={
                "request_id": "opaque-request-id", "csrf_token": "csrf-value", "username": "unknown-user",
                "password": "do-not-reflect-this-password", "consent": "allow",
            })
    response = run(check())
    assert response.status_code == 200
    assert "Sign-in failed. Check your credentials" in response.text
    assert "do-not-reflect-this-password" not in response.text
    assert "unknown-user" not in response.text
    assert 'name="identity_id"' not in response.text


def test_consent_denial_does_not_issue_code_or_redirect_elsewhere():
    async def check():
        fields = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "state_value": "state123",
                  "code_challenge": CHALLENGE, "scope_value": "mcp:tools", "resource": RESOURCE}
        with patch.object(main.database, "get_oauth_request", return_value=fields), \
             patch.object(main.database, "consume_oauth_request", return_value=fields), \
             patch.object(main.database, "oauth_login_delay_seconds", return_value=0.0), \
             patch.object(main.database, "authenticate_oauth_user", return_value=IDENTITY), \
             patch.object(main.database, "clear_oauth_login_failures"), \
             patch.object(main.database, "record_audit") as audit, \
             patch.object(main.database, "create_oauth_code") as create_code:
            response = await request("POST", "/oauth/authorize", data={
                "request_id": "opaque-request-id", "csrf_token": "csrf-value", "consent": "deny",
            })
            return response, audit, create_code
    response, audit, create_code = run(check())
    assert response.status_code == 303
    assert urlsplit(response.headers["location"]).netloc == "127.0.0.1:4567"
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["state123"]
    audit.assert_called_once()
    create_code.assert_not_called()


def test_oauth_token_identity_is_used_for_tool_audit_and_permissions(monkeypatch):
    from apps.gateway.app.mcp_endpoint import call_tool_as_identity
    audit_rows = []
    async def call_upstream(server_name, tool_name, arguments):
        return {"content": [{"type": "text", "text": "ok"}], "is_error": False}
    monkeypatch.setattr(mcp_endpoint, "list_all_tools", lambda: asyncio.sleep(0, result=[{
        "server": "inventory", "name": "list_inventory", "inputSchema": {"type": "object"}
    }]))
    monkeypatch.setattr(mcp_endpoint.database, "is_authorized", lambda identity, server, tool: identity["identity_id"] == 19)
    monkeypatch.setattr(mcp_endpoint, "call_server_tool", call_upstream)
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **row: audit_rows.append(row))
    result = run(call_tool_as_identity({**IDENTITY, "oauth_scopes": ["mcp:tools"]}, "inventory__list_inventory", {}))
    assert result.isError is False
    assert any(row.get("identity", {}).get("identity_id") == 19 and row.get("server_name") == "inventory"
               and row.get("tool_name") == "list_inventory" and row.get("outcome") == "success" for row in audit_rows)


def test_oauth_scope_is_checked_before_upstream_call(monkeypatch):
    from apps.gateway.app.mcp_endpoint import call_tool_as_identity
    audit_rows = []
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **row: audit_rows.append(row))
    with patch.object(mcp_endpoint, "list_all_tools") as discover:
        result = run(call_tool_as_identity({**IDENTITY, "oauth_scopes": []}, "inventory__list_inventory", {}))
    assert result.isError is True
    discover.assert_not_called()
    assert audit_rows[-1]["error_class"] == "insufficient_scope"
    assert audit_rows[-1]["identity"]["identity_id"] == 19


def _mcp_scope(*, authorization=None, api_key=None):
    headers = [(b"content-type", b"application/json")]
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    if api_key:
        headers.append((b"x-api-key", api_key.encode()))
    return {"type": "http", "method": "POST", "path": "/", "headers": headers}


def _asgi(scope):
    messages = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        messages.append(message)
    run(mcp_endpoint.mcp_http_app(scope, receive, send))
    return messages


def test_mcp_accepts_oauth_bearer_and_keeps_api_key_compatibility(monkeypatch):
    calls = []
    async def session(scope, receive, send):
        calls.append(mcp_endpoint._current_identity.get())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    monkeypatch.setattr(mcp_endpoint.oauth, "authenticate_bearer", lambda token: asyncio.sleep(0, result={**IDENTITY, "oauth_scopes": ["mcp:tools"]}))
    monkeypatch.setattr(mcp_endpoint.database, "verify_api_key", lambda key: {**IDENTITY, "key_id": 55, "auth_method": "api_key"})
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)
    monkeypatch.setattr(mcp_endpoint._session_manager, "handle_request", session)
    bearer = _asgi(_mcp_scope(authorization="Bearer opaque-access-token"))
    api_key = _asgi(_mcp_scope(api_key="mcp_legacy-key"))
    assert bearer[0]["status"] == 204
    assert api_key[0]["status"] == 204
    assert calls[0]["identity_id"] == IDENTITY["identity_id"]
    assert calls[0]["auth_method"] == "oauth"
    assert calls[1]["auth_method"] == "api_key"


def test_mcp_invalid_bearer_gets_sanitized_discovery_challenge(monkeypatch):
    async def invalid(_token):
        return None
    monkeypatch.setattr(mcp_endpoint.oauth, "authenticate_bearer", invalid)
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)
    messages = _asgi(_mcp_scope(authorization="Bearer invalid-secret-token"))
    assert messages[0]["status"] == 401
    headers = dict(messages[0]["headers"])
    assert headers[b"www-authenticate"].decode() == (
        'Bearer error="invalid_token", resource_metadata="https://mcp.example.test/.well-known/oauth-protected-resource/mcp", scope="mcp:tools"'
    )
    assert b"invalid-secret-token" not in messages[1]["body"]


def test_mcp_rejects_mixed_authentication_schemes(monkeypatch):
    monkeypatch.setattr(mcp_endpoint.database, "verify_api_key", lambda key: IDENTITY)
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)
    messages = _asgi(_mcp_scope(authorization="Bearer token", api_key="mcp_legacy"))
    assert messages[0]["status"] == 400


def test_mcp_insufficient_scope_returns_403_challenge(monkeypatch):
    async def insufficient(_token):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="The access token lacks the required MCP scope.")
    monkeypatch.setattr(mcp_endpoint.oauth, "authenticate_bearer", insufficient)
    monkeypatch.setattr(mcp_endpoint.database, "record_audit", lambda **kwargs: None)
    messages = _asgi(_mcp_scope(authorization="Bearer insufficient-scope-token"))
    assert messages[0]["status"] == 403
    headers = dict(messages[0]["headers"])
    assert b'error="insufficient_scope"' in headers[b"www-authenticate"]
    assert b'scope="mcp:tools"' in headers[b"www-authenticate"]


def test_oauth_bearer_is_not_accepted_for_rest_admin_routes():
    async def check():
        with patch.object(main.database, "verify_api_key", return_value=None), \
             patch.object(main.database, "record_audit"):
            return await request("GET", "/admin/observability/tool-calls",
                                 headers={"Authorization": "Bearer valid-oauth-token"})
    response = run(check())
    assert response.status_code == 401
    assert "/admin/observability/tool-calls" in main.app.openapi()["paths"]
