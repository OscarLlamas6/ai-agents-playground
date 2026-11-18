"""Las herramientas del agente. La búsqueda es UNA herramienta más: el modelo decide si la usa."""

from langchain.tools import ToolRuntime, tool

import json
from datetime import datetime, date

from memory import NAMESPACE, Context
from ops_data import SLA_HOURS, add_business_hours, add_months, find_asset, find_ticket
from retriever import Retriever


def build_tools(retriever: Retriever, db) -> list:
    @tool
    def search_manual(query: str) -> str:
        """Busca en el manual de la mesa de ayuda de TI (contraseñas, VPN, correo, impresoras, equipos, seguridad).

        Devuelve los 3 fragmentos más parecidos, cada uno con su id entre corchetes y un puntaje de 0 a 1.
        Escribe una consulta corta y específica. Si los resultados no responden, reformula y vuelve a llamar.
        """
        hits = retriever.search(query, k=3)
        return "\n".join(f"[{c.id}] ({c.section}, puntaje {s:.2f}) {c.text}" for c, s in hits)

    @tool
    def list_topics() -> str:
        """Lista los temas que cubre el manual. Úsala para saber si una pregunta cae dentro del alcance."""
        return ", ".join(retriever.topics)

    @tool
    def remember_preference(key: str, value: str, runtime: ToolRuntime[Context]) -> str:
        """Guarda una preferencia DURADERA del usuario (por ejemplo: idioma, sistema operativo, equipo, nivel técnico).

        Úsala solo cuando el usuario diga algo que conviene recordar en futuras conversaciones.
        `key` es un nombre corto (p. ej. "sistema_operativo") y `value` su valor.
        """
        runtime.store.put((NAMESPACE, runtime.context.user_id), key, {"value": value})
        return f"Guardado: {key} = {value}"

    @tool
    def recall_preferences(runtime: ToolRuntime[Context]) -> str:
        """Devuelve las preferencias guardadas del usuario. Úsala al empezar si la respuesta depende de su equipo o contexto."""
        items = runtime.store.search((NAMESPACE, runtime.context.user_id), limit=50)
        return "\n".join(f"{i.key} = {i.value['value']}" for i in items) or "Sin preferencias guardadas."

    @tool
    def get_ticket(ticket_id: str) -> str:
        """Consulta un ticket por su id (por ejemplo "T-101"): título, prioridad, estado, responsable y fechas.

        Es una consulta de solo lectura sobre los tickets de la mesa de ayuda. Devuelve JSON, o un mensaje si no existe.
        """
        ticket = find_ticket(ticket_id)
        return json.dumps(ticket, ensure_ascii=False) if ticket else f"No existe el ticket {ticket_id}."

    @tool
    def sla_deadline(priority: str, opened_at: str, as_of: str = "") -> str:
        """Calcula la fecha límite de primera respuesta (SLA) de un ticket y si ya venció.

        `priority`: crítica, alta, media o baja. `opened_at` y `as_of`: fechas ISO, p. ej. "2026-11-09T09:30:00".
        Suma horas HÁBILES (lunes a viernes, 8:00 a 17:00), algo que no debes calcular de cabeza.
        `as_of` es el momento contra el que se compara; si lo omites, es ahora. Si el ticket ya tuvo respuesta, compara
        con la fecha de esa respuesta en vez de con ahora.
        """
        hours = SLA_HOURS.get(priority.strip().lower())
        if hours is None:
            return f"Prioridad desconocida: {priority}. Usa: {', '.join(SLA_HOURS)}."
        deadline = add_business_hours(datetime.fromisoformat(opened_at), hours)
        now = datetime.fromisoformat(as_of) if as_of else datetime.now()
        state = "VENCIDO" if now > deadline else "dentro del plazo"
        return f"Plazo de {hours} horas hábiles. Límite: {deadline:%Y-%m-%d %H:%M}. Al {now:%Y-%m-%d %H:%M}: {state}."

    @tool
    def lookup_asset(hostname: str) -> str:
        """Busca un equipo del inventario por su hostname (por ejemplo "LT-0017"): modelo, responsable, ubicación y garantía.

        Devuelve JSON con la fecha en que vence su garantía y si sigue vigente hoy. Solo lectura.
        """
        asset = find_asset(hostname)
        if not asset:
            return f"No existe el equipo {hostname}."
        ends = add_months(date.fromisoformat(asset["purchase_date"]), asset["warranty_months"])
        return json.dumps({**asset, "warranty_ends": ends.isoformat(), "warranty_active": ends >= date.today()}, ensure_ascii=False)

    @tool
    def report_gap(question: str, note: str, runtime: ToolRuntime[Context]) -> str:
        """ESCRIBE en la base de datos un hueco del manual: una pregunta que el manual no pudo responder.

        Úsala SOLO cuando, después de buscar, el manual no contiene la respuesta (found = false). `question` es la pregunta del
        usuario y `note` explica en una frase qué faltaba. Es una escritura: no la llames para preguntas que sí respondiste.
        """
        db.add_gap(user_id=runtime.context.user_id, question=question, note=note)
        return "Hueco registrado para que el equipo del manual lo revise."

    return [search_manual, list_topics, get_ticket, sla_deadline, lookup_asset, report_gap, remember_preference, recall_preferences]
