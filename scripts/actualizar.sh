#!/usr/bin/env bash
# Mac / Linux: baja la última versión del repo y reconstruye. Los datos (data/) y el .env no se tocan.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
docker compose up -d --build
echo "Actualizado: http://localhost:8095"
