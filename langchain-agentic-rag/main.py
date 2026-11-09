"""RAG agéntico: el modelo decide si busca en el manual, qué busca y cuántas veces.

    uv run python main.py "¿Cada cuánto vence mi contraseña?"
    uv run python main.py --trace "Olvidé mi contraseña y estoy en casa: ¿cómo la restablezco y cómo me conecto?"
    uv run python main.py --chat            # conversación con memoria (preguntas de seguimiento)
"""

import argparse
import uuid
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from agent import build_agent, build_checkpointer, verify
from model import build_model
from retriever import Retriever

MANUAL = Path(__file__).parent / "data" / "mesa_de_ayuda.md"


def print_trace(messages) -> None:
    """Muestra el camino del agente: 👤 pregunta, 🤖 llamadas a herramientas, 🔧 resultados."""
    for m in messages:
        if m.type == "human":
            print(f"👤 {m.content}")
        elif m.type == "ai":
            for call in m.tool_calls:
                if call["name"] != "Answer":          # "Answer" es la salida estructurada, no una herramienta real
                    print(f"🤖 pide {call['name']}({call['args']})")
        elif m.type == "tool" and m.name != "Answer":
            print(f"🔧 {m.content[:300]}")


def ask(agent, retriever: Retriever, question: str, thread_id: str, trace: bool) -> None:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
    result = agent.invoke({"messages": [{"role": "user", "content": question}]}, config)
    if trace:
        last_turn = max(i for i, m in enumerate(result["messages"]) if m.type == "human")   # solo la pregunta de ahora
        print_trace(result["messages"][last_turn:])
    answer = result["structured_response"]
    print(f"\n{answer.answer}")
    for cid in answer.citations:
        chunk = retriever.retrieved.get(cid)
        print(f"  [{cid}] {chunk.section if chunk else '⚠️ no existe'}")
    for problem in verify(answer, retriever):
        print(f"  ⚠️ {problem}")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG agéntico con LangChain")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--trace", action="store_true", help="muestra las herramientas que pidió el agente")
    ap.add_argument("--chat", action="store_true", help="modo conversación, con memoria de corto plazo")
    args = ap.parse_args()
    load_dotenv(find_dotenv(usecwd=True))

    retriever = Retriever(MANUAL)
    agent = build_agent(build_model(), retriever, checkpointer=build_checkpointer())
    thread = str(uuid.uuid4())                     # una conversación = un thread_id
    if args.chat:
        while question := input("\nTú > ").strip():
            ask(agent, retriever, question, thread, args.trace)
    elif args.question:
        ask(agent, retriever, args.question, thread, args.trace)
    else:
        ap.error("indica una pregunta o usa --chat")


if __name__ == "__main__":
    main()
