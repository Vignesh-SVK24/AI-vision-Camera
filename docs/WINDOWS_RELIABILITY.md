# Windows Unattended Reliability & Power Configuration

Follow these steps so the Windows PC runs for weeks without sleeping, automatically recovers after crashes or frame stalls, and backs up event history weekly.

---

## 1. Windows Power Settings (Never Sleep)

Run in an **elevated PowerShell / Command Prompt**:

```powershell
# Switch to High Performance or Balanced and disable sleep/hibernation on AC power
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change disk-timeout-ac 0
powercfg /change monitor-timeout-ac 15
powercfg /hibernate off
```

Also check:
- **Network Adapter Power Saving**: Device Manager -> Network Adapters -> Properties -> Power Management -> uncheck *"Allow the computer to turn off this device to save power"* so RTSP TCP stays connected continuously.
- **USB Selective Suspend**: Control Panel -> Power Options -> Edit Plan Settings -> Advanced -> USB settings -> **Disabled** (if using an external USB backup drive).
- **Windows Update Active Hours**: Settings -> Windows Update -> Advanced Options -> configure Active Hours and pause unexpected forced restarts when unattended.

---

## 2. Startup Services with Auto-Restart (`tools/service_runner.ps1`)

Register `VisionGuard-Engine`, `VisionGuard-API`, `VisionGuard-go2rtc`, and `VisionGuard-WeeklyBackup` using Task Scheduler (built-in) or NSSM:

```powershell
# Preview startup registration without making changes
.\tools\service_runner.ps1 -Install -BackupDest "E:\VisionBackups" -WhatIf

# Install with Windows Task Scheduler (auto-start at boot, restart every 1m on crash)
.\tools\service_runner.ps1 -Install -BackupDest "E:\VisionBackups"

# Or install using NSSM if nssm.exe is installed on PATH:
.\tools\service_runner.ps1 -Install -UseNssm -BackupDest "E:\VisionBackups"

# Inspect status anytime:
.\tools\service_runner.ps1 -Status
```

---

## 3. Runtime Watchdogs & Disk-Space Guard

- **Graceful Shutdown**: `tools/run_engine.py` traps `SIGINT`, `SIGTERM`, and Windows `SIGBREAK`, draining workers and closing SQLite cleanly in LIFO order.
- **Frame-Stall Watchdog (`ENGINE_FRAME_STALL_SECONDS=30`)**: If camera frames stop advancing for more than 30s, `FrameWatchdog` records a `system_error` event and `EngineSupervisor` restarts the pipeline automatically with exponential backoff.
- **Disk-Space Guard (`DISK_LOW_WARN_MB=2048`, `DISK_CRITICAL_STOP_MB=512`)**:
  - Logs/records a single low-space warning once free space drops below `2048 MB`.
  - Pauses snapshot saving once free space drops below `512 MB` so the OS disk never fills up.

---

## 4. Weekly External Drive Backup (`tools/backup_db.py`)

Test a hot WAL-consistent SQLite backup immediately:

```powershell
python tools\backup_db.py --dest E:\VisionBackups --keep 8
```

This uses `sqlite3.Connection.backup()` (safe while the engine and API are running), verifies `PRAGMA integrity_check == ok`, and keeps the newest 8 weekly snapshots.

---

## 5. Multi-Day / 24-Hour Soak Test (`tools/soak_test.py`)

Run a 24-hour soak test (or 7-day `--days 7` soak test) logging CPU %, RSS memory MB, SQLite counts, and free disk space:

```powershell
python tools\soak_test.py --duration-seconds 86400 --interval-seconds 60 --output logs\soak_24h.csv --max-rss-growth-mb 50
```
