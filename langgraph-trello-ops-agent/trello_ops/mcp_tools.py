"""OPCIONAL (USE_MCP=1): herramientas de servidores MCP (S10) con `langchain-mcp-adapters`: locales por stdio o REMOTOS por HTTP.

MCP_SERVERS_FILE apunta a un JSON con las conexiones. El mismo cliente sirve para un servidor que lanzas tú (stdio) y para uno
remoto que mantiene otra empresa (Streamable HTTP, con o sin autenticación). Ver `mcp_servers.example.json`:

    {"time":    {"transport": "stdio", "command": "uvx", "args": ["mcp-server-time==2026.11.11"]},
     "excession": {"transport": "streamable_http", "url": "https://excessionlabs.ai/mcp/cve"},
     "github":  {"transport": "streamable_http", "url": "https://api.githubcopilot.com/mcp/",
                 "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}}}

- `${VAR}` y `${VAR:-valor}` se sustituyen por variables de entorno: el token vive en el `.env`, nunca en el JSON.
- MCP_TOOL_PREFIX=1 antepone el nombre del servidor a cada herramienta (`excession_cve_search`): se ve de dónde viene.
- Cualquier herramienta MCP se trata como ESCRITURA (pasa por aprobación humana), salvo las que cuadren con algún patrón de
  MCP_READONLY_TOOLS (nombres o comodines separados por comas, p. ej. `excession_cve_search,time_*`): mínimo privilegio por defecto.
  Las anotaciones `readOnlyHint` de un servidor ajeno NO se usan: son pistas del servidor, no una garantía.
- Un servidor remoto está fuera de tu control y puede caerse: si uno no responde se OMITE con un aviso (stderr) y el agente
  arranca con el resto. Con MCP_STRICT=1 el arranque falla en su lugar.
"""

import asyncio
import concurrent.futures
import fnmatch
import json
import os
import re
import sys

from langchain_core.tools import BaseTool

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value):
    """Sustituye `${VAR}` / `${VAR:-valor}` en todas las cadenas de la configuración. Si falta una variable, falla SIN mostrar valores."""
    if isinstance(value, str):
        def repl(m):
            name, default = m.group(1), m.group(2)
            if os.environ.get(name):
                return os.environ[name]
            if default is not None:
                return default
            raise RuntimeError(f"falta la variable de entorno {name}, que usa MCP_SERVERS_FILE")
        return _VAR.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _reason(exc: BaseException) -> str:
    """Causa legible: desenvuelve los ExceptionGroup de anyio y recorta (el mensaje nunca incluye cabeceras)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {str(exc)[:150]}"


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
    strict = os.getenv("MCP_STRICT", "0") == "1"
    prefix = os.getenv("MCP_TOOL_PREFIX", "0") == "1"

    tools: dict[str, BaseTool] = {}
    for server, connection in connections.items():
        try:      # cada servidor por separado: si uno falla (red, token, caído), los demás siguen disponibles
            loaded = run_sync(MultiServerMCPClient({server: expand_env(connection)}, tool_name_prefix=prefix).get_tools())
        except Exception as exc:
            if strict:
                raise RuntimeError(f"El servidor MCP «{server}» no respondió: {_reason(exc)}") from None
            print(f"⚠️  Servidor MCP «{server}» omitido: {_reason(exc)}", file=sys.stderr)
            continue
        for t in loaded:
            if t.name in tools:
                print(f"⚠️  La herramienta MCP «{t.name}» se repite entre servidores; se conserva la primera (usa MCP_TOOL_PREFIX=1).", file=sys.stderr)
                continue
            tools[t.name] = t

    readonly = [n.strip() for n in os.getenv("MCP_READONLY_TOOLS", "").split(",") if n.strip()]
    needs_approval = {name for name in tools if not any(fnmatch.fnmatchcase(name, pattern) for pattern in readonly)}
    return tools, needs_approval


def run_sync(coro):
    """Ejecuta una corutina desde código síncrono, incluso si ya hay un bucle de eventos (p. ej. Jupyter)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _as_text(result) -> str:
    """Una herramienta MCP devuelve una lista de bloques de contenido; se queda el texto. Las nativas ya devuelven texto."""
    if isinstance(result, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in result).strip()
    return str(result)


def drop_blank_args(args: dict) -> dict:
    """Quita los argumentos vacíos (None o solo espacios) antes de llamar a un servidor MCP.

    Algunos modelos no omiten los parámetros opcionales: mandan " " en cada uno, y el servidor ajeno los rechaza.
    `False` y `0` son valores legítimos y se conservan. Si faltara un parámetro obligatorio, el servidor lo dirá con claridad.
    """
    return {k: v for k, v in args.items() if not (v is None or (isinstance(v, str) and not v.strip()))}


def invoke_tool(tool: BaseTool, args: dict) -> str:
    """Invoca una herramienta; las que solo tienen versión asíncrona (las de MCP) se ejecutan con `ainvoke`."""
    if tool.func is None and getattr(tool, "coroutine", None) is not None:
        return _as_text(run_sync(tool.ainvoke(drop_blank_args(args))))
    return _as_text(tool.invoke(args))


if __name__ == "__main__":
    # `python -m trello_ops.mcp_tools` (o `task mcp:tools`): ¿qué herramientas MCP carga esta configuración y cuáles piden aprobación?
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    if os.getenv("USE_MCP", "0") != "1":
        raise SystemExit("MCP apagado: define USE_MCP=1 y MCP_SERVERS_FILE (ver mcp_servers.example.json).")
    found, needs = load_mcp_tools()
    for name in sorted(found):
        print(f"  {'⏸️  pide aprobación' if name in needs else '✅ libre           '}  {name}")
    print(f"\n{len(found)} herramientas MCP; {len(found) - len(needs)} corren sin aprobación.")
