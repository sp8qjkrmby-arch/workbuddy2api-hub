@echo off
chcp 65001 >nul
echo ==== Restart wb-shell (gateway down ~5-10s) ====
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart-wb-shell.ps1"
echo.
echo Log: see logs\restart-*.log
pause
