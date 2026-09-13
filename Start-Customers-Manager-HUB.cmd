@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Customers-Manager-HUB.ps1"
if errorlevel 1 pause
