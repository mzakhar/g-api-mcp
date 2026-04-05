# Register g-api-mcp-sync as a Windows Task Scheduler job.
# Runs every minute as the current user (no admin required).
#
# Prerequisites: pip install -e . must have been run so g-api-mcp-sync is on PATH.
# Usage: .\scripts\register-task-scheduler.ps1

$TaskName = "g-api-mcp-sync"
$ErrorActionPreference = "Stop"

# Use pythonw.exe (no-console Python host) to avoid any terminal window flash
$PythonDir = Split-Path (Get-Command python -ErrorAction Stop).Source
$PythonW = Join-Path $PythonDir "pythonw.exe"
if (-not (Test-Path $PythonW)) { throw "pythonw.exe not found at $PythonW" }
Write-Host "Using pythonw.exe at: $PythonW"

$Action = New-ScheduledTaskAction -Execute $PythonW -Argument "-m g_api_mcp.sync"

# Repeat every 1 minute indefinitely
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1)

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -RunLevel Limited `
    -Force | Out-Null

Write-Host "Registered '$TaskName' -- polls Google Tasks every 1 minute."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  View status  : Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "  Run now      : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  Remove       : Unregister-ScheduledTask -TaskName '$TaskName'"
