@echo off
setlocal
title Kronos Copilot Server

cd /d "%~dp0"

set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
set "DASHBOARD_URL=http://127.0.0.1:8000/app/dashboard.html"
set "READY_URL=http://127.0.0.1:8000/api/auth/session"
set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_EXE%" set "CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

if not exist "%PYTHON_EXE%" (
    echo.
    echo Kronos Copilot could not find its Python environment.
    echo Expected: %PYTHON_EXE%
    echo.
    pause
    exit /b 1
)

if not exist "%CHROME_EXE%" (
    echo.
    echo Kronos Copilot could not find Google Chrome.
    echo Expected Chrome in the standard Google Chrome installation folder.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting Kronos Copilot...
echo Waiting for the local server to become ready.
echo The dashboard will open automatically in Google Chrome.
echo Keep this window open while using the application.
echo Press Ctrl+C in this window to stop the server.
echo.

start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "$readyUrl='%READY_URL%'; $dashboardUrl='%DASHBOARD_URL%'; $chrome='%CHROME_EXE%'; for ($i = 0; $i -lt 120; $i++) { try { $response = Invoke-WebRequest -UseBasicParsing -Uri $readyUrl -TimeoutSec 2; if ($response.StatusCode -eq 200) { Start-Process -FilePath $chrome -ArgumentList @('--new-window', $dashboardUrl); exit 0 } } catch { }; Start-Sleep -Milliseconds 500 }; exit 1"
set "VIRTUAL_ENV=%PROJECT_DIR%.venv"
set "PATH=%VIRTUAL_ENV%\Scripts;%PATH%"
"%PYTHON_EXE%" "app\server.py"

echo.
echo The Kronos Copilot server has stopped.
pause
