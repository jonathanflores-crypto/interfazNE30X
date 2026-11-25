#!/usr/bin/env bash
# Mac: busca las cámaras CamThink (NE301 / NE302) en la red de esta PC.
# La IP cambia en cada red (DHCP), pero la MAC no: empiezan con 44:9f:da o 04:ec:a9.
# Uso: ./scripts/buscar_camaras.sh          (interfaz en0, la del WiFi)
#      ./scripts/buscar_camaras.sh en1      (otra interfaz, p. ej. cable)
IFAZ="${1:-en0}"
PREFIJOS="44:9f:da|04:ec:a9"

IP=$(ipconfig getifaddr "$IFAZ")
if [ -z "$IP" ]; then
  echo "La interfaz $IFAZ no tiene IP. ¿Estás conectado a la red? (prueba con en1)"
  exit 1
fi
RED=${IP%.*}
echo "PC en $IP · buscando en $RED.1–254 ..."

# Pinguear toda la red para que la tabla ARP conozca a todos los equipos
for i in $(seq 1 254); do ping -c1 -t1 "$RED.$i" >/dev/null 2>&1 & done
wait

ENCONTRADAS=0
while read -r ip mac; do
  # arp quita los ceros a la izquierda (0a → a): normalizar
  mac=$(echo "$mac" | awk -F: '{for(i=1;i<=NF;i++) printf "%s%02s", (i>1?":":""), $i}' | tr ' ' 0)
  web=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://$ip/login")
  echo ""
  echo "  Cámara CamThink"
  echo "    IP:   $ip   (MAC $mac)"
  echo "    Web:  http://$ip/login   [HTTP $web]"
  echo "    RTSP: rtsp://$ip:554/live/stream"
  ENCONTRADAS=$((ENCONTRADAS + 1))
done < <(arp -an -i "$IFAZ" | grep -iE " at ($PREFIJOS)" | sed -E 's/^.*\(([0-9.]+)\) at ([0-9a-fA-F:]+).*/\1 \2/' | sort -u)

echo ""
if [ "$ENCONTRADAS" -eq 0 ]; then
  echo "No se encontró ninguna cámara en $RED.x."
  echo "Revisa que la cámara esté prendida y en la misma red que esta PC,"
  echo "o conéctate a su WiFi propio (NE30x_XXXXXX) y entra a http://192.168.10.10"
else
  echo "$ENCONTRADAS cámara(s) encontrada(s). Pon sus IPs en CAMARAS= del .env."
fi
