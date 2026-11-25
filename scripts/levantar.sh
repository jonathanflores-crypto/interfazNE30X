#!/usr/bin/env bash
# Mac / Linux: levanta la interfaz (monitor + broker MQTT). La primera vez crea .env desde .env.ejemplo.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.ejemplo .env
  echo "Se creó .env: pon las IPs y la contraseña de las cámaras y vuelve a correr este script."
  exit 0
fi
docker compose up -d --build
IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Interfaz:     http://localhost:8095"
echo "Broker MQTT:  ${IP:-<IP de esta PC>}:1883  (configúralo en cada cámara)"
