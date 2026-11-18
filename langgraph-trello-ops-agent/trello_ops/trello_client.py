"""Cliente de Trello con dos implementaciones intercambiables: `FakeTrello` (en memoria) y `LiveTrello` (API REST).

`TRELLO_MODE=fake` (por defecto) no usa red ni cuenta. `TRELLO_MODE=live` usa TRELLO_API_KEY y TRELLO_TOKEN del `.env`.

Protecciones de las ESCRITURAS en modo live (además de la aprobación humana del grafo):
- `TRELLO_WRITE_BOARDS`: lista de ids de tableros donde se permite escribir. Vacía = escrituras bloqueadas.
- `TRELLO_DRY_RUN=1`: valida todo pero NO escribe (devuelve "[dry-run] ...").
- Reintentos con espera solo para fallas transitorias (429 y 5xx) y tiempo límite en cada llamada.
- Los errores nunca incluyen la clave ni el token.

Rutas (OpenAPI oficial https://dac-static.atlassian.com/cloud/trello/swagger.v3.json, base https://api.trello.com/1):
GET /members/me/boards · GET /boards/{id}/lists · GET /boards/{id}/cards · GET /search · GET /cards/{id} · GET /lists/{id}
POST /cards (idList, name, desc) · PUT /cards/{id} (idList) · POST /cards/{id}/actions/comments (text)
"""

import os
import time
from typing import Protocol

API_BASE = "https://api.trello.com/1"


class TrelloError(Exception):
    """Error de Trello con un mensaje seguro de mostrar (sin credenciales)."""


class Trello(Protocol):
    def list_boards(self) -> list[dict]: ...
    def list_lists(self, board_id: str) -> list[dict]: ...
    def list_cards(self, board_id: str, list_id: str = "") -> list[dict]: ...
    def search_cards(self, query: str, board_id: str = "") -> list[dict]: ...
    def get_list(self, list_id: str) -> dict: ...
    def get_card(self, card_id: str) -> dict: ...
    def create_card(self, list_id: str, name: str, desc: str = "") -> dict: ...
    def move_card(self, card_id: str, list_id: str) -> dict: ...
    def add_comment(self, card_id: str, text: str) -> dict: ...


# ───────────────────────── Trello simulado (en memoria) ─────────────────────────


def seed_data() -> dict:
    """Datos ficticios: dos tableros de una empresa imaginaria."""
    return {
        "boards": [
            {"id": "b_ops", "name": "Operaciones TI"},
            {"id": "b_cloud", "name": "Migración a la nube"},
        ],
        "lists": [
            {"id": "l_ops_todo", "name": "Por hacer", "idBoard": "b_ops"},
            {"id": "l_ops_doing", "name": "En curso", "idBoard": "b_ops"},
            {"id": "l_ops_review", "name": "En revisión", "idBoard": "b_ops"},
            {"id": "l_ops_done", "name": "Hecho", "idBoard": "b_ops"},
            {"id": "l_cl_backlog", "name": "Backlog", "idBoard": "b_cloud"},
            {"id": "l_cl_doing", "name": "En curso", "idBoard": "b_cloud"},
            {"id": "l_cl_done", "name": "Hecho", "idBoard": "b_cloud"},
        ],
        "cards": [
            {"id": "c1", "name": "Renovar certificado del portal", "desc": "Vence a fin de mes.", "idList": "l_ops_todo"},
            {"id": "c2", "name": "Actualizar parches del servidor de archivos", "desc": "", "idList": "l_ops_doing"},
            {"id": "c3", "name": "Revisar respaldo semanal", "desc": "", "idList": "l_ops_review"},
            {"id": "c4", "name": "Migrar base de datos de inventario", "desc": "Ventana el sábado.", "idList": "l_cl_backlog"},
            {"id": "c5", "name": "Configurar balanceador del portal", "desc": "", "idList": "l_cl_doing"},
        ],
    }


