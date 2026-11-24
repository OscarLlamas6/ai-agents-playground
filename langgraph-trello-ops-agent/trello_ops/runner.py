"""Cómo se EJECUTA el grafo: invocar, detectar la pausa (`__interrupt__`), preguntar a la persona y reanudar."""

from collections.abc import Callable

from langgraph.types import Command

from trello_ops.state import Context
from trello_ops.tools import WRITE_TOOLS

Decision = bool | dict     # True/False, o {"approved": bool, "reason": str}


def ask_in_terminal(request: dict) -> Decision:
    """Muestra las acciones propuestas y pide la decisión: `s` aprueba; `n motivo` rechaza (con motivo opcional)."""
    # No todo lo que pide aprobación escribe: una herramienta MCP no declarada de solo lectura también se pausa (mínimo privilegio).
    if all(a["tool"] in WRITE_TOOLS for a in request["actions"]):
        print("\n⏸️  El agente quiere ESCRIBIR en Trello y espera tu aprobación:")
    else:
        print("\n⏸️  El agente quiere usar herramientas que requieren tu aprobación (no declaradas de solo lectura):")
    for a in request["actions"]:
        print(f"   • {a['summary']}")
    answer = input("   ¿Apruebas? [s = sí · n = no (puedes añadir el motivo)] > ").strip()
    if answer.lower().startswith("s"):
        return {"approved": True}
    return {"approved": False, "reason": answer[1:].strip() or "rechazada"}


def drive(graph, user_text: str, thread_id: str, *, user_id: str = "demo", decide: Callable[[dict], Decision] = ask_in_terminal,
          trace: Callable[[str], None] | None = None, recursion_limit: int = 40) -> dict:
    """Envía un mensaje y, mientras el grafo se pause pidiendo aprobación, consulta `decide` y reanuda.

    `recursion_limit` es la red de seguridad de LangGraph (superpasos máximos); el tope "de negocio" es `max_steps`.
    """
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}
    payload = {"messages": [{"role": "user", "content": user_text}]}
    while True:
        interrupted = None
        for update in graph.stream(payload, config, context=Context(user_id), stream_mode="updates"):
            for node, delta in update.items():
                if node == "__interrupt__":
                    interrupted = delta[0].value
                elif trace:
                    trace(f"▶ {node}")
        if interrupted is None:
            return graph.get_state(config).values
        payload = Command(resume=decide(interrupted))        # la respuesta de la persona es el valor de retorno de interrupt()
