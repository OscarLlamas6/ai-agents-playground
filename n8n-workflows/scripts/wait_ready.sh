#!/usr/bin/env sh
# Espera a que n8n esté listo (base de datos migrada). Sin esto, la CLI puede chocar con las migraciones del primer arranque.
for _ in $(seq 1 90); do
  if curl -fsS http://localhost:5678/healthz/readiness >/dev/null 2>&1; then exit 0; fi
  sleep 2
done
echo "n8n no respondió a tiempo: revisa 'docker compose logs n8n'" >&2
exit 1
