"""La interfaz Streamlit se prueba sin navegador (AppTest) y sin servidor: se sustituye httpx por la app de FastAPI en proceso."""

import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from api import create_app
from test_api import make_service

UI = str(Path(__file__).parents[1] / "ui.py")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    with TestClient(create_app(make_service()), base_url="http://api") as c:
        # ui.py llama a httpx.post/get con la URL de la API: se redirigen al cliente en proceso
        monkeypatch.setattr(httpx, "post", lambda url, **kw: c.post(url.replace("http://localhost:8000", ""), **kw))
        monkeypatch.setattr(httpx, "get", lambda url, **kw: c.get(url.replace("http://localhost:8000", ""), **kw))
        yield c


def test_chat_keeps_the_session_id_between_messages(api):
    at = AppTest.from_file(UI, default_timeout=30).run()
    at.chat_input[0].set_value("Me llamo Marta").run()
    sid = at.session_state["session_id"]
    assert sid and not at.exception
    at.chat_input[0].set_value("¿Cómo me llamo?").run()
    assert at.session_state["session_id"] == sid                       # la misma sesión
    assert "Marta" in at.session_state["messages"][-1]["content"]


def test_new_session_button_clears_the_conversation(api):
    at = AppTest.from_file(UI, default_timeout=30).run()
    at.chat_input[0].set_value("Hola").run()
    at.button[0].click().run()
    assert at.session_state["session_id"] is None and at.session_state["messages"] == []


def test_sidebar_shows_the_session_id_right_after_the_first_answer(api):
    at = AppTest.from_file(UI, default_timeout=30).run()
    assert "sesión nueva" in at.sidebar.code[0].value
    at.chat_input[0].set_value("Hola").run()
    assert at.sidebar.code[0].value == at.session_state["session_id"]        # sin recargar ni escribir otro mensaje
