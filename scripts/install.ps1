<#
    CAI DAT BOOKING CONTROL TREN WINDOWS
    -----------------------------------------
    Mo PowerShell tai thu muc du an roi chay:

        Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
        .\scripts\install.ps1

    Script se: tao virtualenv -> cai thu vien -> tao config -> tao du lieu demo.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "=== Booking Control - cai dat ===" -ForegroundColor Cyan

# --- 1. Kiem tra Python ------------------------------------------------------
$py = $null
foreach ($cmd in @("py -3.11", "py -3", "python")) {
    try {
        $parts = $cmd.Split(" ")
        $ver = & $parts[0] $parts[1..($parts.Length - 1)] --version 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $cmd; Write-Host "Tim thay $ver ($cmd)"; break }
    } catch { }
}
if (-not $py) {
    Write-Host "KHONG TIM THAY PYTHON." -ForegroundColor Red
    Write-Host "Tai Python 3.11+ tai https://www.python.org/downloads/windows/"
    Write-Host 'Khi cai nho TICK vao o "Add python.exe to PATH".'
    exit 1
}

# --- 2. Virtualenv -----------------------------------------------------------
if (-not (Test-Path ".venv")) {
    Write-Host "`nTao moi truong ao .venv ..." -ForegroundColor Yellow
    $parts = $py.Split(" ")
    & $parts[0] $parts[1..($parts.Length - 1)] -m venv .venv
}
$pyExe = Join-Path $root ".venv\Scripts\python.exe"

# --- 3. Thu vien -------------------------------------------------------------
Write-Host "`nCai thu vien (co the mat 1-3 phut) ..." -ForegroundColor Yellow
& $pyExe -m pip install --upgrade pip --quiet
& $pyExe -m pip install -r requirements.txt --quiet
& $pyExe -m pip install -e . --quiet

# --- 4. Config ---------------------------------------------------------------
Write-Host "`nTao file cau hinh ..." -ForegroundColor Yellow
& $pyExe -m booking_control init

# --- 4b. Tai khoan Admin dau tien --------------------------------------------
Write-Host "`nTao tai khoan Admin (dang nhap web). Nhap mat khau 2 lan:" -ForegroundColor Yellow
& $pyExe -m booking_control user add admin --role admin --display-name "Quan tri"

# --- 5. Du lieu demo ---------------------------------------------------------
if (-not (Test-Path "sample_data\FollowUp_UserA.xlsx")) {
    Write-Host "`nTao bo du lieu demo ..." -ForegroundColor Yellow
    & $pyExe -m booking_control sample
}

Write-Host "`n=== XONG ===" -ForegroundColor Green
Write-Host @"

Buoc tiep theo:

  1. Mo config\config.yaml, sua muc  files:  tro dung duong dan Excel that cua ban.
     (OneDrive/SharePoint: dung duong dan da sync ve may, vi du
      C:\Users\<ban>\OneDrive - Cong ty\Logistics\WEEKLY PLANNING 2026.xlsx)

  2. Kiem tra nhan dien sheet:      .\scripts\run.bat check
  3. Chay thu khong ghi file:       .\scripts\run.bat scan --dry-run
  4. Chay that:                     .\scripts\run.bat scan
  5. Chay he thong (web + tu dong):  .\scripts\run.bat run
     roi mo trinh duyet: http://127.0.0.1:8765  (dang nhap admin)
  6. Cho dong nghiep dung chung:    xem docs\INSTALL.md muc 'May chu noi bo (LAN)'
     (Dashboard: http://127.0.0.1:8765)

  Cho Automation tu khoi dong cung Windows:  .\scripts\install_autostart.ps1
"@
