<#
    TU DONG CHAY BOT KHI DANG NHAP WINDOWS
    --------------------------------------
        .\scripts\install_autostart.ps1              # cai (Task Scheduler)
        .\scripts\install_autostart.ps1 -Remove      # go bo
        .\scripts\install_autostart.ps1 -Startup     # dung thu muc Startup thay vi Task Scheduler

    Task Scheduler duoc khuyen dung: chay ngay ca khi may vua khoi dong lai,
    va tu chay lai neu Bot bi tat.
#>

param(
    [switch]$Remove,
    [switch]$Startup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$taskName = "BookingDeadlineBot"
$vbs = Join-Path $root "scripts\start_bot_hidden.vbs"
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "BookingDeadlineBot.lnk"

if ($Remove) {
    try { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; Write-Host "Da go scheduled task." }
    catch { Write-Host "Khong co scheduled task nao ten $taskName" }
    if (Test-Path $startupLnk) { Remove-Item $startupLnk; Write-Host "Da go shortcut Startup." }
    exit 0
}

if (-not (Test-Path (Join-Path $root ".venv\Scripts\pythonw.exe"))) {
    Write-Host "Chua cai dat. Chay scripts\install.ps1 truoc." -ForegroundColor Red
    exit 1
}

if ($Startup) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($startupLnk)
    $lnk.TargetPath = "wscript.exe"
    $lnk.Arguments = """$vbs"""
    $lnk.WorkingDirectory = $root
    $lnk.Description = "Booking Deadline Bot"
    $lnk.Save()
    Write-Host "Da tao shortcut trong thu muc Startup:" -ForegroundColor Green
    Write-Host "  $startupLnk"
    exit 0
}

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument """$vbs""" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Tu dong quan ly deadline booking xuat khau" -Force | Out-Null

Write-Host "Da dang ky scheduled task '$taskName'." -ForegroundColor Green
Write-Host "Bot se tu chay moi lan ban dang nhap Windows (icon o khay he thong)."
Write-Host "Kiem tra: Task Scheduler > Task Scheduler Library > $taskName"
