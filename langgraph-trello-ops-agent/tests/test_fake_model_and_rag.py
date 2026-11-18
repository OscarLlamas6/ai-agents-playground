"""El runbook se recupera bien y el modelo falso resuelve las frases de la clase de punta a punta."""

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from trello_ops.fake_model import FakeOpsModel
from trello_ops.graph import RUNBOOK, build_graph
from trello_ops.retriever import Retriever
from trello_ops.runner import drive


def test_runbook_is_split_and_retrieved():
    r = Retriever(RUNBOOK)
    assert len(r.chunks) >= 6 and r.chunks[0].id == "R1"
    best, _ = r.search("una tarjeta puede saltarse la revisión y pasar a Hecho", k=1)[0]
    assert best.section == "Cómo mover las tarjetas"


def run(trello, text, approve=True, thread="t", user="u"):
    graph = build_graph(FakeOpsModel(), trello, Retriever(RUNBOOK), checkpointer=InMemorySaver(), store=InMemoryStore())
    return drive(graph, text, thread, user_id=user, decide=lambda r: {"approved": approve}), graph


def test_fake_model_creates_a_card_following_the_runbook(trello):
    state, _ = run(trello, 'Crea una tarjeta "Revisar alertas de disco"')
    assert trello.writes == [("create_card", "l_ops_todo", "Revisar alertas de disco")]
    tools = [c["name"] for m in state["messages"] if m.type == "ai" for c in m.tool_calls]
    assert tools[0] == "search_runbook" and tools[-1] == "create_card"      # RAG primero, escritura al final


def test_fake_model_move_and_comment_and_rejection(trello):
    run(trello, 'Mueve la tarjeta "Revisar respaldo semanal" a "Hecho"')
    assert trello.get_card("c3")["listName"] == "Hecho"
    run(trello, 'Comenta en "Renovar certificado" "Pedido el certificado"')
    assert trello.comments["c1"] == ["Pedido el certificado"]
    before = len(trello.writes)
    state, _ = run(trello, 'Mueve la tarjeta "Renovar certificado" a "En curso"', approve=False)
    assert len(trello.writes) == before and "rechazó" in state["messages"][-1].content


def test_fake_model_uses_default_board_from_long_term_memory(trello):
    graph = build_graph(FakeOpsModel(), trello, Retriever(RUNBOOK), checkpointer=InMemorySaver(), store=InMemoryStore())
    drive(graph, 'Mi tablero por defecto es "Migración a la nube"', "h1", user_id="ana")
    state = drive(graph, "Muéstrame las tarjetas", "h2", user_id="ana")           # hilo distinto
    assert "Migrar base de datos de inventario" in state["messages"][-1].content
    state = drive(graph, "Muéstrame las tarjetas", "h3", user_id="luis")          # otro usuario: no tiene preferencia
    assert "¿De qué tablero?" in state["messages"][-1].content
