"""Un modelo guionado, SIN red y SIN costo, para ver el flujo del agente con herramientas MCP.

No razona: aplica reglas sobre el último mensaje. Un modelo real decide con lenguaje; el bucle que lo rodea
(herramientas MCP, aprobación, sesiones) es exactamente el mismo. Sí "recuerda" lo que hay en el historial de la
sesión (los mensajes que le entrega el checkpointer), y por eso sirve para demostrar sesiones persistentes.

Peticiones que entiende:
    "Resume las notas de <nombre>"            → search_contacts → get_contact_notes → resumen
    "Agrega una nota a <nombre>: '<texto>'"   → search_contacts → add_note
    "¿Y sus notas?" / "Agrega una nota ahí: '<texto>'"   → usan el contacto de la conversación anterior (memoria)
    "¿De quién hablábamos?"                   → responde con el contacto que quedó en el historial (o dice que no hay)
    "¿Qué hora es en <zona>?"                 → get_current_time (de un segundo servidor MCP, en el modo multi-servidor)
"""

import json
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _usage(messages, text: str) -> dict[str, int]:
    """Uso de tokens APROXIMADO (4 caracteres ≈ 1 token): solo para que la API tenga algo que sumar en las demos."""
    seen = sum(len(str(getattr(m, "content", ""))) for m in messages)
    return {"input_tokens": max(seen // 4, 1), "output_tokens": max(len(text) // 4, 1), "total_tokens": max(seen // 4, 1) + max(len(text) // 4, 1)}


def _call(name: str, args: dict[str, Any], n: int, messages) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{n}"}], usage_metadata=_usage(messages, json.dumps(args)))


def _say(text: str, messages) -> AIMessage:
    return AIMessage(content=text, usage_metadata=_usage(messages, text))


def remembered_contact(messages) -> dict[str, str] | None:
    """El último contacto que apareció en un resultado de search_contacts del historial."""
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and (m.name or "").endswith("search_contacts"):
            cid, name = re.search(r'"id":\s*"(\d+)"', m.text), re.search(r'"name":\s*"([^"]+)"', m.text)
            if cid:
                return {"id": cid.group(1), "name": name.group(1) if name else cid.group(1)}
    return None


class ScriptedFakeModel(BaseChatModel):
    tool_names: list[str] = []   # las herramientas que recibió bind_tools (con prefijo si hay varios servidores)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tool_names": [getattr(t, "name", None) or t["name"] for t in tools]})

    def _tool(self, short: str) -> str:
        return next((n for n in self.tool_names if n == short or n.endswith("_" + short)), short)

    def _decide(self, messages) -> AIMessage:
        start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))   # el turno actual empieza en el último mensaje humano
        question, last, n = messages[start].text, messages[-1], len(messages)
        before = messages[:start]
        if isinstance(last, HumanMessage):
            q = question.lower()
            if "hora" in q:
                zone = re.search(r"en ([A-Z]\w+/\w+)", question)
                return _call(self._tool("get_current_time"), {"timezone": zone.group(1) if zone else "America/Guatemala"}, n, messages)
            if "hablábamos" in q or "hablabamos" in q:
                c = remembered_contact(before)
                return _say(f"Hablábamos de {c['name']} (contacto {c['id']})." if c else "Todavía no hemos hablado de nadie en esta sesión.", messages)
            who = re.search(r"(?:notas de|nota a)\s+([A-ZÁÉÍÓÚ]\w+)", question)
            if who:
                return _call(self._tool("search_contacts"), {"query": who.group(1)}, n, messages)
            c = remembered_contact(before)        # sin nombre: se usa la memoria de la sesión
            if c and "nota" in q and ("agrega" in q or "agregar" in q):
                body = re.search(r":\s*['\"“](.+?)['\"”]", question)
                if body:
                    return _call(self._tool("add_note"), {"contact_id": c["id"], "body": body.group(1)}, n, messages)
            if c and "nota" in q:
                return _call(self._tool("get_contact_notes"), {"contact_id": c["id"]}, n, messages)
            if "nota" in q:
                return _say("No sé de qué contacto hablas: dime su nombre (por ejemplo, 'Resume las notas de Ana').", messages)
            return _say("Puedo buscar contactos, leer sus notas y agregar una nota. Prueba: 'Resume las notas de Ana'.", messages)
        if not isinstance(last, ToolMessage):
            return _say("No sé qué hacer.", messages)
        text, tool = last.text, last.name or ""
        if tool.endswith("search_contacts"):
            cid = re.search(r'"id":\s*"(\d+)"', text)
            if not cid:
                return _say("No encontré a nadie con ese nombre.", messages)
            if "nota a" in question.lower():
                body = re.search(r":\s*['\"“](.+?)['\"”]", question).group(1)
                return _call(self._tool("add_note"), {"contact_id": cid.group(1), "body": body}, n, messages)
            return _call(self._tool("get_contact_notes"), {"contact_id": cid.group(1)}, n, messages)
        if tool.endswith("get_contact_notes"):
            bodies = re.findall(r'"body":\s*"([^"]+)"', text)
            return _say("Resumen de las notas: " + " | ".join(bodies) if bodies else "Ese contacto no tiene notas.", messages)
        if tool.endswith("add_note"):
            return _say("Listo, la nota quedó guardada." if "created" in text else f"No se guardó: {text[:120]}", messages)
        return _say(f"Resultado: {text[:200]}", messages)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._decide(messages))])
