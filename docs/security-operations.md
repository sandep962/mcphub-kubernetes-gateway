# MCPHub security and operations

## Metadata database

MCPHub uses the existing MariaDB StatefulSet but creates a separate `mcphub` database and `mcphub_app` account. The provisioner reads the already-present `mcphub-db-secret` root password directly from Kubernetes; application pods receive only the restricted app credentials in `mcphub-metadata-secret`. WordPress database/account values are not reused. Keep the metadata secret out of Helm values and source control.

Generate and apply the metadata secret without printing its password (PowerShell, from `C:\MCPHub`):

```powershell
New-Item -ItemType Directory -Force .\.secrets | Out-Null
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 48
$rng.GetBytes($bytes)
$metadataPassword = [Convert]::ToBase64String($bytes)
$payload = [ordered]@{
  apiVersion = 'v1'; kind = 'Secret'
  metadata = @{ name = 'mcphub-metadata-secret'; namespace = 'mcphub' }
  type = 'Opaque'
  stringData = @{ database = 'mcphub'; username = 'mcphub_app'; password = $metadataPassword }
} | ConvertTo-Json -Depth 5 -Compress
$payload | kubectl apply -f -
$metadataPassword = $null; $payload = $null; $bytes = $null; $rng.Dispose()
```

The command output reports only the Secret resource name/status. For a new cluster, deploy MariaDB and WordPress first, then run the metadata provisioning Job. The Job is idempotent and grants only the privileges needed for schema creation and CRUD; it does not modify the WordPress database.

## Build and deploy to the existing Kind cluster

Confirm `kubectl config current-context` is the intended Kind context and that the existing MariaDB pod and PVC are healthy. Do not delete or recreate the cluster, store release, PVCs, or database pods for this update.

Build/load only the changed app images, create the metadata Secret as described above, then install the metadata chart before upgrading app deployments:

```powershell
docker build -f apps/gateway/Dockerfile -t mcphub-gateway:security-v2 .
docker build -f apps/inventory-mcp/Dockerfile -t mcphub-inventory-mcp:persistent-v2 .
docker build -f apps/support-mcp/Dockerfile -t mcphub-support-mcp:persistent-v2 .
docker build -f apps/dashboard/Dockerfile -t mcphub-dashboard:security-v1 .
kind load docker-image mcphub-gateway:security-v2 mcphub-inventory-mcp:persistent-v2 mcphub-support-mcp:persistent-v2 mcphub-dashboard:security-v1 --name kind

helm upgrade --install mcphub-metadata .\infra\helm\mcphub-metadata --namespace mcphub
kubectl wait --for=condition=complete job/mcphub-db-provisioner -n mcphub --timeout=180s
helm upgrade --install mcphub-gateway .\infra\helm\mcphub-gateway --namespace mcphub
helm upgrade --install mcphub-inventory-mcp .\infra\helm\mcphub-inventory-mcp --namespace mcphub
helm upgrade --install mcphub-support-mcp .\infra\helm\mcphub-support-mcp --namespace mcphub
helm upgrade --install mcphub-dashboard .\infra\helm\mcphub-dashboard --namespace mcphub

kubectl rollout status deployment/mcphub-gateway -n mcphub
kubectl rollout status deployment/mcphub-inventory-mcp -n mcphub
kubectl rollout status deployment/mcphub-support-mcp -n mcphub
kubectl rollout status deployment/mcphub-dashboard -n mcphub
kubectl get deployments,pods,pvc -n mcphub
```

The gateway remains two replicas. All MCP and dashboard Services are ClusterIP. NetworkPolicy templates allow app-to-database connections only where needed, gateway-to-MCP calls, and dashboard-to-gateway requests. NetworkPolicy enforcement depends on the selected CNI; Kind's default network stack and a default K3s installation may not enforce these policies without a policy-capable CNI.

## One-time administrator bootstrap

Port-forward the dashboard or gateway in a terminal. Keep the bootstrap key out of command output by redirecting the one-time command directly to a local file. Restrict that file to the current Windows account:

```powershell
New-Item -ItemType Directory -Force .\.secrets | Out-Null
kubectl exec -n mcphub deployment/mcphub-gateway -- python -m apps.shared.bootstrap_admin |
  Set-Content -NoNewline .\.secrets\admin-api-key.txt
icacls .\.secrets\admin-api-key.txt /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
```

The command refuses to create a second `initial-admin`. Back up the local file in an approved secrets manager if needed; never commit it or paste its content into chat/logs. The dashboard keeps an entered key in tab memory only. Sign out or close the tab to clear it. Use the admin panel to create teams and identities, issue keys, grant permissions, revoke keys/grants, and review audit events. An issued key is shown once in the page; copy it to the intended client and close the page when finished.

To rotate a key, issue a replacement for the same identity, move the client to the new key, verify it, and revoke the old key in the admin panel. Revocation takes effect at the next request. Admin identities have full access; operator/viewer identities require explicit team or identity tool grants. Viewers are restricted to the built-in read-only tool set even if given a broader grant.

