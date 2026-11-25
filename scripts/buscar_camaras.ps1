# Windows (PowerShell): busca las camaras CamThink (NE301 / NE302) en la red de esta PC.
# La IP cambia en cada red (DHCP), pero la MAC no: empiezan con 44-9F-DA o 04-EC-A9.
# Uso: .\scripts\buscar_camaras.ps1
#      .\scripts\buscar_camaras.ps1 -Interfaz "Wi-Fi"     (si hay varias redes, el nombre de la interfaz)
param([string]$Interfaz = "")

$Prefijos = @("44-9F-DA", "04-EC-A9")

$ips = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notlike "169.254.*" -and $_.IPAddress -ne "127.0.0.1" -and $_.InterfaceAlias -notlike "vEthernet*" -and
    $_.InterfaceAlias -notlike "*WSL*" -and $_.InterfaceAlias -notlike "*Docker*" -and
    ($Interfaz -eq "" -or $_.InterfaceAlias -eq $Interfaz)
}
$mia = $ips | Select-Object -First 1
if (-not $mia) {
    Write-Host "No encontre una red con IP. Estas conectado? (o pasa -Interfaz con el nombre de tu red)"
    exit 1
}
$red = ($mia.IPAddress -split "\.")[0..2] -join "."
Write-Host "PC en $($mia.IPAddress) ($($mia.InterfaceAlias)) - buscando en $red.1-254 ..."

# Pinguear toda la red a la vez para que la tabla ARP conozca a todos los equipos
$tareas = foreach ($i in 1..254) { (New-Object System.Net.NetworkInformation.Ping).SendPingAsync("$red.$i", 1000) }
try { [System.Threading.Tasks.Task]::WaitAll($tareas) } catch { }

$camaras = Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    $mac = $_.LinkLayerAddress.ToUpper()
    $_.IPAddress -like "$red.*" -and ($Prefijos | Where-Object { $mac.StartsWith($_) })
} | Sort-Object IPAddress -Unique

foreach ($c in $camaras) {
    try {
        $web = (Invoke-WebRequest -Uri "http://$($c.IPAddress)/login" -UseBasicParsing -TimeoutSec 3).StatusCode
    } catch {
        $web = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { "sin respuesta" }
    }
    Write-Host ""
    Write-Host "  Camara CamThink"
    Write-Host "    IP:   $($c.IPAddress)   (MAC $($c.LinkLayerAddress))"
    Write-Host "    Web:  http://$($c.IPAddress)/login   [HTTP $web]"
    Write-Host "    RTSP: rtsp://$($c.IPAddress):554/live/stream"
}

Write-Host ""
if (-not $camaras) {
    Write-Host "No se encontro ninguna camara en $red.x."
    Write-Host "Revisa que la camara este prendida y en la misma red que esta PC,"
    Write-Host "o conectate a su WiFi propio (NE30x_XXXXXX) y entra a http://192.168.10.10"
} else {
    Write-Host "$(@($camaras).Count) camara(s) encontrada(s). Pon sus IPs en CAMARAS= del .env."
}
