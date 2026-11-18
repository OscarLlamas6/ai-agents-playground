"""Las HERRAMIENTAS: funciones que el modelo puede PEDIR. El docstring es lo que el modelo lee para elegirlas.

Se clasifican en tres grupos; el grafo decide por grupo si hace falta aprobación:
- READ_TOOLS: solo leen. Se ejecutan sin preguntar.
- WRITE_TOOLS: escriben en Trello. SIEMPRE pasan por el nodo de aprobación humana (`interrupt`).
- MEMORY_TOOLS: guardan preferencias en el Store; el propio grafo las resuelve (necesitan acceso al Store).
"""

from langchain_core.tools import BaseTool, tool

from trello_ops.retriever import Retriever
from trello_ops.trello_client import Trello

READ_TOOLS = {"list_boards", "list_lists", "list_cards", "search_cards", "search_runbook"}
WRITE_TOOLS = {"create_card", "move_card", "add_comment"}
MEMORY_TOOLS = {"remember_preference"}
PREFERENCE_KEYS = {"default_board", "default_list"}   # lista cerrada: el modelo no guarda cualquier cosa
MAX_ITEMS = 25                                         # tope de líneas por resultado: protege el contexto


def _lines(items: list[str]) -> str:
    shown = items[:MAX_ITEMS]
    extra = f"\n(+{len(items) - MAX_ITEMS} más; afina la búsqueda)" if len(items) > MAX_ITEMS else ""
    return "\n".join(shown) + extra if shown else "Sin resultados."


def _card_line(c: dict) -> str:
    return f"{c['id']} | {c['name']} | lista: {c.get('listName', '')} | tablero: {c.get('idBoard', '')}"


def build_tools(trello: Trello, retriever: Retriever) -> dict[str, BaseTool]:
    @tool
    def list_boards() -> str:
        """Lista los tableros de Trello del usuario como 'id | nombre'."""
        return _lines([f"{b['id']} | {b['name']}" for b in trello.list_boards()])

    @tool
    def list_lists(board_id: str) -> str:
        """Lista las listas (columnas) de un tablero como 'id | nombre'. Necesita el id del tablero."""
        return _lines([f"{x['id']} | {x['name']}" for x in trello.list_lists(board_id)])

    @tool
    def list_cards(board_id: str, list_id: str = "") -> str:
        """Lista las tarjetas de un tablero (opcionalmente solo las de una lista) como 'id | nombre | lista | tablero'."""
        return _lines([_card_line(c) for c in trello.list_cards(board_id, list_id)])

    @tool
    def search_cards(query: str, board_id: str = "") -> str:
        """Busca tarjetas por texto en su nombre o descripción. Opcionalmente limita la búsqueda a un tablero."""
        return _lines([_card_line(c) for c in trello.search_cards(query, board_id)])

    @tool
    def search_runbook(query: str) -> str:
        """Busca en el runbook de operaciones del equipo (reglas para nombrar, crear y mover tarjetas). Devuelve fragmentos con su id."""
        return "\n".join(f"[{c.id}] ({c.section}) {c.text}" for c, _ in retriever.search(query, k=3))

    @tool
    def create_card(list_id: str, name: str, desc: str = "") -> str:
        """ESCRITURA (requiere aprobación). Crea una tarjeta en una lista. Necesita el id de la lista y un título."""
        card = trello.create_card(list_id, name, desc)
        return f"Tarjeta creada: {card['id']} | {card['name']}" + (" (dry-run)" if card.get("dryRun") else "")

    @tool
    def move_card(card_id: str, list_id: str) -> str:
        """ESCRITURA (requiere aprobación). Mueve una tarjeta a otra lista del MISMO tablero."""
        card = trello.move_card(card_id, list_id)
        return f"Tarjeta movida: {card['id']} | {card['name']}" + (" (dry-run)" if card.get("dryRun") else "")

    @tool
    def add_comment(card_id: str, text: str) -> str:
        """ESCRITURA (requiere aprobación). Agrega un comentario a una tarjeta."""
        res = trello.add_comment(card_id, text)
        return f"Comentario agregado a {card_id}" + (" (dry-run)" if res.get("dryRun") else "")

    @tool
    def remember_preference(key: str, value: str) -> str:
        """Guarda una preferencia del usuario para futuras conversaciones. key: 'default_board' o 'default_list'; value: id o nombre."""
        return "(la resuelve el grafo)"   # el cuerpo nunca se ejecuta: el nodo `execute` escribe en el Store

    tools = [list_boards, list_lists, list_cards, search_cards, search_runbook,
             create_card, move_card, add_comment, remember_preference]
    return {t.name: t for t in tools}
