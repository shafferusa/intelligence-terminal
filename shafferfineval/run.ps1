#Requires -Version 5.1
<#
.SYNOPSIS
    ShafferFinEval launcher for Windows PowerShell.

.DESCRIPTION
    First run creates .venv and installs the two dependencies. Every run
    after that just starts the terminal and opens it once the server is
    actually answering.

    In -AppMode the server runs hidden and the terminal opens in a Chrome or
    Edge app window with no browser chrome. Closing that window stops the
    server, so the icon behaves like an application rather than leaving a
    stray process behind.

.PARAMETER Refresh
    Score the universe before launching. Hits Yahoo and FRED.

.PARAMETER AppMode
    Hidden server plus a chromeless app window. This is what the desktop
    shortcut created by install-app.ps1 uses.

.PARAMETER Port
    Override the port. Defaults to SHAFFERFINEVAL_PORT, then 8501.

.PARAMETER NoBrowser
    Start the server and leave the browser alone.

.EXAMPLE
    .\run.ps1
    Start normally, with logs in this console. Ctrl-C stops it.

.EXAMPLE
    .\run.ps1 -Refresh
    Score everything first, then start.

.EXAMPLE
    .\run.ps1 -AppMode
    Launch as an app window.
#>
[CmdletBinding()]
param(
    [switch] $Refresh,
    [switch] $AppMode,
    [switch] $NoBrowser,
    [int]    $Port = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

# ---------------------------------------------------------------------------
# Paths and settings
# ---------------------------------------------------------------------------

$VenvDir    = Join-Path $PSScriptRoot '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$Streamlit  = Join-Path $VenvDir 'Scripts\streamlit.exe'
$Stamp      = Join-Path $VenvDir '.deps-installed'
$Reqs       = Join-Path $PSScriptRoot 'requirements.txt'

if ($Port -le 0) {
    $Port = 8501
    $envPort = [Environment]::GetEnvironmentVariable('SHAFFERFINEVAL_PORT')
    $parsedPort = 0
    if ($envPort -and [int]::TryParse($envPort, [ref] $parsedPort) -and
            $parsedPort -gt 0 -and $parsedPort -lt 65536) {
        $Port = $parsedPort
    }
}
$Url = "http://127.0.0.1:$Port"

function Write-Step { param([string] $Message) Write-Host "  $Message" -ForegroundColor DarkGray }
function Write-Fail { param([string] $Message) Write-Host $Message -ForegroundColor Red }

# ---------------------------------------------------------------------------
# Find a usable Python
# ---------------------------------------------------------------------------

function Get-SystemPython {
    <#
        The py launcher is the reliable route on Windows: it knows about every
        installed version, and `python` on PATH is often the Microsoft Store
        stub that does nothing but open the Store.
    #>
    $candidates = @()
    $py = Get-Command 'py' -ErrorAction SilentlyContinue
    if ($py) { $candidates += ,@($py.Source, @('-3')) }

    foreach ($name in @('python3', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike '*WindowsApps*') {
            $candidates += ,@($cmd.Source, @())
        }
    }

    foreach ($candidate in $candidates) {
        $exe  = $candidate[0]
        $args = $candidate[1]
        try {
            $check = $args + @('-c', 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)')
            & $exe @check 2>$null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{ Exe = $exe; Args = $args }
            }
        } catch {
            continue
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Environment setup
# ---------------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $python = Get-SystemPython
    if (-not $python) {
        Write-Fail 'ERROR: no Python 3.10 or newer found.'
        Write-Host ''
        Write-Host '  Install it from https://python.org/downloads and tick'
        Write-Host '  "Add Python to PATH" during setup, then run this again.'
        Write-Host ''
        Write-Host '  If Python IS installed, "python" on PATH may be the'
        Write-Host '  Microsoft Store stub. Check with:  py -3 --version'
        exit 1
    }

    Write-Host 'First run: creating .venv and installing dependencies...'
    $venvArgs = $python.Args + @('-m', 'venv', $VenvDir)
    & $python.Exe @venvArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'ERROR: could not create the virtual environment.'
        exit 1
    }
    & $VenvPython -m pip install --quiet --upgrade pip
}

# Install on first run, and again whenever requirements.txt changes. The stamp
# is only written after a SUCCESSFUL install, so a failure retries next time
# instead of leaving a half-built environment that looks ready.
$needsInstall = $true
if (Test-Path -LiteralPath $Stamp) {
    $stampTime = (Get-Item -LiteralPath $Stamp).LastWriteTimeUtc
    $reqTime   = (Get-Item -LiteralPath $Reqs).LastWriteTimeUtc
    $needsInstall = $reqTime -gt $stampTime
}

if ($needsInstall) {
    Write-Step 'Installing streamlit and requests...'
    & $VenvPython -m pip install -r $Reqs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Fail 'ERROR: could not install streamlit and requests.'
        Write-Host '  The two usual causes are no internet connection, or a'
        Write-Host '  corporate proxy blocking PyPI. Check that this works:'
        Write-Host "    $VenvPython -m pip install streamlit"
        Write-Host '  Nothing else about the app needs the network to start.'
        exit 1
    }
    New-Item -ItemType File -Path $Stamp -Force | Out-Null
    Write-Step 'Dependencies ready.'
}

