"""Mini-RAG en memoria: carga el manual, lo parte en fragmentos, los vectoriza y busca los más parecidos.

Esto NO es el agente: es la pieza que el agente usará como herramienta (ver tools.py).
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from model import local_url


@dataclass
class Chunk:
    id: str        # identificador citable, p. ej. "D3"
    section: str   # título de la sección de donde sale
    text: str


# ───────────────────────── Embeddings ─────────────────────────


def embed(texts: list[str], kind: str) -> np.ndarray:
    """Vectoriza textos. kind = "document" | "query" (nomic exige un prefijo distinto para cada uno)."""
    provider = os.getenv("EMBEDDINGS_PROVIDER", "openai").lower()
    if provider == "fake":
        return _fake_embed(texts)
    from openai import OpenAI

    if provider == "local":
        client = OpenAI(base_url=local_url(), api_key="lm-studio")
        model = os.getenv("EMBEDDINGS_MODEL", "text-embedding-nomic-embed-text-v2-moe")
        texts = [f"search_{kind}: {t}" for t in texts]
    else:
        client, model = OpenAI(), os.getenv("EMBEDDINGS_MODEL", "text-embedding-3-small")
    vectors = np.array([d.embedding for d in client.embeddings.create(model=model, input=texts).data])
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def _fake_embed(texts: list[str], dims: int = 256) -> np.ndarray:
    """Bolsa de palabras con hash: determinista y sin red. Sirve para pruebas, no para calidad."""
    out = np.zeros((len(texts), dims))
    for i, text in enumerate(texts):
        for word in re.findall(r"\w+", text.lower()):
            out[i, int(hashlib.md5(word.encode()).hexdigest(), 16) % dims] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


# ───────────────────────── Almacén ─────────────────────────


class Retriever:
    def __init__(self, path: str | Path):
        self.chunks = self._split(Path(path).read_text(encoding="utf-8"))
        self.vectors = embed([f"{c.section}. {c.text}" for c in self.chunks], "document")
        self.retrieved: dict[str, Chunk] = {}   # todo lo que el agente ha visto: sirve para verificar sus citas

    @staticmethod
    def _split(markdown: str) -> list[Chunk]:
        """Un fragmento por párrafo, con el título de su sección (`## `) como metadato."""
        chunks, section = [], ""
        for block in re.split(r"\n\s*\n", markdown):
            block = block.strip()
            if block.startswith("## "):
                section = block[3:].strip()
            elif block and not block.startswith("# ") and section:
                chunks.append(Chunk(f"D{len(chunks) + 1}", section, " ".join(block.split())))
        return chunks

    @property
    def topics(self) -> list[str]:
        return list(dict.fromkeys(c.section for c in self.chunks))

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        scores = self.vectors @ embed([query], "query")[0]
        best = [(self.chunks[i], float(scores[i])) for i in np.argsort(-scores)[:k]]
        self.retrieved.update({c.id: c for c, _ in best})
        return best
