"""Run catalog specs: clone -> ClamAV -> licence/provenance/quality -> admit. Resumable."""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import ingest
from .db import jdump, now
from .library import Library
from .sources import by_repo


def _job(lib: Library, key: str, source: str, state: str, payload: dict | None = None):
    lib.con.execute(
        "INSERT INTO ingest_jobs(key,source,state,attempts,payload,updated_at) VALUES (?,?,?,1,?,?) "
        "ON CONFLICT(key) DO UPDATE SET state=excluded.state, attempts=attempts+1, payload=excluded.payload, updated_at=excluded.updated_at",
        (key, source, state, jdump(payload or {}), now()))
    lib.con.commit()


def populate(lib: Library, only: set[str] | None = None, rerun: bool = False, progress=print, stop=lambda: False) -> dict:
    repos = by_repo()
    if only:
        repos = {u: [s for s in ss if s.id in only or u.split("github.com/")[1] in only] for u, ss in repos.items()}
        repos = {u: ss for u, ss in repos.items() if ss}
    done = {r["key"] for r in lib.con.execute("SELECT key FROM ingest_jobs WHERE state='done'")} if not rerun else set()
    todo = {u: [s for s in ss if f"{u}|{s.id}" not in done] for u, ss in repos.items()}
    todo = {u: ss for u, ss in todo.items() if ss}
    progress(f"{sum(len(v) for v in todo.values())} source specs over {len(todo)} repos")

    def clone_one(u):
        try:
            return u, ingest.clone(lib.cfg, u), None
        except Exception as e:  # noqa: BLE001
            return u, None, str(e)

    with ThreadPoolExecutor(6) as ex:
        cloned = list(ex.map(clone_one, todo))
    ctxs = {u: c for u, c, e in cloned if c}
    for u, c, e in cloned:
        if e:
            progress(f"! clone failed {u}: {e[:100]}")
            for s in todo[u]:
                _job(lib, f"{u}|{s.id}", u, "failed", {"error": e[:200]})
    # security gate: ClamAV over every fresh clone before any file is read for admission
    infected: dict = {}
    if ctxs:
        progress(f"ClamAV scanning {len(ctxs)} repos ...")
        infected = ingest.scan_repos([c.path for c in ctxs.values()], lib.cfg.home / "clamav-ingest.log")
    deps = ingest.DepLicenses(lib.cfg)
    total = {"admitted": 0, "rejected": 0}
    for u, ctx in ctxs.items():
        if stop():
            break
        if ctx.path in infected:
            progress(f"! ClamAV detection in {u}: {infected[ctx.path]} -> skipped, moved to quarantine log")
            continue
        for spec in todo[u]:
            t0 = time.time()
            try:
                r = ingest.run_spec(lib, spec, ctx, deps, progress, stop)
                lib.flush_index()
            except Exception as e:  # noqa: BLE001
                _job(lib, f"{u}|{spec.id}", u, "failed", {"error": f"{type(e).__name__}: {e}"[:200]})
                progress(f"! {spec.id} failed: {e}")
                continue
            _job(lib, f"{u}|{spec.id}", u, "interrupted" if r["interrupted"] else "done", r)
            for k in total: total[k] += r[k]
            progress(f"{spec.id:22} [{spec.category:9}] admitted {r['admitted']:4} rejected {r['rejected']:4} of {r['seen']:4} seen  ({time.time()-t0:.0f}s)")
    return total
