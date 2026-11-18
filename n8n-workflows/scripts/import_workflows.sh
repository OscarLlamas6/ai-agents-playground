#!/usr/bin/env sh
# Importa los workflows de ./workflows con la CLI de n8n, publica los que necesitan webhook y reinicia n8n.
# Uso: sh scripts/import_workflows.sh      (con el contenedor `n8n` en marcha)
set -eu
cd "$(dirname "$0")/.."
dc="docker compose"
sh scripts/wait_ready.sh

echo "→ Importando workflows..."
$dc exec -T n8n n8n import:workflow --separate --input=/import/workflows 2>&1 | grep -E "Successfully|Error|error" || true

echo "→ Publicando (primero los sub-workflows y el error workflow: n8n exige que lo referenciado esté publicado)..."
for id in S12SubEnriquecer01 S12SubRunbooks0001 S12ErrorHandler001 S12ServiciosSim001 S12TriajeTickets01 S12ServidorMcp00001; do
  $dc exec -T n8n n8n publish:workflow --id="$id" 2>&1 | grep -E "ublish|rror" || true
done

echo "→ Reiniciando n8n para registrar los webhooks..."
$dc restart n8n >/dev/null
sleep 3
sh scripts/wait_ready.sh && echo "→ Listo: http://localhost:5678"
