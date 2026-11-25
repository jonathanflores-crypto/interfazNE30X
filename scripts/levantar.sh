#!/usr/bin/env bash
# Mac / Linux: levanta la interfaz (monitor + broker MQTT). La primera vez crea .env desde .env.ejemplo.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/ip_comun.sh
if [ ! -f .env ]; then
  cp .env.ejemplo .env
  echo "Se creó .env: pon las IPs y la contraseña de las cámaras y vuelve a correr este script."
  exit 0
fi
export HOST_IP="$(ip_de_esta_pc)"
docker compose up -d --build
mostrar_direcciones "$HOST_IP"
