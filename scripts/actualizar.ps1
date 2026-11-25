# Windows (PowerShell): baja la ultima version del repo y reconstruye. Los datos (data\) y el .env no se tocan.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
git pull --ff-only
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Actualizado: http://localhost:8095"
