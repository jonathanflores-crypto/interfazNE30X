# Windows (PowerShell): baja la ultima version del repo y reconstruye. Los datos (data\) y el .env no se tocan.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "ip_comun.ps1")
git pull --ff-only
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:HOST_IP = Get-IpDeEstaPc
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Show-Direcciones $env:HOST_IP
