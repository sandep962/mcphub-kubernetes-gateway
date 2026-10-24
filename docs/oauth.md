# MCPHub OAuth for external MCP clients

OAuth is opt-in. Existing dashboard and API-key clients continue using the existing `X-API-Key` contract. The gateway accepts OAuth bearer tokens only on `/mcp`; REST administration continues to require an API key.

## Identity enrollment

Passwords are not part of the existing identity records. Enroll an existing identity from a trusted shell after the OAuth schema migration has been approved and applied:

```powershell
python -m apps.shared.enroll_oauth_identity --identity-id <existing-identity-id>
```

The command prompts twice without echoing the password. Passwords have a 12-character minimum and are stored using salted scrypt hashes. Never put a password in command arguments, environment variables, source, chat, or logs. No default password is created. To replace an enrolled password, repeat the command. A future administrative password-reset UI is not included.

## HTTPS issuer configuration

OAuth metadata and authorization are unavailable until both settings name the public HTTPS URLs that MCP clients actually use:

- `MCPHUB_OAUTH_ISSUER`: the authorization-server origin, such as `https://mcp.example.com`.
- `MCPHUB_MCP_RESOURCE`: the canonical MCP resource URL, such as `https://mcp.example.com/mcp`.

Set the equivalent `oauth.issuerUrl` and `oauth.resourceUrl` Helm values only after DNS, TLS, and Ingress routing exist. The issuer must currently be an HTTPS origin. Resource and issuer values are public metadata, not secrets. Local Kind's plain HTTP localhost ports are intentionally not accepted as OAuth identifiers. For local interactive testing, use a trusted local TLS certificate and HTTPS host mapping, or test against an HTTPS development ingress. Do not use an untrusted production certificate or disable client TLS verification.

No signing key is used: authorization codes and access tokens are high-entropy opaque values, stored only as SHA-256 hashes in the shared MariaDB database. This makes token revocation immediate across gateway replicas and avoids per-process signing state.

## Protocol flow

The gateway publishes RFC 9728 protected-resource metadata at `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`, and RFC 8414 authorization-server metadata at `/.well-known/oauth-authorization-server`. Public MCP clients use the registration endpoint `/oauth/register` with authorization-code-only, public-client metadata, then start `/oauth/authorize` with the exact registered redirect URI, `resource`, `state`, and PKCE S256 challenge. The browser login explicitly requires consent. The token endpoint `/oauth/token` accepts one-time codes, the exact redirect URI and resource, and the matching PKCE verifier. Tokens expire after one hour; `/oauth/revoke` revokes them. Refresh tokens are not issued.

Redirect URIs must be HTTPS, or HTTP on localhost/loopback for native clients. Authorization codes expire after 90 seconds, are one-use, and are PKCE-bound. The server checks the token issuer/resource audience, expiry, identity active state, revocation, and `mcp:tools` scope on each MCP request. Existing role and per-tool permission checks still run for every discovery and tool call. OAuth activity is audit-attributed to the database identity; no password, authorization code, bearer token, or raw redirect parameters are written to audit records.

The public-client Dynamic Client Registration endpoint is provided for MCP interoperability. Production deployments should apply edge rate limits to `/oauth/register`, `/oauth/authorize`, and `/oauth/token`. Repeated credential failures add a bounded progressive delay stored in shared database state; this does not replace edge rate limits, especially when clients share an ingress source address. Do not treat this source-only implementation as production-ready until the test suite passes, the schema change is backed up and reviewed, and the public HTTPS metadata is reachable and validated by a real compatible MCP client.

## Test with a compatible MCP client

1. Obtain a real DNS name and valid TLS certificate, route that HTTPS host to the gateway, and set `oauth.issuerUrl` and `oauth.resourceUrl` to the exact externally reachable issuer and `/mcp` URLs. Do not use the example domain above.
2. Before deploying this source, take and verify a MariaDB backup, review schema versions 4 and 5, and obtain approval for the migration. Run the explicit migration Job once before starting application replicas with auto-migration disabled. The Azure demo previously completed versions 1–5; inspect the live database before assuming that state on another cluster. After the approved migration, enroll an existing active identity using the interactive command above.
3. From a browser/client network, request the protected-resource metadata URL and authorization-server metadata URL over HTTPS. Confirm they return JSON with the configured issuer, resource, authorization/token/registration endpoints, and PKCE method `S256`.
4. Configure an OAuth-enabled MCP client with only the gateway MCP URL (`https://<your-host>/mcp`); do not supply an API key. The client must support protected-resource discovery, public-client DCR fallback, Authorization Code with PKCE S256, `state`, and the OAuth `resource` parameter.
5. Connect. The client should register as a public client, open a browser to MCPHub, show the requesting client name, and prompt for username/password and consent. Sign in using the enrolled identity and choose “Sign in and authorize.”
6. Confirm `tools/list` contains only tools allowed for that identity, then call one allowed tool. Confirm the gateway audit table attributes the call to the enrolled identity. Test a disallowed tool and confirm it does not execute.
7. Revoke the OAuth grant/token through the client or its RFC 7009 revocation support. The next MCP HTTP request with that token should receive HTTP 401 and a bearer challenge. Existing API-key clients should continue to work independently.

## Database change and recovery plan

Schema versions 1–3 establish the core tables, audit duration, default upstreams, and demo seed behavior. Version 4 adds `oauth_credentials`, `oauth_clients`, `oauth_authorization_requests`, `oauth_authorization_codes`, `oauth_access_tokens`, `oauth_consents`, and `oauth_login_attempts`. For databases created with the earlier consent schema, version 5 adds and backfills SHA-256 `resource_hash` values and replaces the oversized resource-string primary key with `(identity_id, client_id, resource_hash)` while preserving rows. Fresh databases use the compact hash key in the table definition directly. MariaDB DDL can auto-commit; a failure can leave partial schema changes and needs inspection before retry. Existing identities, API keys, permissions, audit history, store data, and upstream registrations are outside these OAuth table changes. OAuth tables store password hashes, OAuth value hashes, and required state metadata.

The `connection()` repository helper runs `ensure_schema()` by default (`MCPHUB_AUTO_MIGRATE=true` when unset). Gateway, Inventory, and Support Helm values can set `database.autoMigrate: false`; the demo and VPS values do so and use the separate `python -m apps.shared.migrate_database` Job for an explicit run. That command bypasses the environment switch by design. Do not assume replicas coordinate schema changes just because they share a database: take and verify a backup, review the exact numbered migration and MariaDB compatibility, run one controlled migration Job, verify versions and data, then start/roll application replicas. For a code rollback, roll back application code/configuration only and leave additive OAuth tables and data in place; never drop tables as part of rollback. The Azure demo previously completed schema versions 1–5; this does not establish the state of another cluster, so inspect it before proceeding.

## Scope and limitations

This implementation has no refresh-token grant, no password UI for the existing dashboard, no authenticated DCR client management UI, and no automatic identity enrollment. It does not fetch Client ID Metadata Documents; metadata advertises that limitation and offers DCR as the backwards-compatible fallback. Current MCP guidance prefers Client ID Metadata Documents for new deployments, so this source should not be described as full current-spec compliance until CIMD support or an approved pre-registration strategy is added and tested. Existing API-key administration remains unchanged. OAuth tokens authorize the MCP resource only, never REST admin routes. The Azure demo has a configured HTTPS hostname and TLS route; a fresh deployment must provide and verify its own issuer/resource URLs, domain, and TLS routing before OAuth is enabled.
