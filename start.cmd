@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist "runtime32\python\pythonw.exe" goto prepare
if not exist "runtime32\ready" goto prepare
if not exist "data\temp" mkdir "data\temp"
set "TEMP=%CD%\data\temp"
set "TMP=%TEMP%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PLAYWRIGHT_NODEJS_PATH=%CD%\runtime32\node-v22.23.5-win-x86\node.exe"
start "" "runtime32\python\pythonw.exe" -B "main.py"
set "NOTE_STOCK_APP_EXIT=%ERRORLEVEL%"
if not "%NOTE_STOCK_APP_EXIT%"=="0" pause
popd
exit /b %NOTE_STOCK_APP_EXIT%
:prepare
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "NOTE_STOCK_SETUP_EXIT=%ERRORLEVEL%"
if not "%NOTE_STOCK_SETUP_EXIT%"=="0" pause
popd
exit /b %NOTE_STOCK_SETUP_EXIT%
