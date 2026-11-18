#!/usr/bin/env sh
# Dispara el workflow de triaje con un ticket de ejemplo.
# Uso: sh scripts/trigger_ticket.sh data/tickets/01_critico_vpn.json
set -eu
cd "$(dirname "$0")/.."
archivo="${1:-data/tickets/01_critico_vpn.json}"
# Lee N8N_WEBHOOK_TOKEN de .env sin imprimirlo
token="$(grep -E '^N8N_WEBHOOK_TOKEN=' .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//')"
curl -sS -i -X POST "${N8N_URL:-http://localhost:5678}/webhook/tickets" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${token}" \
  --data @"$archivo"
echo
