# MCPHub System Design

## Purpose

MCPHub provides one authenticated Streamable HTTP MCP endpoint for clients while keeping upstream MCP servers and their credentials inside Kubernetes. The dashboard uses the same gateway authorization and audit path as external clients. A Gemini-backed assistant selects from the tools authorized for the signed-in identity and sends every tool call back through the gateway.

## Components and request paths

```text
Browser dashboard ── /api reverse proxy ──┐
External MCP client ── /mcp ──────────────┼── Gateway ── Inventory MCP ── metadata DB
Gemini assistant tool loop ───────────────┘           ├─ Support MCP ──── metadata DB
                                                      └─ WooCommerce MCP ─ WordPress/WooCommerce
```

- **Gateway:** FastAPI health/readiness, API-key authentication, role and identity/team permission checks, tool discovery and routing, chat tool loop, upstream status, admin APIs, and audit logging.
- **Metadata database:** Stores identities, teams, API-key SHA-256 hashes and prefixes, explicit tool grants, upstream registrations, and audit events. It is separate from WordPress's database.
- **Upstreams:** Inventory and Support persist their domain data in metadata storage. WooCommerce MCP connects to WordPress/WooCommerce using server-side Secret-sourced credentials.
- **Dashboard:** React UI for health, upstreams/tools, audit, users/keys/permissions, and chat. The browser holds the signed-in user's API key in tab memory; upstream and LLM credentials stay server-side.
- **Store:** WordPress and WooCommerce use a separate MariaDB database and PVCs. A COD order is a real persistent order even though it does not charge a card.
- **Deployment:** Existing Helm charts run on Kind and Azure k3s with environment-specific values. Internal application Services are ClusterIP; Traefik routes selected public application paths over HTTPS in the Azure demo.

## Security and failure boundaries

The gateway validates the API key on every request, obtains identity and role from the database, checks explicit grants for each tool, and applies role restrictions independently of team membership. Unknown/unauthorized tools are denied. The gateway records identity ID, upstream, tool, outcome, timestamp, duration, and sanitized error category without recording arguments or results.

Upstream endpoints and credentials are held in cluster-side configuration/Secrets and are not returned to clients. Tool names are namespaced by upstream in client discovery. Each upstream is discovered/checked independently and tool calls have bounded deadlines, so a slow or unavailable upstream should not block healthy upstreams.

The Azure demo uses `sandeep-mcp-demo.duckdns.org`, a cert-manager-managed TLS certificate, and HTTP-to-HTTPS redirects. HTTPS endpoint health and certificate readiness were checked on 2026-10-24; this is a point-in-time check, not a production availability guarantee. Cluster administrators remain trusted with access to Kubernetes Secrets, pod environments, and the underlying host. MariaDB and WordPress PVCs are persistent but single-node local-path volumes are not backups or highly available storage.

## Verified deployment snapshot

As checked on 2026-10-24, Azure k3s namespace `mcphub` had two Ready replicas each for the gateway, dashboard, Inventory MCP, Support MCP, and WooCommerce MCP; WordPress and MariaDB each had one Ready pod. The MariaDB and WordPress PVCs were Bound. Dashboard, gateway, and storefront Ingress objects used Traefik and the `mcphub-public-tls` Secret; the certificate and public HTTPS health checks succeeded. This is a point-in-time demo snapshot, not a production SLA. See [the threat model](threat-model.md) and [the deployment runbook](helm-upgrades-rollback.md).
