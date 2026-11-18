"""Memoria de LARGO plazo: preferencias del usuario que sobreviven entre conversaciones y entre ejecuciones.

La memoria de corto plazo (checkpointer + thread_id) recuerda UNA conversación. La de largo plazo vive en un
*store*: un almacén de clave-valor organizado por `namespace` (aquí, el usuario).

- Con `DATABASE_URL`, el store vive en el MISMO Postgres que ya guarda los vectores, las sesiones y la bitácora
  (`PostgresStore`, tabla `store`): sobrevive a reinicios sin archivos aparte.
- Sin base de datos, `InMemoryStore` vive en RAM; para que sobreviva al cerrar el programa, `load_store`/`save_store`
  lo vuelcan a un archivo JSON (solo para pruebas y exploración).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from langgraph.store.memory import InMemoryStore

NAMESPACE = "preferences"


@dataclass
class Context:
    """Datos de ESTA ejecución que el código inyecta (el modelo no los ve ni los elige)."""

    user_id: str


def build_store(dsn: str | None = None):
    """Postgres si hay `dsn` (memoria de largo plazo persistente); si no, un store en RAM."""
    if not dsn:
        return InMemoryStore()
    from langgraph.store.postgres import PostgresStore
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(dsn, max_size=5, kwargs={"autocommit": True, "row_factory": dict_row}, open=True)
    store = PostgresStore(pool)
    store.setup()                                              # crea la tabla `store` si no existe
    return store


def load_store(path: Path | None) -> InMemoryStore:
    store = InMemoryStore()
    if path and path.exists():
        for user_id, prefs in json.loads(path.read_text(encoding="utf-8")).items():
            for key, value in prefs.items():
                store.put((NAMESPACE, user_id), key, {"value": value})
    return store


def save_store(store: InMemoryStore, path: Path | None) -> None:
    if not path:
        return
    data: dict[str, dict[str, str]] = {}
    for user_id in {ns[1] for ns in store.list_namespaces(prefix=(NAMESPACE,))}:
        data[user_id] = {i.key: i.value["value"] for i in store.search((NAMESPACE, user_id), limit=100)}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
