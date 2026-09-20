@echo off
REM ShafferFinEval launcher -- Windows.
REM
REM First run creates a virtual environment and installs the two dependencies.
REM Every run after that just starts the terminal and opens a browser window.
REM
REM   run.bat              start the app
REM   run.bat --refresh    run today's score refresh first, then start
REM
REM Double-clicking this file in Explorer works too.

setlocal
cd /d "%~dp0"

set "VENV=.venv"
set "STAMP=%VENV%\.deps-installed"
if "%SHAFFERFINEVAL_PORT%"=="" set "SHAFFERFINEVAL_PORT=8501"
set "URL=http://127.0.0.1:%SHAFFERFINEVAL_PORT%"

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not found on PATH. Install Python 3.10+ from python.org
  echo and tick "Add Python to PATH" during setup.
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.10 or newer is required.
  pause
  exit /b 1
)

if not exist "%VENV%" (
  echo First run: creating %VENV% and installing dependencies...
  python -m venv "%VENV%"
  "%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
)

if not exist "%STAMP%" (
  "%VENV%\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  echo installed > "%STAMP%"
  echo Dependencies ready.
)

if "%~1"=="--refresh" (
  echo Refreshing scores before launch ^(this hits Yahoo and FRED^)...
  "%VENV%\Scripts\python.exe" daily_job.py
)

echo ShafferFinEval starting at %URL%
echo Press Ctrl-C to stop.

REM Open the browser once the server actually answers, not before, so the
REM first thing seen is the terminal rather than a connection error.
start /b "" powershell -NoProfile -WindowStyle Hidden -Command ^
  "for($i=0;$i -lt 60;$i++){try{if((Invoke-WebRequest -UseBasicParsing -Uri '%URL%/_stcore/health' -TimeoutSec 2).StatusCode -eq 200){Start-Process '%URL%';break}}catch{};Start-Sleep -Milliseconds 500}"

"%VENV%\Scripts\streamlit.exe" run app.py --server.port %SHAFFERFINEVAL_PORT%

endlocal
