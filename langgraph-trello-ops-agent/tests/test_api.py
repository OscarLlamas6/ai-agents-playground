"""API FastAPI sin red: TestClient + modelo guionado + Trello simulado."""

import pytest
from conftest import ScriptedModel, call
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from api import create_app
from trello_ops.fake_model import FakeOpsModel
from trello_ops.graph import RUNBOOK, build_graph
from trello_ops.retriever import Retriever
from trello_ops.trello_client import FakeTrello

CREATE = call("create_card", {"list_id": "l_ops_todo", "name": "Revisar alertas"}, "w1")


def make_client(db, model=None, trello=None):
    trello = trello or FakeTrello()
    model = model or FakeOpsModel()
    retriever = Retriever(RUNBOOK)
    return TestClient(create_app(db, build=lambda checkpointer, store: build_graph(model, trello, retriever, checkpointer=checkpointer, store=store))), trello


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "ops.sqlite")


def test_health(db):
    with make_client(db)[0] as c:
        body = c.get("/health").json()
    assert body["status"] == "ok" and body["trello_mode"] == "fake" and body["sessions_survive_restart"] is True


def test_new_session_is_created_and_returned(db):
    with make_client(db)[0] as c:
        r = c.post("/chat", json={"message": "Muestra mis tableros"}).json()
    assert r["status"] == "completed" and len(r["session_id"]) == 36 and "Operaciones TI" in r["answer"]
    assert r["tools_used"] == ["list_boards"] and r["usage"]["model_calls"] == 2


def test_same_session_continues_and_other_session_is_isolated(db):
    model = ScriptedModel(script=[AIMessage("uno"), AIMessage("dos"), AIMessage("tres")])
    with make_client(db, model)[0] as c:
        a = c.post("/chat", json={"message": "hola"}).json()["session_id"]
        c.post("/chat", json={"message": "sigues ahí", "session_id": a})
        b = c.post("/chat", json={"message": "hola"}).json()["session_id"]
        assert a != b
        assert [m["role"] for m in c.get(f"/sessions/{a}").json()["messages"]] == ["human", "ai", "human", "ai"]
        assert len(c.get(f"/sessions/{b}").json()["messages"]) == 2          # la otra sesión no ve la conversación


def test_unknown_session_and_validation_errors(db):
    with make_client(db)[0] as c:
        assert c.get("/sessions/no-existe").status_code == 404
        assert c.delete("/sessions/no-existe").status_code == 404
        assert c.post("/sessions/no-existe/approve", json={"approved": True}).status_code == 404
        assert c.post("/chat", json={"message": ""}).status_code == 422
        assert c.post("/chat", json={"message": "x", "session_id": "con espacios"}).status_code == 422
        assert c.post("/chat", json={}).status_code == 422


def test_delete_session(db):
    with make_client(db)[0] as c:
        sid = c.post("/chat", json={"message": "Muestra mis tableros"}).json()["session_id"]
        assert c.delete(f"/sessions/{sid}").status_code == 204
        assert c.get(f"/sessions/{sid}").status_code == 404
        assert c.post("/chat", json={"message": "hola", "session_id": sid}).json()["status"] == "completed"   # el id queda libre
        assert len(c.get(f"/sessions/{sid}").json()["messages"]) == 2                                       # y empieza de cero


def test_session_belongs_to_its_user(db):
    with make_client(db)[0] as c:
        sid = c.post("/chat", json={"message": "hola", "user_id": "ana"}).json()["session_id"]
        assert c.post("/chat", json={"message": "hola", "session_id": sid, "user_id": "luis"}).status_code == 403
        assert [s["session_id"] for s in c.get("/sessions", params={"user_id": "ana"}).json()] == [sid]
        assert c.get("/sessions", params={"user_id": "luis"}).json() == []


