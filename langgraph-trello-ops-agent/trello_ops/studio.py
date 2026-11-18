"""Punto de entrada para `langgraph dev` (ver langgraph.json): el servidor aporta su propio checkpointer y store."""

from trello_ops.graph import build_default_graph

graph = build_default_graph()
