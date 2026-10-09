@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\desktop-shortcuts.ps1" %*
exit /b %errorlevel%
