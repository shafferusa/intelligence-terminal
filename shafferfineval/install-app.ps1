#Requires -Version 5.1
<#
.SYNOPSIS
    Install ShafferFinEval as a Windows app: Start menu and desktop icons.

.DESCRIPTION
    Creates shortcuts that launch run.ps1 in -AppMode, so the terminal opens
    in a chromeless window with the ShafferFinEval icon, and closing that
    window shuts the server down.

    Everything is per-user. Nothing is written outside your own profile, no
    administrator rights are needed, and -Uninstall removes exactly what was
    created.

.PARAMETER Desktop
    Also put an icon on the desktop. Off by default.

.PARAMETER StartWithWindows
    Start the server automatically when you log in.

.PARAMETER ScheduleRefresh
    Register a scheduled task running daily_job.py at 16:30 on weekdays.
    This is what makes snapshots accumulate; without it every ML panel
    stays on INSUFFICIENT DATA forever.

.PARAMETER RefreshTime
    Time for that task, as HH:mm. Defaults to 16:30.

.PARAMETER EquitiesOnly
    Score equities only, skipping the non-equity macro pass. Use this when
    FRED_API_KEY is not set: without it the macro engines have no data, and
    the pass costs a minute to score almost nothing. Add the key later and
    re-run this without the switch to turn the macro pass back on.

.PARAMETER Uninstall
    Remove the shortcuts, the startup entry and the scheduled task.

.EXAMPLE
    .\install-app.ps1 -Desktop -ScheduleRefresh
    The usual first-time setup.

.EXAMPLE
    .\install-app.ps1 -Desktop -ScheduleRefresh -EquitiesOnly
    Same, but equities only -- no FRED key needed.

.EXAMPLE
    .\install-app.ps1 -Uninstall
    Remove everything this script created.
