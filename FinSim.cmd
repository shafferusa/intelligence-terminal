@echo off
rem FinSim.cmd - starts FinSim1, the FinSim paper-trading terminal, from any folder (STARTUP-02).
rem   FinSim.cmd            same as: python -m finsim open  - starts the server when needed, opens the app window
rem   FinSim.cmd serve      the server in this window, Ctrl+C stops it; also stop, status, install, uninstall, import
rem   From PowerShell:      .\FinSim.cmd serve
rem The same file sits in C:\Users\logan\intelligence-terminal and in its finsim folder; each runs the code next to it.
rem Python: py -3.12 when the py launcher has 3.12, else python. The exit code is the command's own.
setlocal
set "FINSIM_DIR="
if exist "%~dp0finsim\finsim\terminal\__init__.py" set "FINSIM_DIR=%~dp0finsim"
if not defined FINSIM_DIR if exist "%~dp0finsim\terminal\__init__.py" set "FINSIM_DIR=%~dp0."
if not defined FINSIM_DIR echo FinSim.cmd: no FinSim code next to this file - expected finsim\finsim\terminal or finsim\terminal 1>&2
if not defined FINSIM_DIR endlocal & exit /b 1
set "FINSIM_PY=python"
where py >nul 2>nul && py -3.12 -V >nul 2>nul && set "FINSIM_PY=py -3.12"
set "FINSIM_CMD=open"
if not "%~1"=="" set "FINSIM_CMD="
pushd "%FINSIM_DIR%"
%FINSIM_PY% -m finsim %FINSIM_CMD% %*
set "FINSIM_RC=%ERRORLEVEL%"
popd
endlocal & exit /b %FINSIM_RC%
