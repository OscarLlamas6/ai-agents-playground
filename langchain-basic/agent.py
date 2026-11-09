"""Un agente de IA mínimo con LangChain. Cada elemento está numerado: lee los comentarios (1) a (8).

    uv run python agent.py "¿Cómo está la VPN?"
    uv run python agent.py --trace "¿Cómo está la VPN y cuál es la disponibilidad con 45 minutos caída?"
"""

import sys

from langchain.agents import create_agent
from langgraph.errors import GraphRecursionError

from model import build_model
from tools import get_service_status, uptime_percent
from traces import print_trace

show_trace = "--trace" in sys.argv
question = " ".join(a for a in sys.argv[1:] if a != "--trace") or "¿Cómo está la VPN?"

model = build_model()                                                              # (1) el modelo
tools = [get_service_status, uptime_percent]                                       # (2) las herramientas
SYSTEM_PROMPT = "Eres asistente de una mesa de ayuda de TI. Usa las herramientas; no inventes datos. Responde breve."  # (3) el prompt de sistema
agent = create_agent(model, tools=tools, system_prompt=SYSTEM_PROMPT)              # (4) el agente: modelo + herramientas + bucle

try:
    result = agent.invoke(                                                         # (5) entrada: una lista de mensajes
        {"messages": [{"role": "user", "content": question}]},
        config={"recursion_limit": 8},                                             # (6) límite del bucle: se detiene tras ~4 vueltas
    )
except GraphRecursionError:
    sys.exit("El agente alcanzó el límite de pasos sin terminar.")                 # (6) lo que pasa al topar el límite

if show_trace:
    print_trace(result["messages"])                                                # (7) la traza: todos los mensajes, en orden
    print()
print(result["messages"][-1].text)                                                 # (8) la salida: el último mensaje es la respuesta
