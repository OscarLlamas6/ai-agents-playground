"""Pruebas sin red: embeddings falsos y un modelo guionado."""

from pathlib import Path

import pytest
from conftest import ScriptedModel, call, final

from agent import build_agent, build_checkpointer, verify
from retriever import Retriever

MANUAL = Path(__file__).parents[1] / "data" / "mesa_de_ayuda.md"


@pytest.fixture
def retriever(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    return Retriever(MANUAL)


def run(agent, text, thread="t1"):
    return agent.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}})


def test_manual_is_split_into_cited_chunks(retriever):
    assert len(retriever.chunks) >= 10
    assert retriever.chunks[0].id == "D1" and retriever.chunks[0].section == "Contraseñas y acceso"


def test_search_finds_the_right_section(retriever):
    best, _ = retriever.search("VPN países autorizados excepción", k=1)[0]
    assert best.section == "VPN y trabajo remoto"


def test_agent_searches_then_answers_with_valid_citations(retriever):
    model = ScriptedModel(script=[call("search_manual", {"query": "vencimiento contraseña"}, "c1"), final("Cada 90 días.", ["D1"])])
    result = run(build_agent(model, retriever), "¿Cada cuánto vence mi contraseña?")
    answer = result["structured_response"]
    assert answer.found and answer.citations == ["D1"]
    assert verify(answer, retriever) == []


def test_agent_can_answer_without_searching(retriever):
    model = ScriptedModel(script=[final("42", [])])
    answer = run(build_agent(model, retriever), "¿15 + 27?")["structured_response"]
    assert retriever.retrieved == {} and verify(answer, retriever) == []


def test_verify_flags_invented_and_missing_citations(retriever):
    retriever.search("vpn", k=2)
    bad = final("x", ["D99"]).tool_calls[0]["args"]
    from agent import Answer

    assert verify(Answer(**bad), retriever) == ["cita inexistente: D99"]
    assert verify(Answer(answer="x", citations=[], found=True), retriever) == ["respondió con el manual pero sin citar"]


def test_search_limit_stops_a_runaway_loop(retriever):
    calls = []
    original = retriever.search
    retriever.search = lambda q, k=3: calls.append(q) or original(q, k)
    script = [call("search_manual", {"query": f"consulta {i}"}, f"c{i}") for i in range(6)] + [final("No lo encuentro.", [], found=False)]
    run(build_agent(ScriptedModel(script=script), retriever), "pregunta imposible")
    assert len(calls) == 4          # run_limit=4: las búsquedas 5 y 6 no se ejecutan


def test_memory_keeps_the_conversation_per_thread(retriever):
    model = ScriptedModel(script=[final("uno", []), final("dos", []), final("tres", [])])
    agent = build_agent(model, retriever, checkpointer=build_checkpointer())
    run(agent, "primera", "A")
    run(agent, "segunda", "A")      # mismo hilo: ve el historial
    run(agent, "otra", "B")         # hilo distinto: empieza de cero
    assert model.seen[1] > model.seen[0] and model.seen[2] == model.seen[0]
