"""Un modelo de juguete para demostraciones y pruebas SIN llaves ni red (LLM_PROVIDER=fake).

No es inteligente: decide con expresiones regulares qué herramienta pedir y arma la respuesta final con lo que devolvieron
las herramientas. Sirve para ver el MECANISMO (API, sesiones, bitácora, herramientas) con resultados repetibles. Un modelo
real decide por sí mismo; lo que rodea al modelo es idéntico. Reporta un uso de tokens aproximado (caracteres ÷ 4).
"""

import json
import re
import uuid

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _text(m) -> str:
    return m.content if isinstance(m.content, str) else " ".join(str(b) for b in m.content)


class FakeAgentModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake-agent"

    def bind_tools(self, tools, **kwargs):
        return self

    # ── el "razonamiento" ──────────────────────────────────────────────
    def _decide(self, messages: list) -> AIMessage:
        start = max(i for i, m in enumerate(messages) if m.type == "human")
        question = _text(messages[start]).strip()
        run = messages[start + 1:]                                   # lo ocurrido en ESTA pregunta
        called = [c["name"] for m in run if m.type == "ai" for c in m.tool_calls]
        results = {m.name: _text(m) for m in run if m.type == "tool"}
        q = question.lower()

        def ask(name: str, args: dict) -> AIMessage:
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:8]}"}])

        def answer(text: str, citations=(), found=True) -> AIMessage:
            return ask("Answer", {"answer": text, "citations": list(citations), "found": found})

        # memoria de la conversación: el nombre que dijo antes en ESTA sesión
        if re.search(r"c[oó]mo me llamo", q):
            names = [m2.group(1) for m in messages[:start] if m.type == "human" and (m2 := re.search(r"me llamo (\w+)", _text(m), re.I))]
            return answer(f"Te llamas {names[-1]}." if names else "No me has dicho cómo te llamas en esta conversación.")
        if m2 := re.search(r"me llamo (\w+)", question, re.I):
            return answer(f"Mucho gusto, {m2.group(1)}.")

        # preferencias duraderas (memoria de largo plazo)
        if m2 := re.search(r"\buso (linux|windows|mac\w*)", q):
            if "remember_preference" not in called:
                return ask("remember_preference", {"key": "sistema_operativo", "value": m2.group(1).capitalize()})
            return answer(f"Anotado: usas {m2.group(1).capitalize()}.")
        if re.search(r"qu[eé] sistema uso|mis preferencias", q):
            if "recall_preferences" not in called:
                return ask("recall_preferences", {})
            return answer(f"Esto es lo que recuerdo de ti: {results['recall_preferences'].replace(chr(10), '; ')}")

        # tickets, plazos y equipos
        if m2 := re.search(r"\bT-\d+", question, re.I):
            if "get_ticket" not in called:
                return ask("get_ticket", {"ticket_id": m2.group(0)})
            if re.search(r"sla|venc", q) and "sla_deadline" not in called and results["get_ticket"].startswith("{"):
                t = json.loads(results["get_ticket"])
                return ask("sla_deadline", {"priority": t["priority"], "opened_at": t["opened_at"], "as_of": "2026-11-16T09:00:00"})
            if results["get_ticket"].startswith("{"):
                t = json.loads(results["get_ticket"])
                extra = f" {results['sla_deadline']}" if "sla_deadline" in results else ""
                return answer(f"El ticket {t['id']} ('{t['title']}') está {t['status']} y lo tiene {t['owner']}.{extra}")
            return answer(results["get_ticket"], found=False)
        if m2 := re.search(r"\b(?:LT|PR)-\d+", question, re.I):
            if "lookup_asset" not in called:
                return ask("lookup_asset", {"hostname": m2.group(0)})
            if results["lookup_asset"].startswith("{"):
                a = json.loads(results["lookup_asset"])
                state = "vigente" if a["warranty_active"] else "vencida"
                return answer(f"{a['hostname']} ({a['model']}, de {a['assigned_to']}): garantía {state}, vence el {a['warranty_ends']}.")
            return answer(results["lookup_asset"], found=False)

        # saludos y cálculos simples: sin herramientas
        if m2 := re.search(r"(\d+)\s*\+\s*(\d+)", q):
            return answer(f"{m2.group(1)} + {m2.group(2)} = {int(m2.group(1)) + int(m2.group(2))}.")
        if re.fullmatch(r"(hola|buenas|buenos d[ií]as)[\s!.,¿?a-záéíóú]*", q) and len(q) < 40:
            return answer("¡Hola! Soy el asistente de la mesa de ayuda de Nimbus Soft. ¿En qué te ayudo?")

        # lo que el manual no cubre → buscar, registrar el hueco y decirlo
        if re.search(r"3d|televisi[oó]n|criptomonedas", q):
            if "search_manual" not in called:
                return ask("search_manual", {"query": question})
            if "report_gap" not in called:
                return ask("report_gap", {"question": question, "note": "El manual no cubre este tema."})
            return answer("No lo encuentro en el manual de la mesa de ayuda.", found=False)

        # por defecto: RAG, una búsqueda y respuesta con el mejor fragmento
        if "search_manual" not in called:
            return ask("search_manual", {"query": question})
        hits = re.findall(r"\[(D\d+)\] \(([^,]+), puntaje [\d.]+\) (.+)", results.get("search_manual", ""))
        if not hits:
            return answer("No lo encuentro en el manual de la mesa de ayuda.", found=False)
        words = {w for w in re.findall(r"\w+", q) if len(w) > 3}
        best = max(hits, key=lambda h: len(words & set(re.findall(r"\w+", h[2].lower()))))     # el fragmento que comparte más palabras con la pregunta
        return answer(f"Según el manual ({best[1]}): {best[2][:260]}", [best[0]])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._decide(messages)
        sent = sum(_tokens(_text(m)) for m in messages)
        out = _tokens(_text(msg) + json.dumps(msg.tool_calls, ensure_ascii=False))
        msg.usage_metadata = {"input_tokens": sent, "output_tokens": out, "total_tokens": sent + out}
        return ChatResult(generations=[ChatGeneration(message=msg)])