if (-not (Test-Path -LiteralPath $Streamlit)) {
    Write-Fail "ERROR: streamlit.exe is missing from $VenvDir."
    Write-Host '  Delete the .venv folder and run this again to rebuild it.'
    exit 1
}

# ---------------------------------------------------------------------------
# Optional pre-launch refresh
# ---------------------------------------------------------------------------

if ($Refresh) {
    Write-Host 'Refreshing scores before launch (this hits Yahoo and FRED)...'
    & $VenvPython (Join-Path $PSScriptRoot 'daily_job.py')
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Refresh failed; starting anyway with whatever is already stored.' `
            -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Browser discovery, for the chromeless app window
# ---------------------------------------------------------------------------

function Get-AppModeBrowser {
    <# Chrome and Edge both support --app=, which drops all browser chrome. #>
    # ProgramFiles(x86) is absent on ARM64 and 32-bit Windows, and Join-Path
    # with a null root throws. Build the list defensively rather than letting
    # a missing environment variable take the whole launcher down.
    $roots = @(
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:LOCALAPPDATA
    ) | Where-Object { $_ }

    $relatives = @(
        'Google\Chrome\Application\chrome.exe',
        'Microsoft\Edge\Application\msedge.exe'
    )

    foreach ($relative in $relatives) {
        foreach ($root in $roots) {
            $path = Join-Path $root $relative
            if (Test-Path -LiteralPath $path) { return $path }
        }
    }
    return $null
}

# ---------------------------------------------------------------------------
# Start the server
# ---------------------------------------------------------------------------

Write-Host "ShafferFinEval starting at $Url"

$serverArgs = @('run', 'app.py', '--server.port', "$Port")
$server = $null

if ($AppMode) {
    $server = Start-Process -FilePath $Streamlit -ArgumentList $serverArgs `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
} else {
    Write-Host 'Press Ctrl-C to stop.'
    $server = Start-Process -FilePath $Streamlit -ArgumentList $serverArgs `
        -WorkingDirectory $PSScriptRoot -NoNewWindow -PassThru
}

# Wait for the server to actually answer before opening anything, so the first
# thing seen is the terminal rather than a connection error.
# A deadline rather than an iteration count: each probe can itself block for
# up to the request timeout, so counting loops would not bound the wait.
$ready = $false
$deadline = (Get-Date).AddSeconds(45)
while ((Get-Date) -lt $deadline) {
    if ($server.HasExited) { break }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$Url/_stcore/health" `
            -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        # Still starting. Fall through to the sleep below.
    }
    Start-Sleep -Milliseconds 500
}

if ($server.HasExited) {
    Write-Fail "ERROR: the server exited immediately (code $($server.ExitCode))."
    Write-Host "  Port $Port may already be in use. Try:  .\run.ps1 -Port 8502"
    exit 1
}

if (-not $ready) {
    Write-Host 'The server did not answer within 45 seconds; leaving it running.' `
        -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Open it
# ---------------------------------------------------------------------------

$browserProcess = $null

if (-not $NoBrowser -and $ready) {
    if ($AppMode) {
        $browser = Get-AppModeBrowser
        if ($browser) {
            $profileDir = Join-Path $env:LOCALAPPDATA 'ShafferFinEval\browser'
            $browserProcess = Start-Process -FilePath $browser -PassThru -ArgumentList @(
                "--app=$Url",
                "--user-data-dir=$profileDir",
                '--no-first-run',
                '--no-default-browser-check'
            )
        } else {
            Write-Host 'Chrome/Edge not found; opening the default browser instead.' `
                -ForegroundColor Yellow
            Start-Process $Url
        }
    } else {
        Start-Process $Url
    }
}

# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------

try {
    if ($AppMode -and $browserProcess) {
        # App semantics: closing the window stops the server. Without this the
        # hidden server would linger with nothing attached to it.
        Wait-Process -Id $browserProcess.Id
        Write-Step 'Window closed; stopping the server.'
    } else {
        Wait-Process -Id $server.Id
    }
} finally {
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
