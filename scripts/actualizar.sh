#!/usr/bin/env bash
# Mac / Linux: baja la última versión del repo y reconstruye. Los datos (data/) y el .env no se tocan.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
. scripts/ip_comun.sh
export HOST_IP="$(ip_de_esta_pc)"
docker compose up -d --build
mostrar_direcciones "$HOST_IP"
