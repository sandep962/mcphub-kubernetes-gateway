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

## Store database Secret

For a **fresh** store install, create the database Secret before installing the store chart. The ignored `infra/helm/mcphub-store/values-local.yaml` should contain only `mariadb.existingSecret: mcphub-db-secret`; do not put password values in Helm values because Helm stores release configuration. Prompt for both passwords as secure input and send the Secret manifest directly to `kubectl` stdin:

```powershell
kubectl create namespace mcphub --dry-run=client -o yaml | kubectl apply -f -
$rootSecure = Read-Host 'MariaDB root password' -AsSecureString
$appSecure = Read-Host 'WordPress database password' -AsSecureString
$rootPtr = [IntPtr]::Zero
$appPtr = [IntPtr]::Zero
try {
  $rootPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rootSecure)
  $appPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($appSecure)
  $rootPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($rootPtr)
  $appPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($appPtr)
  $manifest = [ordered]@{
    apiVersion = 'v1'; kind = 'Secret'
    metadata = @{ name = 'mcphub-db-secret'; namespace = 'mcphub' }
    type = 'Opaque'
    stringData = @{ database = 'wordpress'; username = 'wpuser'; password = $appPlain; 'root-password' = $rootPlain }
  } | ConvertTo-Json -Depth 5 -Compress
  $manifest | kubectl apply -f - | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Secret apply failed; inspect the sanitized kubectl error.' }
}
finally {
  if ($rootPtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($rootPtr) }
  if ($appPtr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($appPtr) }
  $rootSecure = $null; $appSecure = $null; $rootPlain = $null; $appPlain = $null; $manifest = $null
}
```

This creates or updates only the named Secret and does not print its values. Use unique strong credentials and a protected administrative terminal. Do not use this procedure to rotate credentials for an existing database without a coordinated account/Secret maintenance plan. The store chart supports `mariadb.existingSecret`; its Secret template is skipped when that value is set.

## Deploying application changes

The former copy-paste image tags and `helm upgrade --install` block was removed because it could drift from the effective release values and overwrite configuration. For an existing release, follow [the upgrade and rollback runbook](helm-upgrades-rollback.md): verify the context, current release values, rendered diff, and rollback revision before changing an image. Use [the VPS guide](vps-k3s-deployment.md) only for a fresh target cluster. Do not rerun metadata provisioning or migrations as routine application deployment steps.

The charts keep application Services internal. NetworkPolicies allow only required app-to-database, gateway-to-MCP, and dashboard-to-gateway traffic. Enforcement depends on a policy-capable CNI; verify the selected cluster actually enforces the policies.

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

To open the local dashboard and storefront, run `C:\MCPHub\scripts\start-local-ports.ps1`, then browse to `http://127.0.0.1:5174` and `http://127.0.0.1:8081`. The dashboard proxies `/api` to the internal gateway Service. The helper also provides the direct gateway/MCP endpoint at `http://127.0.0.1:8010`. These loopback HTTP forwards are for local development. Remote deployments must expose authenticated routes over HTTPS and restrict Kubernetes Secret/Helm release access using RBAC and Secret encryption.

## Tests and troubleshooting

Run the Python suite and dashboard test/lint/build commands listed in the [README validation section](../README.md#validation). For all eight Helm charts, use the environment-specific values and rendering checks in [the Helm runbook](helm-upgrades-rollback.md). The store chart requires password values from a protected file or existing Secret; never put real values in `--set` arguments.

If `/tools` returns 401, provide `X-API-Key`; if it returns 503, check `/ready`, the metadata Secret reference, database service, and the provisioning Job logs without printing Secret values. A tool absent from `/tools` may be upstream-disconnected or not granted to the calling identity. `Unknown MCP tool` from `/call` means the gateway did not discover that tool in its current upstream set; check `/tools` first and inspect gateway/upstream pod logs for connection errors.

## K3s preparation

For a new K3s cluster, use the [VPS deployment guide](vps-k3s-deployment.md) for image delivery, storage, TLS, installation order, backups, and recovery. The Secret and bootstrap procedures above apply there as well. Do not use a fresh-install sequence to alter the existing Azure releases or persistent data.

## Demo recording

Keep recordings outside the repository unless the assessment explicitly requires them. Never show API keys, passwords, Secret values, or customer details in a recording.
