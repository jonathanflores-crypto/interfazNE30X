# Mac / Linux: IP de esta PC en la red con salida (la de la ruta por defecto), para la página y para las cámaras.
ip_de_esta_pc() {
  local ifaz ip
  ifaz=$(route -n get default 2>/dev/null | awk '/interface:/{print $2}')
  [ -n "$ifaz" ] && ip=$(ipconfig getifaddr "$ifaz" 2>/dev/null)
  [ -z "$ip" ] && ip=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
  [ -z "$ip" ] && ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "$ip"
}

mostrar_direcciones() {
  local ip="$1"
  echo
  echo "=================================================================="
  echo "  Interfaz NE30X lista"
  echo "    En esta PC:            http://localhost:8095"
  [ -n "$ip" ] && echo "    Desde otro equipo:     http://$ip:8095"
  echo "    Broker MQTT (cámaras): ${ip:-<IP de esta PC>}  puerto 1883"
  echo "=================================================================="
  [ -z "$ip" ] && echo "  No pude detectar la IP: revisa que estés conectado a la red."
  return 0
}
