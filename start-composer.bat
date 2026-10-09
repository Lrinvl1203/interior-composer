@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher.ps1" -Action Start %*
exit /b %errorlevel%
