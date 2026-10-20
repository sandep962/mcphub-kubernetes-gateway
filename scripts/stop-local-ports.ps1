$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectRoot '.local\local-ports.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output 'No MCPHub local port-forward state file exists.'
    exit 0
}

$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
foreach ($forward in @($state.forwards)) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($forward.pid)" -ErrorAction SilentlyContinue
    if (-not $process) { continue }

    $commandLine = [string]$process.CommandLine
    $namespace = if ($forward.namespace) { [string]$forward.namespace } else { 'mcphub' }
    $namespacePattern = 'kubectl(?:\.exe)?"?\s+-n\s+' + [regex]::Escape($namespace) + '\s+port-forward'
    if ($commandLine -match $namespacePattern -and
        $commandLine.Contains([string]$forward.target) -and
        $commandLine.Contains("$($forward.localPort):")) {
        Stop-Process -Id ([int]$forward.pid) -Force
        Write-Output "Stopped MCPHub $($forward.name) port-forward on 127.0.0.1:$($forward.localPort)."
    }
}

Remove-Item -LiteralPath $statePath -Force
