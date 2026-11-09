"""El MISMO bucle, escrito a mano: lo que `create_agent` hace por ti.

Sigue usando el modelo y las herramientas de LangChain, pero el bucle lo escribes tú.

    uv run python manual_loop.py "¿Cómo está la VPN?"
"""

import sys

from langchain.messages import HumanMessage, SystemMessage

from model import build_model
from tools import get_service_status, uptime_percent
from traces import print_trace

tools = [get_service_status, uptime_percent]
tools_by_name = {t.name: t for t in tools}
model_with_tools = build_model().bind_tools(tools)                  # le describe las herramientas (esquemas) al modelo

messages = [
    SystemMessage("Eres asistente de una mesa de ayuda de TI. Usa las herramientas; no inventes datos. Responde breve."),
    HumanMessage(" ".join(sys.argv[1:]) or "¿Cómo está la VPN?"),
]

for step in range(4):                                               # el límite de vueltas lo pones tú
    reply = model_with_tools.invoke(messages)                       # 1. el modelo responde...
    messages.append(reply)
    if not reply.tool_calls:                                        # 2. ...si no pide herramientas, terminó
        break
    for call in reply.tool_calls:                                   # 3. ...si pide, TU CÓDIGO las ejecuta
        messages.append(tools_by_name[call["name"]].invoke(call))   #    el resultado (un ToolMessage) vuelve al modelo

print_trace(messages)
