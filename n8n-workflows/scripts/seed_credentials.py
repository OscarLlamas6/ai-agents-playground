"""Crea las credenciales del laboratorio dentro de n8n con la CLI, leyendo los valores de `.env`.

Las llaves nunca se escriben en un archivo del repositorio ni se imprimen: el JSON viaja por stdin al contenedor.
Los IDs son fijos para que los workflows exportados encuentren sus credenciales al importarse.

Uso:  python scripts/seed_credentials.py        (con el contenedor `n8n` en marcha)
"""
import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CLAVES = {"N8N_WEBHOOK_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY"}


def leer_env(ruta: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    if not ruta.exists():
        return valores
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.split("  #")[0].strip()      # quita comentarios al final de la línea
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def construir(env: dict[str, str]) -> list[dict]:
    placeholder = "sin-llave-configurada"
    return [
        {"id": "cred-s12-webhook", "name": "S12 Token webhook", "type": "httpHeaderAuth",
         "data": {"name": "X-API-Key", "value": env.get("N8N_WEBHOOK_TOKEN") or placeholder}},
        {"id": "cred-s12-openai", "name": "S12 OpenAI (o LM Studio)", "type": "openAiApi",
         "data": {"apiKey": env.get("OPENAI_API_KEY") or placeholder,
                  "url": env.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"}},
        {"id": "cred-s12-anthropic", "name": "S12 Anthropic", "type": "anthropicApi",
         "data": {"apiKey": env.get("ANTHROPIC_API_KEY") or placeholder}},
    ]


def main() -> int:
    subprocess.run(["sh", "scripts/wait_ready.sh"], cwd=RAIZ, check=True)
    env = {**leer_env(RAIZ / ".env"), **{k: v for k, v in os.environ.items() if k in CLAVES}}   # el entorno gana sobre .env
    if not env.get("N8N_WEBHOOK_TOKEN"):
        print("Aviso: N8N_WEBHOOK_TOKEN está vacío en .env; el webhook quedará con una llave inválida.", file=sys.stderr)
    carga = json.dumps(construir(env))
    cmd = ["docker", "compose", "exec", "-T", "n8n", "sh", "-c",
           "cat > /tmp/creds.json && n8n import:credentials --input=/tmp/creds.json; rc=$?; rm -f /tmp/creds.json; exit $rc"]
    r = subprocess.run(cmd, input=carga, text=True, cwd=RAIZ, capture_output=True)
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "")
    if r.returncode != 0:
        print(r.stderr[-500:], file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
