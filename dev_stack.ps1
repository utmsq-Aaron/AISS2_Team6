[CmdletBinding()]
param(
    [switch]$Bridge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

function Get-PythonPath {
    $localVenv = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $localVenv) {
        return $localVenv
    }

    if ($env:PY -and (Test-Path $env:PY)) {
        return $env:PY
    }

    if ($env:CONDA_PREFIX) {
        $candidate = Join-Path $env:CONDA_PREFIX 'python.exe'
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw 'Python not found. Activate a conda env or set PY to its python.exe.'
}

function Get-PortConfig {
    param([string]$PythonPath)

    $portsJson = & $PythonPath (Join-Path $RepoRoot 'scripts/export_ports.py') --format json
    return $portsJson | ConvertFrom-Json
}

function Test-PythonModule {
    param(
        [string]$PythonPath,
        [string]$ModuleName
    )

    & $PythonPath -c "import $ModuleName" | Out-Null
    return $LASTEXITCODE -eq 0
}

function Test-PortBusy {
    param([int]$Port)

    try {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
    } catch {
        return [bool](netstat -ano | Select-String -Pattern ":$Port\s+.*LISTENING")
    }
}

function Get-PortListenerPids {
    param([int]$Port)

    $pids = @()
    foreach ($line in (netstat -ano)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            $pids += [int]$Matches[1]
        }
    }

    return @($pids | Sort-Object -Unique)
}

function Stop-PortListeners {
    param(
        [int]$Port,
        [string]$Name
    )

    $pids = Get-PortListenerPids -Port $Port
    if (@($pids).Count -eq 0) {
        return
    }

    foreach ($pid in $pids) {
        try {
            Stop-Process -Id $pid -Force -ErrorAction Stop
            Write-Host "Stopped $Name listener on :$Port (pid $pid)"
        } catch {
            Write-Host "WARNING Could not stop $Name listener on :$Port (pid $pid): $($_.Exception.Message)"
        }
    }
}

function Reset-StackPorts {
    param([pscustomobject[]]$PortsToReset)

    foreach ($entry in $PortsToReset) {
        if (Test-PortBusy -Port $entry.Port) {
            Stop-PortListeners -Port $entry.Port -Name $entry.Name
        }
    }
}

function Get-EnvValue {
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name)
    if ($value) {
        return $value
    }

    $envFile = Join-Path $RepoRoot '.env'
    if (-not (Test-Path $envFile)) {
        return $null
    }

    foreach ($line in Get-Content $envFile) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $parsed = $Matches[1].Trim()
            return $parsed.Trim('"').Trim("'")
        }
    }

    return $null
}

function Test-RealValue {
    param([string]$Value)

    return [bool]($Value -and -not $Value.StartsWith('your_'))
}

