"""API HTTP del agente (FastAPI). El agente NO guarda estado en el proceso: el estado vive en el checkpointer (SQLite) y el
cliente solo lleva el `session_id`, que es el `thread_id` de LangGraph.

    uv run uvicorn api:app --port 8011          # documentación interactiva en http://127.0.0.1:8011/docs
"""

import os
import re
import sqlite3
import threading
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from dotenv import find_dotenv, load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from langchain_core.messages import BaseMessage
from langgraph.types import Command
from pydantic import BaseModel, Field

from trello_ops.graph import build_default_graph
from trello_ops.persistence import open_persistence
from trello_ops.state import Context
from trello_ops.tools import WRITE_TOOLS

load_dotenv(find_dotenv(usecwd=True))

ID = r"^[A-Za-z0-9_-]{1,64}$"          # session_id y user_id: sin espacios ni símbolos raros


# ───────────────────────── Modelos de entrada y salida (se ven en /docs) ─────────────────────────


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000, description="Mensaje del usuario")
    session_id: str | None = Field(default=None, pattern=ID, description="Id de la sesión (= thread_id). Vacío: se crea una nueva")
    user_id: str = Field(default="demo", pattern=ID, description="Dueño de la sesión y clave de su memoria de largo plazo")


class ApproveRequest(BaseModel):
    approved: bool
    reason: str = Field(default="", max_length=500, description="Motivo (opcional); el modelo lo recibe si se rechaza")


class Action(BaseModel):
    id: str
    tool: str
    args: dict
    summary: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0


class WriteResult(BaseModel):
    tool: str
    status: Literal["ok", "rejected", "error"]


class ChatResponse(BaseModel):
    session_id: str
    status: Literal["completed", "awaiting_approval", "halted"]
    answer: str | None = None
    pending_actions: list[Action] = []        # lo que espera aprobación (status = awaiting_approval)
    tools_used: list[str] = []
    writes: list[WriteResult] = []            # escrituras ejecutadas o rechazadas en este turno
    usage: Usage


class Message(BaseModel):
    role: Literal["human", "ai", "tool"]
    content: str
    tool_calls: list[dict] = []


class SessionOut(BaseModel):
    session_id: str
    user_id: str
    status: Literal["completed", "awaiting_approval"]
    pending_actions: list[Action] = []
    messages: list[Message]


class SessionSummary(BaseModel):
    session_id: str
    user_id: str
    created_at: str


# ───────────────────────── Registro de sesiones (quién es dueño de cada thread_id) ─────────────────────────


