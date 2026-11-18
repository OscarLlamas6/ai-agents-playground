"""Memoria persistente: checkpointer (corto plazo, por hilo) y Store (largo plazo, por usuario).

Sin ruta, ambos viven en RAM y se pierden al cerrar. Con una ruta, comparten un archivo SQLite y sobreviven al reinicio.
(SQLite es para desarrollo y demostraciones; en producción se usa Postgres.)
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import SqliteStore


@contextmanager
def open_persistence(db_path: str | None = None):
    """Entrega (checkpointer, store); cierra las conexiones al salir."""
    if not db_path:
        yield InMemorySaver(), InMemoryStore()
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)     # p. ej. .data/
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        with SqliteStore.from_conn_string(db_path) as store:
            store.setup()
            yield SqliteSaver(conn), store
    finally:
        conn.close()