class FakeTrello:
    """Trello en memoria. Registra cada escritura en `self.writes` para poder comprobarla en las pruebas."""

    def __init__(self, data: dict | None = None):
        data = data or seed_data()
        self.boards = {b["id"]: dict(b) for b in data["boards"]}
        self.lists = {x["id"]: dict(x) for x in data["lists"]}
        self.cards = {c["id"]: dict(c) for c in data["cards"]}
        self.comments: dict[str, list[str]] = {}
        self.writes: list[tuple] = []
        self._next = len(self.cards) + 1

    def _card(self, card: dict) -> dict:
        lst = self.lists[card["idList"]]
        return {**card, "listName": lst["name"], "idBoard": lst["idBoard"]}

    def list_boards(self) -> list[dict]:
        return list(self.boards.values())

    def list_lists(self, board_id: str) -> list[dict]:
        if board_id not in self.boards:
            raise TrelloError(f"tablero inexistente: {board_id}")
        return [x for x in self.lists.values() if x["idBoard"] == board_id]

    def list_cards(self, board_id: str, list_id: str = "") -> list[dict]:
        self.list_lists(board_id)
        return [self._card(c) for c in self.cards.values()
                if self.lists[c["idList"]]["idBoard"] == board_id and (not list_id or c["idList"] == list_id)]

    def search_cards(self, query: str, board_id: str = "") -> list[dict]:
        words = [w for w in query.lower().split() if len(w) > 2]
        found = [self._card(c) for c in self.cards.values() if any(w in (c["name"] + " " + c["desc"]).lower() for w in words)]
        return [c for c in found if not board_id or c["idBoard"] == board_id]

    def get_list(self, list_id: str) -> dict:
        if list_id not in self.lists:
            raise TrelloError(f"lista inexistente: {list_id}")
        return self.lists[list_id]

    def get_card(self, card_id: str) -> dict:
        if card_id not in self.cards:
            raise TrelloError(f"tarjeta inexistente: {card_id}")
        return self._card(self.cards[card_id])

    def create_card(self, list_id: str, name: str, desc: str = "") -> dict:
        self.get_list(list_id)
        card = {"id": f"c{self._next}", "name": name, "desc": desc, "idList": list_id}
        self._next += 1
        self.cards[card["id"]] = card
        self.writes.append(("create_card", list_id, name))
        return self._card(card)

    def move_card(self, card_id: str, list_id: str) -> dict:
        card = self.get_card(card_id)
        if self.get_list(list_id)["idBoard"] != card["idBoard"]:
            raise TrelloError("solo se permite mover entre listas del mismo tablero")
        self.cards[card_id]["idList"] = list_id
        self.writes.append(("move_card", card_id, list_id))
        return self.get_card(card_id)

    def add_comment(self, card_id: str, text: str) -> dict:
        self.get_card(card_id)
        self.comments.setdefault(card_id, []).append(text)
        self.writes.append(("add_comment", card_id, text))
        return {"cardId": card_id, "text": text}


# ───────────────────────── Trello real (API REST) ─────────────────────────


