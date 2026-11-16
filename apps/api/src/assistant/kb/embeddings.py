"""Local, free embeddings.

* FastEmbedder: ONNX models on CPU via fastembed (no GPU, no API key, no Ollama needed).
* HashEmbedder: deterministic bag-of-words hashing; for tests and fully offline runs.
"""

import asyncio
import hashlib
import math
import re
from typing import Protocol

from assistant.models import EMBEDDING_DIM  # single source of truth (384, bge-small)
from assistant.settings import Settings, get_settings


class Embedder(Protocol):
    name: str
    dim: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    def __init__(self, model: str, cache_dir: str):
        self.name = f"fastembed:{model}"
        self.dim = EMBEDDING_DIM
        self._model_name = model
        self._cache_dir = cache_dir
        self._model = None
        self._lock = asyncio.Lock()

    async def _get(self):
        async with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # heavy import; load lazily

                self._model = await asyncio.to_thread(
                    TextEmbedding, model_name=self._model_name, cache_dir=self._cache_dir
                )
        return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = await self._get()
        vecs = await asyncio.to_thread(lambda: list(model.embed(texts)))
        return [v.tolist() for v in vecs]

    async def embed_query(self, text: str) -> list[float]:
        model = await self._get()
        vecs = await asyncio.to_thread(lambda: list(model.query_embed(text)))
        return vecs[0].tolist()


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "for",
    "on",
    "is",
    "are",
    "do",
    "you",
    "we",
    "our",
    "your",
    "with",
    "what",
    "how",
    "can",
    "i",
    "it",
    "be",
    "does",
    "my",
}


class HashEmbedder:
    name = "hash"
    dim = EMBEDDING_DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in _WORD.findall(text.lower()):
            if w in _STOP:
                continue
            stem = w[:6]  # crude stemming: "migrating" ~ "migration"
            h = int.from_bytes(hashlib.blake2b(stem.encode(), digest_size=4).digest(), "big")
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def build_embedder(s: Settings | None = None) -> Embedder:
    s = s or get_settings()
    if s.embedder == "hash":
        return HashEmbedder()
    if s.embedder == "fastembed":
        return FastEmbedder(s.embed_model, s.embed_cache_dir)
    raise ValueError(f"unknown embedder '{s.embedder}'")
