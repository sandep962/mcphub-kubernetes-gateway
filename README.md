# MCPHub

MCPHub is a local Kubernetes project that places a FastAPI gateway in front of custom MCP servers. WooCommerce, Inventory, and Support are the working upstream integrations. The development cluster is Kind; the VPS/K3s deployment path is documented but not live-verified.

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

The gateway, dashboard, and Inventory/Support servers require the metadata database and `mcphub-metadata-secret`; `/health` stays available to probes while application endpoints require `X-API-Key`. See [docs/security-operations.md](docs/security-operations.md) for the private setup, bootstrap, rotation, verification, and demo steps. K3s deployment instructions are preparation only; this checkout has not been deployed to K3s.

## Architecture, threat model, and tradeoffs

Clients connect to the authenticated gateway. The gateway discovers and calls the WooCommerce, Inventory, and Support MCP servers over internal ClusterIP Services; the servers do not expose NodePorts or LoadBalancers. The dashboard calls the gateway through its same-origin `/api` reverse proxy. Metadata authentication, permissions, and audit records use the separate MCPHub database principal; WooCommerce and WordPress use their own database and credentials.

The gateway stores only SHA-256 API-key hashes and a short display prefix. It authenticates every request, applies identity/team grants and role restrictions to each tool call, and records identity, tool, outcome, and duration without logging API keys, tool arguments, or tool results. Upstream credentials and the LLM key are supplied to server containers using Kubernetes Secret references; they are not sent to the browser. NetworkPolicies restrict MCP ingress to the gateway and limit application egress. Policy behavior depends on a policy-capable CNI and was checked in the current Kind cluster.

The local threat boundary assumes the developer controls the Windows host and Kind cluster. A host or Kubernetes administrator can read container environment and Kubernetes Secrets; use Kubernetes RBAC and Secret encryption at rest on a shared VPS. Local access uses plain HTTP through loopback-only port forwards. The Ingress controller exposes the storefront and dashboard only on the developer's machine at `http://storefront.mcphub.test:8081` and `http://dashboard.mcphub.test:8081`; Windows name resolution requires the hosts-file entry documented in [docs/local-ingress.md](docs/local-ingress.md). For a remote installation, terminate TLS at a controlled reverse proxy/Ingress and expose only the dashboard and gateway. Keep upstream MCP Services internal. MariaDB and WordPress data persist on PVCs, but a single-node Kind/local-path disk is not a backup or high-availability strategy.

The single gateway centralizes authentication and audit but is also a dependency for every client request. Upstream discovery is performed at request time: an unavailable server loses its tools from discovery while healthy upstreams can remain usable. The local Ingress controller is reached through an explicit `kubectl port-forward` tunnel because this Kind cluster does not publish application ports; the tunnel must be running for the named storefront and dashboard URLs. See [docs/local-ingress.md](docs/local-ingress.md) for setup and verification.

## External MCP client connection

Configure an MCP Inspector or other Streamable HTTP client with:

- Server URL: `http://127.0.0.1:8010/mcp`
- Custom HTTP header: `X-API-Key: <the client's MCPHub API key>`
- Transport: Streamable HTTP

Use a key issued for that client identity and store it in the client's protected credential store. Do not connect clients directly to `mcphub-woocommerce-mcp`, `mcphub-inventory-mcp`, or `mcphub-support-mcp`; the gateway must enforce authorization and audit logging.

## VPS/K3s deployment boundary

The Helm charts can be applied to a VPS running K3s using the chart values and secret workflow below. For a fresh K3s store, set the store chart's `storageClassName` to the cluster's `local-path` StorageClass, provide private values only through a protected local file, and create external Secrets through the documented secret workflow. Set image references to a registry reachable by the cluster. The current charts keep all app Services as ClusterIP and do not install an Ingress controller or TLS certificates; configure a controlled TLS ingress/reverse proxy in the target cluster before remote use. This repository's K3s/VPS path is documented but has not been deployed or live-verified.

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
   try { Invoke-RestMethod http://127.0.0.1:8010/tools } catch { $_.Exception.Response.StatusCode }
   Invoke-RestMethod http://127.0.0.1:8010/health
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

## Build and deploy to local K3s

K3s includes a `local-path` StorageClass. The store chart’s default `standard` class is for the current Kind setup; override it to `local-path` for a **new K3s installation**. The store PVC and MariaDB claim template are persistent. Local-path volumes live on a node and bind workloads to that node; they suit a single-node local development cluster, not resilient multi-node storage. Kubernetes does not permit changing the StorageClass of existing claims in place. Do not apply the new-class setting to existing data claims; back up and plan a data migration before moving existing data between clusters.

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

3. Create the `mcphub` namespace and deploy the store with the existing private values file. That ignored file must contain `mariadb.password` and `mariadb.rootPassword`; do not display or commit it. The chart-managed Secret is consumed by the WordPress and MariaDB pods:

   ```powershell
   helm upgrade --install mcphub-store .\infra\helm\mcphub-store `
     --namespace mcphub --create-namespace `
     --values .\infra\helm\mcphub-store\values-local.yaml `
     --set-string storageClassName=local-path
   ```

   This command is for a fresh K3s data store. The chart’s Helm release also stores supplied values; limit Helm release and Secret access with RBAC and enable K3s secrets encryption for sensitive deployments.

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

The K3s workflow has not been run from this checkout. For a new K3s cluster, include Inventory, Support, metadata provisioning, and secret bootstrap using [docs/security-operations.md](docs/security-operations.md); verify cluster context, image import, storage class, and data backup first. Never install the store chart over existing data without a planned migration. The Kubernetes Secret encryption and RBAC settings are cluster operations and are not configured by these Helm charts.

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
