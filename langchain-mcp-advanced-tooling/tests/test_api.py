"""La API completa con TestClient (sin red): servidor MCP en memoria, modelo guionado y SQLite en una carpeta temporal."""

from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session
from langchain_mcp_adapters.tools import load_mcp_tools

from api import create_app
from fake_model import ScriptedFakeModel
from hubspot_client import FakeHubSpot
from mcp_server import build_server
from settings import Settings

NOTE = "Agrega una nota a Ana: 'Enviar la propuesta el lunes'"


def make_app(db, crm=None):
    crm = crm or FakeHubSpot()
    app_settings = Settings(sessions_db=str(db))

    @asynccontextmanager
    async def tools():
        async with create_connected_server_and_client_session(build_server(Settings(), crm)._mcp_server) as session:
            yield await load_mcp_tools(session)

    return create_app(app_settings, tools_factory=tools, model_factory=ScriptedFakeModel), crm


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def client(db):
    app, _ = make_app(db)
    with TestClient(app) as c:
        yield c


def chat(c, message, session_id=None, user_id="demo"):
    r = c.post("/chat", json={"message": message, "session_id": session_id, "user_id": user_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["hubspot_mode"] == "fake" and h["tools"] == ["search_contacts", "get_contact_notes", "add_note"]


def test_new_session_returns_a_session_id_and_the_agent_fields(client):
    r = chat(client, "Resume las notas de Luis.")
    assert len(r["session_id"]) == 36 and r["status"] == "completed" and r["tools_used"] == ["search_contacts", "get_contact_notes"]
    assert "renovará el contrato" in r["answer"] and r["usage"]["model_calls"] == 3 and r["usage"]["input_tokens"] > 0


def test_same_session_id_continues_the_conversation(client):
    first = chat(client, "Resume las notas de Luis.")
    follow = chat(client, "¿Y sus notas?", first["session_id"])
    assert follow["session_id"] == first["session_id"] and follow["tools_used"] == ["get_contact_notes"]   # recordó a Luis
    assert "renovará el contrato" in follow["answer"]
    assert "Luis" in chat(client, "¿De quién hablábamos?", first["session_id"])["answer"]


def test_another_session_does_not_see_the_conversation(client):
    chat(client, "Resume las notas de Luis.")
    other = chat(client, "¿De quién hablábamos?")
    assert "Todavía no hemos hablado de nadie" in other["answer"]


def test_history_endpoint_and_listing(client):
    s = chat(client, "Resume las notas de Ana.", user_id="ana")
    h = client.get(f"/sessions/{s['session_id']}").json()
    assert [m["role"] for m in h["messages"]] == ["human", "ai", "tool", "ai", "tool", "ai"] and h["user_id"] == "ana"
    assert h["messages"][1]["tool_calls"][0]["name"] == "search_contacts"
    assert [x["session_id"] for x in client.get("/sessions", params={"user_id": "ana"}).json()] == [s["session_id"]]
    assert client.get("/sessions", params={"user_id": "otra"}).json() == []


def test_unknown_session_is_404_and_bad_input_is_422(client):
    assert client.get("/sessions/no-existe").status_code == 404
    assert client.delete("/sessions/no-existe").status_code == 404
    assert client.post("/sessions/no-existe/approve", json={"approved": True}).status_code == 404
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "hola", "session_id": "con espacios!"}).status_code == 422
    assert client.post("/chat", json={}).status_code == 422


def test_delete_removes_the_thread(client):
    s = chat(client, "Resume las notas de Luis.")["session_id"]
    assert client.delete(f"/sessions/{s}").status_code == 204
    assert client.get(f"/sessions/{s}").status_code == 404
    assert "Todavía no hemos hablado" in chat(client, "¿De quién hablábamos?", s)["answer"]   # mismo id, historial vacío


def test_write_waits_for_approval_and_approve_saves(db):
    app, crm = make_app(db)
    with TestClient(app) as c:
        r = chat(c, NOTE)
        assert r["status"] == "awaiting_approval" and r["answer"] == "" and crm.writes == []
        assert r["pending_action"]["tool"] == "add_note" and r["pending_action"]["args"]["body"] == "Enviar la propuesta el lunes"
        assert c.get(f"/sessions/{r['session_id']}").json()["status"] == "awaiting_approval"
        assert c.post("/chat", json={"message": "otra cosa", "session_id": r["session_id"]}).status_code == 409   # primero hay que resolver la aprobación
        done = c.post(f"/sessions/{r['session_id']}/approve", json={"approved": True}).json()
        assert done["status"] == "completed" and "quedó guardada" in done["answer"] and len(crm.writes) == 1
        assert c.post(f"/sessions/{r['session_id']}/approve", json={"approved": True}).status_code == 409   # ya no hay nada pendiente


def test_reject_does_not_write(db):
    app, crm = make_app(db)
    with TestClient(app) as c:
        r = chat(c, NOTE)
        done = c.post(f"/sessions/{r['session_id']}/approve", json={"approved": False, "reason": "no es el momento"}).json()
        assert done["status"] == "completed" and crm.writes == [] and "no es el momento" in done["answer"]


def test_sessions_survive_a_restart_of_the_api(db):
    app1, _ = make_app(db)
    with TestClient(app1) as c1:
        first = chat(c1, "Resume las notas de Luis.")
        pending = chat(c1, NOTE)   # y una aprobación pendiente también debe sobrevivir
    app2, crm2 = make_app(db)      # "reinicio": otra aplicación, otro proceso del servidor MCP, el mismo archivo SQLite
    with TestClient(app2) as c2:
        assert "Luis" in chat(c2, "¿De quién hablábamos?", first["session_id"])["answer"]
        assert len(c2.get(f"/sessions/{first['session_id']}").json()["messages"]) > 6
        assert c2.get(f"/sessions/{pending['session_id']}").json()["status"] == "awaiting_approval"
        done = c2.post(f"/sessions/{pending['session_id']}/approve", json={"approved": True}).json()
        assert "quedó guardada" in done["answer"] and len(crm2.writes) == 1


def test_503_when_the_database_cannot_be_opened(tmp_path):
    blocker = tmp_path / "es-un-archivo"
    blocker.write_text("x")
    app, _ = make_app(blocker / "sessions.db")      # el "directorio" padre es un archivo: no se puede crear la base
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "degraded"
        r = c.post("/chat", json={"message": "hola"})
        assert r.status_code == 503 and "no está listo" in r.json()["detail"]
        assert c.get("/sessions/x").status_code == 503
