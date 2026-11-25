@echo off
rem Windows: ejecuta actualizar.ps1 sin importar la politica de scripts. Doble clic o desde CMD/PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\actualizar.ps1" %*
if "%~1"=="" pause
