<#
    DONG GOI THANH FILE .EXE DE PHAT CHO DONG NGHIEP
    ------------------------------------------------
        .\scripts\build_exe.ps1

    Ket qua: dist\BookingBot\BookingBot.exe  (kem thu muc config\ va docs\)
    Copy ca thu muc dist\BookingBot sang may khac la chay duoc, khong can cai Python.
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
    --name BookingBot `
    --noconfirm `
    --clean `
    --windowed `
    --add-data "src\booking_bot\web\templates;booking_bot/web/templates" `
    --hidden-import "uvicorn.logging" `
    --hidden-import "uvicorn.loops.auto" `
    --hidden-import "uvicorn.protocols.http.auto" `
    --hidden-import "uvicorn.protocols.websockets.auto" `
    --hidden-import "uvicorn.lifespan.on" `
    --hidden-import "apscheduler.triggers.interval" `
    --hidden-import "pystray._win32" `
    --collect-submodules "booking_bot" `
    "src\booking_bot\__main__.py"

$dist = Join-Path $root "dist\BookingBot"
Copy-Item -Recurse -Force (Join-Path $root "config") (Join-Path $dist "config")
Copy-Item -Recurse -Force (Join-Path $root "docs") (Join-Path $dist "docs")

@"
@echo off
cd /d "%~dp0"
start "" "BookingBot.exe" run
"@ | Set-Content -Encoding ASCII (Join-Path $dist "Chay Bot.bat")

Write-Host "`nXong: $dist" -ForegroundColor Green
Write-Host 'Tren may moi: mo config\config.yaml sua duong dan file, roi bam "Chay Bot.bat".'
