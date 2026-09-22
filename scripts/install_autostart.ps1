<#
    TU DONG CHAY AUTO KHI DANG NHAP WINDOWS
    --------------------------------------
        .\scripts\install_autostart.ps1              # cai (Task Scheduler)
        .\scripts\install_autostart.ps1 -Remove      # go bo
        .\scripts\install_autostart.ps1 -Startup     # dung thu muc Startup thay vi Task Scheduler

    Task Scheduler duoc khuyen dung: chay ngay ca khi may vua khoi dong lai,
    va tu chay lai neu Automation bi tat.
#>

param(
    [switch]$Remove,
    [switch]$Startup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$taskName = "BookingControl"
$vbs = Join-Path $root "scripts\start_server_hidden.vbs"
$startupLnk = Join-Path ([Environment]::GetFolderPath("Startup")) "BookingControl.lnk"

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
    $lnk.Description = "Booking Control"
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
    -Settings $settings -Description "Booking Control - web noi bo + Automation Engine" -Force | Out-Null

Write-Host "Da dang ky scheduled task '$taskName'." -ForegroundColor Green
Write-Host "He thong se tu chay (an, khong cua so) moi lan ban dang nhap Windows. Mo web: http://127.0.0.1:8765"
Write-Host "Kiem tra: Task Scheduler > Task Scheduler Library > $taskName"
