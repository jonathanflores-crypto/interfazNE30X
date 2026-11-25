# Windows: IP de esta PC en la red con salida (la que tiene puerta de enlace), sin WSL / Docker / VPN virtuales.
function Get-IpDeEstaPc {
    $cfg = Get-NetIPConfiguration | Where-Object {
        $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq "Up" -and
        $_.InterfaceAlias -notlike "vEthernet*" -and $_.InterfaceAlias -notlike "*WSL*" -and $_.InterfaceAlias -notlike "*Docker*"
    } | Select-Object -First 1
    if ($cfg) { return ($cfg.IPv4Address | Select-Object -First 1).IPAddress }
    return ""
}

function Show-Direcciones([string]$ip) {
    Write-Host ""
    Write-Host "=================================================================="
    Write-Host "  Interfaz NE30X lista"
    Write-Host "    En esta PC:            http://localhost:8095"
    if ($ip) { Write-Host "    Desde otro equipo:     http://$($ip):8095" }
    if ($ip) { Write-Host "    Broker MQTT (camaras): $ip  puerto 1883" } else { Write-Host "    Broker MQTT (camaras): <IP de esta PC>  puerto 1883" }
    Write-Host "=================================================================="
    if (-not $ip) { Write-Host "  No pude detectar la IP: revisa que estes conectado a la red." }
}
