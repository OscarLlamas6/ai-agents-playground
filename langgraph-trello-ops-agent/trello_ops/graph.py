"""El GRAFO: se ensamblan nodos y aristas, y se compila con checkpointer (memoria de corto plazo) y store (largo plazo).

         START → load_context → agent ──(sin herramientas)──────────────→ END
                                  │  ├──(tope de pasos / sin progreso)→ halt → END
                                  │  ├──(solo lecturas)────────────────→ execute ─┐
                                  │  └──(hay escrituras)→ approve ─────→ execute ─┤
                                  └────────────────────────────────────────────────┘ (vuelve a agent)
"""

import os
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from trello_ops.mcp_tools import load_mcp_tools
from trello_ops.model import build_model
from trello_ops.nodes import OpsNodes
from trello_ops.retriever import Retriever
from trello_ops.state import Context, OpsState
from trello_ops.tools import build_tools
from trello_ops.trello_client import Trello, build_trello

RUNBOOK = Path(__file__).parents[1] / "data" / "runbook_operaciones.md"


def build_graph(model, trello: Trello, retriever: Retriever, *, checkpointer=None, store=None, max_steps: int = 8,
                extra_tools: dict | None = None, approval_tools: set[str] | None = None):
    """Devuelve el grafo compilado. `extra_tools`/`approval_tools` permiten sumar herramientas MCP (que exigen aprobación)."""
    tools = {**build_tools(trello, retriever), **(extra_tools or {})}
    nodes = OpsNodes(model, tools, trello, max_steps=max_steps)
    nodes.write_tools |= approval_tools or set()

    builder = StateGraph(OpsState, context_schema=Context)
    builder.add_node("load_context", nodes.load_context)
    builder.add_node("agent", nodes.agent)
    builder.add_node("approve", nodes.approve)
    builder.add_node("execute", nodes.execute)
    builder.add_node("halt", nodes.halt)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "agent")
    builder.add_conditional_edges("agent", nodes.route_after_agent,
                                  {"end": END, "halt": "halt", "approve": "approve", "execute": "execute"})
    builder.add_edge("approve", "execute")
    builder.add_edge("execute", "agent")           # el ciclo: tras ejecutar, el modelo ve los resultados
    builder.add_edge("halt", END)
    return builder.compile(checkpointer=checkpointer, store=store)


def build_default_graph(*, checkpointer=None, store=None, max_steps: int | None = None):
    """Arma todo desde las variables de entorno (modelo, Trello, MCP opcional)."""
    extra, needs_approval = load_mcp_tools()
    return build_graph(build_model(), build_trello(), Retriever(RUNBOOK), checkpointer=checkpointer, store=store,
                       max_steps=max_steps or int(os.getenv("MAX_STEPS", "8")), extra_tools=extra, approval_tools=needs_approval)
