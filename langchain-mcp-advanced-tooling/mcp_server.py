"""Servidor MCP propio contra HubSpot CRM (FastMCP, SDK oficial de Python).

Las tres primitivas de MCP en un solo servidor:
    Tools      search_contacts · get_contact_notes (lecturas) · add_note (ESCRITURA)
    Resources  hubspot://contact/{contact_id}   la ficha de un contacto, como texto para dar contexto
    Prompts    prepare_call(contact_id)         plantilla "prepara mi llamada con este contacto"

Transportes:
    python mcp_server.py            stdio (lo lanza el cliente; los logs van a stderr)
    python mcp_server.py --http     Streamable HTTP en MCP_HOST:MCP_PORT/mcp
"""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from hubspot_client import CRM, CRMError, FakeHubSpot, LiveHubSpot
from settings import Settings

# En stdio, stdout es el canal del protocolo: un print() lo corrompe. Todo log va a stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hubspot-mcp")

INSTRUCTIONS = "Servidor de ejemplo para HubSpot CRM: busca contactos, lee sus notas y puede agregar una nota."

# Anotaciones: METADATO que describe la herramienta para el cliente (¿solo lee? ¿repetirla es inocua?).
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITES = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

# Los tipos con restricciones SON el esquema que ve el modelo (JSON Schema) y lo que valida el SDK antes de llamar a la función.
SearchQuery = Annotated[str, Field(min_length=2, max_length=80, description="Texto a buscar: nombre, empresa o correo.")]
ContactId = Annotated[str, Field(pattern=r"^\d{1,15}$", description="Id numérico del contacto (lo devuelve search_contacts).")]
NoteBody = Annotated[str, Field(min_length=3, max_length=2000, description="Texto de la nota.")]
Limit = Annotated[int, Field(ge=1, le=10, description="Máximo de resultados (1 a 10).")]


def build_crm(s: Settings) -> CRM:
    return LiveHubSpot(s.hubspot_token, s.hubspot_api_version) if s.hubspot_mode == "live" else FakeHubSpot()


def flat(c: dict[str, Any]) -> dict[str, Any]:
    p = c["properties"]
    return {"id": c["id"], "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip(), "company": p.get("company"),
            "jobtitle": p.get("jobtitle"), "stage": p.get("lifecyclestage"), "email": p.get("email"), "phone": p.get("phone")}


def build_server(s: Settings | None = None, crm: CRM | None = None) -> FastMCP:
    s = s or Settings.from_env()
    crm = crm or build_crm(s)
    kwargs: dict[str, Any] = {"host": s.host, "port": s.port}
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        # Dentro de un contenedor se escucha en 0.0.0.0: hay que declarar qué cabeceras Host acepta el servidor HTTP
        # (el nombre del servicio de Docker Compose, `mcp`, entre ellas).
        kwargs["transport_security"] = TransportSecuritySettings(allowed_hosts=list(s.allowed_hosts))
    mcp = FastMCP("hubspot-crm", instructions=INSTRUCTIONS, **kwargs)

    # ── Tools ──────────────────────────────────────────────────────────────
    @mcp.tool(annotations=READ_ONLY)
    def search_contacts(query: SearchQuery, limit: Limit = 5) -> dict[str, Any]:
        """Busca contactos por nombre, empresa o correo. Devuelve id, nombre, empresa, cargo y etapa."""
        rows = crm.search_contacts(query, limit)
        log.info("search_contacts → %d resultados", len(rows))
        return {"contacts": [flat(c) for c in rows], "count": len(rows)}

    @mcp.tool(annotations=READ_ONLY)
    def get_contact_notes(contact_id: ContactId, limit: Limit = 5) -> dict[str, Any]:
        """Notas de un contacto, de la más reciente a la más antigua."""
        notes = crm.get_notes(contact_id, limit)
        log.info("get_contact_notes(%s) → %d notas", contact_id, len(notes))
        return {"notes": notes, "count": len(notes)}

    @mcp.tool(annotations=WRITES)
    def add_note(contact_id: ContactId, body: NoteBody) -> dict[str, Any]:
        """Crea una nota asociada a un contacto. ESCRIBE en el CRM (cada llamada crea una nota nueva)."""
        try:
            created = crm.create_note(contact_id, body)
        except CRMError as e:
            log.error("add_note falló: %s", e)
            raise
        log.info("add_note(%s) → nota %s", contact_id, created["id"])
        return {"created": True, "note_id": created["id"], "contact_id": contact_id}

    # ── Resources: datos que la APLICACIÓN decide cargar como contexto (se piden por URI) ─────────────
    @mcp.resource("hubspot://contact/{contact_id}", mime_type="text/markdown")
    def contact_card(contact_id: str) -> str:
        """Ficha de un contacto en Markdown."""
        c = flat(crm.get_contact(contact_id))
        return (f"# {c['name']}\n\n- Empresa: {c['company']}\n- Cargo: {c['jobtitle']}\n- Etapa: {c['stage']}\n"
                f"- Correo: {c['email']}\n- Teléfono: {c['phone']}\n")

    # ── Prompts: plantillas que la PERSONA elige (p. ej. como comando "/") ─────────────
    @mcp.prompt()
    def prepare_call(contact_id: str) -> str:
        """Prepara una llamada de ventas con un contacto."""
        return (f"Prepara mi llamada con el contacto {contact_id}: busca su ficha y sus notas con las herramientas, "
                "y dame un resumen de tres viñetas y dos preguntas para hacerle.")

    return mcp


def main() -> None:
    s = Settings.from_env()
    if s.hubspot_mode == "live" and not s.hubspot_token:
        sys.exit("HUBSPOT_MODE=live necesita HUBSPOT_TOKEN en el .env")
    log.info("hubspot-crm: modo=%s", s.hubspot_mode)
    build_server(s).run(transport="streamable-http" if "--http" in sys.argv else "stdio")


if __name__ == "__main__":
    main()
