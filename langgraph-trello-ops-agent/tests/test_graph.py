"""Pruebas del grafo sin red: modelo guionado + Trello simulado."""

import sqlite3

import pytest
from conftest import call
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from trello_ops.nodes import OpsNodes
from trello_ops.persistence import open_persistence
from trello_ops.runner import drive
from trello_ops.state import Context

CREATE = call("create_card", {"list_id": "l_ops_todo", "name": "Revisar alertas"}, "w1")


def cfg(thread="t1"):
    return {"configurable": {"thread_id": thread}}


def ask(graph, text, thread="t1", user="u1", recursion_limit=25):
    config = {**cfg(thread), "recursion_limit": recursion_limit}
    return graph.invoke({"messages": [{"role": "user", "content": text}]}, config, context=Context(user))


def tool_messages(result):
    return [m for m in result["messages"] if m.type == "tool"]


# ───────────── lecturas: no piden aprobación ─────────────


def test_reads_run_without_interrupt(make_graph, trello):
    graph, _ = make_graph([call("list_boards", {}, "r1"), AIMessage("Tienes dos tableros.")], checkpointer=InMemorySaver())
    result = ask(graph, "¿qué tableros tengo?")
    assert "__interrupt__" not in result and result["messages"][-1].content == "Tienes dos tableros."
    assert "Operaciones TI" in tool_messages(result)[0].content and trello.writes == []


# ───────────── aprobación humana ─────────────


def test_write_pauses_and_approval_executes(make_graph, trello):
    graph, _ = make_graph([CREATE, AIMessage("Listo.")], checkpointer=InMemorySaver())
    paused = ask(graph, "crea una tarjeta")
    request = paused["__interrupt__"][0].value
    assert request["type"] == "approval_request" and "Revisar alertas" in request["actions"][0]["summary"]
    assert trello.writes == []                                   # PAUSADO: todavía no se escribió nada

    final = graph.invoke(Command(resume={"approved": True}), cfg(), context=Context("u1"))
    assert trello.writes == [("create_card", "l_ops_todo", "Revisar alertas")]
    assert final["messages"][-1].content == "Listo."
    assert [a["status"] for a in final["audit"]] == ["ok"]       # la bitácora registra lo ejecutado


def test_rejection_executes_nothing_and_the_model_is_told(make_graph, trello):
    graph, _ = make_graph([CREATE, AIMessage("Entendido, no creé nada.")], checkpointer=InMemorySaver())
    ask(graph, "crea una tarjeta")
    final = graph.invoke(Command(resume={"approved": False, "reason": "no es urgente"}), cfg(), context=Context("u1"))
    assert trello.writes == [] and "w1" not in trello.cards
    assert "RECHAZADA" in tool_messages(final)[-1].content and "no es urgente" in tool_messages(final)[-1].content
    assert final["audit"][-1]["status"] == "rejected"


def test_plain_boolean_resume_is_accepted(make_graph, trello):
    graph, _ = make_graph([CREATE, AIMessage("ok")], checkpointer=InMemorySaver())
    ask(graph, "crea una tarjeta")
    graph.invoke(Command(resume=True), cfg(), context=Context("u1"))
    assert len(trello.writes) == 1


def test_execute_refuses_writes_without_approval(trello):
    """Defensa en profundidad: aunque algo saltara el nodo de aprobación, `execute` no escribe sin ella."""
    from trello_ops.graph import RUNBOOK
    from trello_ops.retriever import Retriever
    from trello_ops.tools import build_tools

    class Runtime:
        store, context = None, Context("u1")

    nodes = OpsNodes(model=type("M", (), {"bind_tools": lambda self, t: self})(), tools=build_tools(trello, Retriever(RUNBOOK)), trello=trello)
    state = {"messages": [AIMessage("", tool_calls=CREATE.tool_calls)], "approval": None}
    out = nodes.execute(state, Runtime())
    assert trello.writes == [] and out["audit"][0]["status"] == "rejected"


