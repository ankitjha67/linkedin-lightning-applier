<#
.SYNOPSIS
    Run LinkedIn Lightning Applier 24/7 on Windows, via Task Scheduler.

.DESCRIPTION
    Windows has no cron, so this registers the same two triggers Task Scheduler
    calls them:

      * at logon   — start the bot when you sign in
      * every N min — check it is alive, restart it if it is not

    The bot's own loop is already continuous, so this is a supervisor rather
    than a scheduler. `autopilot watch` holds a lock, so a trigger that fires
    while the bot is running does nothing. Two Chrome sessions signed into one
    LinkedIn account is the fastest way to look like a bot, and a naive
    repeating task causes it by default.

.PARAMETER Every
    Minutes between liveness checks. Default 5.

.PARAMETER Remove
    Unregister the tasks.

.PARAMETER Status
    Show the registered tasks and whether the bot is up.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\setup_autopilot.ps1
    powershell -ExecutionPolicy Bypass -File tools\setup_autopilot.ps1 -Every 10
    powershell -ExecutionPolicy Bypass -File tools\setup_autopilot.ps1 -Remove
#>
param(
    [int]$Every = 5,
    [switch]$Remove,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'

$ProjectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$TaskBoot   = 'LightningApplier-Autopilot-Logon'
$TaskWatch  = 'LightningApplier-Autopilot-Watch'

# Prefer the project venv, the way the shell installer does.
$Python = $null
foreach ($candidate in @(
    (Join-Path $ProjectDir 'venv\Scripts\pythonw.exe'),
    (Join-Path $ProjectDir '.venv\Scripts\pythonw.exe'),
    (Join-Path $ProjectDir 'venv\Scripts\python.exe'),
    (Join-Path $ProjectDir '.venv\Scripts\python.exe'))) {
    if (Test-Path $candidate) { $Python = $candidate; break }
}
if (-not $Python) {
    # pythonw runs without opening a console window every few minutes.
    $Python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $Python) { $Python = (Get-Command python.exe).Source }
}

if ($Remove) {
    foreach ($name in @($TaskBoot, $TaskWatch)) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "Removed scheduled task: $name"
        }
    }
    Write-Host ''
    Write-Host 'The bot itself is still running if it was. Stop it with:'
    Write-Host '  python tools\autopilot.py stop'
    exit 0
}

if ($Status) {
    foreach ($name in @($TaskBoot, $TaskWatch)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            $info = Get-ScheduledTaskInfo -TaskName $name
            Write-Host ("{0}: {1}  last run {2}  result {3}" -f `
                $name, $task.State, $info.LastRunTime, $info.LastTaskResult)
        } else {
            Write-Host "$name : not registered"
        }
    }
    Write-Host ''
    & $Python (Join-Path $ProjectDir 'tools\autopilot.py') status
    exit 0
}

if ($Every -lt 1 -or $Every -gt 59) {
    Write-Error "The check interval must be 1-59 minutes (got $Every)."
    exit 1
}

$ConfigPath = Join-Path $ProjectDir 'config.yaml'
if (-not (Test-Path $ConfigPath)) {
    Write-Error "config.yaml does not exist in $ProjectDir. The bot cannot log in without it. Run: lla setup"
    exit 1
}

$Script    = Join-Path $ProjectDir 'tools\autopilot.py'
$Arguments = "`"$Script`" watch -c `"$ConfigPath`""
$Action    = New-ScheduledTaskAction -Execute $Python -Argument $Arguments -WorkingDirectory $ProjectDir

# Defaults assume a laptop: keep running on battery, and do not stop the task
# after three days, which is what Task Scheduler does unless told otherwise.
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$LogonTrigger = New-ScheduledTriggerAtLogon -User $env:USERNAME

$WatchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Every) `
    -RepetitionDuration ([TimeSpan]::MaxValue)

foreach ($pair in @(@($TaskBoot, $LogonTrigger), @($TaskWatch, $WatchTrigger))) {
    $name    = $pair[0]
    $trigger = $pair[1]
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false   # idempotent
    }
    Register-ScheduledTask -TaskName $name -Action $Action -Trigger $trigger `
        -Settings $Settings -Description 'LinkedIn Lightning Applier 24/7 autopilot' | Out-Null
    Write-Host "Registered scheduled task: $name"
}

Write-Host ''
Write-Host "Autopilot is installed. The bot runs continuously; Task Scheduler"
Write-Host "restarts it if it dies and starts it when you log on. A trigger that"
Write-Host "fires while it is already running does nothing."
Write-Host ''
Write-Host "  python tools\autopilot.py status     is it up, and applies today"
Write-Host "  Get-Content logs\autopilot_*.log -Wait -Tail 20"
Write-Host "  powershell -File tools\setup_autopilot.ps1 -Remove"
Write-Host ''
Write-Host "Put your API key in $ProjectDir\.env (gitignored) rather than in"
Write-Host "your shell profile — Task Scheduler does not load your profile."

# Start it now, so 24/7 begins now rather than at the next trigger.
& $Python $Script start -c $ConfigPath
