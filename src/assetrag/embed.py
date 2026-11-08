"""Embeddings via any OpenAI-compatible /v1/embeddings endpoint (local: Ollama, LM Studio, llama.cpp/Unsloth; hosted: OpenRouter, OpenAI, Gemini, ...)."""
from __future__ import annotations

import array
import hashlib
import math
import sqlite3

import httpx

from .config import Config


class EmbedError(RuntimeError):
    pass


class Embedder:
    def __init__(self, cfg: Config, con: sqlite3.Connection):
        self.cfg, self.con = cfg, con
        h = {"Content-Type": "application/json"}
        if cfg.embed_key:
            h["Authorization"] = f"Bearer {cfg.embed_key}"
        self.http = httpx.Client(base_url=cfg.embed_url, headers=h, timeout=120)
        self._dim: int | None = None

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.cfg.embed_model}\0{text}".encode()).hexdigest()

    @staticmethod
    def _norm(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def _remote(self, texts: list[str]) -> list[list[float]]:
        try:
            r = self.http.post("/embeddings", json={"model": self.cfg.embed_model, "input": texts})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbedError(
                f"embedding server at {self.cfg.embed_url} failed: {e}. "
                "Start the server, or run `assetrag setup --reconfigure` to pick another provider or key, "
                "or run keyword-only with ASSETRAG_EMBED_MODE=off."
            ) from e
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [self._norm(d["embedding"]) for d in data]

    def embed(self, texts: list[str], kind: str = "doc") -> list[list[float]]:
        if self.cfg.embed_mode == "off":
            return [[0.0] for _ in texts]            # keyword-only mode: placeholders that NullVectors ignores
        pre = self.cfg.query_prefix if kind == "query" else self.cfg.doc_prefix
        texts = [pre + t for t in texts]
        out: list[list[float] | None] = [None] * len(texts)
        todo: list[int] = []
        for i, t in enumerate(texts):
            row = self.con.execute("SELECT vec FROM embed_cache WHERE key=?", (self._key(t),)).fetchone()
            if row:
                a = array.array("f"); a.frombytes(row["vec"]); out[i] = list(a)
            else:
                todo.append(i)
        for s in range(0, len(todo), self.cfg.embed_batch):
            idx = todo[s:s + self.cfg.embed_batch]
            vecs = self._remote([texts[i][:6000] for i in idx])
            for i, v in zip(idx, vecs):
                out[i] = v
                self.con.execute(
                    "INSERT OR REPLACE INTO embed_cache VALUES (?,?,?,?)",
                    (self._key(texts[i]), self.cfg.embed_model, len(v), array.array("f", v).tobytes()))
            self.con.commit()
        return out  # type: ignore[return-value]

    def one(self, text: str, kind: str = "doc") -> list[float]:
        return self.embed([text], kind)[0]

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = 1 if self.cfg.embed_mode == "off" else len(self.one("dimension probe"))
        return self._dim
