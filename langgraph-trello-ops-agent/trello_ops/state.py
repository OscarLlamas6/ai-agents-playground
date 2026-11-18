"""El ESTADO del grafo: lo que viaja entre nodos y lo que el checkpointer guarda tras cada paso.

Dos reglas que conviene recordar:
- Una clave SIN reducer se reemplaza con lo que devuelva el nodo.
- Una clave CON reducer (`add_messages`, `operator.add`) combina lo nuevo con lo anterior.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class OpsState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]   # reducer: agrega (o reemplaza por id) mensajes
    steps: int                                            # llamadas al modelo en ESTA pregunta (se reinicia en cada turno)
    recent_calls: list[str]                               # firmas de las últimas peticiones de herramienta (detecta falta de progreso)
    approval: dict | None                                 # decisión humana sobre las escrituras pendientes
    halted: bool                                          # True si el nodo `halt` detuvo la ejecución de este turno
    prefs: dict                                           # preferencias del usuario, cargadas del Store (largo plazo)
    audit: Annotated[list[dict], operator.add]            # reducer: bitácora acumulada de cada herramienta ejecutada


@dataclass
class Context:
    """Datos de UNA ejecución que el modelo no controla: quién es el usuario (clave de su memoria de largo plazo)."""

    user_id: str = "demo"
