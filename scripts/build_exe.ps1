<#
    DONG GOI THANH FILE .EXE DE PHAT CHO DONG NGHIEP
    ------------------------------------------------
        .\scripts\build_exe.ps1

    Ket qua: dist\BookingControl\BookingControl.exe  (kem thu muc config\ va docs\)
    Copy ca thu muc dist\BookingControl sang may khac la chay duoc, khong can cai Python.
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$pyExe = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $pyExe)) { Write-Host "Chay scripts\install.ps1 truoc." -ForegroundColor Red; exit 1 }

Write-Host "Cai PyInstaller ..." -ForegroundColor Yellow
& $pyExe -m pip install pyinstaller --quiet

Write-Host "Dong goi ..." -ForegroundColor Yellow
& $pyExe -m PyInstaller `
    --name BookingControl `
    --noconfirm `
    --clean `
    --console `
    --add-data "src\booking_control\web\templates;booking_control/web/templates" `
    --add-data "src\booking_control\web\static;booking_control/web/static" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "apscheduler.triggers.interval" `
    --collect-submodules "booking_control" `
    "src\booking_control\__main__.py"

$dist = Join-Path $root "dist\BookingControl"
Copy-Item -Recurse -Force (Join-Path $root "config") (Join-Path $dist "config")
Copy-Item -Recurse -Force (Join-Path $root "docs") (Join-Path $dist "docs")

@"
@echo off
cd /d "%~dp0"
start "" "BookingControl.exe" run
"@ | Set-Content -Encoding ASCII (Join-Path $dist "Chay Booking Control.bat")

Write-Host "`nXong: $dist" -ForegroundColor Green
Write-Host 'Tren may chu: sua config\config.yaml, tao admin (BookingControl.exe user add admin --role admin), roi bam "Chay Booking Control.bat".'
