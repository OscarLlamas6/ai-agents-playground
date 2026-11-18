"""API HTTP del agente (FastAPI). El agente queda detrás de unos pocos endpoints; el estado vive en SQLite.

    uv run uvicorn api:app --port 8001        # documentación interactiva en http://127.0.0.1:8001/docs

Idea central: el agente es un servicio SIN estado en el proceso. El historial de cada conversación lo guarda el
checkpointer (SQLite) bajo un `thread_id`, y el cliente solo lleva el `session_id` (que ES ese thread_id).
Mismo session_id = misma conversación, también después de reiniciar la API.
"""


import asyncio
import os
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

import aiosqlite
from fastapi import Depends, FastAPI, HTTPException, Request
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import build_agent
from model import build_model
from runtime import persistent_tools
from settings import Settings

SessionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
UserId = Annotated[str, Field(pattern=r"^[\w.@-]{1,64}$")]


# ───────────────────────── Modelos de entrada y salida (se ven en /docs) ─────────────────────────


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000, examples=["Resume las notas de Luis."])
    session_id: SessionId | None = Field(default=None, description="Vacío = sesión nueva. Con un id existente, continúa esa conversación.")
    user_id: UserId = Field(default="demo", examples=["demo"])


class PendingAction(BaseModel):
    tool: str
    args: dict[str, Any]
    description: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0


class ChatOut(BaseModel):
    session_id: str
    user_id: str
    status: Literal["completed", "awaiting_approval"]
    answer: str
    tools_used: list[str]
    pending_action: PendingAction | None = None
    usage: Usage


class ApproveIn(BaseModel):
    approved: bool
    reason: str | None = Field(default=None, max_length=300, description="Opcional: por qué se rechaza.")


class MessageOut(BaseModel):
    role: Literal["human", "ai", "tool"]
    content: str
    tool_calls: list[dict[str, Any]] = []
    tool_name: str | None = None


class SessionOut(BaseModel):
    session_id: str
    user_id: str
    created_at: str
    status: Literal["completed", "awaiting_approval"]
    pending_action: PendingAction | None = None
    messages: list[MessageOut]


class SessionSummary(BaseModel):
    session_id: str
    user_id: str
    created_at: str


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    model_provider: str
    hubspot_mode: str
    mcp: str
    tools: list[str]
    error: str | None = None


# ───────────────────────── Utilidades ─────────────────────────


def text_of(m) -> str:
    return m.text if hasattr(m, "text") else str(m.content)


def turn_messages(messages: list) -> list:
    """Los mensajes del turno actual: desde el último mensaje humano."""
    start = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    return messages[start:]


def to_message_out(m) -> MessageOut:
    role = {"human": "human", "ai": "ai", "tool": "tool"}.get(m.type, "ai")
    calls = [{"name": c["name"], "args": c["args"]} for c in getattr(m, "tool_calls", []) or []]
    return MessageOut(role=role, content=text_of(m), tool_calls=calls, tool_name=getattr(m, "name", None) if isinstance(m, ToolMessage) else None)


def pending_from(interrupts) -> PendingAction | None:
    for i in interrupts or []:
        req = i.value["action_requests"][0]
        return PendingAction(tool=req["name"], args=req["args"], description=req.get("description"))
    return None


def usage_of(messages: list) -> Usage:
    u = Usage()
    for m in messages:
        if isinstance(m, AIMessage):
            u.model_calls += 1
            meta = m.usage_metadata or {}
            u.input_tokens += meta.get("input_tokens", 0)
            u.output_tokens += meta.get("output_tokens", 0)
    return u


# ───────────────────────── La aplicación ─────────────────────────

ToolsFactory = Callable[[], Any]   # devuelve un context manager asíncrono que entrega las herramientas MCP


