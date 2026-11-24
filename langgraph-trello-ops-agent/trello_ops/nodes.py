"""Los NODOS: funciones que reciben el estado y devuelven una ACTUALIZACIÓN parcial del estado.

Las ARISTAS condicionales (`route_after_agent`) también son funciones: leen el estado y devuelven el nombre del
siguiente nodo. Todo el control de flujo del agente está en este archivo y en graph.py; el modelo solo decide
qué herramientas pedir dentro de cada paso.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from trello_ops.mcp_tools import invoke_tool
from trello_ops.prompts import SYSTEM_PROMPT, format_prefs
from trello_ops.state import Context, OpsState
from trello_ops.tools import MEMORY_TOOLS, PREFERENCE_KEYS, WRITE_TOOLS
from trello_ops.trello_client import Trello

MAX_RESULT_CHARS = 2000      # un resultado de herramienta no debe inundar el contexto
REPEAT_LIMIT = 3             # la misma petición, con los mismos argumentos, a la 3.ª vez se considera falta de progreso


def signature(call: dict) -> str:
    return f"{call['name']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"


@dataclass
class OpsNodes:
    """Agrupa las dependencias (modelo, herramientas, Trello) para que los nodos sean funciones simples y fáciles de probar."""

    model: object                       # un modelo de chat de LangChain (real o falso)
    tools: dict[str, BaseTool]
    trello: Trello
    max_steps: int = 8                  # presupuesto de llamadas al modelo por pregunta
    write_tools: set[str] = field(default_factory=lambda: set(WRITE_TOOLS))   # las herramientas MCP se agregan aquí

    def __post_init__(self):
        # OpenAI (Responses API): si no se envía `strict`, intenta el modo estricto, donde TODOS los parámetros opcionales pasan a
        # obligatorios y el modelo los rellena con basura (".", "x", la fecha de hoy). Con strict=False puede omitirlos.
        options = {"strict": False} if getattr(self.model, "_llm_type", "") == "openai-chat" else {}
        self.model_with_tools = self.model.bind_tools(list(self.tools.values()), **options)

    # ── Nodo 1: cargar contexto (memoria de largo plazo) y reiniciar los contadores del turno ──
    def load_context(self, state: OpsState, runtime: Runtime[Context]) -> dict:
        items = runtime.store.search(("prefs", runtime.context.user_id)) if runtime.store else []
        return {"prefs": {i.key: i.value["value"] for i in items}, "steps": 0, "recent_calls": [], "approval": None, "halted": False}

    # ── Nodo 2: el modelo decide (responder, o pedir herramientas) ──
    def agent(self, state: OpsState) -> dict:
        system = SystemMessage(SYSTEM_PROMPT.format(prefs=format_prefs(state["prefs"])))
        reply = self.model_with_tools.invoke([system, *state["messages"]])
        calls = [signature(c) for c in getattr(reply, "tool_calls", [])]
        return {"messages": [reply], "steps": state["steps"] + 1, "recent_calls": [*state["recent_calls"], *calls]}

    # ── Arista condicional: ¿qué sigue después del modelo? ──
    def route_after_agent(self, state: OpsState) -> str:
        last = state["messages"][-1]
        if not last.tool_calls:
            return "end"                                           # respondió con texto: terminó
        if state["steps"] >= self.max_steps or self._repeating(state):
            return "halt"                                          # condiciones de parada: presupuesto o falta de progreso
        if any(c["name"] in self.write_tools for c in last.tool_calls):
            return "approve"                                       # hay escrituras: primero, una persona
        return "execute"

    def _repeating(self, state: OpsState) -> bool:
        sigs = [signature(c) for c in state["messages"][-1].tool_calls]
        return any(state["recent_calls"].count(s) >= REPEAT_LIMIT for s in sigs)

    # ── Nodo 3: aprobación humana. SIN efectos secundarios: al reanudar, el nodo vuelve a correr desde su inicio ──
    def approve(self, state: OpsState) -> dict:
        writes = [c for c in state["messages"][-1].tool_calls if c["name"] in self.write_tools]
        decision = interrupt({                                     # pausa el grafo y entrega este payload a quien lo invocó
            "type": "approval_request",
            "actions": [{"id": c["id"], "tool": c["name"], "args": c["args"], "summary": self._describe(c)} for c in writes],
        })
        if isinstance(decision, dict):
            approved, reason = bool(decision.get("approved")), decision.get("reason", "")
        else:
            approved, reason = bool(decision), ""
        return {"approval": {"approved": approved, "reason": reason or ("aprobada" if approved else "rechazada")}}

    def _describe(self, call: dict) -> str:
        """Resumen legible para la persona que aprueba (resuelve ids a nombres con lecturas, que no tienen efectos)."""
        a = call["args"]
        try:
            if call["name"] == "create_card":
                return f"Crear la tarjeta «{a.get('name')}» en la lista «{self.trello.get_list(a['list_id'])['name']}»"
            if call["name"] == "move_card":
                card = self.trello.get_card(a["card_id"])
                return f"Mover «{card['name']}» de «{card['listName']}» a «{self.trello.get_list(a['list_id'])['name']}»"
            if call["name"] == "add_comment":
                return f"Comentar en «{self.trello.get_card(a['card_id'])['name']}»: {a.get('text')}"
        except Exception:                                          # si no se puede resolver, se muestran los argumentos crudos
            pass
        return f"{call['name']}({a})"

    # ── Nodo 4: ejecutar las herramientas pedidas. Las escrituras SOLO corren si hay una aprobación ──
    def execute(self, state: OpsState, runtime: Runtime[Context]) -> dict:
        approval, results, audit = state.get("approval"), [], []
        for call in state["messages"][-1].tool_calls:
            name, args = call["name"], call["args"]
            if name in self.write_tools and not (approval and approval["approved"]):
                reason = approval["reason"] if approval else "sin aprobación"
                content, status = f"RECHAZADA por la persona ({reason}). No se ejecutó nada.", "rejected"
            elif name in MEMORY_TOOLS:
                content, status = self._remember(runtime, args)
            elif name not in self.tools:
                content, status = f"ERROR: la herramienta {name} no existe.", "error"
            else:
                content, status = self._run(self.tools[name], args)
            results.append(ToolMessage(content[:MAX_RESULT_CHARS], tool_call_id=call["id"], name=name))
            audit.append({"at": datetime.now().isoformat(timespec="seconds"), "tool": name, "args": args, "status": status})
        return {"messages": results, "approval": None, "audit": audit}

    @staticmethod
    def _run(tool: BaseTool, args: dict) -> tuple[str, str]:
        try:
            return str(invoke_tool(tool, args)), "ok"
        except Exception as exc:                                   # el error llega al modelo como mensaje: puede corregir y reintentar
            return f"ERROR: {exc}", "error"

    @staticmethod
    def _remember(runtime: Runtime[Context], args: dict) -> tuple[str, str]:
        key, value = args.get("key", ""), str(args.get("value", ""))[:200]
        if key not in PREFERENCE_KEYS:
            return f"ERROR: solo se guardan las preferencias {sorted(PREFERENCE_KEYS)}.", "error"
        if runtime.store is None:
            return "ERROR: no hay memoria de largo plazo configurada.", "error"
        runtime.store.put(("prefs", runtime.context.user_id), key, {"value": value})
        return f"Preferencia guardada: {key} = {value}", "ok"

    # ── Nodo 5: parada controlada (tope de pasos o falta de progreso) ──
    def halt(self, state: OpsState) -> dict:
        reason = "alcancé el tope de pasos" if state["steps"] >= self.max_steps else "estoy repitiendo la misma acción y no avanzo"
        pending = state["messages"][-1].tool_calls
        skipped = [ToolMessage("No ejecutada: el agente se detuvo.", tool_call_id=c["id"], name=c["name"]) for c in pending]
        # Cada petición de herramienta necesita su respuesta: si no, el historial del hilo queda inválido para el siguiente turno.
        text = f"Me detuve porque {reason} sin completar la tarea. Revisa lo hecho hasta aquí o reformula la solicitud."
        return {"halted": True, "messages": [*skipped, AIMessage(text)], "audit": [{"at": datetime.now().isoformat(timespec="seconds"),
                                                                    "tool": "-", "args": {}, "status": "halted"}]}
