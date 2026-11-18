"""Mini-RAG en memoria sobre el runbook (versión mínima de la de `langchain-agentic-rag`).

Parte el manual en párrafos con el título de su sección, los vectoriza y busca los más parecidos.
EMBEDDINGS_PROVIDER = fake (por defecto, sin red) | openai | local (LM Studio).
"""

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from trello_ops.model import local_url


@dataclass
class Chunk:
    id: str        # identificador citable, p. ej. "R3"
    section: str
    text: str


def embed(texts: list[str], kind: str) -> np.ndarray:
    provider = os.getenv("EMBEDDINGS_PROVIDER", "fake").lower()
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


class Retriever:
    def __init__(self, path: str | Path):
        self.chunks = self._split(Path(path).read_text(encoding="utf-8"))
        self.vectors = embed([f"{c.section}. {c.text}" for c in self.chunks], "document")

    @staticmethod
    def _split(markdown: str) -> list[Chunk]:
        chunks, section = [], ""
        for block in re.split(r"\n\s*\n", markdown):
            block = block.strip()
            if block.startswith("## "):
                section = block[3:].strip()
            elif block and not block.startswith("# ") and section:
                chunks.append(Chunk(f"R{len(chunks) + 1}", section, " ".join(block.split())))
        return chunks

    def search(self, query: str, k: int = 3) -> list[tuple[Chunk, float]]:
        scores = self.vectors @ embed([query], "query")[0]
        return [(self.chunks[i], float(scores[i])) for i in np.argsort(-scores)[:k]]
