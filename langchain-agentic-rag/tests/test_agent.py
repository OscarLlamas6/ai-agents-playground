"""Pruebas sin red: embeddings falsos y un modelo guionado."""

from pathlib import Path

import pytest
from conftest import ScriptedModel, call, final

from agent import build_agent, build_checkpointer, verify
from db import MemoryDatabase
from retriever import Retriever

MANUAL = Path(__file__).parents[1] / "data" / "mesa_de_ayuda.md"


@pytest.fixture
def retriever(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    r = Retriever(MANUAL, MemoryDatabase())
    r.ingest()
    return r


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
    result = run(build_agent(model, retriever, retriever.db), "¿Cada cuánto vence mi contraseña?")
    answer = result["structured_response"]
    assert answer.found and answer.citations == ["D1"]
    assert verify(answer, retriever) == []


def test_agent_can_answer_without_searching(retriever):
    model = ScriptedModel(script=[final("42", [])])
    answer = run(build_agent(model, retriever, retriever.db), "¿15 + 27?")["structured_response"]
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
    run(build_agent(ScriptedModel(script=script), retriever, retriever.db), "pregunta imposible")
    assert len(calls) == 4          # run_limit=4: las búsquedas 5 y 6 no se ejecutan


def test_memory_keeps_the_conversation_per_thread(retriever):
    model = ScriptedModel(script=[final("uno", []), final("dos", []), final("tres", [])])
    agent = build_agent(model, retriever, retriever.db, checkpointer=build_checkpointer())
    run(agent, "primera", "A")
    run(agent, "segunda", "A")      # mismo hilo: ve el historial
    run(agent, "otra", "B")         # hilo distinto: empieza de cero
    assert model.seen[1] > model.seen[0] and model.seen[2] == model.seen[0]


# ── Ingeniería del bucle y memoria de largo plazo ─────────────────────────────

from langchain_core.messages import AIMessage  # noqa: E402

from memory import Context, load_store, save_store  # noqa: E402


def run_ctx(agent, text, thread="t1", user="ana"):
    return agent.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": thread}}, context=Context(user_id=user))


def test_no_progress_stops_a_repeating_agent(retriever):
    same = lambda i: call("search_manual", {"query": "vpn"}, f"c{i}")  # noqa: E731
    model = ScriptedModel(script=[same(1), same(2), final("nunca llega", [])])
    result = run_ctx(build_agent(model, retriever, retriever.db), "vpn")
    assert result.get("structured_response") is None
    assert "sin progreso" in result["messages"][-1].content


def test_token_budget_stops_the_loop(retriever):
    first = AIMessage(content="", tool_calls=[{"name": "search_manual", "args": {"query": "vpn"}, "id": "c1"}],
                      usage_metadata={"input_tokens": 900, "output_tokens": 200, "total_tokens": 1100})
    model = ScriptedModel(script=[first, final("nunca llega", [])])
    result = run_ctx(build_agent(model, retriever, retriever.db, max_tokens=1000), "vpn")
    assert "presupuesto de tokens" in result["messages"][-1].content


def test_long_term_memory_survives_across_threads_and_runs(retriever, tmp_path):
    path = tmp_path / "prefs.json"
    store = load_store(path)
    save_model = ScriptedModel(script=[call("remember_preference", {"key": "sistema_operativo", "value": "Linux"}, "c1"), final("Anotado.", [], found=False)])
    run_ctx(build_agent(save_model, retriever, retriever.db, store=store), "Uso Linux", thread="conversacion-1")
    save_store(store, path)

    # otra ejecución del programa, otra conversación: el store se recarga desde el archivo
    store2 = load_store(path)
    recall_model = ScriptedModel(script=[call("recall_preferences", {}, "c2"), final("Usas Linux.", [], found=False)])
    result = run_ctx(build_agent(recall_model, retriever, retriever.db, store=store2), "¿Qué sistema uso?", thread="conversacion-2")
    tool_msg = next(m for m in result["messages"] if m.type == "tool" and m.name == "recall_preferences")
    assert "sistema_operativo = Linux" in tool_msg.content


def test_preferences_are_isolated_per_user(retriever):
    store = load_store(None)
    model = ScriptedModel(script=[call("remember_preference", {"key": "idioma", "value": "inglés"}, "c1"), final("ok", [], found=False)])
    run_ctx(build_agent(model, retriever, retriever.db, store=store), "prefiero inglés", user="ana")
    other = ScriptedModel(script=[call("recall_preferences", {}, "c2"), final("ok", [], found=False)])
    result = run_ctx(build_agent(other, retriever, retriever.db, store=store), "¿mis preferencias?", user="luis")
    tool_msg = next(m for m in result["messages"] if m.type == "tool" and m.name == "recall_preferences")
    assert tool_msg.content == "Sin preferencias guardadas."
