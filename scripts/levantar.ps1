# Windows (PowerShell): levanta la interfaz (monitor + broker MQTT). La primera vez crea .env desde .env.ejemplo.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path ".env")) {
    Copy-Item ".env.ejemplo" ".env"
    Write-Host "Se creo .env: pon las IPs y la contrasena de las camaras y vuelve a correr este script."
    exit 0
}
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.PrefixOrigin -eq "Dhcp" -or $_.PrefixOrigin -eq "Manual" } |
       Where-Object { $_.IPAddress -notlike "169.*" -and $_.IPAddress -ne "127.0.0.1" } | Select-Object -First 1).IPAddress
Write-Host ""
Write-Host "Interfaz:     http://localhost:8095"
Write-Host "Broker MQTT:  $ip`:1883  (configuralo en cada camara)"
