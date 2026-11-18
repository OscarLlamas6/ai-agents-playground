"""Bitácora de consultas: la escribe el CÓDIGO después de cada pregunta, no el modelo.

Una auditoría que depende de que el modelo "se acuerde" de llamarla no es una auditoría. Además, el modelo no puede
conocer los tokens de su propia respuesta: se cuentan al terminar. Por eso se hace con un middleware que corre
`after_agent`, cuando la pregunta ya terminó y todos los mensajes (con su uso de tokens) están en el estado.
"""

from langchain.agents.middleware import after_agent
from langgraph.config import get_config


def audit_middleware(db):
    @after_agent(name="AuditLog")
    def log(state, runtime):
        messages = state["messages"]
        start = max((i for i, m in enumerate(messages) if m.type == "human"), default=0)
        run = messages[start:]                                         # solo esta pregunta, no el historial anterior
        ai = [m for m in run if m.type == "ai"]
        usage = [m.usage_metadata or {} for m in ai]
        tools = [c["name"] for m in ai for c in m.tool_calls if c["name"] != "Answer"]
        answer = state.get("structured_response")
        stopped = None if answer is not None else str(messages[-1].content)[:200]    # un guardián cortó el bucle
        try:
            thread_id = get_config()["configurable"].get("thread_id")
        except Exception:                                              # fuera de un contexto de ejecución (pruebas)
            thread_id = None
        question = run[0].content if run and run[0].type == "human" else ""
        db.log_query(
            user_id=getattr(runtime.context, "user_id", None), thread_id=thread_id,
            question=question if isinstance(question, str) else str(question),
            input_tokens=sum(u.get("input_tokens", 0) for u in usage), output_tokens=sum(u.get("output_tokens", 0) for u in usage),
            model_calls=len(ai), tools_used=tools, found=None if answer is None else answer.found, stopped_by=stopped,
        )
        return None

    return log
