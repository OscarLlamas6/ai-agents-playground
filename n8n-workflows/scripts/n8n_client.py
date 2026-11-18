"""Cliente mínimo (requests) para el laboratorio: dispara tickets, aprueba y lee la bitácora simulada.

Uso:
    python scripts/n8n_client.py send data/tickets/01_critico_vpn.json
    python scripts/n8n_client.py approve TCK-1001 --decision Aprobar --aprobador OL
    python scripts/n8n_client.py log [--tipo auditoria] [--ticket TCK-1001]
    python scripts/n8n_client.py clear
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
BASE = os.getenv("N8N_URL", "http://localhost:5678")


def token() -> str:
    """Lee el token de la variable de entorno o de .env (nunca se imprime)."""
    if os.getenv("N8N_WEBHOOK_TOKEN"):
        return os.environ["N8N_WEBHOOK_TOKEN"]
    env = RAIZ / ".env"
    if env.exists():
        for linea in env.read_text(encoding="utf-8").splitlines():
            if linea.startswith("N8N_WEBHOOK_TOKEN="):
                return linea.split("=", 1)[1].split("  #")[0].strip()
    raise SystemExit("Falta N8N_WEBHOOK_TOKEN (en el entorno o en .env)")


def send_ticket(ticket: dict, api_key: str | None = None) -> requests.Response:
    """POST al webhook autenticado. Responde 202 de inmediato; el trabajo sigue en segundo plano."""
    cabeceras = {"X-API-Key": api_key if api_key is not None else token()}
    return requests.post(f"{BASE}/webhook/tickets", json=ticket, headers=cabeceras, timeout=15)


def read_log(tipo: str | None = None, ticket_id: str | None = None) -> list[dict]:
    params = {k: v for k, v in {"tipo": tipo, "ticket_id": ticket_id}.items() if v}
    r = requests.get(f"{BASE}/webhook/sim-registro", params=params, timeout=10)
    r.raise_for_status()
    return r.json()["entradas"]


def clear_log() -> None:
    requests.delete(f"{BASE}/webhook/sim-registro", timeout=10).raise_for_status()


def wait_for(ticket_id: str, tipo: str, timeout: float = 30.0, **filtros) -> dict | None:
    """Espera (con sondeo) a que aparezca una entrada en la bitácora."""
    limite = time.time() + timeout
    while time.time() < limite:
        for e in reversed(read_log(tipo=tipo, ticket_id=ticket_id)):
            if all(e.get(k) == v for k, v in filtros.items()):
                return e
        time.sleep(1)
    return None


def approval_url(ticket_id: str, timeout: float = 20.0) -> str | None:
    """Espera la notificación de 'aprobación pendiente' y devuelve el enlace del formulario."""
    limite = time.time() + timeout
    while time.time() < limite:
        con_url = [e for e in read_log("notificacion", ticket_id) if e.get("url_aprobacion")]
        if con_url:
            return con_url[-1]["url_aprobacion"]
        time.sleep(1)
    return None


def approve(ticket_id: str, decision: str = "Aprobar", aprobador: str = "XX", comentario: str = "") -> int:
    """Envía el formulario de aprobación (los campos del Wait son field-0, field-1 y field-2)."""
    url = approval_url(ticket_id)
    if url is None:
        raise SystemExit(f"No hay solicitud de aprobación pendiente para {ticket_id}")
    campos = {"field-0": (None, decision), "field-1": (None, aprobador), "field-2": (None, comentario)}
    return requests.post(url, files=campos, timeout=15).status_code


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send"); s.add_argument("archivo")
    a = sub.add_parser("approve"); a.add_argument("ticket_id")
    a.add_argument("--decision", choices=["Aprobar", "Rechazar"], default="Aprobar")
    a.add_argument("--aprobador", default="XX"); a.add_argument("--comentario", default="")
    lg = sub.add_parser("log"); lg.add_argument("--tipo"); lg.add_argument("--ticket")
    sub.add_parser("clear")
    args = ap.parse_args()

    if args.cmd == "send":
        r = send_ticket(json.loads(Path(args.archivo).read_text(encoding="utf-8")))
        print(r.status_code, r.text)
    elif args.cmd == "approve":
        print("HTTP", approve(args.ticket_id, args.decision, args.aprobador, args.comentario))
    elif args.cmd == "log":
        for e in read_log(args.tipo, args.ticket):
            print(json.dumps(e, ensure_ascii=False))
    elif args.cmd == "clear":
        clear_log(); print("bitácora limpia")
    return 0


if __name__ == "__main__":
    sys.exit(main())
