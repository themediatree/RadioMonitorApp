# =============================================================================
# scripts/rotate_logs.ps1
# =============================================================================
# Rotates any .log file over 50MB in D:\RadioMonitor\logs, deletes rotated
# logs older than 30 days. Run weekly via Task Scheduler.
# =============================================================================

$LogDir = "D:\RadioMonitor\logs"
$MaxSizeMB = 50
$RetentionDays = 30

if (-not (Test-Path $LogDir)) {
    Write-Host "Log directory $LogDir does not exist -- nothing to rotate."
    exit 0
}

$rotated = 0
Get-ChildItem -Path $LogDir -Filter "*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt ($MaxSizeMB * 1MB) } |
    ForEach-Object {
        $stamp = Get-Date -Format "yyyyMMdd"
        $newName = "$($_.FullName).$stamp"
        Rename-Item -Path $_.FullName -NewName $newName -Force
        Write-Host "Rotated: $($_.Name) -> $(Split-Path $newName -Leaf)"
        $rotated++
    }

$cutoff = (Get-Date).AddDays(-$RetentionDays)
$removed = 0
Get-ChildItem -Path $LogDir -Filter "*.log.*" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    ForEach-Object {
        Remove-Item $_.FullName -Force
        Write-Host "Removed old rotated log: $($_.Name)"
        $removed++
    }

Write-Host "Log rotation complete. Rotated $rotated file(s), removed $removed old rotated log(s)."
