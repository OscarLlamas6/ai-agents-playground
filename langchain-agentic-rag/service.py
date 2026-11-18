"""El servicio del agente: lo que comparten la API (api.py) y la línea de comandos (cli.py).

Una pregunta entra con un `session_id` (= el `thread_id` del checkpointer); sale con la respuesta, las herramientas usadas y
los tokens de ESTA pregunta. El proceso no guarda nada de la conversación: el estado vive en el checkpointer (Postgres).
"""

import os
import threading
import uuid
from pathlib import Path

from agent import build_agent, build_checkpointer, verify
from db import build_database
from memory import Context, build_store, load_store, save_store
from model import build_model
from retriever import IndexNotReady, Retriever

MANUAL = Path(__file__).parent / "data" / "mesa_de_ayuda.md"


class SessionNotFound(KeyError):
    pass


class AgentService:
    def __init__(self, db, retriever: Retriever, agent, checkpointer, store, memory_file: Path | None):
        self.db, self.retriever, self.agent, self.checkpointer, self.store, self.memory_file = db, retriever, agent, checkpointer, store, memory_file
        self._lock = threading.Lock()      # `retriever.retrieved` es compartido: una pregunta a la vez (suficiente para una demo)

    @classmethod
    def from_env(cls, auto_ingest: bool = False) -> "AgentService":
        db = build_database()
        retriever = Retriever(MANUAL, db)
        if auto_ingest:
            retriever.ingest()
        checkpointer = build_checkpointer(os.getenv("DATABASE_URL"))          # Postgres si hay DATABASE_URL; si no, RAM
        dsn = os.getenv("DATABASE_URL")
        memory_file = None
        if dsn:
            store = build_store(dsn)                                          # memoria de largo plazo en el mismo Postgres
        else:                                                                 # sin base: RAM, con volcado a un JSON para no perderla
            memory_file = Path(os.getenv("MEMORY_FILE", "state/preferences.json"))
            memory_file.parent.mkdir(parents=True, exist_ok=True)
            store = load_store(memory_file)
        agent = build_agent(build_model(), retriever, db, checkpointer=checkpointer, store=store)
        return cls(db, retriever, agent, checkpointer, store, memory_file)

    @staticmethod
    def _config(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 25}

    def chat(self, message: str, session_id: str | None = None, user_id: str = "demo") -> dict:
        if not self.retriever.ready:                                            # falla pronto y claro, antes de gastar tokens
            raise IndexNotReady("El manual no está indexado. Ejecuta primero: uv run python cli.py ingest")
        session_id = session_id or str(uuid.uuid4())                           # sin id → sesión nueva; el cliente debe reutilizar el que se le devuelve
        with self._lock:
            self.retriever.retrieved.clear()
            result = self.agent.invoke({"messages": [{"role": "user", "content": message}]}, self._config(session_id),
                                       context=Context(user_id=user_id))
            save_store(self.store, self.memory_file)                           # la memoria de largo plazo sobrevive a un reinicio
            messages = result["messages"]
            turn = messages[max(i for i, m in enumerate(messages) if m.type == "human"):]
            ai = [m for m in turn if m.type == "ai"]
            usage = [m.usage_metadata or {} for m in ai]
            answer = result.get("structured_response")
            return {
                "session_id": session_id, "user_id": user_id,
                "status": "answered" if answer is not None else "stopped",
                "answer": answer.answer if answer is not None else str(turn[-1].content),
                "found": answer.found if answer is not None else None,
                "citations": [{"id": c, "section": self.retriever.retrieved[c].section if c in self.retriever.retrieved else None}
                              for c in (answer.citations if answer is not None else [])],
                "problems": verify(answer, self.retriever) if answer is not None else [],
                "tools_used": [c["name"] for m in ai for c in m.tool_calls if c["name"] != "Answer"],
                "usage": {"input_tokens": sum(u.get("input_tokens", 0) for u in usage),
                          "output_tokens": sum(u.get("output_tokens", 0) for u in usage), "model_calls": len(ai)},
            }

    def history(self, session_id: str) -> list[dict]:
        state = self.agent.get_state(self._config(session_id))
        if not state.values.get("messages"):
            raise SessionNotFound(session_id)
        out = []
        for m in state.values["messages"]:
            if m.type == "human":
                out.append({"role": "user", "content": str(m.content)})
            elif m.type == "ai" and not any(c["name"] == "Answer" for c in m.tool_calls):
                if m.tool_calls:
                    out.append({"role": "assistant", "tool_calls": [{"name": c["name"], "args": c["args"]} for c in m.tool_calls]})
                elif m.content:
                    out.append({"role": "assistant", "content": str(m.content)})
            elif m.type == "ai":                                                # la respuesta final viaja como llamada a "Answer"
                call = next(c for c in m.tool_calls if c["name"] == "Answer")
                out.append({"role": "assistant", "content": call["args"].get("answer", "")})
            elif m.type == "tool" and m.name != "Answer":
                out.append({"role": "tool", "name": m.name, "content": str(m.content)[:500]})
        return out

    def delete(self, session_id: str) -> None:
        self.history(session_id)                                                # 404 si no existe
        self.checkpointer.delete_thread(session_id)

    def ready(self) -> bool:
        return self.retriever.ready

    def close(self) -> None:
        self.db.close()
