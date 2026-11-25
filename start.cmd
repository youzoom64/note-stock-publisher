@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist ".venv\Scripts\pythonw.exe" goto prepare
if not exist "data\runtime-ready" goto prepare
if not exist "data\temp" mkdir "data\temp"
set "TEMP=%CD%\data\temp"
set "TMP=%TEMP%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\data\browsers"
start "" ".venv\Scripts\pythonw.exe" "main.py"
set "NOTE_STOCK_APP_EXIT=%ERRORLEVEL%"
if not "%NOTE_STOCK_APP_EXIT%"=="0" (
    echo note Stock could not start or closed unexpectedly. Exit code: %NOTE_STOCK_APP_EXIT%
    echo Startup details: "%CD%\data\logs"
    pause
)
popd
exit /b %NOTE_STOCK_APP_EXIT%

:prepare
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "NOTE_STOCK_SETUP_EXIT=%ERRORLEVEL%"
if not "%NOTE_STOCK_SETUP_EXIT%"=="0" pause
popd
exit /b %NOTE_STOCK_SETUP_EXIT%