class Sessions:
    """Tabla `api_sessions` en el mismo archivo SQLite que el checkpointer."""

    def __init__(self, db_path: str | None):
        self.conn = sqlite3.connect(db_path or ":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS api_sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL)")
        self.conn.commit()

    def get(self, session_id: str) -> tuple[str, str] | None:
        row = self.conn.execute("SELECT user_id, created_at FROM api_sessions WHERE session_id=?", (session_id,)).fetchone()
        return tuple(row) if row else None

    def create(self, session_id: str, user_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO api_sessions VALUES (?,?,?)", (session_id, user_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        self.conn.commit()

    def delete(self, session_id: str) -> None:
        self.conn.execute("DELETE FROM api_sessions WHERE session_id=?", (session_id,))
        self.conn.commit()

    def list(self, user_id: str | None) -> list[SessionSummary]:
        sql, args = ("SELECT session_id,user_id,created_at FROM api_sessions WHERE user_id=? ORDER BY created_at", (user_id,)) if user_id else \
                    ("SELECT session_id,user_id,created_at FROM api_sessions ORDER BY created_at", ())
        return [SessionSummary(session_id=a, user_id=b, created_at=c) for a, b, c in self.conn.execute(sql, args)]


# ───────────────────────── La aplicación ─────────────────────────


def create_app(db_path: str | None = None, build=build_default_graph) -> FastAPI:
    """`build(checkpointer=..., store=...)` devuelve el grafo; en las pruebas se pasa uno con modelo guionado."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.graph, app.state.error, app.state.lock = None, None, threading.Lock()
        with ExitStack() as stack:
            try:
                checkpointer, store = stack.enter_context(open_persistence(db_path))
                app.state.sessions = Sessions(db_path)
                app.state.checkpointer = checkpointer
                app.state.graph = build(checkpointer=checkpointer, store=store)
            except Exception as exc:                       # sin base o sin grafo: la API arranca y responde 503 con el motivo
                app.state.error = f"{type(exc).__name__}: {exc}"
            yield

    app = FastAPI(title="Agente de operaciones de Trello", version="1.0.0", lifespan=lifespan,
                  description="Agente LangGraph expuesto por HTTP. `session_id` = `thread_id` del checkpointer; las escrituras esperan aprobación humana.")

    def graph_of(request: Request):
        if request.app.state.graph is None:
            raise HTTPException(503, f"El agente no está disponible: {request.app.state.error}")
        return request.app.state.graph

    def config_of(session_id: str) -> dict:
        return {"configurable": {"thread_id": session_id}, "recursion_limit": 40}

    def pending_of(snapshot) -> list[Action]:
        return [Action(**a) for t in snapshot.tasks for i in t.interrupts for a in i.value["actions"]]

    def turn_response(session_id: str, snapshot) -> ChatResponse:
        """Resume lo ocurrido desde el último mensaje humano (el turno actual)."""
        values = snapshot.values
        messages: list[BaseMessage] = values["messages"]
        start = max(i for i, m in enumerate(messages) if m.type == "human")
        turn = messages[start + 1:]
        pending = pending_of(snapshot)
        tools_used = [c["name"] for m in turn if m.type == "ai" for c in m.tool_calls]
        writes = [WriteResult(tool=m.name, status="rejected" if m.content.startswith("RECHAZADA") else "error" if m.content.startswith("ERROR") else "ok")
                  for m in turn if m.type == "tool" and m.name in WRITE_TOOLS]
        ai = [m for m in turn if m.type == "ai"]
        usage = Usage(model_calls=values.get("steps", 0),
                      input_tokens=sum((m.usage_metadata or {}).get("input_tokens", 0) for m in ai),
                      output_tokens=sum((m.usage_metadata or {}).get("output_tokens", 0) for m in ai))
        status = "awaiting_approval" if pending else "halted" if values.get("halted") else "completed"
        answer = None if pending else (ai[-1].text if ai else None)
        return ChatResponse(session_id=session_id, status=status, answer=answer, pending_actions=pending, tools_used=tools_used, writes=writes, usage=usage)

    def owned_session(request: Request, session_id: str, user_id: str | None = None) -> str:
        found = request.app.state.sessions.get(session_id)
        if not found:
            raise HTTPException(404, f"La sesión {session_id} no existe")
        if user_id and found[0] != user_id:
            raise HTTPException(403, "La sesión pertenece a otro usuario")
        return found[0]

    @app.get("/health")
    def health(request: Request):
        ok = request.app.state.graph is not None
        return {"status": "ok" if ok else "degraded", "error": request.app.state.error, "llm_provider": os.getenv("LLM_PROVIDER", "fake"),
                "trello_mode": os.getenv("TRELLO_MODE", "fake"), "persistence": db_path or "memory", "sessions_survive_restart": bool(db_path)}

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request, graph=Depends(graph_of)):
        import uuid

        session_id = body.session_id or str(uuid.uuid4())
        with request.app.state.lock:
            if request.app.state.sessions.get(session_id):
                owned_session(request, session_id, body.user_id)
                if pending_of(graph.get_state(config_of(session_id))):
                    raise HTTPException(409, "La sesión espera una aprobación: usa POST /sessions/{id}/approve")
            else:
                request.app.state.sessions.create(session_id, body.user_id)
            graph.invoke({"messages": [{"role": "user", "content": body.message}]}, config_of(session_id), context=Context(body.user_id))
            return turn_response(session_id, graph.get_state(config_of(session_id)))

    @app.post("/sessions/{session_id}/approve", response_model=ChatResponse)
    def approve(session_id: str, body: ApproveRequest, request: Request, graph=Depends(graph_of)):
        user_id = owned_session(request, session_id)
        with request.app.state.lock:
            if not pending_of(graph.get_state(config_of(session_id))):
                raise HTTPException(409, "La sesión no tiene ninguna acción pendiente de aprobación")
            graph.invoke(Command(resume={"approved": body.approved, "reason": body.reason}), config_of(session_id), context=Context(user_id))
            return turn_response(session_id, graph.get_state(config_of(session_id)))

    @app.get("/sessions", response_model=list[SessionSummary])
    def list_sessions(request: Request, user_id: str | None = None, graph=Depends(graph_of)):
        return request.app.state.sessions.list(user_id)

    @app.get("/sessions/{session_id}", response_model=SessionOut)
    def get_session(session_id: str, request: Request, graph=Depends(graph_of)):
        user_id = owned_session(request, session_id)
        snapshot = graph.get_state(config_of(session_id))
        pending = pending_of(snapshot)
        messages = [Message(role=m.type, content=m.text, tool_calls=[{"name": c["name"], "args": c["args"]} for c in getattr(m, "tool_calls", [])])
                    for m in snapshot.values.get("messages", []) if m.type in ("human", "ai", "tool")]
        return SessionOut(session_id=session_id, user_id=user_id, status="awaiting_approval" if pending else "completed", pending_actions=pending, messages=messages)

    @app.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str, request: Request, graph=Depends(graph_of)):
        owned_session(request, session_id)
        with request.app.state.lock:
            request.app.state.checkpointer.delete_thread(session_id)
            request.app.state.sessions.delete(session_id)

    return app


app = create_app(os.getenv("OPS_DB", ".data/ops.sqlite"))
