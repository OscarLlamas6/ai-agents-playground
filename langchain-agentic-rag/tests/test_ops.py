"""Pruebas sin red: herramientas de operación, bitácora y escritura en la base. Las de Postgres se activan con DATABASE_URL_TEST."""

import os
from datetime import datetime
from pathlib import Path

import pytest
from conftest import ScriptedModel, call, final
from langchain_core.messages import AIMessage

from agent import build_agent
from db import MemoryDatabase, PgDatabase
from memory import Context
from ops_data import add_business_hours, add_months
from retriever import Retriever

MANUAL = Path(__file__).parents[1] / "data" / "mesa_de_ayuda.md"


@pytest.fixture
def retriever(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    r = Retriever(MANUAL, MemoryDatabase())
    r.ingest()
    return r


def run(agent, text, user="ana"):
    return agent.invoke({"messages": [{"role": "user", "content": text}]}, {"configurable": {"thread_id": "t1"}}, context=Context(user_id=user))


# ── reglas puras ──
def test_business_hours_skip_nights_and_weekends():
    assert add_business_hours(datetime(2026, 11, 9, 9, 30), 4) == datetime(2026, 11, 9, 13, 30)        # lunes, mismo día
    assert add_business_hours(datetime(2026, 11, 13, 16, 0), 4) == datetime(2026, 11, 16, 11, 0)       # viernes tarde → lunes
    assert add_business_hours(datetime(2026, 11, 14, 10, 0), 2) == datetime(2026, 11, 16, 10, 0)       # sábado → lunes
    assert add_business_hours(datetime(2026, 11, 13, 18, 0), 2) == datetime(2026, 11, 16, 10, 0)       # viernes tras el cierre


def test_add_months_clamps_to_month_end():
    assert add_months(datetime(2024, 1, 31).date(), 1).isoformat() == "2024-02-29"


# ── herramientas de solo lectura ──
def tool_output(result, name):
    return next(m.content for m in result["messages"] if m.type == "tool" and m.name == name)


def test_read_only_tools(retriever):
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[
            {"name": "get_ticket", "args": {"ticket_id": "t-101"}, "id": "a"},
            {"name": "sla_deadline", "args": {"priority": "alta", "opened_at": "2026-11-09T09:30:00", "as_of": "2026-11-09T16:00:00"}, "id": "b"},
            {"name": "lookup_asset", "args": {"hostname": "LT-0088"}, "id": "c"},
        ]),
        final("ok", [], found=False),
    ])
    result = run(build_agent(model, retriever, retriever.db), "datos")
    assert "Marta Solís" in tool_output(result, "get_ticket")
    assert "VENCIDO" in tool_output(result, "sla_deadline") and "2026-11-09 13:30" in tool_output(result, "sla_deadline")
    assert '"warranty_ends": "2024-09-20"' in tool_output(result, "lookup_asset")


def test_unknown_ticket_and_priority_are_reported_not_invented(retriever):
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[{"name": "get_ticket", "args": {"ticket_id": "T-999"}, "id": "a"},
                                          {"name": "sla_deadline", "args": {"priority": "urgente", "opened_at": "2026-11-09T09:00:00"}, "id": "b"}]),
        final("ok", [], found=False)])
    result = run(build_agent(model, retriever, retriever.db), "x")
    assert "No existe el ticket" in tool_output(result, "get_ticket")
    assert "Prioridad desconocida" in tool_output(result, "sla_deadline")