def test_drive_helper_loops_until_done(make_graph, trello):
    graph, _ = make_graph([CREATE, AIMessage("Hecho.")], checkpointer=InMemorySaver())
    asked = []
    state = drive(graph, "crea una tarjeta", "t9", decide=lambda r: asked.append(r) or {"approved": True})
    assert len(asked) == 1 and state["messages"][-1].content == "Hecho." and len(trello.writes) == 1


def test_tool_errors_reach_the_model_as_messages(make_graph):
    bad = call("list_lists", {"board_id": "no-existe"}, "r1")
    graph, _ = make_graph([bad, AIMessage("Ese tablero no existe.")], checkpointer=InMemorySaver())
    result = ask(graph, "listas del tablero x")
    assert tool_messages(result)[0].content.startswith("ERROR: tablero inexistente")


# ───────────── tope de pasos y falta de progreso ─────────────


def distinct_searches(n=30):
    return [call("search_cards", {"query": f"consulta {i}"}, f"s{i}") for i in range(n)]


def test_step_limit_stops_the_loop(make_graph):
    graph, model = make_graph(distinct_searches(), checkpointer=InMemorySaver(), max_steps=3)
    result = ask(graph, "busca sin parar")
    assert len(model.system_prompts) == 3 and result["steps"] == 3
    assert "tope de pasos" in result["messages"][-1].content and result["audit"][-1]["status"] == "halted"
    # El historial queda válido: cada petición de herramienta tiene su respuesta.
    asked = {c["id"] for m in result["messages"] if m.type == "ai" for c in m.tool_calls}
    answered = {m.tool_call_id for m in tool_messages(result)}
    assert asked == answered


def test_repeated_identical_calls_are_detected_as_no_progress(make_graph):
    graph, model = make_graph([call("search_cards", {"query": "lo mismo"}, "s")], checkpointer=InMemorySaver(), max_steps=20)
    result = ask(graph, "busca")
    assert len(model.system_prompts) == 3 and "repitiendo" in result["messages"][-1].content



def test_recursion_limit_is_the_hard_backstop(make_graph):
    graph, _ = make_graph(distinct_searches(), checkpointer=InMemorySaver(), max_steps=50)
    with pytest.raises(GraphRecursionError):
        ask(graph, "busca sin parar", recursion_limit=6)


def test_steps_reset_on_each_turn(make_graph):
    graph, _ = make_graph([call("list_boards", {}, "a"), AIMessage("uno"), call("list_boards", {}, "b"), AIMessage("dos")],
                          checkpointer=InMemorySaver(), max_steps=2)
    assert ask(graph, "uno")["steps"] == 2
    assert ask(graph, "dos")["steps"] == 2          # el presupuesto es por pregunta, no por hilo


# ───────────── memoria de corto plazo: persistencia entre ejecuciones del hilo ─────────────


def test_thread_state_survives_a_new_process(make_graph, tmp_path):
    db = str(tmp_path / "ops.sqlite")
    with open_persistence(db) as (saver, store):
        graph, _ = make_graph([AIMessage("Hola, soy el agente.")], checkpointer=saver, store=store)
        ask(graph, "Hola", thread="conv")
    # "Otro proceso": conexión y grafo nuevos sobre el mismo archivo.
    with open_persistence(db) as (saver, store):
        graph, model = make_graph([AIMessage("Me dijiste hola.")], checkpointer=saver, store=store)
        result = ask(graph, "¿qué te dije?", thread="conv")
    assert [m.content for m in result["messages"] if m.type == "human"] == ["Hola", "¿qué te dije?"]
    assert len(result["messages"]) == 4
    with open_persistence(db) as (saver, store):                  # otro hilo: empieza de cero
        graph, _ = make_graph([AIMessage("nuevo")], checkpointer=saver, store=store)
        assert len(ask(graph, "Hola", thread="otra")["messages"]) == 2


