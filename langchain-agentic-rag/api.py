"""El agente expuesto como un servicio HTTP con FastAPI.

    uv run uvicorn api:app --reload          # http://localhost:8000  ·  documentación interactiva en /docs
    uv run python cli.py serve               # lo mismo, con la CLI

Idea para llevarse: el agente es un servicio SIN estado en el proceso. La conversación vive en el checkpointer (Postgres) y el
cliente solo lleva un identificador: `session_id` en la API = `thread_id` en LangGraph. Mismo id, misma conversación, aunque
reinicies el servidor.
"""

import os
from contextlib import asynccontextmanager

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from retriever import IndexNotReady
from service import AgentService, SessionNotFound

load_dotenv(find_dotenv(usecwd=True))


# ── Contratos de entrada y salida (se ven en /docs) ──────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000, description="La pregunta del usuario.")
    session_id: str | None = Field(default=None, description="Id de la conversación. Omítelo para empezar una nueva; reenvía el que te devolvimos para continuarla.")
    user_id: str = Field(default="demo", min_length=1, max_length=64, description="Quién pregunta (memoria de largo plazo y bitácora).")


class Citation(BaseModel):
    id: str
    section: str | None


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    model_calls: int


class ChatResponse(BaseModel):
    session_id: str
    user_id: str
    status: str = Field(description="answered | stopped (un límite del bucle cortó la ejecución).")
    answer: str
    found: bool | None
    citations: list[Citation]
    problems: list[str] = Field(description="Verificaciones en código que fallaron (p. ej. una cita inexistente).")
    tools_used: list[str]
    usage: Usage


class Message(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict] | None = None


class Health(BaseModel):
    status: str
    llm_provider: str
    embeddings_provider: str
    index_ready: bool


def create_app(service: AgentService | None = None) -> FastAPI:
    """Fábrica de la app. En pruebas se le pasa un servicio ya armado; en producción se construye al arrancar."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or AgentService.from_env(auto_ingest=os.getenv("AUTO_INGEST") == "1")
        yield
        if service is None:
            app.state.service.close()

    app = FastAPI(title="Agente RAG de la mesa de ayuda", version="1.0", lifespan=lifespan,
                  description="Un agente (LangChain) con RAG sobre pgvector, expuesto por HTTP. `session_id` = `thread_id`.")

    def svc() -> AgentService:
        return app.state.service

    @app.get("/health", response_model=Health, tags=["estado"])
    def health():
        return Health(status="ok", llm_provider=os.getenv("LLM_PROVIDER", "openai"),
                      embeddings_provider=os.getenv("EMBEDDINGS_PROVIDER", "openai"), index_ready=svc().ready())

    @app.post("/chat", response_model=ChatResponse, tags=["agente"])
    def chat(req: ChatRequest):
        try:
            return svc().chat(req.message, req.session_id, req.user_id)
        except IndexNotReady as e:
            raise HTTPException(503, str(e))

    @app.get("/sessions/{session_id}", response_model=list[Message], tags=["sesiones"])
    def get_session(session_id: str):
        try:
            return svc().history(session_id)
        except SessionNotFound:
            raise HTTPException(404, f"No existe la sesión {session_id}")

    @app.delete("/sessions/{session_id}", status_code=204, tags=["sesiones"])
    def delete_session(session_id: str):
        try:
            svc().delete(session_id)
        except SessionNotFound:
            raise HTTPException(404, f"No existe la sesión {session_id}")

    @app.get("/log", tags=["bitácora"], summary="Últimas consultas registradas (tokens, herramientas)")
    def log(limit: int = Query(10, ge=1, le=100)):
        return [{**r, "created_at": str(r.get("created_at")), "tools_used": list(r["tools_used"])} for r in svc().db.recent_queries(limit)]

    @app.get("/gaps", tags=["bitácora"], summary="Preguntas que el manual no pudo responder")
    def gaps():
        return [{**g, "created_at": str(g.get("created_at"))} for g in svc().db.all_gaps()]

    return app


app = create_app()                      # uvicorn api:app (el servicio se construye al arrancar, no al importar)
