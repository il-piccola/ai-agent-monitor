$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $root '.runtime'
$portsFile = Join-Path $runtime 'ports.env'
$pidFile = Join-Path $runtime 'monitor.pid'
if (-not (Test-Path -LiteralPath $portsFile)) {
    throw "No deployment state found at $portsFile"
}

$settings = @{}
foreach ($line in Get-Content -LiteralPath $portsFile) {
    if ($line -match '^([A-Z_]+)=(\d+)$') {
        $settings[$Matches[1]] = [int]$Matches[2]
    }
}
foreach ($name in @('BACKEND_PORT', 'TAILSCALE_HTTPS_PORT', 'MONITOR_PID')) {
    if (-not $settings.ContainsKey($name)) { throw "Missing $name in $portsFile" }
}

$backendUrl = "http://127.0.0.1:$($settings.BACKEND_PORT)"
$port = $settings.TAILSCALE_HTTPS_PORT
$serveJson = & tailscale serve status --json
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Tailscale Serve configuration.' }
$serve = $serveJson | ConvertFrom-Json
$hostName = (& tailscale status --json | ConvertFrom-Json).Self.DNSName.TrimEnd('.')
$webEntry = if ($serve.Web) { $serve.Web.PSObject.Properties["${hostName}:$port"] }
if ($webEntry) {
    $handler = $webEntry.Value.Handlers.PSObject.Properties['/'].Value
    if (-not $handler -or $handler.Proxy -ne $backendUrl) {
        throw "Tailscale Serve port $port no longer points to $backendUrl; refusing to change it."
    }

    & tailscale serve "--https=$port" off
    if ($LASTEXITCODE -ne 0) { throw "Could not stop Tailscale Serve port $port." }
}

$monitorProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($settings.MONITOR_PID)"
if ($monitorProcess -and $monitorProcess.CommandLine -like "*monitor.py*--port $($settings.BACKEND_PORT)*") {
    Stop-Process -Id $settings.MONITOR_PID
}
Remove-Item -LiteralPath $pidFile, $portsFile -ErrorAction SilentlyContinue
'AI Agent Monitor deployment stopped.'