function Start-PythonService {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [int]$Port = 0
    )

    if ($Port -gt 0 -and (Test-PortBusy -Port $Port)) {
        Write-Host "OK $Name already on :$Port"
        return $null
    }

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    $proc = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $RepoRoot `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $script:StartedProcesses += $proc
    Write-Host "Started $Name (pid $($proc.Id))"
    return $proc
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            # Keep waiting until the service is actually reachable.
        }
        [System.Threading.Thread]::Sleep(500)
    }

    throw "Timed out waiting for $Url"
}

$Python = Get-PythonPath
$Ports = Get-PortConfig -PythonPath $Python
$FASTAPI_PORT = [int]$Ports.FASTAPI_PORT
$MLFLOW_PORT = 5001
$TELEGRAM_MCP_PORT = [int]$Ports.MCP_PORTS.telegram
$ORCHESTRATOR_PORT = [int]$Ports.AGENT_PORTS.orchestrator
$MCP_SERVERS = @(
    [pscustomobject]@{ Name = 'weather'; Port = [int]$Ports.MCP_PORTS.weather },
    [pscustomobject]@{ Name = 'routes'; Port = [int]$Ports.MCP_PORTS.routes },
    [pscustomobject]@{ Name = 'strava'; Port = [int]$Ports.MCP_PORTS.strava },
    [pscustomobject]@{ Name = 'garmin'; Port = [int]$Ports.MCP_PORTS.garmin },
    [pscustomobject]@{ Name = 'calendar'; Port = [int]$Ports.MCP_PORTS.calendar },
    [pscustomobject]@{ Name = 'flythrough'; Port = [int]$Ports.MCP_PORTS.flythrough },
    [pscustomobject]@{ Name = 'google_maps'; Port = [int]$Ports.MCP_PORTS.google_maps },
    [pscustomobject]@{ Name = 'athlete'; Port = [int]$Ports.MCP_PORTS.athlete }
)
$AGENT_PORTS = @(
    [pscustomobject]@{ Name = 'recovery'; Port = [int]$Ports.AGENT_PORTS.recovery },
    [pscustomobject]@{ Name = 'load'; Port = [int]$Ports.AGENT_PORTS.load },
    [pscustomobject]@{ Name = 'context'; Port = [int]$Ports.AGENT_PORTS.context },
    [pscustomobject]@{ Name = 'route'; Port = [int]$Ports.AGENT_PORTS.route },
    [pscustomobject]@{ Name = 'fitness'; Port = [int]$Ports.AGENT_PORTS.fitness },
    [pscustomobject]@{ Name = 'coach'; Port = [int]$Ports.AGENT_PORTS.coach },
    [pscustomobject]@{ Name = 'orchestrator'; Port = [int]$Ports.AGENT_PORTS.orchestrator }
)
$LogDir = Join-Path $RepoRoot '.logs\windows'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$script:StartedProcesses = @()
$PreviousPy = [Environment]::GetEnvironmentVariable('PY')
if ($Python) {
    $env:PY = $Python
}

$viteFlag = Get-EnvValue 'VITE_SHOW_GMAIL_REGISTRATION_PAGE'
if ($viteFlag) {
    $env:VITE_SHOW_GMAIL_REGISTRATION_PAGE = $viteFlag
}
$devAutoLoginEmail = Get-EnvValue 'VITE_DEV_AUTO_LOGIN_EMAIL'
if ($devAutoLoginEmail) {
    $env:VITE_DEV_AUTO_LOGIN_EMAIL = $devAutoLoginEmail
}

try {
    Write-Host '=== FitDash Windows stack ==='
    Write-Host "Python: $Python"

    Reset-StackPorts -PortsToReset @(
        [pscustomobject]@{ Name = 'mlflow'; Port = $MLFLOW_PORT },
        [pscustomobject]@{ Name = 'fastapi'; Port = $FASTAPI_PORT },
        [pscustomobject]@{ Name = 'mcp_weather'; Port = [int]$Ports.MCP_PORTS.weather },
        [pscustomobject]@{ Name = 'mcp_routes'; Port = [int]$Ports.MCP_PORTS.routes },
        [pscustomobject]@{ Name = 'mcp_strava'; Port = [int]$Ports.MCP_PORTS.strava },
        [pscustomobject]@{ Name = 'mcp_garmin'; Port = [int]$Ports.MCP_PORTS.garmin },
        [pscustomobject]@{ Name = 'mcp_calendar'; Port = [int]$Ports.MCP_PORTS.calendar },
        [pscustomobject]@{ Name = 'mcp_flythrough'; Port = [int]$Ports.MCP_PORTS.flythrough },
        [pscustomobject]@{ Name = 'mcp_google_maps'; Port = [int]$Ports.MCP_PORTS.google_maps },
        [pscustomobject]@{ Name = 'mcp_athlete'; Port = [int]$Ports.MCP_PORTS.athlete },
        [pscustomobject]@{ Name = 'agent_recovery'; Port = [int]$Ports.AGENT_PORTS.recovery },
        [pscustomobject]@{ Name = 'agent_load'; Port = [int]$Ports.AGENT_PORTS.load },
        [pscustomobject]@{ Name = 'agent_context'; Port = [int]$Ports.AGENT_PORTS.context },
        [pscustomobject]@{ Name = 'agent_route'; Port = [int]$Ports.AGENT_PORTS.route },
        [pscustomobject]@{ Name = 'agent_fitness'; Port = [int]$Ports.AGENT_PORTS.fitness },
        [pscustomobject]@{ Name = 'agent_coach'; Port = [int]$Ports.AGENT_PORTS.coach },
        [pscustomobject]@{ Name = 'agent_orchestrator'; Port = [int]$Ports.AGENT_PORTS.orchestrator },
        [pscustomobject]@{ Name = 'telegram_mcp'; Port = $TELEGRAM_MCP_PORT }
    )

    $requiredModules = @('fastapi', 'uvicorn', 'a2a', 'mlflow')
    $missingModules = @()
    foreach ($moduleName in $requiredModules) {
        if (-not (Test-PythonModule -PythonPath $Python -ModuleName $moduleName)) {
            $missingModules += $moduleName
        }
    }

    if ($missingModules.Count -gt 0) {
        throw "Python environment is missing required packages: $($missingModules -join ', '). Activate the project env (for example the aiss2026 conda env) or set PY to that interpreter, then install requirements with `pip install -r requirements.txt`."
    }

    Start-PythonService -Name 'mlflow' -Arguments @(
        '-m', 'mlflow', 'server',
        '--host', '127.0.0.1',
        '--port', $MLFLOW_PORT,
        '--backend-store-uri', 'sqlite:///mlflow.db'
    ) -Port $MLFLOW_PORT | Out-Null

    foreach ($server in $MCP_SERVERS) {
        if ($server.Name -eq 'telegram') {
            continue
        }
        Start-PythonService -Name "mcp_$($server.Name)" -Arguments @('-m', "servers.$($server.Name)_mcp") -Port $server.Port | Out-Null
    }

    $hasSentenceTransformers = $true
    & $Python -c 'import sentence_transformers' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        $hasSentenceTransformers = $false
    }

    if ($hasSentenceTransformers) {
        Write-Host 'Building fitness RAG index if missing...'
        & $Python -m scripts.build_fitness_index --if-missing
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'WARNING fitness index unavailable - the fitness agent will degrade gracefully'
        }
    } else {
        Write-Host 'WARNING sentence_transformers missing - skipping fitness index build'
    }

    foreach ($agent in $AGENT_PORTS) {
        $module = if ($agent.Name -eq 'orchestrator') { 'core.orchestrator_agent' } else { "agents.$($agent.Name)_agent" }
        Start-PythonService -Name "agent_$($agent.Name)" -Arguments @('-m', $module) -Port $agent.Port | Out-Null
    }

    Start-PythonService -Name 'fastapi' -Arguments @(
        '-m', 'uvicorn', 'api.main:app',
        '--host', '127.0.0.1',
        '--port', $FASTAPI_PORT,
        '--reload'
    ) -Port $FASTAPI_PORT | Out-Null

    Wait-HttpReady -Url "http://127.0.0.1:$FASTAPI_PORT/api/ping"

    if ($Bridge) {
        $apiId = Get-EnvValue 'TELEGRAM_API_ID'
        $apiHash = Get-EnvValue 'TELEGRAM_API_HASH'
        $session = Get-EnvValue 'TELEGRAM_SESSION_STRING'
        if (-not (Test-RealValue $session)) {
            $session = Get-EnvValue 'TELEGRAM_BRIDGE_SESSION_STRING'
        }

        if ((Test-RealValue $apiId) -and (Test-RealValue $apiHash) -and (Test-RealValue $session)) {
            Start-PythonService -Name 'telegram_bridge' -Arguments @('telegram_bridge.py') | Out-Null
        } else {
            Write-Host 'WARNING Telegram bridge skipped - TELEGRAM_API_ID, TELEGRAM_API_HASH and a session string are required.'
        }
    }

    $webDir = Join-Path $RepoRoot 'web'
    if (-not (Test-Path (Join-Path $webDir 'node_modules'))) {
        Write-Host 'Installing web deps (first run)...'
        Push-Location $webDir
        try {
            & npm ci
        } finally {
            Pop-Location
        }
    }

    Write-Host 'Starting Vite on :5173 (native web app)...'
    Push-Location $webDir
    try {
        & npm run dev
    } finally {
        Pop-Location
    }
}
finally {
    if ($null -ne $PreviousPy) {
        $env:PY = $PreviousPy
    } else {
        Remove-Item Env:PY -ErrorAction SilentlyContinue
    }
    foreach ($proc in $script:StartedProcesses) {
        try {
            if (-not $proc.HasExited) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        } catch {
            # Best effort cleanup only.
        }
    }
}