def create_app(settings: Settings | None = None, tools_factory: ToolsFactory | None = None, model_factory: Callable[[], Any] | None = None) -> FastAPI:
    """`tools_factory` y `model_factory` se pueden sustituir (así lo hacen las pruebas: servidor MCP en memoria y modelo guionado)."""
    settings = settings or Settings.from_env()
    tools_factory = tools_factory or (lambda: persistent_tools(settings.mcp_url))
    model_factory = model_factory or build_model

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.error, app.state.agent, app.state.saver, app.state.db, app.state.tools = None, None, None, None, []
        app.state.locks = {}
        async with AsyncExitStack() as stack:
            try:
                Path(settings.sessions_db).parent.mkdir(parents=True, exist_ok=True)
                app.state.saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(settings.sessions_db))
                db = await aiosqlite.connect(settings.sessions_db)
                stack.push_async_callback(db.close)
                await db.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL)")
                await db.commit()
                app.state.db = db
                tools = await stack.enter_async_context(tools_factory())
                app.state.tools = [t.name for t in tools]
                app.state.agent = build_agent(model_factory(), tools, checkpointer=app.state.saver)
            except Exception as e:   # sin base o sin servidor MCP: la API arranca y responde 503 con el motivo
                app.state.error = f"{type(e).__name__}: {e}"
                app.state.agent = None
            yield

    app = FastAPI(title="Agente MCP de HubSpot", version="0.1.0", lifespan=lifespan,
                  description="Un agente de LangChain con herramientas MCP (CRM), expuesto por HTTP. `session_id` = `thread_id` del checkpointer.")

    def ready(request: Request):
        if request.app.state.agent is None:
            raise HTTPException(503, f"El servicio no está listo: {request.app.state.error or 'iniciando'}")
        return request.app

    Ready = Annotated[FastAPI, Depends(ready)]

    async def known(app: FastAPI, session_id: str) -> tuple[str, str] | None:
        async with app.state.db.execute("SELECT user_id, created_at FROM sessions WHERE session_id = ?", (session_id,)) as cur:
            return await cur.fetchone()

    async def state_of(app: FastAPI, session_id: str):
        return await app.state.agent.aget_state({"configurable": {"thread_id": session_id}})

    def reply(session_id: str, user_id: str, result: dict[str, Any]) -> ChatOut:
        turn = turn_messages(result["messages"])
        pending = pending_from([i for i in result.get("__interrupt__", [])])
        final = [m for m in turn if isinstance(m, AIMessage) and not m.tool_calls]
        return ChatOut(
            session_id=session_id, user_id=user_id, status="awaiting_approval" if pending else "completed",
            answer="" if pending else (text_of(final[-1]) if final else ""),
            tools_used=[c["name"] for m in turn if isinstance(m, AIMessage) for c in m.tool_calls],
            pending_action=pending, usage=usage_of(turn),
        )

    @app.get("/health", response_model=Health, tags=["estado"])
    async def health(request: Request) -> Health:
        s = request.app.state
        return Health(status="ok" if s.agent else "degraded", model_provider=os.getenv("LLM_PROVIDER", "fake"), hubspot_mode=settings.hubspot_mode,
                      mcp=settings.mcp_url or "stdio (subproceso)", tools=s.tools, error=s.error)

    @app.post("/chat", response_model=ChatOut, tags=["chat"], summary="Enviar un mensaje (crea la sesión si no se envía session_id)")
    async def chat(body: ChatIn, app: Ready) -> ChatOut:
        session_id = body.session_id or str(uuid.uuid4())
        lock = app.state.locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            if await known(app, session_id) is None:
                await app.state.db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session_id, body.user_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
                await app.state.db.commit()
            elif pending_from((await state_of(app, session_id)).interrupts):
                raise HTTPException(409, "La sesión espera una aprobación: responde con POST /sessions/{id}/approve antes de seguir.")
            config = {"configurable": {"thread_id": session_id}, "recursion_limit": 25}
            result = await app.state.agent.ainvoke({"messages": [{"role": "user", "content": body.message}]}, config)
            return reply(session_id, body.user_id, result)

    @app.post("/sessions/{session_id}/approve", response_model=ChatOut, tags=["chat"], summary="Aprobar o rechazar la escritura pendiente")
    async def approve(session_id: SessionId, body: ApproveIn, app: Ready) -> ChatOut:
        row = await known(app, session_id)
        if row is None:
            raise HTTPException(404, "Sesión inexistente")
        async with app.state.locks.setdefault(session_id, asyncio.Lock()):
            if pending_from((await state_of(app, session_id)).interrupts) is None:
                raise HTTPException(409, "La sesión no tiene ninguna acción pendiente de aprobación.")
            decision = {"type": "approve"} if body.approved else {"type": "reject", "message": body.reason or "La persona no autorizó la escritura."}
            config = {"configurable": {"thread_id": session_id}, "recursion_limit": 25}
            result = await app.state.agent.ainvoke(Command(resume={"decisions": [decision]}), config)
            return reply(session_id, row[0], result)

    @app.get("/sessions", response_model=list[SessionSummary], tags=["sesiones"], summary="Listar las sesiones de un usuario")
    async def list_sessions(app: Ready, user_id: UserId = "demo") -> list[SessionSummary]:
        async with app.state.db.execute("SELECT session_id, user_id, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)) as cur:
            return [SessionSummary(session_id=a, user_id=b, created_at=c) for a, b, c in await cur.fetchall()]

    @app.get("/sessions/{session_id}", response_model=SessionOut, tags=["sesiones"], summary="Historial de la conversación")
    async def get_session(session_id: SessionId, app: Ready) -> SessionOut:
        row = await known(app, session_id)
        if row is None:
            raise HTTPException(404, "Sesión inexistente")
        st = await state_of(app, session_id)
        pending = pending_from(st.interrupts)
        return SessionOut(session_id=session_id, user_id=row[0], created_at=row[1], status="awaiting_approval" if pending else "completed",
                          pending_action=pending, messages=[to_message_out(m) for m in st.values.get("messages", [])])

    @app.delete("/sessions/{session_id}", status_code=204, tags=["sesiones"], summary="Borrar la conversación")
    async def delete_session(session_id: SessionId, app: Ready) -> None:
        if await known(app, session_id) is None:
            raise HTTPException(404, "Sesión inexistente")
        await app.state.saver.adelete_thread(session_id)
        await app.state.db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        await app.state.db.commit()
        app.state.locks.pop(session_id, None)

    return app


def default_app() -> FastAPI:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    return create_app()


app = default_app()   # `uvicorn api:app`
