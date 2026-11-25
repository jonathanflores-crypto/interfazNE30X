@echo off
rem Windows: ejecuta buscar_camaras.ps1 sin importar la politica de scripts. Doble clic o desde CMD/PowerShell.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\buscar_camaras.ps1" %*
if "%~1"=="" pause
