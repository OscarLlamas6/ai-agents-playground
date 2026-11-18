"""Un cliente MCP mínimo (SDK oficial, sin LangChain) para EXPLORAR y DEPURAR cualquier servidor.

    uv run python mini_client.py                                  # el servidor de referencia mcp-server-time (versión fijada)
    uv run python mini_client.py -- python mcp_server.py          # nuestro servidor de HubSpot (simulado)
    uv run python mini_client.py --url http://127.0.0.1:8000/mcp  # un servidor por HTTP

Hace: initialize → tools/list → resources/list → prompts/list y, si existen, lee un recurso y pide un prompt.
"""

import asyncio
import json
import sys
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

REFERENCE = ["uvx", "mcp-server-time==2026.11.11"]   # servidor oficial de referencia, con la versión fijada


@asynccontextmanager
async def connect(command: list[str] | None = None, url: str | None = None):
    if url:
        async with streamablehttp_client(url) as (read, write, _), ClientSession(read, write) as session:
            yield session
    else:
        params = StdioServerParameters(command=command[0], args=command[1:])   # stdio: el cliente lanza el servidor
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            yield session


async def explore(command: list[str] | None = None, url: str | None = None) -> None:
    async with connect(command, url) as session:
        init = await session.initialize()                                   # 1) apretón de manos: versión y capacidades
        print(f"servidor: {init.serverInfo.name} · protocolo {init.protocolVersion}")
        print(f"capacidades: {sorted(init.capabilities.model_dump(exclude_none=True))}")
        for t in (await session.list_tools()).tools:                        # 2) descubrimiento
            ann = t.annotations.model_dump(exclude_none=True) if t.annotations else {}
            print(f"🔧 {t.name}: {(t.description or '').splitlines()[0]}  {ann or ''}")
            print(f"     entrada: {json.dumps(t.inputSchema.get('properties', {}), ensure_ascii=False)[:150]}")
        if init.capabilities.resources:
            for r in (await session.list_resources()).resources:
                print(f"📄 recurso {r.uri}")
            for r in (await session.list_resource_templates()).resourceTemplates:
                print(f"📄 plantilla de recurso {r.uriTemplate}")
        if init.capabilities.prompts:
            for p in (await session.list_prompts()).prompts:
                print(f"💬 prompt {p.name}({', '.join(a.name for a in p.arguments or [])})")
        names = {t.name for t in (await session.list_tools()).tools}
        if "get_current_time" in names:                                     # 3) una llamada de ejemplo
            r = await session.call_tool("get_current_time", {"timezone": "America/Guatemala"})
            print("llamada get_current_time →", r.content[0].text)
        if "search_contacts" in names:
            r = await session.read_resource("hubspot://contact/101")
            print("lectura del recurso hubspot://contact/101 →\n" + r.contents[0].text)


if __name__ == "__main__":
    url = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else None
    cmd = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else (None if url else REFERENCE)
    asyncio.run(explore(cmd, url))
