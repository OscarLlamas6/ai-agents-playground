"""Ingeniería del bucle (loop engineering): ¿cuándo debe detenerse un agente?

Un bucle de agente necesita tres cosas: qué lo dispara, cómo se sabe que terminó y qué límites lo detienen.
`create_agent` ya trae el tope de llamadas (ModelCallLimitMiddleware, ToolCallLimitMiddleware). Aquí se añaden
los dos que faltan, escritos como middleware propio:

- `TokenBudget`: corta cuando la ejecución actual ya gastó más tokens de los permitidos.
- `NoProgress`: corta cuando el modelo pide EXACTAMENTE la misma herramienta con los mismos argumentos otra vez.
"""

import json

from langchain.agents.middleware import after_model
from langchain_core.messages import AIMessage


def _current_run(messages: list) -> list:
    """Los mensajes de la pregunta actual (desde el último mensaje humano)."""
    start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
    return messages[start:]


def token_budget(max_tokens: int):
    """Middleware: detiene al agente si la ejecución actual supera `max_tokens` (entrada + salida)."""

    @after_model(can_jump_to=["end"], name="TokenBudget")
    def guard(state, runtime):
        used = sum((m.usage_metadata or {}).get("total_tokens", 0) for m in _current_run(state["messages"]) if m.type == "ai")
        if used > max_tokens:
            note = f"Detenido: se agotó el presupuesto de tokens ({used} > {max_tokens})."
            return {"messages": [AIMessage(content=note)], "jump_to": "end"}
        return None

    return guard


def no_progress():
    """Middleware: detiene al agente si repite la misma llamada a una herramienta (mismo nombre y mismos argumentos)."""

    @after_model(can_jump_to=["end"], name="NoProgress")
    def guard(state, runtime):
        run = _current_run(state["messages"])
        seen: set[str] = set()
        for m in (m for m in run if m.type == "ai" and m.tool_calls):
            for c in m.tool_calls:
                if c["name"] == "Answer":                       # la salida estructurada no cuenta como herramienta
                    continue
                signature = f"{c['name']}:{json.dumps(c['args'], sort_keys=True, ensure_ascii=False)}"
                if signature in seen:
                    return {"messages": [AIMessage(content=f"Detenido: sin progreso, repitió {c['name']} con los mismos argumentos.")],
                            "jump_to": "end"}
                seen.add(signature)
        return None

    return guard
