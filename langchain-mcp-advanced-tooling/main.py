"""Agente LangChain que consume el servidor MCP de HubSpot (simulado por defecto).

    uv run python main.py --trace "Resume las notas de Luis."                      # servidor MCP por stdio
    uv run python main.py --trace "Agrega una nota a Ana: 'Enviar la propuesta'"   # te pregunta antes de escribir
    uv run python main.py --multi --trace "¿Qué hora es en America/Guatemala?"     # nuestro servidor + el de hora a la vez
    uv run python main.py --url http://127.0.0.1:8000/mcp "Busca a Ana"            # el servidor corre aparte, por HTTP
"""

import argparse
import asyncio
import uuid

from dotenv import find_dotenv, load_dotenv

from agent import approve_all, build_agent, reject_all, run_turn
from model import build_model
from runtime import http_tools, multi_tools, stdio_tools


def ask_human(requests):
    """La aprobación humana: muestra QUÉ se va a escribir y espera sí/no."""
    decisions = []
    for r in requests:
        print(f"\n⚠️  El agente quiere ejecutar {r['name']} con:")
        for k, v in r["args"].items():
            print(f"     {k}: {v}")
        ok = input("   ¿Apruebas? [s/N] > ").strip().lower() in ("s", "si", "sí", "y", "yes")
        decisions.append({"type": "approve"} if ok else {"type": "reject", "message": "La persona no autorizó la escritura."})
    return decisions


def print_trace(messages) -> None:
    for m in messages:
        if m.type == "human":
            print(f"👤 {m.text}")
        elif m.type == "ai":
            for c in m.tool_calls:
                print(f"🤖 pide {c['name']}({c['args']})")
        elif m.type == "tool":
            print(f"🔧 {m.text[:300]}")


async def amain(args) -> None:
    tools = await (http_tools(args.url) if args.url else multi_tools() if args.multi else stdio_tools())
    print(f"Herramientas MCP: {[t.name for t in tools]}")
    agent = build_agent(build_model(), tools)
    decide = {"approve": approve_all, "reject": reject_all, None: ask_human}[args.auto]
    result = await run_turn(agent, args.question, str(uuid.uuid4()), decide)
    if args.trace:
        print_trace(result["messages"])
    print(f"\n{result['messages'][-1].text}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agente LangChain + servidor MCP de HubSpot")
    ap.add_argument("question")
    ap.add_argument("--trace", action="store_true", help="muestra las herramientas que pidió el agente")
    ap.add_argument("--multi", action="store_true", help="conecta DOS servidores: HubSpot y el de hora (mcp-server-time, vía uvx)")
    ap.add_argument("--auto", choices=["approve", "reject"], help="responde solo a la aprobación (pruebas)")
    ap.add_argument("--url", help="URL de un servidor MCP por HTTP, p. ej. http://127.0.0.1:8000/mcp (por defecto: stdio)")
    args = ap.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    asyncio.run(amain(args))


if __name__ == "__main__":
    main()
