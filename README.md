# MCPHub

MCPHub is a Kubernetes-hosted MCP gateway with custom WooCommerce, Inventory, and Support upstreams. The current demo is running on Azure k3s in namespace `mcphub`; local Kind configuration is also retained. The Azure demo is reachable at `https://sandeep-mcp-demo.duckdns.org` with a cert-manager-managed TLS certificate and HTTP-to-HTTPS redirect. Use synthetic demo data only; the public demo is not a production service.

## Current implementation

- **WooCommerce MCP server:** custom Python server using MCP Streamable HTTP. It exposes `list_products`, `create_product`, `list_orders`, `get_order`, and `update_order_status`.
- **Gateway:** FastAPI service with public `/health`, database-backed `/ready`, and API-key-protected `/whoami`, `/tools`, `/call`, and `/admin/*` endpoints. It discovers and dispatches WooCommerce, Inventory, and Support MCP tools.
- **Authentication and authorization:** SHA-256 hashes of high-entropy API keys, admin/operator/viewer roles, team and identity tool grants, revocation, and default-deny checks on every request.
- **Audit:** Authentication and tool-call outcomes are stored without request arguments, tool results, or raw credentials.
- **Metadata:** A dedicated `mcphub` MariaDB database and restricted `mcphub_app` user hold auth metadata, audit events, inventory, and support tickets. The WordPress database and account are not used by the MCPHub applications.
- **Dashboard:** React tool explorer and Gemini-backed chat assistant with in-memory API-key login and administrator controls for teams, identities, keys, permissions, and audit history.
- **Inventory and Support:** MCP tools are backed by shared MariaDB tables with safe stock/ticket updates across replicas.
- **Store:** WordPress with WooCommerce and MariaDB, using persistent volume claims and Kubernetes Secrets for database environment variables.
- **Deployment:** Helm charts cover the store, metadata setup, gateway, dashboard, and all three upstream MCP services. App Services are ClusterIP. NetworkPolicies limit MCP ingress to gateway pods and app egress to required services.

The gateway, dashboard, and Inventory/Support servers require the metadata database and `mcphub-metadata-secret`; `/health` stays available to probes while application endpoints require `X-API-Key`. See [docs/security-operations.md](docs/security-operations.md) for private setup, bootstrap, rotation, verification, and demo steps. Azure k3s deployment is live-verified for the current demo; the instructions later in this README remain general deployment guidance and must be checked against current Helm releases before reuse.

## Architecture, threat model, and tradeoffs

Clients connect to the authenticated gateway. The gateway discovers and calls the WooCommerce, Inventory, and Support MCP servers over internal ClusterIP Services; the servers do not expose NodePorts or LoadBalancers. The dashboard calls the gateway through its same-origin `/api` reverse proxy. Metadata authentication, permissions, and audit records use the separate MCPHub database principal; WooCommerce and WordPress use their own database and credentials.

The gateway stores only SHA-256 API-key hashes and a short display prefix. It authenticates every request, applies identity/team grants and role restrictions to each tool call, and records identity, tool, outcome, and duration without logging API keys, tool arguments, or tool results. Upstream credentials and the LLM key are supplied to server containers using Kubernetes Secret references; they are not sent to the browser. NetworkPolicies restrict MCP ingress to the gateway and limit application egress. Policy behavior depends on a policy-capable CNI and was checked in the current Kind cluster.

The local threat boundary assumes the developer controls the Windows host and Kind cluster. A host or Kubernetes administrator can read container environment and Kubernetes Secrets; use Kubernetes RBAC and Secret encryption at rest on a shared cluster. Local access uses plain HTTP through loopback-only port forwards. The Azure demo routes dashboard, gateway, and storefront traffic over HTTPS and redirects HTTP to HTTPS; its TLS certificate is managed by cert-manager. Do not use real customer data or production credentials in the demo. Keep upstream MCP Services internal. MariaDB and WordPress data persist on PVCs, but single-node local-path storage is neither backup nor high availability. See [docs/threat-model.md](docs/threat-model.md) for the trust boundaries and residual risks.

The single gateway centralizes authentication and audit but is also a dependency for every client request. Upstream discovery is performed at request time: an unavailable server loses its tools from discovery while healthy upstreams can remain usable. The local Ingress controller is reached through an explicit `kubectl port-forward` tunnel because this Kind cluster does not publish application ports; the tunnel must be running for the named storefront and dashboard URLs. See [docs/local-ingress.md](docs/local-ingress.md) for setup and verification.

## External MCP client connection

Configure an MCP Inspector or other Streamable HTTP client with:

- Server URL: `http://127.0.0.1:8010/mcp`
- Custom HTTP header: `X-API-Key: <the client's MCPHub API key>`
- Transport: Streamable HTTP

