"""The approved library: the single admission gate, storage, versions, usage."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import licenses, security
from .config import Config, load
from .db import connect, jdump, now
from .embed import Embedder
from .vectors import ASSETS, NullVectors, Vectors

CATEGORIES = {"ui", "template", "backend", "cli", "animation", "algorithm"}
VISUAL = {"ui", "template", "animation"}          # human-provenance required
# Soft targets (usefulness over count): visuals weighted high, backends small but tested.
MINIMUMS = {"ui": 10000, "template": 300, "backend": 100, "cli": 150, "animation": 150, "algorithm": 150}


class Rejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason); self.reason = reason


@dataclass
class Candidate:
    name: str
    category: str
    kind: str
    description: str
    source_url: str                 # original source (page/repo path)
    repo: str
    commit_sha: str
    files: dict[str, bytes]
    license_text: str               # verbatim license text found at the source
    license_url: str
    declared_license: str | None    # what the manifest/registry claims (must agree)
    copyright: str = ""
    provenance: str = "human"       # human | generated | harvested
    frameworks: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)   # name -> license
    tags: list[str] = field(default_factory=list)
    difficulty: int = 2
    checks: dict[str, str] = field(default_factory=dict)         # "name": "pass: detail"


def content_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(k.encode()); h.update(b"\0"); h.update(files[k]); h.update(b"\0")
    return h.hexdigest()


def simhash(text: str) -> int:
    toks = re.findall(r"[A-Za-z_]\w+|\d+", text)
    if len(toks) < 30:
        return 0   # too small to near-dup compare; exact hash still applies
    v = [0] * 64
    for i in range(len(toks) - 2):
        h = int.from_bytes(hashlib.blake2b(" ".join(toks[i:i + 3]).encode(), digest_size=8).digest(), "big")
        for b in range(64):
            v[b] += 1 if (h >> b) & 1 else -1
    h = sum(1 << b for b in range(64) if v[b] > 0)
    return h - (1 << 64) if h >= (1 << 63) else h   # signed, fits SQLite INTEGER


def _hamming(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


class Library:
    def __init__(self, cfg: Config | None = None, lock_wait: float = 60.0):
        self.cfg = cfg or load()
        self.lock_wait = lock_wait  # embedded Qdrant is single-process: wait this long for another holder to release it
        self.con = connect(self.cfg.db_path)
        self.emb = Embedder(self.cfg, self.con)
        self._vec: Vectors | None = None
        self._sims: list[tuple[str, str, int]] | None = None

    @property
    def vec(self) -> Vectors:
        if self._vec is None and self.cfg.embed_mode == "off":
            self._vec = NullVectors()
        if self._vec is None:
            end = time.time() + self.lock_wait
            while True:
                try:
                    self._vec = Vectors(self.cfg, self.emb.dim)
                    break
                except RuntimeError:  # qdrant: "already accessed by another instance"
                    if time.time() > end:
                        raise
                    time.sleep(0.5)
            if self._vec.rebuilt:
                self._vec.rebuilt = False
                self._reembed_all()
        return self._vec

    def _reembed_all(self):
        from .memory import reembed_lessons
        self.reindex(force=True)
        reembed_lessons(self)
        from . import knowledge
        knowledge.reembed(self)

    flush_every = 50

    def flush_index(self) -> int:
        """Write queued assets to Qdrant, then release the store so searches can proceed."""
        ids = [r[0] for r in self.con.execute("SELECT asset_id FROM index_queue")]
        if not ids:
            return 0
        for i in range(0, len(ids), 128):
            self.index(*ids[i:i + 128])
            self.con.executemany("DELETE FROM index_queue WHERE asset_id=?", [(x,) for x in ids[i:i + 128]])
            self.con.commit()
        self.release_vec()
        return len(ids)

    def release_vec(self):
        if self._vec:
            self._vec.close()
            self._vec = None

    def close(self):
        if self._vec:
            self._vec.close()
        self.con.close()

    # ---------------------------------------------------------------- gate
    def vet(self, c: Candidate) -> tuple[str, str]:
        """All rules; returns (spdx, evidence_hash) or raises Rejected."""
        if c.category not in CATEGORIES:
            raise Rejected(f"unknown category {c.category}")
        if not c.source_url.startswith("https://") or not c.repo:
            raise Rejected("no identifiable original source")
        if not c.files:
            raise Rejected("empty asset")
        if c.category in VISUAL and c.provenance != "human":
            raise Rejected("AI-generated visual assets are not permitted in the permanent library")
        detected = licenses.detect(c.license_text)
        declared = licenses.normalize_spdx(c.declared_license) if c.declared_license else None
        if c.declared_license and declared is None:
            raise Rejected(licenses.reject_reason(c.declared_license))
        if detected is None:
            raise Rejected("license text missing or not verifiable")
        if declared and declared != detected:
            raise Rejected(f"conflicting license claims: declared {declared}, text is {detected}")
        v = licenses.check(detected, "open_source", dep_licenses=c.dependencies)
        if not v.ok:
            raise Rejected(v.reason)
        total = sum(len(b) for b in c.files.values())
        if total > 3_000_000:
            raise Rejected("asset too large (>3MB)")
        for rel in c.files:
            security.safe_join(Path("/x"), rel)
        for rel, b in c.files.items():
            if Path(rel).suffix.lower() in security.BINARY_EXT:
                raise Rejected(f"executable/binary file {rel}")
            if b"\0" not in b[:2048]:
                t = b.decode("utf-8", "ignore")
                s = security.find_secrets(t)
                if s:
                    raise Rejected(f"secret detected ({s[0][0]}) in {rel}:{s[0][1]}")
                if security.DANGEROUS_CODE.search(t):
                    raise Rejected(f"dangerous shell pattern in {rel}")
        failed = [k for k, v in c.checks.items() if v.startswith("fail")]
        if failed:
            raise Rejected(f"quality check failed: {failed[0]}")
        if not any(v.startswith("pass") for v in c.checks.values()):
            raise Rejected("no passing quality check recorded (untested code is not admitted)")
        return detected, licenses.evidence_hash(c.license_text)

    def _sim_index(self):
        if self._sims is None:
            self._sims = [(r["id"], r["category"], r["simhash"]) for r in
                          self.con.execute("SELECT id,category,simhash FROM assets WHERE simhash!=0")]
        return self._sims

    def admit(self, c: Candidate, embed: bool = True) -> str:
        spdx, ev = self.vet(c)
        ch = content_hash(c.files)
        if self.con.execute("SELECT 1 FROM assets WHERE content_hash=?", (ch,)).fetchone():
            raise Rejected("duplicate (identical content)")
        main = "\n".join(b.decode("utf-8", "ignore") for b in c.files.values() if b"\0" not in b[:1024])
        sh = simhash(main)
        if sh:
            for aid, cat, h in self._sim_index():
                if cat == c.category and _hamming(sh, h) <= 3:
                    raise Rejected(f"near-duplicate of {aid}")
        aid = "a_" + ch[:12]
        lic = licenses.POLICY[spdx]
        rel = f"{c.category}/{aid}"
        d = self.cfg.library / rel
        self._write_files(d, c.files)
        (d / "LICENSE.evidence").write_text(c.license_text)
        meta = {"id": aid, "name": c.name, "source_url": c.source_url, "repo": c.repo,
                "commit": c.commit_sha, "license": spdx, "copyright": c.copyright}
        (d / "ASSET.json").write_text(json.dumps(meta, indent=2))
        quality = {"checks": c.checks, "last_validated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "injection_flags": security.flag_injection(main)}
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.con.execute(
            """INSERT INTO assets (id,name,category,kind,description,source_url,repo,commit_sha,license_spdx,
               license_evidence_hash,license_evidence_url,copyright,attribution_required,copyleft,provenance,
               frameworks,languages,dependencies,tags,content_hash,simhash,rel_path,size_bytes,quality,difficulty,
               version,verified_at,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, c.name, c.category, c.kind, c.description, c.source_url, c.repo, c.commit_sha, spdx, ev,
             c.license_url, c.copyright, int(lic.attribution), lic.copyleft, c.provenance,
             jdump(c.frameworks), jdump(c.languages), jdump(c.dependencies), jdump(c.tags), ch, sh, rel,
             sum(len(b) for b in c.files.values()), jdump(quality), c.difficulty, 1, ts, now()))
        self.con.execute("INSERT INTO assets_fts(id,name,description,tags,kind) VALUES (?,?,?,?,?)",
                         (aid, c.name, c.description, " ".join(c.tags + c.frameworks + c.languages), c.kind))
        self.con.execute("INSERT INTO asset_versions VALUES (?,?,?,?,?,?)", (aid, 1, ch, rel, now(), "initial"))
        self.con.commit()
        if sh:
            self._sim_index().append((aid, c.category, sh))
        if embed:
            # Embed now (cached in SQLite, no vector-store lock needed) and queue the Qdrant write so that
            # long ingestion runs only hold the single-process vector store for short flushes.
            self.con.execute("INSERT OR IGNORE INTO index_queue VALUES (?)", (aid,))
            self.con.commit()
            self.emb.one(self.embed_text(self.get_row(aid)))
            if self.con.execute("SELECT COUNT(*) FROM index_queue").fetchone()[0] >= self.flush_every:
                self.flush_index()
        return aid

    @staticmethod
    def _write_files(d: Path, files: dict[str, bytes]):
        if d.exists():
            shutil.rmtree(d)
        for rel, b in files.items():
            p = security.safe_join(d, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b)

    # ------------------------------------------------------------- indexing
    def embed_text(self, r) -> str:
        return (f"{r['name']} ({r['category']}/{r['kind']}). {r['description']} "
                f"frameworks: {' '.join(json.loads(r['frameworks']))}; languages: {' '.join(json.loads(r['languages']))}; "
                f"tags: {' '.join(json.loads(r['tags']))}")

    def index(self, *ids: str):
        self.index_rows([self.con.execute("SELECT * FROM assets WHERE id=?", (i,)).fetchone() for i in ids])

    def index_rows(self, rows):
        rows = [r for r in rows if r]
        vecs = self.emb.embed([self.embed_text(r) for r in rows])
        self.vec.upsert(ASSETS, [(r["id"], v, {"category": r["category"], "license": r["license_spdx"], "kind": r["kind"]})
                                 for r, v in zip(rows, vecs)])

    def reindex(self, force: bool = False) -> int:
        """Incremental: only assets missing from Qdrant (or all with force)."""
        if self.cfg.embed_mode == "off":
            return 0
        if not force and self.vec.lacks_kind():             # one-off payload migration, no re-embedding
            self.vec.backfill_kind({r["id"]: r["kind"] for r in self.con.execute("SELECT id, kind FROM assets")})
        have = set() if force else {p.payload["id"] for p in self.vec.c.scroll(ASSETS, limit=100000, with_payload=True)[0]}
        todo = [r for r in self.con.execute("SELECT * FROM assets") if r["id"] not in have]
        for s in range(0, len(todo), 256):
            self.index_rows(todo[s:s + 256])
        return len(todo)

    # ---------------------------------------------------------------- reads
    def get_row(self, aid: str):
        return self.con.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()

    def read_files(self, aid: str, max_bytes: int = 60_000) -> dict[str, str]:
        r = self.get_row(aid)
        if not r:
            raise KeyError(aid)
        root = self.cfg.library / r["rel_path"]
        out, used = {}, 0
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.name not in ("LICENSE.evidence", "ASSET.json"):
                b = p.read_bytes()
                if b"\0" in b[:1024]:
                    out[str(p.relative_to(root))] = f"[binary, {len(b)} bytes]"; continue
                t = b.decode("utf-8", "ignore")
                used += len(t)
                out[str(p.relative_to(root))] = t if used <= max_bytes else "[omitted: size cap]"
        return out

    def record_use(self, aid: str, ok: bool, detail: str = ""):
        col = "uses_ok" if ok else "uses_fail"
        self.con.execute(f"UPDATE assets SET {col}={col}+1 WHERE id=?", (aid,))
        self.con.execute("INSERT INTO usage_events(ts,asset_id,event,detail) VALUES (?,?,?,?)",
                         (now(), aid, "success" if ok else "failure", detail[:500]))
        self.con.commit()

    def count_reject(self, reason: str):
        key = re.sub(r"[:(].*", "", reason)[:60].strip()
        self.con.execute("INSERT INTO reject_stats VALUES (?,1) ON CONFLICT(reason) DO UPDATE SET n=n+1", (key,))
        self.con.commit()

    def stats(self) -> dict:
        by = {r["category"]: r["n"] for r in self.con.execute("SELECT category,COUNT(*) n FROM assets GROUP BY category")}
        lic = {r["license_spdx"]: r["n"] for r in self.con.execute("SELECT license_spdx,COUNT(*) n FROM assets GROUP BY 1 ORDER BY 2 DESC")}
        total = sum(by.values())
        return {
            "total": total, "by_category": by,
            "minimums": {k: {"have": by.get(k, 0), "need": v, "met": by.get(k, 0) >= v} for k, v in MINIMUMS.items()},
            "launch_target_met": all(by.get(k, 0) >= v for k, v in MINIMUMS.items()),
            "licenses": lic,
            "lessons": self.con.execute("SELECT COUNT(*) FROM lessons").fetchone()[0],
            "vectors": self.vec.count(ASSETS) if total else 0,
            "rejected_counts": {r["reason"]: r["n"] for r in self.con.execute("SELECT * FROM reject_stats ORDER BY n DESC")},
        }
