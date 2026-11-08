@echo off
cd /d "%~dp0"
echo Starting Media Vault...
python server.py
if errorlevel 1 (
  echo.
  echo "python" not found, trying "py -3" ...
  py -3 server.py
)
echo.
echo Server stopped.
pause
