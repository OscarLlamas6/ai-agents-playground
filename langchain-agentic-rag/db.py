"""La base de datos del agente: Postgres con pgvector (o una versión en memoria para pruebas).

Una sola base guarda TRES cosas, con tablas distintas:
  - `manual_chunks`: los fragmentos del manual y sus vectores (lo que busca el RAG).
  - `query_log`:     la bitácora de preguntas (la escribe el CÓDIGO, siempre; ver audit.py).
  - `manual_gaps`:   preguntas que el manual no pudo responder (la escribe una HERRAMIENTA, cuando el modelo decide).

`PgDatabase` es la real. `MemoryDatabase` tiene la misma interfaz y sirve para pruebas sin Docker
(se activa con VECTOR_STORE=memory).
"""

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np


@dataclass
class Hit:
    id: str
    section: str
    text: str
    score: float


class MemoryDatabase:
    """Misma interfaz que PgDatabase, todo en RAM. Pruebas y exploración sin infraestructura."""

    def __init__(self):
        self.chunks: list = []
        self.vectors = np.zeros((0, 1))
        self.fingerprint = ""
        self.queries: list[dict] = []
        self.gaps: list[dict] = []

    def has_index(self, fingerprint: str) -> bool:
        return self.fingerprint == fingerprint and bool(self.chunks)

    def index_chunks(self, chunks, vectors: np.ndarray, fingerprint: str) -> None:
        self.chunks, self.vectors, self.fingerprint = list(chunks), vectors, fingerprint

    def search(self, vector: np.ndarray, k: int) -> list[Hit]:
        scores = self.vectors @ vector
        return [Hit(self.chunks[i].id, self.chunks[i].section, self.chunks[i].text, float(scores[i])) for i in np.argsort(-scores)[:k]]

    def log_query(self, **row) -> None:
        self.queries.append({**row, "created_at": datetime.now(timezone.utc)})

    def add_gap(self, **row) -> None:
        self.gaps.append({**row, "created_at": datetime.now(timezone.utc)})

    def recent_queries(self, n: int = 10) -> list[dict]:
        return self.queries[-n:][::-1]

    def all_gaps(self) -> list[dict]:
        return list(self.gaps)

    def close(self) -> None:
        pass


class PgDatabase:
    """Postgres + pgvector. Crea las tablas al conectarse; el índice vectorial se crea al indexar."""

    def __init__(self, dsn: str):
        import psycopg

        self.conn = psycopg.connect(dsn, autocommit=True)
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")      # pgvector
        from pgvector.psycopg import register_vector

        register_vector(self.conn)                                     # permite pasar arreglos de numpy como `vector`
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS query_log (
                id            BIGSERIAL PRIMARY KEY,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                user_id       TEXT,
                thread_id     TEXT,
                question      TEXT NOT NULL,
                input_tokens  INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                model_calls   INTEGER NOT NULL,
                tools_used    TEXT[] NOT NULL DEFAULT '{}',
                found         BOOLEAN,
                stopped_by    TEXT
            )""")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_gaps (
                id         BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                user_id    TEXT,
                question   TEXT NOT NULL,
                note       TEXT
            )""")
        self.conn.execute("CREATE TABLE IF NOT EXISTS index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    # ── vectores ──
    def has_index(self, fingerprint: str) -> bool:
        row = self.conn.execute("SELECT value FROM index_meta WHERE key = 'fingerprint'").fetchone()
        exists = self.conn.execute("SELECT to_regclass('manual_chunks') IS NOT NULL").fetchone()[0]
        return bool(row and row[0] == fingerprint and exists)

    def index_chunks(self, chunks, vectors: np.ndarray, fingerprint: str) -> None:
        dims = int(vectors.shape[1])
        self.conn.execute("DROP TABLE IF EXISTS manual_chunks")           # el manual es pequeño: se reconstruye completo
        self.conn.execute(f"CREATE TABLE manual_chunks (id TEXT PRIMARY KEY, section TEXT, text TEXT, embedding vector({dims}))")
        with self.conn.cursor() as cur:
            cur.executemany("INSERT INTO manual_chunks (id, section, text, embedding) VALUES (%s, %s, %s, %s)",
                            [(c.id, c.section, c.text, v) for c, v in zip(chunks, vectors)])
        self.conn.execute("""INSERT INTO index_meta (key, value) VALUES ('fingerprint', %s)
                             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", (fingerprint,))

    def search(self, vector: np.ndarray, k: int) -> list[Hit]:
        # `<=>` es la distancia coseno de pgvector; 1 - distancia = similitud (1 = idéntico)
        rows = self.conn.execute(
            "SELECT id, section, text, 1 - (embedding <=> %s) AS score FROM manual_chunks ORDER BY embedding <=> %s LIMIT %s",
            (vector, vector, k)).fetchall()
        return [Hit(i, s, t, float(sc)) for i, s, t, sc in rows]

    # ── bitácora y huecos ──
    def log_query(self, **r) -> None:
        self.conn.execute(
            """INSERT INTO query_log (user_id, thread_id, question, input_tokens, output_tokens, model_calls, tools_used, found, stopped_by)
               VALUES (%(user_id)s, %(thread_id)s, %(question)s, %(input_tokens)s, %(output_tokens)s, %(model_calls)s, %(tools_used)s, %(found)s, %(stopped_by)s)""", r)

    def add_gap(self, **r) -> None:
        self.conn.execute("INSERT INTO manual_gaps (user_id, question, note) VALUES (%(user_id)s, %(question)s, %(note)s)", r)

    def recent_queries(self, n: int = 10) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM query_log ORDER BY id DESC LIMIT %s", (n,))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def all_gaps(self) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM manual_gaps ORDER BY id")
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


def build_database():
    """DATABASE_URL → Postgres con pgvector. VECTOR_STORE=memory → RAM (pruebas). Sin ninguna de las dos, un error con la receta."""
    url = os.getenv("DATABASE_URL")
    if url:
        return PgDatabase(url)
    if os.getenv("VECTOR_STORE", "").lower() == "memory":
        return MemoryDatabase()
    raise SystemExit("Falta DATABASE_URL. Levanta Postgres con `docker compose up -d db` y copia en tu .env la línea "
                     "DATABASE_URL de .env.example (o usa VECTOR_STORE=memory para probar sin base de datos).")
