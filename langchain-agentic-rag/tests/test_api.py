"""Pruebas de la API con TestClient (sin red, sin servidor): modelo falso, base en memoria."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import build_agent, build_checkpointer
from api import create_app
from db import MemoryDatabase
from fake_model import FakeAgentModel
from memory import load_store
from retriever import Retriever
from service import AgentService

MANUAL = Path(__file__).parents[1] / "data" / "mesa_de_ayuda.md"


def make_service(db=None, checkpointer=None, store=None, ingest=True):
    os.environ["EMBEDDINGS_PROVIDER"] = "fake"
    db = db or MemoryDatabase()
    retriever = Retriever(MANUAL, db)
    if ingest:
        retriever.ingest()
    checkpointer = checkpointer or build_checkpointer()
    store = store or load_store(None)
    agent = build_agent(FakeAgentModel(), retriever, db, checkpointer=checkpointer, store=store)
    return AgentService(db, retriever, agent, checkpointer, store, None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    with TestClient(create_app(make_service())) as c:
        yield c


def chat(client, message, **kw):
    r = client.post("/chat", json={"message": message, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["index_ready"] is True and body["llm_provider"] == "fake"


def test_new_session_returns_an_id_and_cites_the_manual(client):
    r = chat(client, "¿Cada cuánto vence mi contraseña?")
    assert r["session_id"] and r["status"] == "answered" and r["found"] is True
    assert r["tools_used"] == ["search_manual"] and r["citations"] and r["problems"] == []
    assert r["usage"]["input_tokens"] > 0 and r["usage"]["model_calls"] == 2


def test_same_session_remembers_and_another_session_does_not(client):
    first = chat(client, "Me llamo Marta")
    sid = first["session_id"]
    assert "Marta" in chat(client, "¿Cómo me llamo?", session_id=sid)["answer"]            # misma sesión: recuerda
    other = chat(client, "¿Cómo me llamo?")                                                  # sesión nueva: no sabe
    assert other["session_id"] != sid and "No me has dicho" in other["answer"]


def test_history_and_delete(client):
    sid = chat(client, "Hola")["session_id"]
    history = client.get(f"/sessions/{sid}").json()
    assert history[0] == {"role": "user", "content": "Hola", "name": None, "tool_calls": None} and history[-1]["role"] == "assistant"
    assert client.delete(f"/sessions/{sid}").status_code == 204
    assert client.get(f"/sessions/{sid}").status_code == 404


def test_unknown_session_and_validation_errors(client):
    assert client.get("/sessions/no-existe").status_code == 404
    assert client.delete("/sessions/no-existe").status_code == 404
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={}).status_code == 422


def test_write_tool_and_audit_log_are_visible_through_the_api(client):
    r = chat(client, "¿Dan soporte a impresoras 3D?", user_id="luis")
    assert r["found"] is False and r["tools_used"] == ["search_manual", "report_gap"]
    assert client.get("/gaps").json()[0]["user_id"] == "luis"
    row = client.get("/log").json()[0]
    assert row["tools_used"] == ["search_manual", "report_gap"] and row["input_tokens"] == r["usage"]["input_tokens"]


def test_long_term_memory_is_per_user_across_sessions(client):
    chat(client, "Uso Linux", user_id="ana")
    assert "Linux" in chat(client, "¿Qué sistema uso?", user_id="ana")["answer"]             # sesión distinta, mismo usuario
    assert "Sin preferencias" in chat(client, "¿Qué sistema uso?", user_id="luis")["answer"]


def test_503_when_the_manual_was_not_ingested(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    with TestClient(create_app(make_service(ingest=False))) as c:
        assert c.get("/health").json()["index_ready"] is False
        r = c.post("/chat", json={"message": "¿Cada cuánto vence mi contraseña?"})
        assert r.status_code == 503 and "ingest" in r.json()["detail"]


def test_session_survives_an_app_restart(monkeypatch):
    """Dos apps distintas comparten checkpointer y base (como dos arranques del servidor sobre el mismo Postgres)."""
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    db, cp = MemoryDatabase(), build_checkpointer()
    with TestClient(create_app(make_service(db, cp))) as a:
        sid = chat(a, "Me llamo Marta")["session_id"]
    with TestClient(create_app(make_service(db, cp))) as b:                                  # "reinicio"
        assert "Marta" in chat(b, "¿Cómo me llamo?", session_id=sid)["answer"]


@pytest.mark.skipif(not os.getenv("DATABASE_URL_TEST"), reason="define DATABASE_URL_TEST para probar PostgresSaver real")
def test_postgres_checkpointer_persists_between_services(monkeypatch):
    from db import PgDatabase
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    dsn = os.environ["DATABASE_URL_TEST"]
    a = make_service(PgDatabase(dsn), build_checkpointer(dsn))
    sid = a.chat("Me llamo Marta", None, "ana")["session_id"]
    b = make_service(PgDatabase(dsn), build_checkpointer(dsn))                               # otro servicio = otro "proceso"
    assert "Marta" in b.chat("¿Cómo me llamo?", sid, "ana")["answer"]
    b.delete(sid)
