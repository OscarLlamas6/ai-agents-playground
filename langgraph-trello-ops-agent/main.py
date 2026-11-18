"""Agente de operaciones de Trello (LangGraph). Escrituras con aprobación humana.

    uv run python main.py "Muestra mis tableros"
    uv run python main.py --trace "Crea una tarjeta \"Revisar alertas de disco\""
    uv run python main.py --chat --db .data/ops.sqlite        # memoria que sobrevive a cerrar el programa
"""

import argparse
import os
import sys
import uuid

from dotenv import find_dotenv, load_dotenv

from trello_ops.graph import build_default_graph
from trello_ops.persistence import open_persistence
from trello_ops.runner import ask_in_terminal, drive


def print_turn(state: dict) -> None:
    """Muestra la traza del último turno: 👤 pregunta, 🤖 peticiones, 🔧 resultados, y la respuesta."""
    messages = state["messages"]
    start = max(i for i, m in enumerate(messages) if m.type == "human")
    for m in messages[start:-1]:
        if m.type == "human":
            print(f"👤 {m.text}")
        elif m.type == "ai":
            for c in m.tool_calls:
                print(f"🤖 pide {c['name']}({c['args']})")
        elif m.type == "tool":
            print(f"🔧 {m.content[:200]}")
    print(f"\n{messages[-1].text}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Agente de operaciones de Trello con LangGraph")
    ap.add_argument("message", nargs="?")
    ap.add_argument("--chat", action="store_true", help="conversación con memoria de corto plazo")
    ap.add_argument("--trace", action="store_true", help="muestra los nodos y herramientas del recorrido")
    ap.add_argument("--user", default="demo", help="usuario: clave de la memoria de largo plazo (Store)")
    ap.add_argument("--thread", default=None, help="id del hilo (conversación); por defecto uno nuevo")
    ap.add_argument("--db", default=os.getenv("OPS_DB"), help="archivo SQLite: hace persistentes la conversación y las preferencias")
    ap.add_argument("--approve", choices=["ask", "yes", "no"], default="ask",
                    help="ask = preguntar (por defecto) · yes/no = decidir sin preguntar (solo para demostraciones y pruebas)")
    args = ap.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    if not args.chat and not args.message:
        ap.error("indica un mensaje o usa --chat")
    if args.approve == "ask" and not sys.stdin.isatty() and not args.chat:
        print("(sin terminal interactiva: las escrituras se rechazan; usa --approve yes para aprobarlas)", file=sys.stderr)

    decide = {"ask": ask_in_terminal, "yes": lambda r: {"approved": True}, "no": lambda r: {"approved": False, "reason": "rechazada"}}[args.approve]
    if args.approve == "ask" and not sys.stdin.isatty():
        decide = lambda r: {"approved": False, "reason": "sin terminal interactiva"}   # noqa: E731

    thread = args.thread or str(uuid.uuid4())
    with open_persistence(args.db) as (checkpointer, store):
        graph = build_default_graph(checkpointer=checkpointer, store=store)
        say = print if args.trace else None
        if args.chat:
            print(f"(hilo {thread} · usuario {args.user} · línea vacía para salir)")
            while text := input("\nTú > ").strip():
                print_turn(drive(graph, text, thread, user_id=args.user, decide=decide, trace=say))
        else:
            print_turn(drive(graph, args.message, thread, user_id=args.user, decide=decide, trace=say))


if __name__ == "__main__":
    main()
