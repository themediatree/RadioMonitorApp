# =============================================================================
# RadioMonitor -- AUDIOREC Scheduled Task Setup
# Run once as Administrator on AUDIOREC.
# Creates all tasks in DISABLED state. Enable individually when ready.
#
# Tasks created:
#   RadioMonitor-WebApp         -- Uvicorn web app, starts on boot
#   RadioMonitor-Activation     -- pending->active job, nightly at 23:45
# =============================================================================

$PythonExe  = "D:\RadioMonitorApp\.venv\Scripts\python.exe"
$AppDir     = "D:\RadioMonitorApp"
$LogDir     = "D:\RadioMonitor\logs"

# Create log directory if it doesn't exist
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# -----------------------------------------------------------------------------
# Task 1: Web App (Uvicorn)
# Trigger: At system startup
# Action:  uvicorn main:app --host 0.0.0.0 --port 8000
# -----------------------------------------------------------------------------
$action1 = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "-m uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info" `
    -WorkingDirectory $AppDir

$trigger1 = New-ScheduledTaskTrigger -AtStartup

$settings1 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -StartWhenAvailable

$principal1 = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName   "RadioMonitor-WebApp" `
    -TaskPath   "\RadioMonitor\" `
    -Action     $action1 `
    -Trigger    $trigger1 `
    -Settings   $settings1 `
    -Principal  $principal1 `
    -Description "RadioMonitor FastAPI web app via Uvicorn. Disable and start manually during development." `
    -Force

Disable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-WebApp"
Write-Host "Created: RadioMonitor-WebApp (DISABLED)" -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# Task 2: Activation Job (pending -> active)
# Trigger: Daily at 23:45
# Action:  python scripts/activate_commercials.py
# Must run BEFORE the pipeline's midnight library-manager rebuild.
# -----------------------------------------------------------------------------
$action2 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& '$PythonExe' 'scripts\activate_commercials.py' *>> '$LogDir\activation.log'`"" `
    -WorkingDirectory $AppDir

$trigger2 = New-ScheduledTaskTrigger -Daily -At "23:45"

$settings2 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

$principal2 = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName   "RadioMonitor-Activation" `
    -TaskPath   "\RadioMonitor\" `
    -Action     $action2 `
    -Trigger    $trigger2 `
    -Settings   $settings2 `
    -Principal  $principal2 `
    -Description "Flips Commercial.Status from pending to active for campaigns whose StartDate has arrived. Must run before the pipeline midnight rebuild (00:00)." `
    -Force

Disable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-Activation"
Write-Host "Created: RadioMonitor-Activation (DISABLED)" -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
Write-Host ""
Write-Host "All tasks created under \RadioMonitor\ task folder, DISABLED." -ForegroundColor Cyan
Write-Host ""
Write-Host "To view:   Get-ScheduledTask -TaskPath '\RadioMonitor\'"
Write-Host "To enable: Enable-ScheduledTask -TaskPath '\RadioMonitor\' -TaskName 'RadioMonitor-WebApp'"
Write-Host "           Enable-ScheduledTask -TaskPath '\RadioMonitor\' -TaskName 'RadioMonitor-Activation'"
Write-Host "To run now (test): Start-ScheduledTask -TaskPath '\RadioMonitor\' -TaskName 'RadioMonitor-Activation'"
Write-Host ""
Write-Host "Pipeline tasks (library-manager, puller, recorder, detector)"
Write-Host "are set up on AUDIOPROC by the pipeline thread -- coordinate timing"
Write-Host "so their midnight rebuild runs AFTER this activation job (23:45 < 00:00)."

# -----------------------------------------------------------------------------
# Task 3: Monthly Token Credit (Enterprise/Premium allocations)
# Trigger: 1st of every month at 00:30 (after midnight rebuild)
# -----------------------------------------------------------------------------
$action3 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& '$PythonExe' 'scripts\credit_monthly_tokens.py' *>> '$LogDir\monthly_credit.log'`"" `
    -WorkingDirectory $AppDir

# Monthly trigger: 1st day of each month at 00:30
$trigger3 = New-ScheduledTaskTrigger -Weekly -WeeksInterval 4 -DaysOfWeek Monday -At "00:30"
# Note: True monthly triggers require XML; use Task Scheduler UI to change to
# "Monthly" -> "Day 1" after running this script.

$settings3 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   "RadioMonitor-MonthlyCredit" `
    -TaskPath   "\RadioMonitor\" `
    -Action     $action3 `
    -Trigger    $trigger3 `
    -Settings   $settings3 `
    -Principal  $principal2 `
    -Description "Credits monthly token allocations for Enterprise and Premium subscribers. Edit trigger in Task Scheduler UI to run on Day 1 of each month." `
    -Force

Disable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-MonthlyCredit"
Write-Host "Created: RadioMonitor-MonthlyCredit (DISABLED)" -ForegroundColor Yellow
Write-Host "  NOTE: Open Task Scheduler UI and change trigger to Monthly -> Day 1" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Task 4: Daily Database + Audio Archive Backup
# Trigger: Daily at 02:00
# -----------------------------------------------------------------------------
$action4 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& 'D:\RadioMonitorApp\scripts\backup_database.ps1' *>> 'D:\RadioMonitor\logs\backup.log'`"" `
    -WorkingDirectory $AppDir

$trigger4 = New-ScheduledTaskTrigger -Daily -At "02:00"

$settings4 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   "RadioMonitor-Backup" `
    -TaskPath   "\RadioMonitor\" `
    -Action     $action4 `
    -Trigger    $trigger4 `
    -Settings   $settings4 `
    -Principal  $principal2 `
    -Description "Daily full database backup (30-day retention) and audio_archive mirror, both to D:\Backups. Strongly recommend D:\Backups eventually points to a separate physical disk or network share." `
    -Force

Disable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-Backup"
Write-Host "Created: RadioMonitor-Backup (DISABLED)" -ForegroundColor Yellow
Write-Host "  NOTE: D:\Backups should ideally be a separate disk/share from D:\ -- currently same volume as the DB itself" -ForegroundColor Cyan

# -----------------------------------------------------------------------------
# Task 5: Weekly Log Rotation
# Trigger: Weekly, Sunday at 03:00
# -----------------------------------------------------------------------------
$action5 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"& 'D:\RadioMonitorApp\scripts\rotate_logs.ps1' *>> 'D:\RadioMonitor\logs\rotate_logs.log'`"" `
    -WorkingDirectory $AppDir

$trigger5 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "03:00"

$settings5 = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName   "RadioMonitor-LogRotation" `
    -TaskPath   "\RadioMonitor\" `
    -Action     $action5 `
    -Trigger    $trigger5 `
    -Settings   $settings5 `
    -Principal  $principal2 `
    -Description "Rotates any log file in D:\RadioMonitor\logs over 50MB, deletes rotated logs older than 30 days." `
    -Force

Disable-ScheduledTask -TaskPath "\RadioMonitor\" -TaskName "RadioMonitor-LogRotation"
Write-Host "Created: RadioMonitor-LogRotation (DISABLED)" -ForegroundColor Yellow

Write-Host ""
Write-Host "All scheduled tasks created under \RadioMonitor\, all DISABLED." -ForegroundColor Cyan
Write-Host "Review each task in Task Scheduler, then enable when ready:" -ForegroundColor Cyan
Write-Host "  Enable-ScheduledTask -TaskPath '\RadioMonitor\' -TaskName '<TaskName>'"
