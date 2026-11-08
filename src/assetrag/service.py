"""Background ingestion service: single worker (flock), graceful shutdown, resumable jobs, status report."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .config import Config, load
from .library import Library


class Busy(RuntimeError):
    pass


class Service:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load()
        self.lockf = self.cfg.home / "ingest.lock"
        self.pidf = self.cfg.home / "ingest.pid"
        self.log = self.cfg.home / "ingest.log"
        self._stop = False
        self._fh = None

    # ---- worker
    def run(self, only: set[str] | None = None, discover: list[tuple[str, int]] | None = None):
        self._fh = open(self.lockf, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy("another ingestion worker is already running")
        self._fh.write(str(os.getpid())); self._fh.flush()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: setattr(self, "_stop", True))
        from .populate import populate
        lib = Library(self.cfg)
        try:
            t0 = time.time()
            print(f"[{time.strftime('%F %T')}] ingest worker pid {os.getpid()} start", flush=True)
            r = populate(lib, only=only, progress=lambda m: print(m, flush=True), stop=lambda: self._stop)
            print("catalog:", r, flush=True)
            for kind, limit in (discover or []):
                if self._stop:
                    break
                from .discover import discover as disc
                have = lib.con.execute("SELECT COUNT(*) FROM ingest_jobs WHERE source=? AND state='done'", (f"discover:{kind}",)).fetchone()[0]
                print(f"discover {kind} (previously processed {have})", flush=True)
                print(disc(lib, kind, limit, progress=lambda m: print(m, flush=True), stop=lambda: self._stop), flush=True)
                lib.flush_index()
            lib.flush_index()
            print(f"[{time.strftime('%F %T')}] ingest worker {'stopped (resumable)' if self._stop else 'finished'} after {time.time()-t0:.0f}s", flush=True)
        finally:
            lib.flush_index() if lib.con else None
            lib.close()
            write_status(self.cfg)
            fcntl.flock(self._fh, fcntl.LOCK_UN)

    # ---- control
    def pid(self) -> int | None:
        try:
            pid = int(self.lockf.read_text().strip())
            os.kill(pid, 0)
            fh = open(self.lockf, "a")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return None          # lock free => stale pid
            except OSError:
                return pid
            finally:
                fh.close()
        except (ValueError, FileNotFoundError, ProcessLookupError, PermissionError):
            return None

    def start(self, args: list[str]) -> int:
        if self.pid():
            raise Busy(f"already running (pid {self.pid()})")
        with open(self.log, "ab") as lf:
            p = subprocess.Popen([sys.executable, "-u", "-m", "assetrag.cli", "ingest", "run", *args], stdout=lf, stderr=lf,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        return p.pid

    def stop(self, wait: int = 60) -> bool:
        pid = self.pid()
        if not pid:
            return False
        os.kill(pid, signal.SIGTERM)
        for _ in range(wait):
            time.sleep(1)
            if not self.pid():
                return True
        return False


def status(cfg: Config) -> dict:
    lib = Library(cfg)
    try:
        jobs = [dict(r) for r in lib.con.execute("SELECT key,state,attempts,payload,updated_at FROM ingest_jobs ORDER BY updated_at DESC")]
        by_state: dict[str, int] = {}
        for j in jobs:
            by_state[j["state"]] = by_state.get(j["state"], 0) + 1
        per_source = []
        for j in jobs:
            try: pl = json.loads(j["payload"])
            except ValueError: pl = {}
            per_source.append({"job": j["key"].split("|")[-1], "state": j["state"], **{k: pl.get(k) for k in ("seen", "admitted", "rejected", "skipped", "error") if k in pl}})
        return {"worker_pid": Service(cfg).pid(), "jobs_by_state": by_state, "queued_for_index": lib.con.execute("SELECT COUNT(*) FROM index_queue").fetchone()[0],
                "assets": lib.stats()["by_category"], "per_source": per_source,
                "reject_reasons": dict(list(lib.stats()["rejected_counts"].items())[:8])}
    finally:
        lib.close()


def write_status(cfg: Config, path: Path | None = None):
    s = status(cfg)
    path = path or Path(__file__).resolve().parents[2] / "INGESTION_STATUS.md"
    L = ["# Ingestion status", "", f"_Generated {time.strftime('%F %T')} by `assetrag ingest status`._", "",
         f"- Worker: {'running pid ' + str(s['worker_pid']) if s['worker_pid'] else 'not running'}",
         f"- Jobs by state: {s['jobs_by_state']}", f"- Waiting for vector flush: {s['queued_for_index']}",
         f"- Assets by category: {s['assets']}", "", "## Per-source", "", "| job | state | seen | admitted | rejected | skipped | error |", "|---|---|---|---|---|---|---|"]
    for j in s["per_source"]:
        L.append(f"| {j['job']} | {j['state']} | {j.get('seen','')} | {j.get('admitted','')} | {j.get('rejected','')} | {j.get('skipped','')} | {str(j.get('error',''))[:60]} |")
    L += ["", "## Top reject reasons", ""] + [f"- {k}: {v}" for k, v in s["reject_reasons"].items()]
    L += ["", "## Resume", "", "`assetrag ingest start` (detached, single worker, safe to repeat) or `assetrag ingest stop`. Completed jobs are not re-run; interrupted jobs resume and skip already-admitted content by hash."]
    path.write_text("\n".join(L) + "\n")
    return s
