$Source      = "C:\Users\jogreenw\Desktop\StillPointVaults\WorkNotes2026-2"
$Destination = "C:\Users\jogreenw\OneDrive - Capgemini\Documents\Vaults\WorkNotes2026-2"
$LogFolder   = "C:\Users\jogreenw\OneDrive - Capgemini\Documents\Vaults\Logs"

# Make sure log folder exists
if (!(Test-Path $LogFolder)) {
    New-Item -ItemType Directory -Path $LogFolder | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile   = Join-Path $LogFolder "backup-$Timestamp.log"

# /MIR  = mirror source to destination (includes deletes)
# /Z    = restartable mode
# /R:3  = retry 3 times on failure
# /W:5  = wait 5 seconds between retries
# /FFT  = tolerate time differences between file systems
# /XA:H = skip hidden files (optional)
# /XD   = exclude dirs (example)
robocopy $Source $Destination /MIR /Z /R:3 /W:5 /FFT /LOG:$LogFile

<#
## 2. Wire it up to run every hour (Task Scheduler)

1. Press **Win + R**, type `taskschd.msc`, press Enter.
2. On the right, click **Create Task…** (not “Basic Task”).

### General tab

* **Name:** `Hourly Folder Backup`
* Check **Run whether user is logged on or not**
* Check **Run with highest privileges**

### Triggers tab

1. Click **New…**
2. Settings:

   * **Begin the task:** On a schedule
   * **Daily**
   * **Start:** (today, now)
   * Check **Repeat task every:** `1 hour`
   * **For a duration of:** `Indefinitely`
3. Click **OK**

### Actions tab

1. Click **New…**
2. **Action:** Start a program
3. **Program/script:**

   ```text
   powershell.exe
   ```
4. **Add arguments (optional):**

   ```text
   -ExecutionPolicy Bypass -File "C:\Scripts\backup.ps1"
   ```
5. Click **OK**

### Conditions / Settings

Optional but nice:

* **Settings** tab:

  * Check **Run task as soon as possible after a scheduled start is missed**
* **Conditions** tab:

  * If it’s a desktop, you might *uncheck* “Start the task only if the computer is on AC power” (for laptops you might leave it).

Click **OK** to save the task. It may prompt for your password (so it can run in the background).

---

## 3. Test it once manually

In Task Scheduler:

* Right-click **Hourly Folder Backup → Run**
* Confirm files show up in `D:\Backups\MyApp`
* Check `D:\Backups\Logs` for a log file

#>
