"""Un "modelo" falso y determinista (sin red): lee la petición con expresiones regulares y encadena herramientas.

Sirve para ver el grafo completo (lectura, runbook, aprobación, escritura, memoria) sin llaves ni costo. NO es un modelo
de lenguaje: solo entiende unas pocas frases (ver README). Con un modelo real el mismo grafo entiende lenguaje natural.
"""

import hashlib
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

Q = r"[\"“«']([^\"”»']+)[\"”»']"          # texto entre comillas


class FakeOpsModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-ops"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._decide(messages))])

    # ── helpers ──
    @staticmethod
    def _call(name: str, args: dict) -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"fake-{name}-{hashlib.md5(str(args).encode()).hexdigest()[:6]}"}])

    def _decide(self, messages) -> AIMessage:
        system = next((m.content for m in messages if m.type == "system"), "")
        last_human = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
        text = messages[last_human].content
        turn = messages[last_human + 1:]
        done = {m.name: m.content for m in turn if isinstance(m, ToolMessage)}     # herramienta → último resultado de este turno
        low = text.lower()
        board = (re.search(r"default_board = ([^;]+)", system) or [None, ""])[1].strip()

        if "rechazada" in " ".join(done.values()).lower():
            return AIMessage("La persona rechazó la acción, así que no hice ningún cambio. Dime si quieres otra alternativa.")

        # 1) Preferencia
        if m := re.search(r"tablero (?:por defecto|predeterminado)\D+?" + Q, text, re.I):
            if "remember_preference" not in done:
                return self._call("remember_preference", {"key": "default_board", "value": m[1]})
            return AIMessage(f"Listo: recordaré «{m[1]}» como tu tablero por defecto.")

        # 2) Tableros
        if re.search(r"\btableros\b", low) and not re.search(r"tarjeta", low):
            return self._call("list_boards", {}) if "list_boards" not in done else AIMessage("Tus tableros:\n" + done["list_boards"])

        # 3) Crear tarjeta
        if m := re.search(r"crea\w*\s+(?:una\s+)?tarjeta\D*?" + Q, text, re.I):
            return self._create(m[1], text, done, board)

        # 4) Mover tarjeta
        if m := re.search(r"mueve\w*\s+(?:la\s+tarjeta\s+)?" + Q + r"\s+a\s+(?:la\s+lista\s+)?" + Q, text, re.I):
            return self._move(m[1], m[2], done)

        # 5) Comentar
        if m := re.search(r"coment\w*\s+(?:en\s+)?(?:la\s+tarjeta\s+)?" + Q + r"\D*?" + Q, text, re.I):
            return self._comment(m[1], m[2], done)

        # 6) Buscar o mostrar tarjetas
        if m := re.search(r"busca\w*\D*?" + Q, text, re.I):
            if "search_cards" not in done:
                return self._call("search_cards", {"query": m[1], "board_id": ""})
            return AIMessage("Encontré esto:\n" + done["search_cards"])
        if "tarjeta" in low:
            if not board:
                return AIMessage("¿De qué tablero? Dime el nombre o guarda uno como tu tablero por defecto.")
            return self._cards_of(board, done)

        return AIMessage("Soy el modelo falso de la clase: entiendo frases como «muestra mis tableros», «crea una tarjeta \"X\"», "
                         "«mueve la tarjeta \"X\" a \"En curso\"», «comenta en \"X\": \"texto\"» o «mi tablero por defecto es \"Operaciones TI\"».")

    def _cards_of(self, board: str, done: dict) -> AIMessage:
        if "list_boards" not in done:
            return self._call("list_boards", {})
        board_id = self._find(done["list_boards"], board)
        if "list_cards" not in done:
            return self._call("list_cards", {"board_id": board_id})
        return AIMessage(f"Tarjetas de «{board}»:\n" + done["list_cards"])

    def _create(self, title: str, text: str, done: dict, board: str) -> AIMessage:
        if "search_runbook" not in done:
            return self._call("search_runbook", {"query": "en qué lista crear una tarjeta nueva y cómo nombrarla"})
        if "list_boards" not in done:
            return self._call("list_boards", {})
        board_name = board or "Operaciones TI"
        board_id = self._find(done["list_boards"], board_name)
        if "list_lists" not in done:
            return self._call("list_lists", {"board_id": board_id})
        wanted = (re.search(r"\ben\s+(?:la\s+lista\s+)?" + Q, text.split(title, 1)[-1]) or [None, "Por hacer" if board_name == "Operaciones TI" else "Backlog"])[1]
        if "create_card" not in done:
            return self._call("create_card", {"list_id": self._find(done["list_lists"], wanted), "name": title})
        return AIMessage(f"Hecho. {done['create_card']} (en «{wanted}», siguiendo el runbook).")

    def _move(self, title: str, target: str, done: dict) -> AIMessage:
        if "search_runbook" not in done:
            return self._call("search_runbook", {"query": "cómo mover tarjetas entre listas"})
        if "search_cards" not in done:
            return self._call("search_cards", {"query": title})
        line = next((ln for ln in done["search_cards"].splitlines() if title.lower() in ln.lower()), "")
        if not line:
            return AIMessage(f"No encontré ninguna tarjeta llamada «{title}».")
        card_id, board_id = line.split(" | ")[0], line.rsplit("tablero: ", 1)[-1]
        if "list_lists" not in done:
            return self._call("list_lists", {"board_id": board_id})
        if "move_card" not in done:
            return self._call("move_card", {"card_id": card_id, "list_id": self._find(done["list_lists"], target)})
        return AIMessage(f"Hecho. {done['move_card']} → «{target}».")

    def _comment(self, title: str, comment: str, done: dict) -> AIMessage:
        if "search_cards" not in done:
            return self._call("search_cards", {"query": title})
        line = next((ln for ln in done["search_cards"].splitlines() if title.lower() in ln.lower()), "")
        if not line:
            return AIMessage(f"No encontré ninguna tarjeta llamada «{title}».")
        if "add_comment" not in done:
            return self._call("add_comment", {"card_id": line.split(" | ")[0], "text": comment})
        return AIMessage(f"Hecho. {done['add_comment']}.")

    @staticmethod
    def _find(listing: str, name: str) -> str:
        """Devuelve el id de la línea 'id | nombre' cuyo nombre coincide (sin distinguir mayúsculas ni tildes)."""
        norm = lambda s: re.sub(r"\W", "", s.lower())   # noqa: E731
        for line in listing.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and (norm(parts[1]) == norm(name) or parts[0] == name):
                return parts[0]
        return name
