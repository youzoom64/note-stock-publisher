@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Run setup.cmd first.
    pause
    popd
    exit /b 1
)
if not exist "data\temp" mkdir "data\temp"
set "TEMP=%CD%\data\temp"
set "TMP=%TEMP%"
set "PYTHONDONTWRITEBYTECODE=1"
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\data\browsers"
start "" ".venv\Scripts\pythonw.exe" "main.py"
popd
