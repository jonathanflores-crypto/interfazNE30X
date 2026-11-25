# Windows (PowerShell): levanta la interfaz (monitor + broker MQTT). La primera vez crea .env desde .env.ejemplo.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
. (Join-Path $PSScriptRoot "ip_comun.ps1")
if (-not (Test-Path ".env")) {
    Copy-Item ".env.ejemplo" ".env"
    Write-Host "Se creo .env: pon las IPs y la contrasena de las camaras y vuelve a correr este script."
    exit 0
}
$env:HOST_IP = Get-IpDeEstaPc
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Show-Direcciones $env:HOST_IP
