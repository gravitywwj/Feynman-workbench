@echo off
setlocal
cd /d "%~dp0"

set "URL=http://127.0.0.1:8001/"
set "PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo [Error] Virtual environment was not found: %PYTHON%
  echo Please install dependencies first: .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)

rem Reuse an already-running local server instead of starting a second one.
powershell -NoProfile -Command "try { [void](Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri '%URL%'); exit 0 } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 goto :open

echo Starting Feynman Workbench...
set "WORKBENCH_PYTHON=%PYTHON%"
set "WORKBENCH_DIR=%CD%"
powershell -NoProfile -Command "Start-Process -FilePath $env:WORKBENCH_PYTHON -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001') -WorkingDirectory $env:WORKBENCH_DIR -WindowStyle Hidden"

for /L %%I in (1,1,20) do (
  powershell -NoProfile -Command "try { [void](Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 -Uri '%URL%'); exit 0 } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto :open
  rem timeout fails when this file is launched without an interactive console.
  powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul
)

echo [Error] The service did not start within 20 seconds.
echo Check whether port 8001 is occupied, then run this file again.
pause
exit /b 1

:open
start "" "%URL%"
exit /b 0