def test_write_waits_for_approval_and_approval_executes(db):
    client, trello = make_client(db, ScriptedModel(script=[CREATE, AIMessage("Listo.")]))
    with client as c:
        r = c.post("/chat", json={"message": "crea una tarjeta"}).json()
        assert r["status"] == "awaiting_approval" and r["answer"] is None
        assert r["pending_actions"][0]["tool"] == "create_card" and "Revisar alertas" in r["pending_actions"][0]["summary"]
        assert trello.writes == []                                                     # nada se escribió todavía
        assert c.post("/chat", json={"message": "otra", "session_id": r["session_id"]}).status_code == 409
        assert c.get(f"/sessions/{r['session_id']}").json()["status"] == "awaiting_approval"
        done = c.post(f"/sessions/{r['session_id']}/approve", json={"approved": True}).json()
    assert done["status"] == "completed" and done["answer"] == "Listo." and done["writes"] == [{"tool": "create_card", "status": "ok"}]
    assert trello.writes == [("create_card", "l_ops_todo", "Revisar alertas")]


def test_rejection_executes_nothing(db):
    client, trello = make_client(db, ScriptedModel(script=[CREATE, AIMessage("Entendido.")]))
    with client as c:
        sid = c.post("/chat", json={"message": "crea una tarjeta"}).json()["session_id"]
        done = c.post(f"/sessions/{sid}/approve", json={"approved": False, "reason": "no urge"}).json()
        assert c.post(f"/sessions/{sid}/approve", json={"approved": True}).status_code == 409     # ya no hay nada pendiente
    assert trello.writes == [] and done["writes"] == [{"tool": "create_card", "status": "rejected"}]


def test_step_limit_is_reported_as_halted(db):
    model = ScriptedModel(script=[call("search_cards", {"query": f"q{i}"}, f"s{i}") for i in range(30)])
    trello, retriever = FakeTrello(), Retriever(RUNBOOK)
    app = create_app(db, build=lambda checkpointer, store: build_graph(model, trello, retriever, checkpointer=checkpointer, store=store, max_steps=3))
    with TestClient(app) as c:
        r = c.post("/chat", json={"message": "busca sin parar"}).json()
    assert r["status"] == "halted" and "tope de pasos" in r["answer"] and r["usage"]["model_calls"] == 3


def test_session_survives_an_api_restart(db):
    client, trello = make_client(db, ScriptedModel(script=[CREATE, AIMessage("Listo."), AIMessage("Sí, lo recuerdo.")]))
    with client as c:                                                  # 1.ª "vida" de la API: queda una aprobación pendiente
        sid = c.post("/chat", json={"message": "crea una tarjeta"}).json()["session_id"]
    client2, _ = make_client(db, ScriptedModel(script=[AIMessage("Listo."), AIMessage("Sí, lo recuerdo.")]), trello)
    with client2 as c:                                                 # proceso nuevo, mismo archivo y mismo id
        assert c.get(f"/sessions/{sid}").json()["status"] == "awaiting_approval"
        assert c.post(f"/sessions/{sid}/approve", json={"approved": True}).json()["status"] == "completed"
        again = c.post("/chat", json={"message": "¿me recuerdas?", "session_id": sid}).json()
        assert len(c.get(f"/sessions/{sid}").json()["messages"]) >= 6 and again["answer"] == "Sí, lo recuerdo."
    assert len(trello.writes) == 1


def test_long_term_preference_is_shared_across_sessions(db):
    with make_client(db)[0] as c:
        c.post("/chat", json={"message": 'Mi tablero por defecto es "Migración a la nube"', "user_id": "ana"})
        r = c.post("/chat", json={"message": "Muéstrame las tarjetas", "user_id": "ana"}).json()      # otra sesión
        other = c.post("/chat", json={"message": "Muéstrame las tarjetas", "user_id": "luis"}).json()
    assert "Migrar base de datos" in r["answer"] and "¿De qué tablero?" in other["answer"]


def test_503_when_the_database_is_unavailable():
    with make_client("/proc/no-se-puede/ops.sqlite")[0] as c:
        assert c.get("/health").json()["status"] == "degraded"
        assert c.post("/chat", json={"message": "hola"}).status_code == 503
