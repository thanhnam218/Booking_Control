@echo off
REM Chay Booking Deadline Bot bang moi truong ao trong thu muc du an.
REM     run.bat check
REM     run.bat scan --dry-run
REM     run.bat run
setlocal
set ROOT=%~dp0..
set PYEXE=%ROOT%\.venv\Scripts\python.exe

if not exist "%PYEXE%" (
  echo Chua cai dat. Hay chay:  powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  exit /b 1
)

pushd "%ROOT%"
"%PYEXE%" -m booking_bot %*
set RC=%ERRORLEVEL%
popd
exit /b %RC%
