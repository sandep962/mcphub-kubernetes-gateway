# MCPHub

MCPHub is a Kubernetes-hosted Model Context Protocol (MCP) gateway for WooCommerce, Inventory, and Support tools. The gateway exposes one authenticated Streamable HTTP endpoint, enforces per-identity tool permissions, and records tool-call audit metadata. Upstream services and their credentials remain inside the cluster.

The Azure k3s demo runs in namespace `mcphub` at [sandeep-mcp-demo.duckdns.org](https://sandeep-mcp-demo.duckdns.org). Dashboard, storefront, and gateway routes use HTTPS through Traefik; cert-manager manages the certificate and HTTP redirects to HTTPS. This is a synthetic-data demo, not a production service. Recheck availability and certificate status before use.

## Architecture

- **Gateway:** FastAPI REST and MCP Streamable HTTP endpoints; aggregates tools from the three upstreams, authenticates API keys, enforces role and explicit tool grants, and writes audit records.
- **Dashboard:** React operations UI and Gemini-backed assistant. Assistant tool calls use the authenticated gateway and the caller's permissions.
- **Upstreams:** WooCommerce MCP calls WordPress/WooCommerce; Inventory and Support use the separate MCPHub metadata database.
- **Persistence:** WordPress and MCPHub metadata use separate MariaDB databases. WordPress files and MariaDB data use PVCs. Database persistence is not a backup or high-availability strategy.
- **Deployment:** Helm charts keep application and database Services internal. Only selected dashboard, storefront, and gateway paths are exposed through Ingress.

See [system design](docs/system-design.md) for routing and failure boundaries, and the [threat model](docs/threat-model.md) for trust assumptions and residual risks.

## Local Kind development

From `C:\MCPHub`, start the localhost-only port forwards:

```powershell
.\scripts\start-local-ports.ps1
```

- Dashboard: `http://127.0.0.1:5174`
- Storefront through Traefik: `http://127.0.0.1:8081`
- Gateway REST and MCP: `http://127.0.0.1:8010` and `http://127.0.0.1:8010/mcp`

Stop only those forwards with `.\scripts\stop-local-ports.ps1`. The dashboard proxies `/api` to the gateway. For host-based Ingress routing, optional hosts-file entries, and exact route checks, see [Local Ingress](docs/local-ingress.md).

Configure MCP Inspector or another external client for Streamable HTTP and the gateway URL. Send that client's own key in `X-API-Key`; do not connect to upstream Services directly. For Azure, use `https://sandeep-mcp-demo.duckdns.org/mcp`. For local WooCommerce MCP setup and read-only checks, see [WooCommerce MCP](docs/woocommerce-mcp.md).

## Chat and storefront

The dashboard assistant uses a server-configured OpenAI-compatible Gemini endpoint and offers only tools authorized for the signed-in identity. The browser does not receive the provider key. See [Chat assistant](docs/chat-agent.md) for configuration and request flow.

Storefront checkout and product creation persist WooCommerce data. Use synthetic details and inspect the catalog/orders before creating test records. The [demo recording checklist](docs/demo-recording-script.md) describes the current two-user demonstration and what not to record.

## Deployment and operations

- [Security and operations](docs/security-operations.md): Secret setup, bootstrap, key lifecycle, local validation, and troubleshooting.
- [Helm upgrade and rollback](docs/helm-upgrades-rollback.md): release-specific upgrade, health-check, and rollback procedures.
- [New VPS/k3s deployment](docs/vps-k3s-deployment.md): fresh-cluster prerequisites, image delivery, storage, TLS, installation, backup, and recovery. It is not a runbook for changing the existing Azure demo.
- [OAuth implementation and limitations](docs/oauth.md): supported flows, schema requirements, and interoperability caveats. API-key auth remains the documented assessment path.
- [Bonus features](docs/bonus-features.md): implementation evidence and limitations, separate from mandatory assessment requirements.

Never put API keys, database credentials, WooCommerce credentials, provider keys, or TLS private keys in source files or Helm values. The repository `.gitignore` excludes local secret files, private values, database exports, and image archives; review the staged diff before publishing.

## Validation

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
Push-Location .\apps\dashboard
npm test
npm run lint
npm run build
Pop-Location
```

Lint and render Helm charts using the environment-specific value files documented in [the Helm runbook](docs/helm-upgrades-rollback.md) and [the VPS guide](docs/vps-k3s-deployment.md). Do not use chart defaults to upgrade an existing release. The demo video is not stored in this repository.
