@echo off
chcp 65001 >nul
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897
cd /d "%~dp0..\repo"
echo ==== Push main and tags to GitHub (browser auth) ====
git push -u origin main
if errorlevel 1 goto fail
git push origin --tags
if errorlevel 1 goto fail
echo.
echo ==== Done. ====
pause
exit /b 0
:fail
echo.
echo ==== Failed. Check auth / permission / proxy. ====
pause
exit /b 1
