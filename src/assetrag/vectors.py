"""Qdrant (embedded/local mode) wrapper. Full-dimension vectors, cosine."""
from __future__ import annotations

import uuid

from qdrant_client import QdrantClient, models

from .config import Config

ASSETS, LESSONS, KNOWLEDGE = "assets", "lessons", "knowledge"


def _pid(id_: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, id_))


class Vectors:
    def __init__(self, cfg: Config, dim: int):
        self.c = QdrantClient(path=str(cfg.qdrant_path))
        self.dim = dim
        self.rebuilt = False
        # Vectors are derived data (rebuildable from SQLite): a new embedding model,
        # dimension or prompt format invalidates them, so start the collections fresh.
        stamp = cfg.home / "embed_model.txt"
        want = f"{cfg.embed_model}|{dim}|{cfg.doc_prefix}"
        if stamp.exists() and stamp.read_text() != want:
            for name in (ASSETS, LESSONS, KNOWLEDGE):
                if self.c.collection_exists(name):
                    self.c.delete_collection(name)
            self.rebuilt = True
        for name in (ASSETS, LESSONS, KNOWLEDGE):   # unstamped legacy store with another dimension
            if self.c.collection_exists(name):
                size = self.c.get_collection(name).config.params.vectors.size
                if size != dim:
                    self.c.delete_collection(name)
                    self.rebuilt = True
        stamp.write_text(want)
        for name in (ASSETS, LESSONS, KNOWLEDGE):
            if not self.c.collection_exists(name):
                self.c.create_collection(name, vectors_config=models.VectorParams(
                    size=dim, distance=models.Distance.COSINE))

    def upsert(self, coll: str, items: list[tuple[str, list[float], dict]]):
        self.c.upsert(coll, points=[models.PointStruct(id=_pid(i), vector=v, payload={**p, "id": i})
                                    for i, v, p in items])

    def delete(self, coll: str, id_: str):
        self.c.delete(coll, points_selector=models.PointIdsList(points=[_pid(id_)]))

    def backfill_kind(self, kinds: dict[str, str]) -> int:
        """Add the `kind` payload to assets indexed before it existed (no re-embedding): {asset id: kind}."""
        by: dict[str, list[str]] = {}
        for i, k in kinds.items():
            by.setdefault(k, []).append(_pid(i))
        for k, pids in by.items():
            for s in range(0, len(pids), 1000):
                self.c.set_payload(ASSETS, payload={"kind": k}, points=pids[s:s + 1000])
        return len(kinds)

    def lacks_kind(self) -> bool:
        pts = self.c.scroll(ASSETS, limit=1, with_payload=True)[0]
        return bool(pts) and "kind" not in pts[0].payload

    def search(self, coll: str, vec: list[float], limit: int = 50, category: str | None = None, kind: str | None = None,
               not_kinds: list[str] | None = None):
        flt = None
        must = []
        if category:
            must.append(models.FieldCondition(key="category", match=models.MatchValue(value=category)))
        if kind:
            must.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        if must or not_kinds:
            flt = models.Filter(must=must or None, must_not=[models.FieldCondition(key="kind", match=models.MatchAny(any=not_kinds))] if not_kinds else None)
        res = self.c.query_points(coll, query=vec, limit=limit, query_filter=flt, with_payload=True)
        return [(p.payload["id"], p.score) for p in res.points]

    def count(self, coll: str) -> int:
        return self.c.count(coll).count

    def close(self):
        self.c.close()


class NullVectors:
    """Keyword-only mode (embed_mode=off): same interface as Vectors, stores nothing, finds nothing. Search then relies on SQLite FTS5."""
    rebuilt = False
    dim = 1

    def __init__(self, cfg: Config | None = None, dim: int = 1):
        pass

    def upsert(self, coll, items): pass
    def delete(self, coll, id_): pass
    def search(self, coll, vec, limit=50, category=None, kind=None, not_kinds=None): return []
    def count(self, coll): return 0
    def close(self): pass
    def backfill_kind(self, kinds): return 0
    def lacks_kind(self): return False