#>
[CmdletBinding()]
param(
    [switch] $Desktop,
    [switch] $StartWithWindows,
    [switch] $ScheduleRefresh,
    [switch] $EquitiesOnly,
    [string] $RefreshTime = '16:30',
    [switch] $Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$AppName   = 'ShafferFinEval'
$TaskName  = 'ShafferFinEval Daily Refresh'
$Root      = $PSScriptRoot
$RunScript = Join-Path $Root 'run.ps1'
$IconPath  = Join-Path $Root 'assets\shafferfineval.ico'

$StartMenu    = Join-Path $env:APPDATA   'Microsoft\Windows\Start Menu\Programs'
$StartupDir   = Join-Path $env:APPDATA   'Microsoft\Windows\Start Menu\Programs\Startup'
$DesktopDir   = [Environment]::GetFolderPath('Desktop')

$StartMenuLink = Join-Path $StartMenu  "$AppName.lnk"
$DesktopLink   = Join-Path $DesktopDir "$AppName.lnk"
$StartupLink   = Join-Path $StartupDir "$AppName.lnk"

function Write-Ok   { param([string] $m) Write-Host "  OK    $m" -ForegroundColor Green }
function Write-Info { param([string] $m) Write-Host "  ..    $m" -ForegroundColor DarkGray }
function Write-Warn { param([string] $m) Write-Host "  WARN  $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------

if ($Uninstall) {
    Write-Host "Removing $AppName..."
    foreach ($link in @($StartMenuLink, $DesktopLink, $StartupLink)) {
        if (Test-Path -LiteralPath $link) {
            Remove-Item -LiteralPath $link -Force
            Write-Ok "removed $link"
        }
    }
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Ok "removed scheduled task '$TaskName'"
    }
    Write-Host ''
    Write-Host 'Done. The code, the database and .venv were left alone.'
    Write-Host "Delete $Root yourself if you want those gone too."
    exit 0
}

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

if (-not (Test-Path -LiteralPath $RunScript)) {
    Write-Host "ERROR: run.ps1 not found next to this script." -ForegroundColor Red
    Write-Host "  Run install-app.ps1 from inside the shafferfineval folder."
    exit 1
}

if (-not (Test-Path -LiteralPath $IconPath)) {
    Write-Warn 'assets\shafferfineval.ico is missing; shortcuts will use the'
    Write-Warn 'default PowerShell icon. Regenerate it with: python make_icon.py'
    $IconPath = $null
}

Write-Host "Installing $AppName for $env:USERNAME"
Write-Info "source: $Root"

# ---------------------------------------------------------------------------
# Shortcuts
# ---------------------------------------------------------------------------

function New-AppShortcut {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Description,
        [string[]] $ExtraArgs = @()
    )

    $shell = New-Object -ComObject WScript.Shell
    try {
        $shortcut = $shell.CreateShortcut($Path)

        # -WindowStyle Hidden keeps the PowerShell console out of the way, so
        # what you see is the app window and nothing else.
        $argumentList = @(
            '-NoProfile'
            '-ExecutionPolicy', 'Bypass'
            '-WindowStyle', 'Hidden'
            '-File', "`"$RunScript`""
            '-AppMode'
        ) + $ExtraArgs

        $shortcut.TargetPath       = (Get-Command powershell.exe).Source
        $shortcut.Arguments        = $argumentList -join ' '
        $shortcut.WorkingDirectory = $Root
        $shortcut.Description      = $Description
        $shortcut.WindowStyle      = 7          # start minimized
        if ($IconPath) { $shortcut.IconLocation = "$IconPath,0" }
        $shortcut.Save()
        Write-Ok "created $Path"
    } finally {
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
    }
}

New-AppShortcut -Path $StartMenuLink `
    -Description 'Personal multi-asset research and risk terminal'

if ($Desktop) {
    New-AppShortcut -Path $DesktopLink `
        -Description 'Personal multi-asset research and risk terminal'
}

if ($StartWithWindows) {
    New-AppShortcut -Path $StartupLink `
        -Description 'Start ShafferFinEval at login'
}

# ---------------------------------------------------------------------------
# Scheduled refresh -- the part that makes the ML Lab possible
# ---------------------------------------------------------------------------

if ($ScheduleRefresh) {
    $venvPython = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Warn 'No .venv yet, so the scheduled task cannot be created.'
        Write-Warn 'Run .\run.ps1 once to build it, then re-run this with'
        Write-Warn '-ScheduleRefresh.'
    } else {
        $parsed = [datetime]::MinValue
        $formats = [string[]] @('HH:mm', 'H:mm')
        if (-not [datetime]::TryParseExact($RefreshTime, $formats,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None, [ref]$parsed)) {
            Write-Host "ERROR: -RefreshTime '$RefreshTime' is not HH:mm." -ForegroundColor Red
            exit 1
        }

        $jobArguments = 'daily_job.py'
        if ($EquitiesOnly) { $jobArguments = 'daily_job.py --equities-only' }

        $action = New-ScheduledTaskAction -Execute $venvPython `
            -Argument $jobArguments -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -Weekly `
            -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday `
            -At $parsed
        # A laptop that was asleep at 16:30 should still take the snapshot
        # when it wakes: a missed weekday is evidence that cannot be recovered.
        $settings = New-ScheduledTaskSettingsSet `
            -StartWhenAvailable `
            -DontStopIfGoingOnBatteries `
            -AllowStartIfOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2)

        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }

        Register-ScheduledTask -TaskName $TaskName -Action $action `
            -Trigger $trigger -Settings $settings `
            -Description 'Immutable daily Shaffer score snapshot' | Out-Null
        Write-Ok "scheduled '$TaskName' weekdays at $RefreshTime"
        if ($EquitiesOnly) {
            Write-Ok 'equities only -- the macro pass is skipped'
        }

        if (-not $EquitiesOnly -and
                -not [Environment]::GetEnvironmentVariable('FRED_API_KEY', 'User')) {
            Write-Warn 'FRED_API_KEY is not set for your user account, so the'
            Write-Warn 'macro engines will stay dark in scheduled runs. Either set it:'
            Write-Warn '  [Environment]::SetEnvironmentVariable("FRED_API_KEY","<key>","User")'
            Write-Warn 'or re-run this with -EquitiesOnly to skip the pass entirely.'
        }
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

Write-Host ''
Write-Host "$AppName installed." -ForegroundColor Green
Write-Host '  Start menu: search for "ShafferFinEval".'
Write-Host '  Pin it to the taskbar by right-clicking the tile.'
if (-not $ScheduleRefresh) {
    Write-Host ''
    Write-Host '  Nothing is scheduled yet, so no snapshots will accumulate.' -ForegroundColor Yellow
    Write-Host '  Add the daily job with:  .\install-app.ps1 -ScheduleRefresh'
}
Write-Host ''
Write-Host '  Remove it all again with:  .\install-app.ps1 -Uninstall'
