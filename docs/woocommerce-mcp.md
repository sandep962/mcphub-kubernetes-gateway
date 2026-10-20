# WooCommerce MCP authentication and deployment

The store endpoint in local development is plain HTTP through `kubectl port-forward`. WooCommerce's supported authentication for HTTP is OAuth 1.0a. The MCP server signs requests and sends OAuth fields in the `Authorization` header; keys and secrets are never put in URLs. For an HTTPS store endpoint it uses HTTP Basic authentication.

WooCommerce validates OAuth against its configured WordPress home URL plus the incoming request path, rather than the internal Kubernetes service hostname. Set `WOOCOMMERCE_CANONICAL_URL` to the canonical origin WooCommerce uses for that request: use the WordPress home URL shown by the REST API index, and ensure the scheme matches what PHP sees (`is_ssl()`) for the in-cluster request. `WOOCOMMERCE_BASE_URL` remains the address used for the network request; inside Kubernetes that should be the internal WordPress Service URL. For the current HTTP port-forward setup, both URLs are `http://localhost:8081`.

The server validates the base URL, canonical URL, and both API credentials before Uvicorn starts and exposes `/health` without returning configuration values. API errors include the HTTP status and WooCommerce's safe error code/message; malformed or non-JSON responses receive a bounded generic diagnostic.

## Run locally from PowerShell

Run from `C:\MCPHub`. Keep this in one PowerShell window. Each credential prompt is masked. The server child process receives the environment at launch, and the parent process variables are cleared immediately afterward.

```powershell
$root = (Resolve-Path .).Path
$env:WOOCOMMERCE_BASE_URL = 'http://localhost:8081'
$env:WOOCOMMERCE_CANONICAL_URL = 'http://localhost:8081'

function Set-ProcessSecretFromPrompt([string]$Name, [string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [Environment]::SetEnvironmentVariable(
            $Name,
            [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer),
            'Process'
        )
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}

Set-ProcessSecretFromPrompt 'WOOCOMMERCE_CONSUMER_KEY' 'WooCommerce consumer key'
Set-ProcessSecretFromPrompt 'WOOCOMMERCE_CONSUMER_SECRET' 'WooCommerce consumer secret'

$log = Join-Path $env:TEMP 'mcphub-woocommerce-mcp.log'
$errorLog = Join-Path $env:TEMP 'mcphub-woocommerce-mcp-error.log'
$server = Start-Process `
    -FilePath (Join-Path $root '.venv\Scripts\python.exe') `
    -ArgumentList @('-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '8001') `
    -WorkingDirectory (Join-Path $root 'apps\woocommerce-mcp') `
    -RedirectStandardOutput $log `
    -RedirectStandardError $errorLog `
    -PassThru

Remove-Item Env:WOOCOMMERCE_CONSUMER_KEY, Env:WOOCOMMERCE_CONSUMER_SECRET
```

Wait for startup, then check health and run the read-only MCP verification:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/health
& .\.venv\Scripts\python.exe .\scripts\test_woocommerce_mcp.py
```

The client exits nonzero if any read operation fails. It verifies `list_products`, `list_orders`, and `get_order(16)`. Only after these succeed, create a test product with:

```powershell
& .\.venv\Scripts\python.exe .\scripts\test_woocommerce_mcp.py --create-product
```

When finished:

```powershell
Stop-Process -Id $server.Id
Remove-Item Env:WOOCOMMERCE_BASE_URL, Env:WOOCOMMERCE_CANONICAL_URL -ErrorAction SilentlyContinue
```

If startup configuration is missing, the process exits with the names of missing variables only. Inspect the local log for safe startup/network diagnostics; never add credentials to a command, URL, log, or tracked file.

## Deploy inside Kind

The chart creates a ClusterIP service only. Its NetworkPolicy permits ingress to the MCP pod from gateway pods labeled `app=mcphub-gateway`. The gateway Deployment uses that label and connects to `http://mcphub-woocommerce-mcp.mcphub.svc.cluster.local:8001/mcp`. NetworkPolicy enforcement depends on the cluster CNI. The chart sets two MCP replicas and accepts a separate canonical WordPress home URL through `woocommerceCanonicalUrl`.

Build and load the custom image:

```powershell
docker build -f apps/woocommerce-mcp/Dockerfile -t mcphub-woocommerce-mcp:local .
kind load docker-image mcphub-woocommerce-mcp:local
```

Create the Kubernetes Secret without putting credential values in command arguments or shell history. The temporary files are removed after the Secret is applied:

```powershell
$secretDir = Join-Path $env:TEMP ([guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $secretDir | Out-Null
'http://mcphub-wordpress' | Set-Content -NoNewline (Join-Path $secretDir 'base-url')

function Write-SecretFileFromPrompt([string]$Name, [string]$Prompt, [string]$Path) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        [IO.File]::WriteAllText($Path, [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer))
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        $secure.Dispose()
    }
}

Write-SecretFileFromPrompt 'consumer-key' 'WooCommerce consumer key' (Join-Path $secretDir 'consumer-key')
Write-SecretFileFromPrompt 'consumer-secret' 'WooCommerce consumer secret' (Join-Path $secretDir 'consumer-secret')

kubectl create secret generic mcphub-woocommerce-api -n mcphub `
    --from-file=base-url=(Join-Path $secretDir 'base-url') `
    --from-file=consumer-key=(Join-Path $secretDir 'consumer-key') `
    --from-file=consumer-secret=(Join-Path $secretDir 'consumer-secret') `
    --dry-run=client -o yaml | kubectl apply -f -
Remove-Item -LiteralPath $secretDir -Recurse -Force

helm upgrade --install mcphub-woocommerce-mcp .\infra\helm\mcphub-woocommerce-mcp `
    --namespace mcphub --create-namespace
```

Kubernetes Secrets are base64-encoded in etcd by default, not encrypted by that encoding. Enable encryption at rest and restrict RBAC access to Secrets in a production cluster.
