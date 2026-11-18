"""Cómo se conecta el agente a servidores MCP: en memoria (pruebas), por stdio, por HTTP o a varios a la vez."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared.memory import create_connected_server_and_client_session

from hubspot_client import CRM
from mcp_server import build_server
from settings import Settings

SERVER = str(Path(__file__).with_name("mcp_server.py"))
ENV_VARS = ("HUBSPOT_MODE", "HUBSPOT_TOKEN", "HUBSPOT_API_VERSION")
# Segundo servidor, ya hecho: el de referencia de hora (versión fijada). Se lanza con uvx (viene con uv).
TIME_SERVER = {"transport": "stdio", "command": "uvx", "args": ["mcp-server-time==2026.11.11"]}


@asynccontextmanager
async def in_memory(settings: Settings | None = None, crm: CRM | None = None):
    """Servidor y cliente en el mismo proceso (sin subprocesos ni red). Devuelve (tools, server)."""
    server = build_server(settings or Settings(), crm)
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        yield await load_mcp_tools(session), server


def hubspot_stdio() -> dict:
    """Nuestro servidor como subproceso. El hijo no hereda todo tu entorno: se le pasan solo las variables de HubSpot."""
    env = {k: os.environ[k] for k in ENV_VARS if k in os.environ}
    return {"transport": "stdio", "command": sys.executable, "args": [SERVER], "env": env}


async def stdio_tools():
    return await MultiServerMCPClient({"hubspot": hubspot_stdio()}).get_tools()


async def multi_tools():
    """DOS servidores a la vez: el nuestro (CRM) y el de hora. Los nombres llevan prefijo para no chocar."""
    client = MultiServerMCPClient({"hubspot": hubspot_stdio(), "time": TIME_SERVER}, tool_name_prefix=True)
    return await client.get_tools()


@asynccontextmanager
async def persistent_tools(url: str = ""):
    """UNA sesión MCP abierta mientras viva el contexto (lo que necesita un servicio de larga vida, como la API).

    Con `url`, el servidor MCP corre aparte por HTTP; sin ella, se lanza como subproceso stdio y vive lo mismo que la API
    (así el CRM simulado conserva sus datos entre una pregunta y otra).
    """
    conn = {"transport": "streamable_http", "url": url} if url else hubspot_stdio()
    async with MultiServerMCPClient({"hubspot": conn}).session("hubspot") as session:
        yield await load_mcp_tools(session)


async def http_tools(url: str):
    return await MultiServerMCPClient({"hubspot": {"transport": "streamable_http", "url": url}}).get_tools()
