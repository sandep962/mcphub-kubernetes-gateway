::: {align="center"}
# MCPHub

### One authenticated Streamable HTTP endpoint for multiple MCP services

WooCommerce · Inventory · Support

![Python](https://img.shields.io/badge/Python-FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/Dashboard-React-61DAFB?logo=react&logoColor=black)
![Kubernetes](https://img.shields.io/badge/Deploy-Kubernetes%20%7C%20Helm-326CE5?logo=kubernetes&logoColor=white)
![MCP](https://img.shields.io/badge/Protocol-Streamable%20HTTP-6E56CF)

[Azure demo](#azure-demo) · [Architecture](#architecture) · [Local
setup](#local-development) · [Order
workflow](#placing-a-woocommerce-order) · [MCP
clients](#connecting-an-external-mcp-client) ·
[Security](#security-and-threat-model) ·
[Validation](#validation-and-tests)
:::

MCPHub is a Kubernetes-hosted Model Context Protocol (MCP) gateway.
Clients connect to one authenticated gateway instead of connecting
directly to individual upstream MCP servers. The gateway authenticates
each request, filters tool discovery by explicit permissions, checks
authorization again when a tool is invoked, routes the call to the
correct upstream, and records sanitized audit metadata.

The project includes a React dashboard and chat assistant, a FastAPI
gateway, WooCommerce/Inventory/Support MCP servers, Helm charts, and
local Kind plus Azure K3s deployment configuration.

## Contents

-   [Azure demo](#azure-demo)
-   [Features](#features)
-   [Architecture](#architecture)
-   [Local development](#local-development)
-   [Production-like K3s deployment](#production-like-k3s-deployment)
-   [Using the dashboard and chat
    assistant](#using-the-dashboard-and-chat-assistant)
-   [Placing a WooCommerce order](#placing-a-woocommerce-order)
-   [Connecting an external MCP
    client](#connecting-an-external-mcp-client)
-   [System design and tradeoffs](#system-design-and-tradeoffs)
-   [Security and threat model](#security-and-threat-model)
-   [Validation and tests](#validation-and-tests)
-   [Known limitations and verification
    status](#known-limitations-and-verification-status)
-   [Detailed documentation](#detailed-documentation)

## Azure demo

The assessment demo was deployed on an Azure Ubuntu VM using K3s, Helm,
and Traefik. The public hostname is `sandeep-mcp-demo.duckdns.org`.

  --------------------------------------------------------------------------------------------------
  Service                 URL                                                Purpose
  ----------------------- -------------------------------------------------- -----------------------
  Dashboard               <https://sandeep-mcp-demo.duckdns.org/dashboard>   Dashboard,
                                                                             administration, tools,
                                                                             audit, and chat

  Storefront              <https://sandeep-mcp-demo.duckdns.org/shop/>       WordPress/WooCommerce
                                                                             storefront

  MCP endpoint            <https://sandeep-mcp-demo.duckdns.org/mcp>         Streamable HTTP
                                                                             endpoint for MCP
                                                                             clients

  WordPress admin         <https://sandeep-mcp-demo.duckdns.org/wp-admin/>   Store administration
  --------------------------------------------------------------------------------------------------

Traefik routes public paths to internal Services in namespace `mcphub`,
using IngressClass `traefik` and TLS Secret `mcphub-public-tls`. HTTP
redirects to HTTPS. Do not send API keys over HTTP.

**Recorded deployment status:** repository documentation records an
Azure verification on 2026-10-24. At that check, the gateway, dashboard,
Inventory MCP, Support MCP, and WooCommerce MCP each had two Ready
replicas; WordPress and MariaDB each had one Ready pod; both store PVCs
were Bound; HTTPS and the certificate were valid. This is a dated
snapshot, not a live availability guarantee. Check the cluster before a
demo.

The store Helm release is named `mcphub-store` in namespace `default`,
while its Kubernetes workloads are rendered into `mcphub`. Other
application releases are in `mcphub`. The demo uses persistent MariaDB
and WordPress volumes and synthetic assessment data. It is a single-node
demonstration environment, not a production high-availability or backup
guarantee.

## Features

-   One authenticated Streamable HTTP MCP endpoint for multiple upstream
    servers.
-   Upstream-qualified tool names such as `inventory__list_inventory`,
    preventing collisions between servers.
-   API-key authentication through `X-API-Key`; optional OAuth support
    is present in source but is not a verified public OAuth deployment.
-   Default-deny tool authorization with explicit identity/team grants
    and checks during discovery and invocation.
-   Admin dashboard for identities, API keys, teams, permissions,
    upstream registry/status, audit events, and aggregate tool-call
    metrics.
-   Gemini-backed chat assistant with multi-round tool calling; model
    calls and tool execution go through the gateway.
-   Per-upstream discovery/status isolation, bounded tool-call
    deadlines, sanitized errors, and audit outcomes.
-   WooCommerce, Inventory, and Support MCP servers.
-   Kubernetes Deployments/Services, persistent store volumes,
    NetworkPolicies, readiness/liveness probes, resource settings, and
    Helm releases.
-   Local Kind configuration and separate VPS/K3s production-like
    values.

  ------------------------------------------------------------------------
  Upstream                Read tools              Write tools
  ----------------------- ----------------------- ------------------------
  Inventory               `list_inventory`        `update_stock`

  Support                 `list_tickets`,         `create_ticket`,
                          `get_ticket`            `update_ticket_status`

  WooCommerce             `list_products`,        `create_product`,
                          `list_orders`,          `update_order_status`
                          `get_order`             
  ------------------------------------------------------------------------

Tool availability depends on upstream status and the signed-in
identity's grants. A role alone does not automatically grant every tool.
Viewers are additionally restricted to the built-in read-only tool set.

## Architecture

``` mermaid
flowchart LR
    Client[External MCP client] -->|Streamable HTTP /mcp| Gateway
    Browser[Dashboard and chat] -->|same-origin /api| Gateway
    Gateway -->|identity, grants, registry, audit| Meta[(MariaDB metadata DB)]
    Gateway --> Inventory[Inventory MCP]
    Gateway --> Support[Support MCP]
    Gateway --> WooMCP[WooCommerce MCP]
    Inventory --> Meta
    Support --> Meta
    WooMCP -->|WooCommerce REST API| WordPress[WordPress / WooCommerce]
    WordPress --> StoreDB[(MariaDB store DB)]
    Gateway <-->|authorized tool loop| LLM[Configured Gemini-compatible API]
```

### Main components

-   **Gateway:** FastAPI service for authentication, identity
    resolution, role/grant checks, tool discovery and routing, chat tool
    loop, upstream status, admin APIs, health/readiness, and audit
    logging.
-   **Dashboard:** React interface for health, upstreams, tool
    exploration, user/key/permission administration, audit events,
    aggregate metrics, and chat.
-   **Inventory MCP and Support MCP:** Internal MCP servers that persist
    domain data in metadata storage.
-   **WooCommerce MCP:** Internal MCP server that connects to
    WordPress/WooCommerce using server-side credentials.
-   **Metadata database:** Separate `mcphub` database for identities,
    teams, API-key hashes/prefixes, explicit grants, upstream
    registrations, audit events, and Inventory/Support data.
-   **Store:** WordPress/WooCommerce uses a separate `wordpress`
    database and persistent storage.
-   **Chat assistant:** Uses an OpenAI Chat Completions-compatible
    provider configured for Gemini. The gateway supplies only tools
    authorized for the current identity and re-authorizes each proposed
    tool call.
-   **Deployment:** Helm charts deploy services to Kind or K3s. Internal
    application Services use `ClusterIP`; Traefik routes only selected
    public paths.

### Request flow

1.  A dashboard or external MCP client sends a request to the gateway.
2.  The gateway authenticates the API key or, when configured, an MCP
    OAuth bearer token.
3.  The gateway resolves identity and role from metadata storage; it
    does not trust client-supplied identity fields.
4.  Tool discovery returns only tools permitted for that identity.
5.  On invocation, the gateway checks authorization again and routes the
    call to the selected upstream.
6.  The gateway records identity, server/tool, outcome, duration,
    timestamp, and sanitized error category. Raw tool arguments and
    results are not written to audit records.
7.  For chat, model-proposed tool calls are treated as untrusted
    proposals, checked against the authorized tool catalog, and executed
    through the same gateway path.

## Local development

### Prerequisites

-   Windows PowerShell (commands below use PowerShell)
-   Git
-   Python 3.12
-   Node.js `^20.19.0` or `>=22.12.0`
-   Docker Desktop
-   `kubectl`, Helm, and Kind
-   A local Kind cluster and MCPHub workloads installed before using the
    port-forward helper

> The port-forward script does **not** create a cluster or install
> charts. It connects to an already-running Kind deployment.

### Start local port forwards

From the repository root:

``` powershell
cd C:\MCPHub
.\scripts\start-local-ports.ps1
```

  Component                    Local URL
  ---------------------------- -----------------------------
  Dashboard                    <http://127.0.0.1:5174>
  Storefront through Traefik   <http://127.0.0.1:8081>
  Gateway REST API             <http://127.0.0.1:8010>
  Gateway MCP endpoint         <http://127.0.0.1:8010/mcp>

The helper expects a current Kubernetes context named `kind-kind` and
the dashboard, gateway, WordPress, Traefik, MariaDB, and enabled
upstreams already deployed.

Stop only the port-forward processes created by the helper:

``` powershell
.\scripts\stop-local-ports.ps1
```

For local Ingress setup, install/update Traefik and route resources
using [docs/local-ingress.md](docs/local-ingress.md). The local
storefront uses HTTP and local host mappings; no TLS certificate is
configured for Kind URLs.

### Configure the chat assistant locally

Set these process environment variables on the gateway before starting
it:

-   `MCPHUB_LLM_BASE_URL`: OpenAI-compatible API base URL, including its
    API prefix if required; the gateway appends `/chat/completions`.
-   `MCPHUB_LLM_MODEL`: provider model identifier supporting
    tool/function calling.
-   `MCPHUB_LLM_API_KEY`: provider API key.

Keep the key server-side. Do not put it in dashboard JavaScript, browser
storage, a Vite `VITE_` variable, Git, or a recording. If any value is
absent, `/assistant` returns HTTP 503 with a setup message.

### WooCommerce MCP local configuration

The WooCommerce MCP server needs `WOOCOMMERCE_BASE_URL`,
`WOOCOMMERCE_CANONICAL_URL`, `WOOCOMMERCE_CONSUMER_KEY`, and
`WOOCOMMERCE_CONSUMER_SECRET`. Follow
[docs/woocommerce-mcp.md](docs/woocommerce-mcp.md), which documents
masked credential prompts, safe startup, health verification, and
read-only MCP checks. Do not put credentials in command arguments, URLs,
logs, or tracked files.

### Local Ingress

The local Ingress guide installs Traefik and `mcphub-ingress` and
explains the `storefront.mcphub.test` and `dashboard.mcphub.test` host
mappings. See [docs/local-ingress.md](docs/local-ingress.md).

## Production-like K3s deployment

The repository includes a guide and separate values files for deploying
to a **new Linux VPS/K3s cluster**. This guide is not a command sequence
for modifying the existing Azure demo. The fresh-target deployment
sequence has not been run end-to-end as written.

Read [docs/vps-k3s-deployment.md](docs/vps-k3s-deployment.md) before
deployment. It covers prerequisites, image delivery, storage, secrets,
migrations, TLS, installation order, verification, backups, and
recovery.

### Production-specific configuration

-   Replace all `REPLACE_WITH_...` placeholders in
    `infra/helm/values/vps-k3s/`.
-   Build and push reviewed images to an approved registry with unique
    immutable tags or digests; local Kind image tags are not available
    on a new VPS.
-   Configure real DNS hostnames, ingress class/controller, TLS
    certificate, firewall, and public IP.
-   Create required Kubernetes Secrets through a protected workflow
    before installing workloads.
-   Configure the store storage class and capacity. K3s `local-path`
    volumes are tied to the node and are not backups or
    high-availability storage.
-   Enable/verify K3s Secret encryption, restrict kubeconfig and Secret
    access, and verify NetworkPolicy enforcement by the selected CNI.
-   Use the explicit, reviewed metadata migration Job when a schema
    change is approved. Back up and verify the database before
    migrations; MariaDB DDL may auto-commit.
-   Configure provider egress narrowly if the chat assistant is enabled.
    Do not replace restricted egress with unrestricted access just to
    make the provider reachable.

Production values files:

-   `infra/helm/values/vps-k3s/mcphub-store.yaml`
-   `infra/helm/values/vps-k3s/mcphub-metadata.yaml`
-   `infra/helm/values/vps-k3s/mcphub-woocommerce-mcp.yaml`
-   `infra/helm/values/vps-k3s/mcphub-inventory-mcp.yaml`
-   `infra/helm/values/vps-k3s/mcphub-support-mcp.yaml`
-   `infra/helm/values/vps-k3s/mcphub-gateway.yaml`
-   `infra/helm/values/vps-k3s/mcphub-dashboard.yaml`
-   `infra/helm/values/vps-k3s/mcphub-ingress.yaml`

Do not apply production store values to the existing Kind/Azure store
without a separate reviewed Secret-ownership handoff and backup plan.
See [docs/helm-upgrades-rollback.md](docs/helm-upgrades-rollback.md)
before changing an existing release.

## Using the dashboard and chat assistant

1.  Start the deployment and open the dashboard URL for that
    environment.
2.  Sign in with an API key for the intended identity. The dashboard
    holds the entered key in tab memory; sign out or close the tab to
    clear it.
3.  Use the admin panel (admin identity only) to create teams and
    identities, issue API keys, grant explicit tool permissions, revoke
    keys/grants, and review audit events.
4.  Open the tools/upstream view to inspect available tools and upstream
    statuses.
5.  Open chat and ask a question that matches the tools granted to the
    signed-in identity. The assistant receives only that identity's
    authorized tool catalog.
6.  Review the tool calls and success/failure indicators shown by the
    chat UI.

The assistant supports up to eight rounds of tool calls in one request.
The gateway rechecks authorization on tool execution. The configured
model provider receives the conversation, authorized tool schemas, and
tool results; do not include secrets or sensitive customer data in
prompts.

## Placing a WooCommerce order

1.  Open the storefront at the environment's storefront URL and browse
    synthetic assessment products.
2.  Add the desired product to the cart and proceed through WooCommerce
    checkout.
3.  Enter test customer details and choose a configured payment method.
    Use synthetic assessment data only.
4.  Submit the order and confirm WooCommerce displays an order
    confirmation.
5.  If you have dashboard/admin access, inspect the order in WooCommerce
    admin or use authorized read tools such as `list_orders` and
    `get_order`.
6.  Confirm the order remains present after refreshing the page or
    recreating the WordPress pod, where that persistence test is
    appropriate.

A cash-on-delivery (COD) order is a real persistent WooCommerce order
record even though it does not charge a card. Do not use real customer
information or a live payment method during an assessment demo. Exact
checkout fields and payment methods depend on the current
WordPress/WooCommerce configuration.

The WooCommerce MCP tool list includes `list_products`, `list_orders`,
`get_order`, `create_product`, and `update_order_status`. Inventory and
Support have their own read/write tools, subject to explicit grants.

## Connecting an external MCP client

The public MCP endpoint is:

``` text
https://sandeep-mcp-demo.duckdns.org/mcp
```

For a different deployment, replace this with that environment's HTTPS
`/mcp` URL.

### API-key authentication

The verified assessment authentication path is the `X-API-Key` header.
Configure a compatible MCP client to use **Streamable HTTP** and the
gateway URL above, and add the API key as a custom HTTP header:

``` text
X-API-Key: <YOUR_IDENTITY_API_KEY>
```

Use a key issued for the intended identity. Do not paste a real key into
source control, screenshots, chat, or logs. Do not connect an external
client directly to Inventory, Support, or WooCommerce MCP Services; they
are intended to remain internal.

A terminal MCP `initialize` request to the HTTPS endpoint using
`X-API-Key` was tested. The available PowerShell history did not retain
the successful POST command and response, so this README does not claim
a specific HTTP status from that test. Earlier SSH-tunnel MCP-client
checks covered `tools/list` and read calls. Claude Code and other
client-specific external integrations have not been verified here.

### OAuth (optional source implementation)

OAuth code and migrations exist, but a public OAuth deployment should
not be considered complete without a live protocol/security review and
successful compatible-client test. OAuth is opt-in and requires valid
HTTPS issuer and resource URLs. API-key clients continue using
`X-API-Key`; OAuth bearer tokens apply to `/mcp`, not REST
administration. See [docs/oauth.md](docs/oauth.md) for protocol details,
configuration, migration safety, and limitations.

## System design and tradeoffs

### Architecture choices

-   **Central gateway:** provides one MCP endpoint and a shared place
    for authentication, permission checks, routing, and auditing. It
    also becomes a shared dependency.
-   **Upstream-qualified tool names:** names such as
    `inventory__list_inventory` avoid collisions. REST `/call` rejects
    ambiguous native tool names with HTTP 409 instead of routing
    arbitrarily.
-   **Separate metadata and store databases:** separates
    identity/permission/audit data from WordPress/WooCommerce data, but
    both still depend on the database infrastructure.
-   **Kubernetes Services and Helm:** keep upstreams internal and make
    deployments repeatable across local Kind and K3s. A single-node
    `local-path` cluster does not provide high availability.
-   **Explicit grants and default deny:** tool visibility is filtered
    during discovery and authorization is checked again during
    invocation. A role by itself does not grant every tool.
-   **Bounded upstream calls:** discovery/status and tool calls are
    isolated per upstream where possible; timeouts and sanitized errors
    limit impact from slow or failing services. The gateway, provider,
    and metadata database remain shared dependencies.

### Failure handling

-   Upstream discovery is bounded and performed independently; working
    upstreams can remain available when another upstream fails.
-   Tool calls use a configurable deadline
    (`MCPHUB_UPSTREAM_TOOL_CALL_TIMEOUT_SECONDS`, default 30 seconds).
-   The gateway uses sanitized error categories and audit outcomes
    rather than logging raw tool arguments/results.
-   REST `/call` returns controlled errors for unknown, ambiguous,
    unauthorized, timed-out, or failed calls.
-   MariaDB schema changes need special care because DDL may
    auto-commit. Inspect database state and backups after a failed
    migration; do not blindly retry or assume Helm rollback restores
    data.

### What changes for production

-   **DNS and ingress:** use real hostnames, a verified ingress
    controller/class, public routing only for intended endpoints, and
    valid HTTPS certificates.
-   **Storage:** choose and size a storage class intentionally.
    Local-path volumes survive pod recreation but are not off-node
    backups or node-loss recovery.
-   **Secrets:** create database, WooCommerce, LLM, registry-pull, and
    TLS Secrets through a protected workflow. Keep secret values out of
    Helm values, Git, command history, and logs.
-   **Images:** use reviewed immutable image tags/digests in an approved
    registry; local Kind images are not available on a new VPS.
-   **Migrations:** back up and verify databases, review schema
    versions, and run the controlled migration Job before deploying
    application versions that require it.
-   **Operations:** restrict Kubernetes/SSH access, enable Secret
    encryption at rest, verify NetworkPolicy enforcement, maintain
    off-node encrypted backups, and test restores before relying on
    disaster recovery.

## Security and threat model

### Shared upstream credentials: what can a member do without seeing them?

WooCommerce credentials and the LLM provider key are injected into
server-side workloads. A normal member does not receive raw upstream
credentials, but can still cause the gateway to perform actions exposed
by tools granted to that identity. Effective capability is therefore
bounded by the member's tool permissions---not by whether they can read
the credential itself. A member granted a write tool may cause that
action to execute through the shared upstream account. Grant write tools
narrowly and review side effects.

### Why client tokens are not passed through upstream

The gateway uses its own server-side upstream credentials and
authorization policy. A client's API key authenticates the client to
MCPHub; it is not an upstream credential and should not be forwarded to
WooCommerce or other upstreams. This keeps upstream credentials hidden
and prevents an upstream from treating the client's gateway key as its
own authentication token. The gateway enforces the caller's permissions
before using its server-side credentials.

### Untrusted upstreams and prompt injection

Tool descriptions, schemas, results, and model-proposed calls are
treated as untrusted data. Only tools in the authenticated identity's
authorized catalog are offered to the model, and proposed calls are
allowlisted and re-authorized by the gateway. Tool names are namespaced
and calls have bounded deadlines. These controls reduce risk but do not
guarantee model output is truthful or that an authorized upstream is
safe. Review upstream code, descriptions, permissions, endpoint changes,
and side effects before enabling it; do not treat tool output as
instructions.

### What one team member can do to another

Team membership does not automatically grant administrator access.
Backend routes derive identity and role from metadata storage, and
non-admin tool access is default-deny with explicit identity/team
grants. A member can use tools permitted to their identity and team,
including write tools explicitly granted, but should not be able to
administer users or access another member's data merely by being on the
same team. Review exact grants and data-access logic before assuming
isolation for every domain-specific record.

### Remaining risks

-   Kubernetes administrators and node/root access remain privileged
    trust boundaries and may expose Secrets or pod environments.
-   A compromised authorized upstream can return malicious data or
    perform the side effects its tools expose.
-   A stolen client key can be replayed until revoked; revoke and
    replace suspected compromised keys.
-   Audit metadata can reveal usage patterns and should be
    access-controlled.
-   Persistent volumes are not backups, and single-node K3s is not
    highly available.
-   The demo does not provide a production SLA, managed secret store, or
    automatic disaster recovery.

## Validation and tests

### Python

``` powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
.\.venv\Scripts\python.exe -m pytest -q
```

### Dashboard

``` powershell
Push-Location .\apps\dashboard
npm ci
npm test
npm run lint
npm run build
Pop-Location
```

### Helm

All eight repository charts were linted and rendered locally according
to the runbook on 2026-10-22. This is local validation, not proof of a
future deployment upgrade.

Use environment-specific values and release namespaces from
[docs/helm-upgrades-rollback.md](docs/helm-upgrades-rollback.md). Before
changing a release, inspect the current context, effective non-secret
values, revision history, rendered diff, PVC state, and backup status.
Never print secret-bearing values or full rendered Secret manifests.

A no-op Helm upgrade/rollback lifecycle check was performed for the
Support MCP release in Kind. It verified the Helm command path for that
stateless release without a pod restart. It does **not** verify an
image-changing upgrade, the store/MariaDB procedure, or the gateway
procedure.

## Known limitations and verification status

-   Azure status in this README is a dated snapshot from 2026-10-24;
    recheck before use.
-   The public MCP endpoint's terminal connectivity was tested, but the
    successful POST command/response was not retained in available shell
    history. No specific HTTP status is claimed here.
-   Claude Code and other client-specific external MCP integrations have
    not been verified.
-   OAuth source code and migrations exist, but a public OAuth
    deployment is not claimed as complete.
-   The fresh-target VPS/K3s deployment guide is a runbook and has not
    been run end-to-end on a second VPS.
-   Automated timeout handling is tested; a live artificial slow
    response was not induced.
-   A Support upstream outage test verified per-upstream isolation; no
    live slow-response simulation was performed.
-   Persistent volumes are not backups, and the single-node Azure demo
    is not an HA environment.
-   Use synthetic assessment data only. Do not expose secrets, real
    customer data, or database dumps in recordings or Git.

## Detailed documentation

-   [System design and tradeoffs](docs/system-design.md)
-   [Threat model](docs/threat-model.md)
-   [VPS/K3s deployment](docs/vps-k3s-deployment.md)
-   [Helm upgrades and rollback](docs/helm-upgrades-rollback.md)
-   [Security and operations](docs/security-operations.md)
-   [Chat assistant](docs/chat-agent.md)
-   [OAuth](docs/oauth.md)
-   [WooCommerce MCP](docs/woocommerce-mcp.md)
-   [Local Ingress](docs/local-ingress.md)
-   [Bonus features and limitations](docs/bonus-features.md)
