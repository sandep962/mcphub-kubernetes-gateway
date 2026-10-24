"""Built-in OAuth authorization server for MCP clients.

OAuth uses opaque, random access tokens stored only as SHA-256 hashes. All
authorization state is persisted in the shared metadata database so both
gateway replicas observe the same codes, tokens, and revocations.
"""

from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import secrets
import time
import asyncio
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from apps.shared import database

router = APIRouter()
OAUTH_SCOPE = "mcp:tools"
ACCESS_TOKEN_TTL_SECONDS = 3600
_CHALLENGE_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")


def oauth_urls() -> tuple[str, str]:
    issuer = os.environ.get("MCPHUB_OAUTH_ISSUER", "").strip().rstrip("/")
    resource = os.environ.get("MCPHUB_MCP_RESOURCE", "").strip().rstrip("/")
    try:
        parsed_issuer, parsed_resource = urlsplit(issuer), urlsplit(resource)
        for parsed in (parsed_issuer, parsed_resource):
            _ = parsed.port
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise ValueError
    except ValueError:
        raise HTTPException(status_code=503, detail="OAuth requires configured HTTPS issuer and MCP resource URLs.") from None
    if parsed_resource.path != "/mcp":
        raise HTTPException(status_code=503, detail="The MCP resource URL must identify the HTTPS /mcp endpoint.")
    if parsed_issuer.path not in ("", "/"):
        raise HTTPException(status_code=503, detail="The OAuth issuer must be configured at an HTTPS origin.")
    return issuer, resource


def _resource_metadata_url(resource: str) -> str:
    parsed = urlsplit(resource)
    base_path = parsed.path[:-4] if parsed.path.endswith("/mcp") else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, base_path + "/.well-known/oauth-protected-resource/mcp", "", ""))


def validate_redirect_uri(uri: str) -> bool:
    if (not isinstance(uri, str) or len(uri) > 2048
            or re.search(r"[\x00-\x20\x7f\\]", uri)):
        return False
    try:
        parsed = urlsplit(uri)
        _ = parsed.port
    except ValueError:
        return False
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _html_page(title: str, content: str, *, status_code: int = 200) -> HTMLResponse:
    safe_title = html.escape(title)
    body = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_title} · MCPHub</title>
