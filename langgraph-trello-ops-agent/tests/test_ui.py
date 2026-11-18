"""La interfaz Streamlit se prueba sin navegador con AppTest; las llamadas HTTP van al TestClient de la API (en proceso)."""

from pathlib import Path

import pytest
import requests
from conftest import ScriptedModel, call
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

from api import create_app
from trello_ops.graph import RUNBOOK, build_graph
from trello_ops.retriever import Retriever
from trello_ops.trello_client import FakeTrello

CREATE = call("create_card", {"list_id": "l_ops_todo", "name": "Revisar alertas"}, "w1")


@pytest.fixture
def ui(monkeypatch, tmp_path):
    trello, retriever = FakeTrello(), Retriever(RUNBOOK)
    model = ScriptedModel(script=[AIMessage("Hola, ¿en qué te ayudo?"), CREATE, AIMessage("Listo, tarjeta creada.")])
    app = create_app(str(tmp_path / "ops.sqlite"), build=lambda checkpointer, store: build_graph(model, trello, retriever, checkpointer=checkpointer, store=store))
    with TestClient(app) as client:
        def fake_request(method, url, timeout=None, **kw):             # redirige requests.request al TestClient
            return client.request(method, url.replace("http://api.test", ""), **kw)

        monkeypatch.setattr(requests, "request", fake_request)
        monkeypatch.setenv("API_URL", "http://api.test")
        at = AppTest.from_file(str(Path(__file__).parents[1] / "ui.py"), default_timeout=30).run()
        yield at, trello


def test_chat_and_approval_flow(ui):
    at, trello = ui
    assert not at.exception and "demo" in at.sidebar.text_input[0].value
    at.chat_input[0].set_value("hola").run()
    assert not at.exception and any("Hola, ¿en qué te ayudo?" in m.markdown[0].value for m in at.chat_message)
    session = at.session_state.session_id
    assert len(session) == 36

    at.chat_input[0].set_value("crea una tarjeta").run()
    assert at.session_state.session_id == session and at.session_state.pending       # misma sesión, ahora en pausa
    assert {b.label for b in at.button} == {"Nueva sesión", "Aprobar", "Rechazar"} and trello.writes == []
    next(b for b in at.button if b.label == "Aprobar").click().run()
    assert trello.writes == [("create_card", "l_ops_todo", "Revisar alertas")] and not at.session_state.pending
    assert any("Listo, tarjeta creada." in str(m.markdown[0].value) for m in at.chat_message)


def test_new_session_button_resets_the_conversation(ui):
    at, _ = ui
    at.chat_input[0].set_value("hola").run()
    first = at.session_state.session_id
    next(b for b in at.button if b.label == "Nueva sesión").click().run()
    assert at.session_state.session_id is None and at.session_state.history == [] and first
