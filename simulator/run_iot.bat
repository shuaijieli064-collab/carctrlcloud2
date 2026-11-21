@echo off
rem One-click launcher for the IoT simulator suite (broker + car device + top-down view).
rem Keep this file ASCII-only: cmd parses batch files in the ANSI codepage, and
rem non-ASCII text here gets garbled / crashes the script. Real logic: run_iot.py
setlocal
cd /d "%~dp0"
set "PY=D:\xl\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" run_iot.py
echo.
pause
endlocal