Use a key issued for that client identity and store it in the client's protected credential store. Do not connect clients directly to `mcphub-woocommerce-mcp`, `mcphub-inventory-mcp`, or `mcphub-support-mcp`; the gateway must enforce authorization and audit logging.

## Chat agent and storefront ordering

The dashboard Chat Playground uses the server-configured Gemini provider and offers only tools authorized for the signed-in identity. See [docs/chat-agent.md](docs/chat-agent.md) for provider setup, request flow, and troubleshooting. A tool call that creates a WooCommerce product is a persistent store mutation; check the catalog for duplicates before requesting it.

For the demo storefront, add the selected test product to the cart, enter synthetic test details, select the available shipping method, and choose Cash on Delivery. COD avoids an online charge but still creates a persistent WooCommerce order. The currently verified sample is product #40 and COD order #41; do not submit another checkout for that product merely to repeat a test. See [docs/demo-recording-script.md](docs/demo-recording-script.md) for the current two-user workflow and privacy checklist.

## VPS/K3s deployment boundary

The Azure k3s demo has been configured with HTTPS routing for the dashboard, gateway, and storefront. The current host is `sandeep-mcp-demo.duckdns.org`; certificate readiness and endpoint health were checked on 2026-10-24. Recheck live status before recording because deployments and certificates can change. The broader guide is for a new VPS, not an instruction to alter the existing demo; see [docs/vps-k3s-deployment.md](docs/vps-k3s-deployment.md) and [docs/helm-upgrades-rollback.md](docs/helm-upgrades-rollback.md).

For the Azure demo, use `https://sandeep-mcp-demo.duckdns.org/dashboard` and `https://sandeep-mcp-demo.duckdns.org/mcp`. Never enter an API key over HTTP. An SSH tunnel remains an optional alternative for dashboard access; the following tunnel maps dashboard/gateway service addresses locally:

```powershell
ssh -i D:\DOWNLOAD\urumi-demo-vm_key.pem -N `
  -L 18080:10.43.17.148:8080 `
  -L 18000:10.43.207.184:8000 `
  azureuser@172.198.62.38
```

Use the storefront only with synthetic demo details. Keep upstream MCP Services and MariaDB private; the public Ingress exposes only the selected application routes.

## Assignment demonstration

Follow the end-to-end checklist in [docs/security-operations.md](docs/security-operations.md#demo-recording-checklist): show discovery, allowed reads, a denied member mutation, audit attribution, key revocation, and the test/build results without exposing credentials or customer data. No demo video file is currently present in this repository; record it separately if the submission requires one.

## Repository layout

```text
apps/gateway/                 FastAPI gateway and MCP client
apps/shared/                  MariaDB schema, auth, audit, and repositories
apps/woocommerce-mcp/         WooCommerce MCP server
apps/inventory-mcp/           Persistent Inventory MCP server
apps/support-mcp/             Persistent Support MCP server
apps/dashboard/               React operations dashboard
apps/store/                   WordPress image with WooCommerce
infra/helm/mcphub-gateway/     Gateway chart
infra/helm/mcphub-metadata/    Separate MCPHub DB principal setup
infra/helm/mcphub-inventory-mcp/ Inventory chart
infra/helm/mcphub-support-mcp/ Support chart
infra/helm/mcphub-store/       WordPress and MariaDB chart
infra/helm/mcphub-woocommerce-mcp/  Internal MCP chart
tests/                        Offline tests
scripts/                       MCP read and optional mutation checks
docs/                          Design and operations notes
```

## Local development on Kind

Run commands from `C:\MCPHub`. The existing Kind cluster keeps application Services internal. The helper starts localhost-only `kubectl port-forward` tunnels: dashboard and gateway use their direct Services, while storefront port 8081 targets Traefik and exercises the installed Ingress rules.

```powershell
Set-Location C:\MCPHub
.\scripts\start-local-ports.ps1
```

- Dashboard: `http://127.0.0.1:5174` (the deployed dashboard; `/api` is proxied internally to the gateway)
- WooCommerce storefront: `http://127.0.0.1:8081`
- Named Ingress URLs: `http://storefront.mcphub.test:8081` and `http://dashboard.mcphub.test:8081` (add the hosts-file mapping documented in [docs/local-ingress.md](docs/local-ingress.md))
- Direct gateway API for local diagnostics and external MCP clients: `http://127.0.0.1:8010`

Stop only forwards started by the helper with `.\scripts\stop-local-ports.ps1`. The cluster itself is unchanged. The Traefik controller and Ingress resources route storefront and dashboard traffic without recreating Kind; the named `.test` URLs require the local hosts-file mapping.

1. Activate the virtual environment and confirm the Kind context:

   ```powershell
   Set-Location C:\MCPHub
   .\.venv\Scripts\Activate.ps1
   kubectl config current-context
   ```