class LiveTrello:
    def __init__(self, api_key: str, token: str, write_boards: set[str] | None = None, dry_run: bool = False,
                 session=None, sleep=time.sleep):
        if not api_key or not token:
            raise TrelloError("faltan TRELLO_API_KEY y TRELLO_TOKEN en el .env (o usa TRELLO_MODE=fake)")
        import requests

        self.session = session or requests.Session()
        # Autenticación documentada: cabecera Authorization (así la clave no viaja en la URL ni en los registros).
        self.session.headers["Authorization"] = f'OAuth oauth_consumer_key="{api_key}", oauth_token="{token}"'
        self.write_boards = write_boards or set()
        self.dry_run = dry_run
        self._sleep = sleep

    def _request(self, method: str, path: str, **params) -> dict | list:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        for attempt in range(3):
            try:
                r = self.session.request(method, f"{API_BASE}{path}", params=params, timeout=15)
            except Exception as exc:                       # red caída, tiempo agotado...
                if attempt == 2:
                    raise TrelloError(f"sin conexión con Trello ({type(exc).__name__})") from None
                self._sleep(2**attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                self._sleep(2**attempt)                    # falla transitoria: espera creciente y reintenta
                continue
            if r.status_code == 401:
                raise TrelloError("401: clave o token inválidos o vencidos")
            if r.status_code >= 400:
                raise TrelloError(f"{r.status_code} en {method} {path}")   # sin la URL completa: nada de credenciales
            return r.json()
        raise TrelloError("reintentos agotados")

    # Lecturas
    def list_boards(self) -> list[dict]:
        return self._request("GET", "/members/me/boards", filter="open", fields="name")

    def list_lists(self, board_id: str) -> list[dict]:
        return self._request("GET", f"/boards/{board_id}/lists", filter="open", fields="name,idBoard")

    def list_cards(self, board_id: str, list_id: str = "") -> list[dict]:
        names = {x["id"]: x["name"] for x in self.list_lists(board_id)}
        cards = self._request("GET", f"/boards/{board_id}/cards", fields="name,desc,idList,idBoard")
        return [{**c, "listName": names.get(c["idList"], "")} for c in cards if not list_id or c["idList"] == list_id]

    def search_cards(self, query: str, board_id: str = "") -> list[dict]:
        res = self._request("GET", "/search", query=query, modelTypes="cards", card_fields="name,desc,idList,idBoard",
                            card_list="true", cards_limit=10, idBoards=board_id)
        return [{**c, "listName": (c.get("list") or {}).get("name", "")} for c in res.get("cards", [])]

    def get_list(self, list_id: str) -> dict:
        return self._request("GET", f"/lists/{list_id}", fields="name,idBoard")

    def get_card(self, card_id: str) -> dict:
        card = self._request("GET", f"/cards/{card_id}", fields="name,desc,idList,idBoard")
        return {**card, "listName": self.get_list(card["idList"])["name"]}

    # Escrituras (con guardas)
    def _guard(self, board_id: str) -> None:
        if not self.write_boards:
            raise TrelloError("escrituras bloqueadas: define TRELLO_WRITE_BOARDS con los ids de tableros permitidos")
        if board_id not in self.write_boards:
            raise TrelloError(f"el tablero {board_id} no está en TRELLO_WRITE_BOARDS")

    def create_card(self, list_id: str, name: str, desc: str = "") -> dict:
        self._guard(self.get_list(list_id)["idBoard"])
        if self.dry_run:
            return {"id": "dry-run", "name": name, "idList": list_id, "dryRun": True}
        return self._request("POST", "/cards", idList=list_id, name=name, desc=desc)

    def move_card(self, card_id: str, list_id: str) -> dict:
        card, target = self.get_card(card_id), self.get_list(list_id)
        self._guard(card["idBoard"])
        if target["idBoard"] != card["idBoard"]:
            raise TrelloError("solo se permite mover entre listas del mismo tablero")
        if self.dry_run:
            return {**card, "idList": list_id, "dryRun": True}
        return self._request("PUT", f"/cards/{card_id}", idList=list_id)

    def add_comment(self, card_id: str, text: str) -> dict:
        self._guard(self.get_card(card_id)["idBoard"])
        if self.dry_run:
            return {"cardId": card_id, "text": text, "dryRun": True}
        return self._request("POST", f"/cards/{card_id}/actions/comments", text=text)


def build_trello() -> Trello:
    """Elige la implementación según TRELLO_MODE (fake por defecto)."""
    mode = os.getenv("TRELLO_MODE", "fake").strip().lower()
    if mode == "fake":
        return FakeTrello()
    if mode == "live":
        boards = {b.strip() for b in os.getenv("TRELLO_WRITE_BOARDS", "").split(",") if b.strip()}
        return LiveTrello(os.getenv("TRELLO_API_KEY", ""), os.getenv("TRELLO_TOKEN", ""), boards,
                          dry_run=os.getenv("TRELLO_DRY_RUN", "0") == "1")
    raise ValueError(f"TRELLO_MODE debe ser fake o live (recibido: {mode!r})")