# ── bitácora (escrita por el código) ──
def test_audit_log_records_tokens_tools_and_found(retriever):
    first = AIMessage(content="", tool_calls=[{"name": "search_manual", "args": {"query": "vpn"}, "id": "c1"}],
                      usage_metadata={"input_tokens": 300, "output_tokens": 20, "total_tokens": 320})
    last = call("Answer", {"answer": "Instala el cliente.", "citations": ["D5"], "found": True}, "fin")
    last.usage_metadata = {"input_tokens": 500, "output_tokens": 40, "total_tokens": 540}
    run(build_agent(ScriptedModel(script=[first, last]), retriever, retriever.db), "¿Cómo uso la VPN?")
    (row,) = retriever.db.queries
    assert (row["input_tokens"], row["output_tokens"], row["model_calls"]) == (800, 60, 2)
    assert row["tools_used"] == ["search_manual"] and row["found"] is True and row["user_id"] == "ana"
    assert row["question"] == "¿Cómo uso la VPN?" and row["stopped_by"] is None


def test_audit_log_marks_runs_stopped_by_a_guard(retriever):
    same = lambda i: call("search_manual", {"query": "vpn"}, f"c{i}")  # noqa: E731
    run(build_agent(ScriptedModel(script=[same(1), same(2), final("nunca", [])]), retriever, retriever.db), "vpn")
    (row,) = retriever.db.queries
    assert row["found"] is None and "sin progreso" in row["stopped_by"]


# ── herramienta de escritura ──
def test_report_gap_writes_to_the_database(retriever):
    model = ScriptedModel(script=[
        call("report_gap", {"question": "¿Dan soporte a impresoras 3D?", "note": "El manual no menciona impresoras 3D."}, "c1"),
        final("No lo encuentro en el manual.", [], found=False)])
    run(build_agent(model, retriever, retriever.db), "¿Dan soporte a impresoras 3D?", user="luis")
    (gap,) = retriever.db.all_gaps()
    assert gap["user_id"] == "luis" and "3D" in gap["question"]


def test_new_manual_section_is_searchable(retriever):
    best, _ = retriever.search("tiempo máximo de atención de un ticket crítico horas hábiles", k=1)[0]
    assert best.section == "Tickets y tiempos de atención"


# ── Postgres + pgvector real (opcional) ──
@pytest.mark.skipif(not os.getenv("DATABASE_URL_TEST"), reason="define DATABASE_URL_TEST para probar con Postgres + pgvector")
def test_pgvector_end_to_end(monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "fake")
    db = PgDatabase(os.environ["DATABASE_URL_TEST"])
    retriever = Retriever(MANUAL, db)
    assert retriever.ingest(force=True)["reindexed"]
    best, score = retriever.search("VPN países autorizados excepción", k=1)[0]
    assert best.section == "VPN y trabajo remoto" and 0 < score <= 1
    again = Retriever(MANUAL, db)                                  # misma huella: la ingesta no se repite
    assert again.ready and not again.ingest()["reindexed"]
    db.add_gap(user_id="t", question="q", note="n")
    db.log_query(user_id="t", thread_id="x", question="q", input_tokens=1, output_tokens=2, model_calls=1, tools_used=["search_manual"], found=True, stopped_by=None)
    assert db.recent_queries(1)[0]["tools_used"] == ["search_manual"] and any(g["question"] == "q" for g in db.all_gaps())
    assert again.chunks


@pytest.mark.skipif(not os.getenv("DATABASE_URL_TEST"), reason="define DATABASE_URL_TEST para probar con Postgres + pgvector")
def test_long_term_memory_lives_in_postgres_and_survives_a_new_store(retriever):
    from memory import build_store

    dsn = os.environ["DATABASE_URL_TEST"]
    model = ScriptedModel(script=[call("remember_preference", {"key": "idioma_pg", "value": "español"}, "c1"), final("ok", [], found=False)])
    run(build_agent(model, retriever, retriever.db, store=build_store(dsn)), "prefiero español", user="ana-pg")
    other = ScriptedModel(script=[call("recall_preferences", {}, "c2"), final("ok", [], found=False)])
    result = run(build_agent(other, retriever, retriever.db, store=build_store(dsn)), "¿mis preferencias?", user="ana-pg")   # un store NUEVO: lee de la base
    assert "idioma_pg = español" in tool_output(result, "recall_preferences")
