@echo off
chcp 65001 >nul
echo ==== Push with fine-grained PAT ====
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push-with-token.ps1"
echo.
pause
