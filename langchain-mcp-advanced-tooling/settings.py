"""Configuración del servidor MCP. Todo viene de variables de entorno; nada de llaves en el código."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    hubspot_mode: str = "fake"            # fake (CRM simulado en memoria) | live (HubSpot real)
    hubspot_token: str = field(default="", repr=False)   # repr=False: el token no sale al imprimir la configuración
    hubspot_api_version: str = "2026-03"  # fecha de la API de HubSpot, o "v3" para la ruta heredada
    host: str = "127.0.0.1"               # solo para el transporte HTTP
    port: int = 8000
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "mcp:*")   # cabeceras Host que acepta el servidor HTTP
    sessions_db: str = "data/sessions.db"   # SQLite de la API: checkpointer (historial de las sesiones) y tabla de sesiones
    mcp_url: str = ""                        # si se define, la API usa un servidor MCP por HTTP; si no, lanza uno por stdio

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("HUBSPOT_MODE", "fake").lower()
        if mode not in ("fake", "live"):
            raise ValueError("HUBSPOT_MODE debe ser fake o live")
        return cls(
            hubspot_mode=mode,
            hubspot_token=os.getenv("HUBSPOT_TOKEN", ""),
            hubspot_api_version=os.getenv("HUBSPOT_API_VERSION", "2026-03"),
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MCP_PORT", "8000")),
            allowed_hosts=tuple(h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,mcp:*").split(",") if h.strip()),
            sessions_db=os.getenv("SESSIONS_DB", "data/sessions.db"),
            mcp_url=os.getenv("MCP_URL", ""),
        )
