"""OPCIONAL (USE_MCP=1): herramientas de servidores MCP (S10) con `langchain-mcp-adapters`.

MCP_SERVERS_FILE apunta a un JSON con las conexiones, por ejemplo:
    {"docs": {"transport": "stdio", "command": "python", "args": ["mi_servidor.py"]}}
Cualquier herramienta MCP se trata como ESCRITURA (pasa por aprobación humana), salvo las que
listes en MCP_READONLY_TOOLS (separadas por comas): mínimo privilegio por defecto.
"""

import asyncio
import concurrent.futures
import json
import os

from langchain_core.tools import BaseTool


def load_mcp_tools() -> tuple[dict[str, BaseTool], set[str]]:
    """Devuelve (herramientas, nombres que requieren aprobación). Sin USE_MCP=1 no hace nada."""
    if os.getenv("USE_MCP", "0") != "1":
        return {}, set()
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as exc:
        raise RuntimeError("USE_MCP=1 requiere `uv sync --group mcp` (langchain-mcp-adapters)") from exc
    path = os.getenv("MCP_SERVERS_FILE", "")
    if not path:
        raise RuntimeError("USE_MCP=1 requiere MCP_SERVERS_FILE con las conexiones de los servidores")
    with open(path, encoding="utf-8") as f:
        connections = json.load(f)
    tools = run_sync(MultiServerMCPClient(connections).get_tools())
    readonly = {n.strip() for n in os.getenv("MCP_READONLY_TOOLS", "").split(",") if n.strip()}
    return {t.name: t for t in tools}, {t.name for t in tools} - readonly


def run_sync(coro):
    """Ejecuta una corutina desde código síncrono, incluso si ya hay un bucle de eventos (p. ej. Jupyter)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def invoke_tool(tool: BaseTool, args: dict):
    """Invoca una herramienta; las que solo tienen versión asíncrona (las de MCP) se ejecutan con `ainvoke`."""
    if tool.func is None and getattr(tool, "coroutine", None) is not None:
        return run_sync(tool.ainvoke(args))
    return tool.invoke(args)
