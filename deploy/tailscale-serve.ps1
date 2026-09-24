$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $root '.runtime'
$portsFile = Join-Path $runtime 'ports.env'
$pidFile = Join-Path $runtime 'monitor.pid'

if (Test-Path -LiteralPath $portsFile) {
    $saved = @{}
    foreach ($line in Get-Content -LiteralPath $portsFile) {
        if ($line -match '^([A-Z_]+)=(\d+)$') {
            $saved[$Matches[1]] = [int]$Matches[2]
        }
    }
    if ($saved.ContainsKey('MONITOR_PID') -and $saved.ContainsKey('BACKEND_PORT')) {
        $previousProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($saved.MONITOR_PID)"
        if ($previousProcess -and $previousProcess.CommandLine -like "*monitor.py*--port $($saved.BACKEND_PORT)*") {
            throw "AI Agent Monitor is already running with state at $portsFile."
        }
    }
    & (Join-Path $PSScriptRoot 'tailscale-stop.ps1') | Out-Null
}

$python = (& uv python find --no-project).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $python)) {
    throw 'An installed Python interpreter could not be found with uv.'
}

$serveJson = & tailscale serve status --json
if ($LASTEXITCODE -ne 0) {
    throw 'Could not read the existing Tailscale Serve configuration.'
}
$serve = $serveJson | ConvertFrom-Json
$servePorts = [System.Collections.Generic.HashSet[int]]::new()
if ($serve.TCP) {
    foreach ($entry in $serve.TCP.PSObject.Properties) {
        [void]$servePorts.Add([int]$entry.Name)
    }
}
if ($serve.Web) {
    foreach ($entry in $serve.Web.PSObject.Properties) {
        if ($entry.Name -match ':(\d+)$') {
            [void]$servePorts.Add([int]$Matches[1])
        }
    }
}

function Test-PortFree([string]$address, [int]$port) {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Parse($address), $port
    )
    try {
        $listener.Start()
        return $true
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

$backendPort = 8765..8799 | Where-Object { Test-PortFree '127.0.0.1' $_ } | Select-Object -First 1
$httpsPort = ((9443..9499) + (10443..10499)) |
    Where-Object { -not $servePorts.Contains($_) -and (Test-PortFree '0.0.0.0' $_) } |
    Select-Object -First 1
if (-not $backendPort -or -not $httpsPort) {
    throw 'No free backend or Tailscale HTTPS port was found.'
}

New-Item -ItemType Directory -Path $runtime -Force | Out-Null
$monitor = Join-Path $root 'monitor.py'
$backendUrl = "http://127.0.0.1:$backendPort"
$process = $null
try {
    $process = Start-Process -FilePath $python `
        -ArgumentList ('"{0}" --port {1}' -f $monitor, $backendPort) `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $runtime 'monitor.stdout.log') `
        -RedirectStandardError (Join-Path $runtime 'monitor.stderr.log')

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($process.HasExited) { break }
        try {
            $response = Invoke-WebRequest -Uri "$backendUrl/" -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 200
        }
    }
    if (-not $ready) { throw 'Monitor did not return HTTP 200.' }

    $serveOutput = & tailscale serve --bg --yes "--https=$httpsPort" $backendUrl 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Tailscale Serve failed: $serveOutput" }

    "BACKEND_PORT=$backendPort`nTAILSCALE_HTTPS_PORT=$httpsPort`nMONITOR_PID=$($process.Id)" |
        Set-Content -LiteralPath $portsFile -Encoding ascii
    $process.Id | Set-Content -LiteralPath $pidFile -Encoding ascii

    $serveOutput
    "Backend: $backendUrl"
    "Tailscale HTTPS port: $httpsPort"
    "Runtime details: $portsFile"
}
catch {
    if ($process -and -not $process.HasExited) {
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    }
    throw
}
