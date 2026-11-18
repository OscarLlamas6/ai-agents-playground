"""El agente: `create_agent` + herramientas MCP (langchain-mcp-adapters) + aprobación humana antes de escribir."""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, ToolCallLimitMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

SYSTEM_PROMPT = """Eres un asistente de ventas con acceso a un CRM mediante herramientas. Respondes en español.
- Para preguntas sobre contactos o sus notas, usa las herramientas: no inventes datos.
- Solo guardas una nota si la persona lo pidió explícitamente.
- Si una herramienta devuelve un error, léelo y corrige tu llamada."""


def build_agent(model, tools, *, approve_writes: bool = True, checkpointer=None):
    """Las herramientas llegan ya convertidas de MCP a `@tool` de LangChain: el agente no sabe de dónde vienen.

    Con `approve_writes=True` el agente se PAUSA antes de `add_note` (HumanInTheLoopMiddleware, visto en la S9) y una
    persona aprueba o rechaza. Pausar y reanudar exige un checkpointer y un thread_id.
    """
    middleware: list[Any] = [ToolCallLimitMiddleware(run_limit=8)]   # tope de llamadas a herramientas por pregunta
    if approve_writes:
        middleware.append(HumanInTheLoopMiddleware(interrupt_on={"add_note": {"allowed_decisions": ["approve", "reject"]}},
                                                   description_prefix="El agente quiere ESCRIBIR en el CRM"))
    return create_agent(model, tools=tools, system_prompt=SYSTEM_PROMPT, middleware=middleware, checkpointer=checkpointer or InMemorySaver())


Decide = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def approve_all(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "approve"} for _ in requests]


def reject_all(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"type": "reject", "message": "La persona no autorizó la escritura."} for _ in requests]


async def run_turn(agent, question: str, thread_id: str, decide: Decide = reject_all, recursion_limit: int = 25) -> dict[str, Any]:
    """Un turno completo. Si el agente se pausa pidiendo aprobación, `decide` (la persona) responde y se reanuda."""
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}
    payload: Any = {"messages": [{"role": "user", "content": question}]}
    approvals: list[dict[str, Any]] = []
    while True:
        result = await agent.ainvoke(payload, config)
        if not result.get("__interrupt__"):
            result["approvals"] = approvals
            return result
        request = result["__interrupt__"][0].value
        decisions = decide(request["action_requests"])
        approvals += [{"action": a["name"], "args": a["args"], "decision": d["type"]} for a, d in zip(request["action_requests"], decisions)]
        payload = Command(resume={"decisions": decisions})