## Gateway and dashboard validation

From `C:\MCPHub`, start the dashboard, storefront, and direct gateway localhost forwards:

```powershell
.\scripts\start-local-ports.ps1
```

Verify health and authentication behavior, then use the key without printing it:

```powershell
Invoke-RestMethod http://127.0.0.1:8010/health
try { Invoke-RestMethod http://127.0.0.1:8010/tools } catch { $_.Exception.Response.StatusCode }
$keyLines = @(Get-Content .\.secrets\admin-api-key.txt | Where-Object { $_.Trim() })
if ($keyLines.Count -ne 1) { throw 'Expected exactly one API key in the file.' }
$apiKey = $keyLines[0].Trim()
$headers = @{ 'X-API-Key' = $apiKey }
Invoke-RestMethod http://127.0.0.1:8010/whoami -Headers $headers
$tools = Invoke-RestMethod http://127.0.0.1:8010/tools -Headers $headers
$tools | Select-Object name,server
$body = @{ tool = 'list_inventory'; arguments = @{} } | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8010/call -Headers $headers -ContentType 'application/json' -Body $body
Invoke-RestMethod http://127.0.0.1:8010/admin/audit -Headers $headers
Remove-Variable apiKey,headers
```

Keep exactly one API key per file. When storing separate keys, use separate protected files; do not append multiple keys to `admin-api-key.txt`. The check above prevents a multi-line file from being sent as one invalid `X-API-Key` value.

To open the deployed dashboard and storefront, run `C:\MCPHub\scripts\start-local-ports.ps1`, then browse to `http://127.0.0.1:5174` and `http://127.0.0.1:8081`. The dashboard proxies `/api` to the internal gateway Service. The helper also provides the direct gateway/MCP endpoint at `http://127.0.0.1:8010` for external MCP clients. Use the initial admin key from the protected local file. These localhost-only HTTP forwards are for local development; expose K3s through an HTTPS reverse proxy with authentication rate limits and restrict Kubernetes Secret/Helm release access using RBAC and secret encryption.

## Tests and troubleshooting

```powershell
Set-Location C:\MCPHub
.\.venv\Scripts\python.exe -m compileall -q apps tests
.\.venv\Scripts\python.exe -m pytest -q
Push-Location .\apps\dashboard; npm run lint; npm run build; Pop-Location
helm lint .\infra\helm\mcphub-gateway
helm lint .\infra\helm\mcphub-inventory-mcp
helm lint .\infra\helm\mcphub-support-mcp
helm lint .\infra\helm\mcphub-dashboard
helm lint .\infra\helm\mcphub-metadata
```

If `/tools` returns 401, provide `X-API-Key`; if it returns 503, check `/ready`, the metadata Secret reference, database service, and the provisioning Job logs without printing Secret values. A tool absent from `/tools` may be upstream-disconnected or not granted to the calling identity. `Unknown MCP tool` from `/call` means the gateway did not discover that tool in its current upstream set; check `/tools` first and inspect gateway/upstream pod logs for connection errors.

## K3s preparation

The K3s path has not been exercised here. Verify the target context and storage class before any changes. For a **fresh** K3s installation, set the store chart storage class to `local-path`; never apply that setting to existing Kind claims or reuse WordPress PVCs across clusters. Build all five images above and import them into every eligible containerd node or publish them to a private registry. Create `mcphub-db-secret` for the fresh store through a protected workflow, deploy store/MariaDB first, create the independent metadata Secret, then follow the metadata/app chart sequence. Use K3s secret encryption and narrowly scoped RBAC, and expose the dashboard only behind TLS. Do not switch Kubernetes contexts as part of the Kind workflow.

## Demo recording checklist

1. Show the architecture and Kind pods/PVCs, without opening Secret data.
2. Open the dashboard and sign in with the admin key from the protected local file.
3. Show `/tools` discovery across WooCommerce, Inventory, and Support; run `list_inventory` and a read-only WooCommerce query.
4. Create a demo team and viewer identity; grant `inventory/list_inventory`; issue a key and copy it once.
5. Demonstrate allowed inventory listing and denied write access with the viewer key, then show the audit rows as admin.
6. Revoke the demo key, demonstrate its rejection, and finish with test/build output and the repository link. Do not show credentials, customer data, or secret values in the recording.

On Windows, start/stop the screen recording with `Win+Alt+R` (Xbox Game Bar) or your preferred recorder. Save the video outside the repository unless the assignment explicitly asks for the video file in the repo. Before submitting source, run `git diff --check`, review `git status --short`, and confirm `.secrets/admin-api-key.txt` is ignored. Stage only the project files you reviewed, commit with a descriptive message, and push the assignment branch to its configured remote. Do not stage `.secrets`, local Helm values, `.env` files, or kubeconfig files.
