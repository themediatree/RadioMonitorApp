# =============================================================================
# scripts/backup_database.ps1
# =============================================================================
# Daily SQL Server backup with 30-day retention.
# SQL Server Express has no SQL Server Agent (this runs via Windows Task
# Scheduler instead) and does NOT support backup compression -- that's an
# Enterprise/Standard-only feature. Backups will be roughly the size of the
# live database; budget disk space accordingly.
#
# Usage: powershell -File backup_database.ps1
# =============================================================================

$ErrorActionPreference = "Stop"

$BackupDir = "D:\Backups"
$DbServer = "localhost\SQLEXPRESS"
$DbName = "RadioMonitor"
$DbUser = "radiomonitor_user"
$RetentionDays = 30

# Read DB password from .env rather than hardcoding it here.
# Values may be quoted (single or double) to safely contain '#' or other
# special characters -- a quoted value is taken verbatim between the
# matching quotes, with anything after the closing quote treated as a
# trailing comment. An unquoted value stops at the first '#' or end of line.
$EnvPath = "D:\RadioMonitorApp\.env"
$DbPassword = $null
if (Test-Path $EnvPath) {
    Get-Content $EnvPath | ForEach-Object {
        if ($_ -match '^\s*DB_PASSWORD\s*=\s*(.*)$') {
            $rest = $Matches[1]
            if ($rest -match "^'([^']*)'") {
                $DbPassword = $Matches[1]
            } elseif ($rest -match '^"([^"]*)"') {
                $DbPassword = $Matches[1]
            } else {
                # Unquoted -- stop at the first '#' (comment) or trim trailing whitespace
                $DbPassword = ($rest -split '#')[0].Trim()
            }
        }
    }
}
if (-not $DbPassword) {
    Write-Error "Could not read DB_PASSWORD from $EnvPath -- aborting backup."
    exit 1
}

# Ensure backup directory exists
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$Timestamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$BackupFile = Join-Path $BackupDir "RadioMonitor_$Timestamp.bak"

Write-Host "Starting backup to $BackupFile ..."

$Query = @"
BACKUP DATABASE [$DbName]
TO DISK = N'$BackupFile'
WITH STATS = 10, CHECKSUM;
"@

sqlcmd -S $DbServer -U $DbUser -P $DbPassword -d $DbName -C -Q $Query -b

if ($LASTEXITCODE -ne 0) {
    Write-Error "Backup FAILED -- sqlcmd exit code $LASTEXITCODE"
    exit 1
}

$Size = (Get-Item $BackupFile).Length / 1MB
Write-Host ("Backup complete: {0:N1} MB" -f $Size)

# -----------------------------------------------------------------------------
# Retention -- delete backups older than $RetentionDays
# -----------------------------------------------------------------------------
$cutoff = (Get-Date).AddDays(-$RetentionDays)
$removed = 0
Get-ChildItem -Path $BackupDir -Filter "RadioMonitor_*.bak" |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        Remove-Item $_.FullName -Force
        $removed++
    }

Write-Host "Retention cleanup: removed $removed backup(s) older than $RetentionDays days."

# -----------------------------------------------------------------------------
# Also back up the audio archive -- these files cannot be regenerated if lost
# -----------------------------------------------------------------------------
$ArchiveSource = "D:\RadioMonitor\audio_archive"
$ArchiveBackupRoot = "D:\Backups\audio_archive"

if (Test-Path $ArchiveSource) {
    Write-Host "Mirroring audio_archive to $ArchiveBackupRoot ..."
    robocopy $ArchiveSource $ArchiveBackupRoot /MIR /NFL /NDL /NJH /NJS /R:2 /W:5
    Write-Host "Audio archive backup complete."
} else {
    Write-Warning "Audio archive source not found at $ArchiveSource -- skipped."
}

Write-Host "Backup job finished successfully."
