"""Utilidades de prueba: un modelo guionado (responde con mensajes preparados) y un arnés para armar el grafo. Sin red."""

import sys
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

sys.path.insert(0, str(Path(__file__).parents[1]))

from trello_ops.graph import RUNBOOK, build_graph  # noqa: E402
from trello_ops.retriever import Retriever  # noqa: E402
from trello_ops.trello_client import FakeTrello  # noqa: E402


class ScriptedModel(BaseChatModel):
    """Entrega, en orden, los mensajes del guion. Si el guion se acaba, repite el último (útil para simular un bucle)."""

    script: list
    system_prompts: list = []     # el prompt de sistema que recibió en cada llamada

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.system_prompts.append(next((m.content for m in messages if m.type == "system"), ""))
        msg = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        return ChatResult(generations=[ChatGeneration(message=msg.model_copy(update={"id": None}))])


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Garantiza que ninguna prueba use proveedores reales aunque exista un .env en el entorno."""
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    monkeypatch.setenv("TRELLO_MODE", "fake")
    monkeypatch.setenv("USE_MCP", "0")


@pytest.fixture
def trello():
    return FakeTrello()


@pytest.fixture
def make_graph(trello):
    retriever = Retriever(RUNBOOK)

    def _make(script, **kwargs):
        model = ScriptedModel(script=script)
        return build_graph(model, trello, retriever, **kwargs), model

    return _make
