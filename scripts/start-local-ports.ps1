$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$stateDirectory = Join-Path $projectRoot '.local'
$statePath = Join-Path $stateDirectory 'local-ports.json'
$kubectl = (Get-Command kubectl -ErrorAction Stop).Source

if ((& $kubectl config current-context).Trim() -ne 'kind-kind') {
    throw 'Refusing to forward MCPHub ports: the current Kubernetes context is not kind-kind.'
}

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$state = if (Test-Path -LiteralPath $statePath) {
    Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
} else {
    [pscustomobject]@{ forwards = @() }
}

function Test-LocalEndpoint([string] $url, [string] $hostHeader = '') {
    try {
        $requestOptions = @{ Uri = $url; TimeoutSec = 2; UseBasicParsing = $true }
        if ($hostHeader) { $requestOptions.Headers = @{ Host = $hostHeader } }
        $response = Invoke-WebRequest @requestOptions
        return [int]$response.StatusCode -ge 200 -and [int]$response.StatusCode -lt 500
    } catch {
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            return $status -ge 200 -and $status -lt 500
        }
        return $false
    }
}

function Start-LocalForward([string] $name, [string] $namespace, [string] $target, [string] $mapping, [string] $checkUrl, [string] $checkHost = '') {
    for ($check = 0; $check -lt 5; $check++) {
        if (Test-LocalEndpoint $checkUrl $checkHost) {
            Write-Output "$name already responds at $checkUrl"
            return
        }
        Start-Sleep -Milliseconds 500
    }

    $localPort = [int]($mapping.Split(':')[0])
    $listener = Get-NetTCPConnection -State Listen -LocalPort $localPort -ErrorAction SilentlyContinue
    if ($listener) {
        throw "Local port $localPort is already occupied, but $name did not respond at $checkUrl."
    }

    $stdoutPath = Join-Path $stateDirectory "$name-port-forward.out.log"
    $stderrPath = Join-Path $stateDirectory "$name-port-forward.err.log"
    $process = Start-Process -FilePath $kubectl `
        -ArgumentList @('-n', $namespace, 'port-forward', '--address', '127.0.0.1', $target, $mapping) `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($process.HasExited) { break }
        if (Test-LocalEndpoint $checkUrl $checkHost) { $ready = $true; break }
    }
    if (-not $ready) {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
        throw "Could not start $name port-forward. Review the local diagnostic log at $stderrPath."
    }

    $state.forwards = @($state.forwards) + @([pscustomobject]@{
        name = $name
        namespace = $namespace
        target = $target
        localPort = $localPort
        pid = $process.Id
    })
    Write-Output "$name is ready at $checkUrl"
}

Start-LocalForward 'dashboard' 'mcphub' 'service/mcphub-dashboard' '5174:8080' 'http://127.0.0.1:5174/api/health'
Start-LocalForward 'storefront' 'traefik-system' 'service/traefik' '8081:80' 'http://127.0.0.1:8081/' 'storefront.mcphub.test:8081'
Start-LocalForward 'gateway' 'mcphub' 'service/mcphub-gateway' '8010:8000' 'http://127.0.0.1:8010/health'
$state | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
Write-Output 'Dashboard:  http://127.0.0.1:5174'
Write-Output 'Storefront: http://127.0.0.1:8081'
Write-Output 'Gateway API: http://127.0.0.1:8010'
Write-Output 'Ingress storefront: http://storefront.mcphub.test:8081'
Write-Output 'Ingress dashboard:  http://dashboard.mcphub.test:8081'