2. Start the dashboard, storefront, and direct gateway localhost forwards:

   ```powershell
   .\scripts\start-local-ports.ps1
   ```

   Open the dashboard at `http://127.0.0.1:5174` and the storefront at `http://127.0.0.1:8081`.

3. In another window, use the masked credential setup and MCP server start commands in [docs/woocommerce-mcp.md](docs/woocommerce-mcp.md). For this port-forward setup, both `WOOCOMMERCE_BASE_URL` and `WOOCOMMERCE_CANONICAL_URL` are `http://localhost:8081`.

4. Verify health, tool discovery, and read-only calls:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8010/health
   Invoke-RestMethod http://127.0.0.1:8010/ready
   $keyLines = @(Get-Content .\.secrets\admin-api-key.txt | Where-Object { $_.Trim() })
   if ($keyLines.Count -ne 1) { throw 'Expected exactly one API key in the file.' }
   $apiKey = $keyLines[0].Trim()
   $headers = @{ 'X-API-Key' = $apiKey }
   Invoke-RestMethod http://127.0.0.1:8010/tools -Headers $headers
   $body = @{ tool = 'list_inventory'; arguments = @{} } | ConvertTo-Json -Depth 5
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/call -Headers $headers -ContentType 'application/json' -Body $body
   ```

The MCP client test performs those three reads (`list_products`, `list_orders`, `get_order(16)`) against the local server:

```powershell
python scripts/test_woocommerce_mcp.py
```

Do not use `--create-product` or `--test-order-status` during routine validation; those options change store data.

## Fresh K3s installation notes

The following sequence is for a **new, empty K3s installation only**. It is not the runbook for the existing Azure demo and must not be applied to its persistent releases/data without a reviewed change plan. For the deployed Azure demo, use [docs/helm-upgrades-rollback.md](docs/helm-upgrades-rollback.md) and inspect current Helm values first.

The target cluster must provide a usable StorageClass such as `local-path`. The store chart’s default `standard` class is intended for the current Kind setup; override it for a **new K3s installation** only. The store PVC and MariaDB claim template are persistent. Local-path volumes live on a node and bind workloads to that node; they suit a single-node demo, not resilient multi-node storage. Kubernetes does not permit changing the StorageClass of existing claims in place. Do not apply the new-class setting to existing data claims; back up and plan a data migration before moving existing data between clusters.

1. Select and verify the K3s kubeconfig context. Do not use the Kind context for this deployment:

   ```powershell
   kubectl config get-contexts
   kubectl config use-context <your-k3s-context>
   kubectl get nodes
   kubectl get storageclass
   ```

2. Build the app images. Use the same tags in `kind load` or on each K3s node:

   ```powershell
   docker build -f apps/store/Dockerfile -t mcphub-wordpress:local .
   docker build -f apps/woocommerce-mcp/Dockerfile -t mcphub-woocommerce-mcp:local .
   docker build -f apps/gateway/Dockerfile -t mcphub-gateway:security-v2 .
   docker build -f apps/inventory-mcp/Dockerfile -t mcphub-inventory-mcp:persistent-v2 .
   docker build -f apps/support-mcp/Dockerfile -t mcphub-support-mcp:persistent-v2 .
   docker build -f apps/dashboard/Dockerfile -t mcphub-dashboard:security-v1 .
   ```

   K3s uses containerd. For a single-node local K3s host, transfer the images to the Linux node and import them there. For a multi-node cluster, import them on every eligible node or publish them to a registry reachable by all nodes:

   ```powershell
   docker save -o .\mcphub-images.tar mcphub-wordpress:local mcphub-woocommerce-mcp:local mcphub-gateway:security-v2 mcphub-inventory-mcp:persistent-v2 mcphub-support-mcp:persistent-v2 mcphub-dashboard:security-v1
   $k3sNode = Read-Host 'SSH target for the local K3s server node'
   scp .\mcphub-images.tar "${k3sNode}:/tmp/mcphub-images.tar"
   ssh $k3sNode 'sudo k3s ctr images import /tmp/mcphub-images.tar && rm -f /tmp/mcphub-images.tar'
   Remove-Item .\mcphub-images.tar
   ```

3. Create the `mcphub` namespace and create `mcphub-db-secret` through the secure prompt workflow in [docs/security-operations.md](docs/security-operations.md#store-database-secret). The local values file references that pre-created Secret; it must not contain database passwords. The WordPress and MariaDB pods consume Secret references:

   ```powershell
   helm upgrade --install mcphub-store .\infra\helm\mcphub-store `
     --namespace mcphub --create-namespace `
     --values .\infra\helm\mcphub-store\values-local.yaml `
     --set-string storageClassName=local-path
   ```

   This command is for a fresh K3s data store. Helm stores supplied release values, which is why database passwords are supplied through the Kubernetes Secret rather than Helm values. Limit Helm release and Secret access with RBAC and enable K3s Secrets encryption for sensitive deployments.