def test_pending_approval_survives_a_restart(make_graph, trello, tmp_path):
    db = str(tmp_path / "ops.sqlite")
    with open_persistence(db) as (saver, store):
        graph, _ = make_graph([CREATE, AIMessage("Listo.")], checkpointer=saver, store=store)
        assert "__interrupt__" in ask(graph, "crea una tarjeta", thread="pend")
    assert trello.writes == []
    with open_persistence(db) as (saver, store):                  # el proceso "murió" esperando; otro lo reanuda
        graph, _ = make_graph([AIMessage("Listo.")], checkpointer=saver, store=store)
        graph.invoke(Command(resume={"approved": True}), cfg("pend"), context=Context("u1"))
    assert len(trello.writes) == 1


def test_time_travel_lists_checkpoints(make_graph):
    graph, _ = make_graph([call("list_boards", {}, "a"), AIMessage("ok")], checkpointer=InMemorySaver())
    ask(graph, "hola")
    history = list(graph.get_state_history(cfg()))
    assert len(history) >= 5 and history[0].next == ()           # el más reciente está terminado
    assert any(s.next == ("agent",) for s in history)             # y hay puntos intermedios a los que volver


# ───────────── memoria de largo plazo (Store) ─────────────


def test_preference_is_shared_across_threads_but_not_across_users(make_graph):
    remember = call("remember_preference", {"key": "default_board", "value": "Operaciones TI"}, "m1")
    graph, model = make_graph([remember, AIMessage("Guardado."), AIMessage("Hola de nuevo."), AIMessage("Hola, desconocido.")],
                              checkpointer=InMemorySaver(), store=InMemoryStore())
    ask(graph, "mi tablero por defecto es Operaciones TI", thread="hilo-1", user="ana")
    ask(graph, "hola", thread="hilo-2", user="ana")               # OTRO hilo, mismo usuario
    ask(graph, "hola", thread="hilo-3", user="luis")              # otro usuario
    assert "default_board = Operaciones TI" not in model.system_prompts[0]
    assert "default_board = Operaciones TI" in model.system_prompts[2]
    assert "ninguna guardada" in model.system_prompts[3]


def test_preferences_persist_in_sqlite_and_keys_are_whitelisted(make_graph, tmp_path):
    db = str(tmp_path / "ops.sqlite")
    bad = call("remember_preference", {"key": "password", "value": "x"}, "m0")
    good = call("remember_preference", {"key": "default_list", "value": "Por hacer"}, "m1")
    with open_persistence(db) as (saver, store):
        graph, _ = make_graph([bad, good, AIMessage("ok")], checkpointer=saver, store=store)
        result = ask(graph, "guarda cosas", thread="a", user="ana")
    assert "solo se guardan" in tool_messages(result)[0].content
    with open_persistence(db) as (saver, store):
        graph, model = make_graph([AIMessage("hola")], checkpointer=saver, store=store)
        ask(graph, "hola", thread="b", user="ana")
    assert "default_list = Por hacer" in model.system_prompts[0] and "password" not in model.system_prompts[0]


def test_openai_tools_are_not_strict_so_optional_arguments_can_be_omitted(trello, monkeypatch):
    """En modo estricto OpenAI obliga a rellenar todos los parámetros opcionales (filtros inventados que vacían las búsquedas)."""
    from langchain_openai import ChatOpenAI

    from trello_ops.graph import RUNBOOK
    from trello_ops.retriever import Retriever
    from trello_ops.tools import build_tools

    nodes = OpsNodes(ChatOpenAI(model="gpt-6-luna", api_key="sk-falsa"), build_tools(trello, Retriever(RUNBOOK)), trello)
    sent = nodes.model_with_tools.kwargs["tools"]            # lo que viajaría en la petición; no se llama a la red
    assert sent and all(t.get("function", t).get("strict") is False for t in sent)
