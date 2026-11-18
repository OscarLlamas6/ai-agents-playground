"""La interfaz Streamlit probada con AppTest (sin navegador). Las llamadas HTTP de la UI se enrutan a la API en proceso."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from test_api import make_app


@pytest.fixture
def ui(tmp_path, monkeypatch):
    app, crm = make_app(tmp_path / "sessions.db")
    monkeypatch.setenv("API_URL", "http://testserver")
    with TestClient(app) as client:
        monkeypatch.setattr(httpx, "request", lambda method, url, **kw: client.request(method, url, **{k: v for k, v in kw.items() if k != "timeout"}))
        at = AppTest.from_file(str(Path(__file__).parents[1] / "ui.py"), default_timeout=30).run()
        yield at, crm


def texts(at):
    return [m.markdown[0].value for m in at.chat_message]


def test_chat_shows_answer_tools_and_session_id(ui):
    at, _ = ui
    assert not at.exception and at.sidebar.code[0].value.startswith("(se crea")
    at.chat_input[0].set_value("Resume las notas de Luis.").run()
    assert not at.exception
    assert "renovará el contrato" in texts(at)[-1]
    assert len(at.sidebar.code[0].value) == 36                                   # el session_id aparece en la barra lateral
    assert any("search_contacts, get_contact_notes" in c.value for c in at.caption)


def test_follow_up_keeps_memory_and_new_session_forgets(ui):
    at, _ = ui
    at.chat_input[0].set_value("Resume las notas de Luis.").run()
    sid = at.sidebar.code[0].value
    at.chat_input[0].set_value("¿De quién hablábamos?").run()
    assert "Luis" in texts(at)[-1] and at.sidebar.code[0].value == sid
    at.sidebar.button[0].click().run()                                           # "Nueva sesión"
    assert at.sidebar.code[0].value.startswith("(se crea") and not at.chat_message
    at.chat_input[0].set_value("¿De quién hablábamos?").run()
    assert "Todavía no hemos hablado" in texts(at)[-1]


def test_approval_buttons_approve_and_reject(ui):
    at, crm = ui
    at.chat_input[0].set_value("Agrega una nota a Ana: 'Enviar la propuesta el lunes'").run()
    assert at.json and "Enviar la propuesta" in str(at.json[0].value) and crm.writes == []
    assert at.chat_input[0].disabled                                              # no se puede seguir hasta resolver la aprobación
    at.button(key="approve").click().run()
    assert len(crm.writes) == 1 and "quedó guardada" in texts(at)[-1] and not at.chat_input[0].disabled

    at.sidebar.button[0].click().run()
    at.chat_input[0].set_value("Agrega una nota a Ana: 'Otra nota'").run()
    at.button(key="reject").click().run()
    assert len(crm.writes) == 1                                                   # rechazar no escribió