4. Create the `mcphub-woocommerce-api` Kubernetes Secret using the masked, temporary-file workflow in [docs/woocommerce-mcp.md](docs/woocommerce-mcp.md). Its `base-url` must target WordPress over the internal Service (`http://mcphub-wordpress`). Do not put consumer credentials in Helm values, command arguments, URLs, or logs.

5. Create the separate metadata credentials and run the provisioning chart as documented in [docs/security-operations.md](docs/security-operations.md). Then deploy both MCP replicas, gateway, and dashboard. Set the canonical URL to the WordPress home URL shown by its REST index. The canonical URL is used only to sign OAuth; the API request still goes to the internal Service address:

   ```powershell
   $canonicalUrl = 'http://localhost:8081' # Replace if the WordPress home URL differs.
   helm upgrade --install mcphub-woocommerce-mcp .\infra\helm\mcphub-woocommerce-mcp `
     --namespace mcphub --set-string woocommerceCanonicalUrl=$canonicalUrl
   helm upgrade --install mcphub-gateway .\infra\helm\mcphub-gateway --namespace mcphub
   helm upgrade --install mcphub-inventory-mcp .\infra\helm\mcphub-inventory-mcp --namespace mcphub
   helm upgrade --install mcphub-support-mcp .\infra\helm\mcphub-support-mcp --namespace mcphub
   helm upgrade --install mcphub-dashboard .\infra\helm\mcphub-dashboard --namespace mcphub
   ```

   The MCP Deployment hashes the externally managed WooCommerce Secret into its pod template, so a subsequent Helm upgrade after Secret rotation rolls both replicas and reloads the configuration.

6. Wait for rollouts and test via a local gateway port-forward:

   ```powershell
   kubectl rollout status deployment/mcphub-woocommerce-mcp -n mcphub
   kubectl rollout status deployment/mcphub-gateway -n mcphub
   kubectl get deployments,pods -n mcphub -o wide
   kubectl port-forward -n mcphub service/mcphub-gateway 8010:8000
   ```

   In another window, repeat the health, `/tools`, `list_products`, and `get_order(16)` calls from the local verification section. These are read-only. Confirm two MCP pods are ready and use the same deployment image/configuration. Never run `kubectl get secret -o yaml` when sharing terminal output.

The Azure K3s demo has been deployed and live-checked, but this section describes a **new, empty K3s installation**, not the procedure used for or safe to apply to the existing Azure releases. Verify the target context and storage class before changes. For a fresh installation, set the store chart storage class to `local-path`; never apply that setting to existing Kind claims or reuse WordPress PVCs across clusters. Build all required images from a reviewed source revision and import them into every eligible containerd node or publish them to a registry reachable by all nodes. Create `mcphub-db-secret` through the protected workflow in [docs/security-operations.md](docs/security-operations.md#store-database-secret), deploy store/MariaDB first, create the independent metadata Secret, then follow the metadata/app chart sequence. Use K3s Secret encryption and narrowly scoped RBAC, and expose authenticated interfaces only behind TLS. Do not switch Kubernetes contexts as part of the Kind workflow.

## WooCommerce OAuth URL handling

For OAuth over HTTP, WooCommerce reconstructs the URL used for signature validation from its WordPress home URL and the incoming request path. In Kubernetes, the MCP server therefore signs with `WOOCOMMERCE_CANONICAL_URL` while sending the request to `WOOCOMMERCE_BASE_URL` (the internal Service URL). The current WordPress REST index reports `http://localhost:8081` as the canonical URL. If WordPress changes its home URL or proxy scheme, update the Helm value to match the URL WooCommerce uses for validation, including the scheme PHP derives from `is_ssl()`.

The MCP server exposes errors with status and WooCommerce code/message, redacting configured credentials and avoiding request-header logging. Health endpoints expose no configuration values.

## Checks

Run the Python tests and compilation, dashboard lint/build, and Helm checks from the repository root:

```powershell
Set-Location C:\MCPHub
.\.venv\Scripts\Activate.ps1
python -m pytest -q
python -m compileall -q apps tests
Push-Location apps\dashboard; npm run lint; npm run build; Pop-Location
helm lint .\infra\helm\mcphub-gateway
helm lint .\infra\helm\mcphub-inventory-mcp
helm lint .\infra\helm\mcphub-support-mcp
helm lint .\infra\helm\mcphub-metadata
helm lint .\infra\helm\mcphub-woocommerce-mcp
helm lint .\infra\helm\mcphub-store --set mariadb.password=lint-only --set mariadb.rootPassword=lint-only
```

The store Helm lint values above are dummy strings for rendering only. Never use real credentials in `--set` arguments.