<style>body{{margin:0;background:#f4f5f9;color:#182033;font:16px system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}}main{{box-sizing:border-box;background:white;border:1px solid #dfe3ed;border-radius:16px;padding:32px;width:min(460px,calc(100% - 32px));box-shadow:0 16px 48px #17203312}}h1{{font-size:24px;margin:0 0 8px}}p{{color:#58647b;line-height:1.55}}label{{display:block;font-weight:600;margin:16px 0 6px}}input{{box-sizing:border-box;width:100%;padding:12px;border:1px solid #cbd3e2;border-radius:8px;font:inherit}}button{{margin-top:20px;padding:12px 18px;border:0;border-radius:8px;background:#682de0;color:white;font-weight:700;cursor:pointer}}.error{{color:#a51d32;background:#fff0f1;border:1px solid #ffd0d4;padding:10px;border-radius:8px}}small{{display:block;margin-top:16px;color:#657187}}</style>
<main><div style="color:#682de0;font-size:12px;font-weight:800;letter-spacing:.12em">MCPHUB ACCESS</div><h1>{safe_title}</h1>{content}</main></html>"""
    return HTMLResponse(body, status_code=status_code, headers={
        "Cache-Control": "no-store", "Pragma": "no-cache", "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
    })


def _login_form(request_id: str, csrf: str, *, error: str = "", client_name: str = "MCP client") -> HTMLResponse:
    safe_id, safe_csrf = html.escape(request_id, quote=True), html.escape(csrf, quote=True)
    safe_client = html.escape(client_name)
    error_html = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
    content = f"""<p>Sign in to authorize <strong>{safe_client}</strong> to access MCPHub tools allowed for your account.</p>{error_html}
<form method="post" action="/oauth/authorize" autocomplete="on">
<input type="hidden" name="request_id" value="{safe_id}"><input type="hidden" name="csrf_token" value="{safe_csrf}">
<label for="username">Username</label><input id="username" name="username" autocomplete="username" required maxlength="120">
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024">
<p>Authorizing grants access only to tools permitted for your account.</p>
<button type="submit" name="consent" value="allow">Sign in and authorize</button></form>
<form method="post" action="/oauth/authorize"><input type="hidden" name="request_id" value="{safe_id}"><input type="hidden" name="csrf_token" value="{safe_csrf}">
<button type="submit" name="consent" value="deny" formnovalidate style="background:#687386">Deny</button></form>
<small>Credentials are checked by MCPHub and are never shared with the MCP client.</small>"""
    return _html_page("Sign in to MCPHub", content)


def _oauth_error(error: str, description: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status_code,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _read_form(request: Request) -> dict[str, list[str]] | None:
    """Read a bounded form-urlencoded body without unbounded multipart parsing."""
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        return None
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                return None
        return parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=32)
    except (UnicodeDecodeError, ValueError):
        return None


def _redirect(uri: str, params: dict[str, str]) -> RedirectResponse:
    parsed = urlsplit(uri)
    query = parsed.query + ("&" if parsed.query else "") + urlencode(params)
    return RedirectResponse(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, "")), status_code=303,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata():
    issuer, resource = oauth_urls()
    return {"resource": resource, "authorization_servers": [issuer], "scopes_supported": [OAUTH_SCOPE],
            "bearer_methods_supported": ["header"]}


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata():
    issuer, _ = oauth_urls()
    return {"issuer": issuer, "authorization_endpoint": issuer + "/oauth/authorize",
            "token_endpoint": issuer + "/oauth/token", "registration_endpoint": issuer + "/oauth/register",
            "revocation_endpoint": issuer + "/oauth/revoke", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"], "scopes_supported": [OAUTH_SCOPE],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": False}


@router.post("/oauth/register", status_code=201)
async def register_client(request: Request):
    oauth_urls()
    try:
        raw_body = bytearray()
        async for chunk in request.stream():
            raw_body.extend(chunk)
            if len(raw_body) > 16384:
                return _oauth_error("invalid_client_metadata", "The client registration document is too large.")

        def unique_members(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON member")
                result[key] = value
            return result

        body = json.loads(raw_body, object_pairs_hook=unique_members)
    except Exception:
        return _oauth_error("invalid_client_metadata", "A JSON client registration document is required.")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "Invalid client registration document.")
    redirects = body.get("redirect_uris")
    name = body.get("client_name", "MCP client")
    if (not isinstance(redirects, list) or not redirects or len(redirects) > 20
            or any(not validate_redirect_uri(uri) for uri in redirects)
            or len(set(redirects)) != len(redirects)
            or not isinstance(name, str) or not name.strip() or len(name) > 120
            or body.get("token_endpoint_auth_method", "none") != "none"
            or body.get("grant_types", ["authorization_code"]) != ["authorization_code"]
            or body.get("response_types", ["code"]) != ["code"]):
        return _oauth_error("invalid_client_metadata", "Only public authorization-code clients with validated HTTPS or loopback redirects are supported.")
    client_id = secrets.token_urlsafe(32)
    try:
        database.register_oauth_client(client_id, name.strip(), redirects)
    except database.StorageUnavailable:
        return _oauth_error("temporarily_unavailable", "OAuth storage is unavailable.", 503)
    return JSONResponse({"client_id": client_id, "client_id_issued_at": int(time.time()),
                         "client_name": name.strip(), "redirect_uris": redirects,
                         "grant_types": ["authorization_code"], "response_types": ["code"],
                         "token_endpoint_auth_method": "none", "scope": OAUTH_SCOPE}, status_code=201,
                        headers={"Cache-Control": "no-store"})


@router.get("/oauth/authorize", response_class=HTMLResponse)
async def authorize_get(request: Request, response_type: str, client_id: str, redirect_uri: str, code_challenge: str,
                        code_challenge_method: str, resource: str, state: str, scope: str = OAUTH_SCOPE):
    issuer, expected_resource = oauth_urls()
    required = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method", "resource", "state")
    if (any(len(request.query_params.getlist(key)) != 1 for key in required)
            or len(request.query_params.getlist("scope")) > 1):
        return _html_page("Invalid authorization request", "<p class=error>The authorization request is invalid. Return to your MCP client and retry.</p>", status_code=400)
    if (response_type != "code" or not state or len(state) > 512 or not _CLIENT_ID_RE.fullmatch(client_id)
            or code_challenge_method != "S256" or not _CHALLENGE_RE.fullmatch(code_challenge)):
        return _html_page("Invalid authorization request", "<p class=error>The authorization request is invalid. Return to your MCP client and retry.</p>", status_code=400)
    if resource != expected_resource or scope not in ("", OAUTH_SCOPE):
        return _html_page("Invalid resource", "<p class=error>The requested MCP resource does not match this gateway.</p>", status_code=400)
    try:
        client = database.get_oauth_client(client_id)
    except database.StorageUnavailable:
        return _html_page("Temporarily unavailable", "<p>Authorization storage is unavailable. Retry later.</p>", status_code=503)
    if not client or redirect_uri not in client["redirect_uris"]:
        return _html_page("Invalid client", "<p class=error>The client or registered redirect URI is invalid.</p>", status_code=400)
    request_id, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    fields = {"client_id": client_id, "redirect_uri": redirect_uri, "state": state,
              "code_challenge": code_challenge, "scope": OAUTH_SCOPE, "resource": resource}
    try:
        database.create_oauth_request(request_id, fields, csrf)
    except database.StorageUnavailable:
        return _html_page("Temporarily unavailable", "<p>Authorization storage is unavailable. Retry later.</p>", status_code=503)
    return _login_form(request_id, csrf, client_name=client["client_name"])


@router.post("/oauth/authorize", response_class=HTMLResponse)
async def authorize_post(request: Request):
    oauth_urls()
    form = await _read_form(request)
    if form is None:
        return _html_page("Invalid request", "<p class=error>The authorization form is invalid.</p>", status_code=400)
    request_id = form.get("request_id", [""])[0] if len(form.get("request_id", [])) == 1 else ""
    csrf_token = form.get("csrf_token", [""])[0] if len(form.get("csrf_token", [])) == 1 else ""
    consent = form.get("consent", [""])[0] if len(form.get("consent", [])) == 1 else ""
    try:
        fields = database.get_oauth_request(request_id, csrf_token)
        if not fields:
            return _html_page("Request expired", "<p class=error>This authorization request expired or was already used. Return to the client and retry.</p>", status_code=400)
        if consent != "allow":
            if not database.consume_oauth_request(request_id, csrf_token):
                return _html_page("Request expired", "<p class=error>This authorization request expired or was already used. Return to the client and retry.</p>", status_code=400)
            database.record_audit(event_type="oauth_authorization", outcome="denied", error_class="user_denied")
            return _redirect(fields["redirect_uri"], {"error": "access_denied", "state": fields["state_value"], "iss": oauth_urls()[0]})
        username = form.get("username", [""])[0] if len(form.get("username", [])) == 1 else ""
        password = form.get("password", [""])[0] if len(form.get("password", [])) == 1 else ""
        if not username or not password:
            return _login_form(request_id, csrf_token, error="Sign-in failed. Check your credentials and try again.")
        if len(password) > 1024 or len(username) > 120:
            return _login_form(request_id, csrf_token, error="Sign-in failed. Check your credentials and try again.")
        client_fingerprint = request.client.host if request.client else "unknown"
        delay = database.oauth_login_delay_seconds(username, client_fingerprint)
        if delay > 0:
            await asyncio.sleep(delay)
        identity = database.authenticate_oauth_user(username, password)
        if not identity:
            database.record_oauth_login_failure(username, client_fingerprint)
            record_auth_failure()
            return _login_form(request_id, csrf_token, error="Sign-in failed. Check your credentials and try again.")
        if not database.consume_oauth_request(request_id, csrf_token):
            return _html_page("Request expired", "<p class=error>This authorization request expired or was already used. Return to the client and retry.</p>", status_code=400)
        database.clear_oauth_login_failures(username, client_fingerprint)
        code = secrets.token_urlsafe(32)
        database.create_oauth_code(code, {"client_id": fields["client_id"], "redirect_uri": fields["redirect_uri"],
                                 "code_challenge": fields["code_challenge"], "scope": fields["scope_value"],
                                 "resource": fields["resource"]}, identity["identity_id"])
        database.record_audit(event_type="oauth_authorization", outcome="success", identity=identity)
        return _redirect(fields["redirect_uri"], {"code": code, "state": fields["state_value"], "iss": oauth_urls()[0]})
    except database.StorageUnavailable:
        return _html_page("Temporarily unavailable", "<p>Authorization storage is unavailable. Retry later.</p>", status_code=503)


def record_auth_failure() -> None:
    try:
        database.record_audit(event_type="oauth_login", outcome="denied", error_class="invalid_credentials")
    except database.StorageUnavailable:
        pass


@router.post("/oauth/token")
async def token_endpoint(request: Request):
    oauth_urls()
    form = await _read_form(request)
    if form is None:
        return _oauth_error("invalid_request", "A form-encoded token request is required.")
    fields = ("grant_type", "client_id", "code", "redirect_uri", "code_verifier", "resource")
    if any(len(form.get(key, [])) != 1 for key in fields):
        return _oauth_error("invalid_request", "The token request contains missing or duplicate fields.")
    if len(form.get("scope", [])) > 1:
        return _oauth_error("invalid_request", "The token request contains duplicate scope fields.")
    if form.get("scope", [""])[0] not in ("", OAUTH_SCOPE):
        return _oauth_error("invalid_scope", "The requested scope was not granted.")
    grant_type, client_id, code, redirect_uri, code_verifier, resource = (form[key][0] for key in fields)
    if not all((grant_type, client_id, code, redirect_uri, code_verifier, resource)):
        return _oauth_error("invalid_request", "Required token request fields are missing.")
    if grant_type != "authorization_code":
        return _oauth_error("unsupported_grant_type", "Only authorization_code is supported.")
    _, expected_resource = oauth_urls()
    if resource != expected_resource or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
        return _oauth_error("invalid_grant", "The authorization grant is invalid or expired.")
    access_token = secrets.token_urlsafe(32)
    try:
        token = database.exchange_oauth_code(code, code_verifier, client_id, redirect_uri, resource, access_token)
    except database.StorageUnavailable:
        return _oauth_error("temporarily_unavailable", "OAuth storage is unavailable.", 503)
    if not token:
        return _oauth_error("invalid_grant", "The authorization grant is invalid or expired.")
    return JSONResponse({"access_token": access_token, "token_type": "Bearer", "expires_in": ACCESS_TOKEN_TTL_SECONDS,
                         "scope": token["scope"]}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.post("/oauth/revoke")
async def revoke_token(request: Request):
    oauth_urls()
    form = await _read_form(request)
    if form is None:
        return _oauth_error("invalid_request", "A form-encoded revocation request is required.")
    if any(len(form.get(key, [])) > 1 for key in ("token", "client_id", "token_type_hint")):
        return _oauth_error("invalid_request", "The revocation request contains duplicate fields.")
    token = form.get("token", [""])[0]
    client_id = form.get("client_id", [""])[0]
    token_type_hint = form.get("token_type_hint", ["access_token"])[0]
    if token_type_hint not in ("access_token", "refresh_token"):
        return JSONResponse({}, headers={"Cache-Control": "no-store"})
    if not token or not client_id:
        return _oauth_error("invalid_request", "The revocation request requires a token and client_id.")
    try:
        database.revoke_oauth_access_token(token, client_id)
    except database.StorageUnavailable:
        return _oauth_error("temporarily_unavailable", "OAuth storage is unavailable.", 503)
    return JSONResponse({}, headers={"Cache-Control": "no-store"})


async def authenticate_bearer(token: str) -> dict[str, Any] | None:
    _, resource = oauth_urls()
    identity = database.verify_oauth_access_token(token, resource)
    if identity and OAUTH_SCOPE not in identity.get("oauth_scopes", []):
        database.record_audit(event_type="authentication", outcome="denied", error_class="insufficient_scope",
                              identity=identity)
        raise HTTPException(status_code=403, detail="The access token lacks the required MCP scope.")
    return identity
