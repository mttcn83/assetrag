"""Seed the knowledge store. Bug fixes and recipes are *executed* in the sandbox first; anything whose
claim does not reproduce is rejected, never stored as verified. Idempotent: re-running updates nothing."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .. import kverify, knowledge, sandbox
from ..config import Config, load
from ..library import Library
from .bugfixes import SEEDS as _SEEDS
from .bugfixes_more import MORE
from .playbooks import PLAYBOOKS
from .recipes import all_recipes
from .rules import RULES

SEEDS = _SEEDS + MORE
# config-file "languages" are not programming languages: leaving them on an entry would hide it in every real project
AGNOSTIC = {"systemd", "dockerfile", "yaml", "pkgbuild", "headers", "sql", "css", "json", "hcl"}      # bash is a real language: its recipes must not surface in a Python/Go project
SELF_URL = "https://github.com/mttcn83/assetrag"  # authored here; no third-party text is copied


def rust_sysroot() -> str | None:
    try:
        out = subprocess.run(["rustc", "--print", "sysroot"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def verify_recipe(cfg: Config, r: dict) -> dict:
    d = Path(tempfile.mkdtemp(dir=cfg.work))
    try:
        for k, v in r["files"].items():
            (d / k).parent.mkdir(parents=True, exist_ok=True)
            (d / k).write_text(v)
        env = dict(kverify.ENV)
        if r["lang"] == "go":
            (d / "go.mod").write_text("module m\n\ngo 1.21\n")
            env.pop("GO111MODULE", None)
        binds = None
        if r["lang"] == "rust":                                  # toolchain mounted read-only; build output goes to the sandbox tmpfs
            root = rust_sysroot()
            if not root:
                raise kverify.VerifyFailed("rustc not found on the host")
            binds, env["PATH"] = [(root, "/rust")], "/rust/bin:/usr/bin"
        rc, out = sandbox.run(r["cmd"], d, 240, env=env, binds=binds)
        if rc != 0:
            raise kverify.VerifyFailed(f"recipe tests failed: {out[-300:]}")
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:] or [""]
        return {"method": "tests executed in bubblewrap sandbox (no network)", "command": " ".join(r["cmd"]), "exit": rc,
                "result": re.sub(r"\s*(?:finished )?in [\d.]+s\b", "", tail[0])[:160]}      # timings would make every re-seed look like an edit
    finally:
        shutil.rmtree(d, ignore_errors=True)


_SANDBOX: tuple[bool, str] | None = None


def _sandbox_state() -> tuple[bool, str]:
    """bwrap on PATH is not enough: containers and hardened kernels refuse user namespaces. Probe once."""
    global _SANDBOX
    if _SANDBOX is None:
        from .. import maint
        _SANDBOX = maint.sandbox_works()
    return _SANDBOX


def _skip_reason(cmd: list[str], mode: str, lang: str = "") -> str:
    """Why an entry cannot be executed here ('' = run it). A failing test is a rejection; a missing sandbox or toolchain is not."""
    if mode == "off":
        return "verification skipped (seed --no-verify)"
    ok, why = _sandbox_state()
    if not ok:
        return f"sandbox unusable ({why})"
    if lang == "rust" and not rust_sysroot():              # rust recipes run as `sh -c rustc ...`: look for the real tool
        return "rustc not installed"
    if cmd and shutil.which(cmd[0]) is None:
        return f"{cmd[0]} not installed"
    return ""


def _unverified(entry: dict, why: str) -> dict:
    """Same entry, honestly labelled: authored and shipped with executed tests, but not executed on this machine."""
    entry["verification"], entry["confidence"] = "unverified", "medium"
    entry["data"]["verification_evidence"] = {"method": f"not executed on this machine: {why}", "note": "run `assetrag seed` after installing it to verify"}
    return entry


def _jobs() -> int:
    import os
    try:
        return max(1, int(os.environ.get("ASSETRAG_SEED_JOBS", "")))
    except ValueError:
        pass
    try:                                    # Go/Rust builds are memory hungry (20 workers got OOM-killed): budget ~1.5 GB each
        avail_gb = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemAvailable")) / 1048576
    except (OSError, StopIteration, ValueError):
        avail_gb = 8
    return max(1, min(8, os.cpu_count() or 2, int(avail_gb / 1.5)))


def build(cfg: Config, log=print, verify: str = "auto", progress=None, jobs: int | None = None) -> tuple[list[dict], list[tuple[str, str]]]:
    """Verify everything first (no vector-store lock held), return entries ready to add.
    verify='auto' executes whatever this machine can and loads the rest as `unverified`; 'off' executes nothing.
    Tests run in parallel (jobs workers); a failure is re-run once on its own, so load-induced flakes are not reported as rejections.
    progress(done, total) is called as each test finishes."""
    from concurrent.futures import ThreadPoolExecutor
    work = []                                                    # (index in output order, kind, seed, skip reason)
    for s in SEEDS:
        work.append(("bugfix", s, _skip_reason(s["repro"]["cmd"], verify, s["lang"])))
    for r in all_recipes():
        work.append(("recipe", r, _skip_reason(r["cmd"], verify, r["lang"])))
    todo = [i for i, (_, _, skip) in enumerate(work) if not skip]
    total, done = len(todo), 0
    results: dict[int, dict | Exception] = {}

    def run(i):
        kind, item, _ = work[i]
        return kverify.verify(cfg, item) if kind == "bugfix" else verify_recipe(cfg, item)

    def note():
        nonlocal done
        done += 1
        if progress:
            progress(done, total)

    if progress:
        progress(0, total)
    with ThreadPoolExecutor(max_workers=jobs or _jobs()) as ex:
        futs = {i: ex.submit(run, i) for i in todo}
        for i, f in futs.items():
            try:
                results[i] = f.result()
            except Exception as e:  # noqa: BLE001
                results[i] = e
            note()
    for i, v in list(results.items()):                           # retry failures alone: a timing-sensitive test can fail only because 8 others were running
        if isinstance(v, Exception):
            try:
                results[i] = run(i)
            except Exception as e:  # noqa: BLE001
                results[i] = e

    out_bug, out_rec, rules, plays, bad = [], [], [], [], []
    for i, (kind, item, skip) in enumerate(work):
        ev = {}
        if not skip:
            v = results[i]
            if isinstance(v, Exception):
                bad.append((item["key"], str(v)[:200])); log(f"  x {kind} {item['key']}: {v}"); continue
            ev = v
        if kind == "bugfix":
            s = item
            ent = {"kind": "bugfix", "title": s["title"], "summary": s["root_cause"],
                   "data": {"error": s["error"], "root_cause": s["root_cause"], "fix": s["fix"], "pitfalls": s.get("pitfalls", []),
                            "repro": s["repro"], "fixed_example": s["fixed"], "verification_evidence": ev, "key": s["key"]},
                   "languages": [s["lang"]], "frameworks": s.get("frameworks", []), "tags": s.get("tags", []),
                   "version_spec": s.get("version_spec", ""), "provenance": "authored", "verification": "verified",
                   "confidence": "high", "source_name": "AssetRAG seed (executed)", "source_url": SELF_URL}
            out_bug.append(_unverified(ent, skip) if skip else ent)
        else:
            r = item
            ent = {"kind": "recipe", "title": r["title"], "summary": r["problem"],
                   "data": {"problem": r["problem"], "approach": r["approach"], "files": r["files"], "dependencies": [],
                            "license": "MIT (authored for AssetRAG; stdlib-only)", "assumptions": r["assumptions"],
                            "compatibility": r["compat"], "avoid": r["avoid"], "test_command": " ".join(r["cmd"]),
                            "verification_evidence": ev, "key": r["key"]},
                   "languages": [] if r["lang"] in AGNOSTIC else [r["lang"]], "tags": r["tags"] + ([r["lang"]] if r["lang"] in AGNOSTIC else []),
                   "provenance": "authored", "verification": "verified",
                   "confidence": "high", "source_name": "AssetRAG seed (executed)", "source_url": SELF_URL}
            out_rec.append(_unverified(ent, skip) if skip else ent)
    for r in RULES:
        rules.append({"kind": "rule", "title": r["statement"][:120], "summary": r["rationale"],
                      "data": {k: r.get(k) for k in ("statement", "rationale", "severity", "domain", "applies_when", "detect", "overridable", "refs", "key")},
                      "languages": r.get("languages", []), "frameworks": r.get("frameworks", []), "tags": [r["domain"]],
                      "scope": r.get("scope", "universal"), "provenance": "authored", "verification": "unverified", "confidence": "high",
                      "source_name": "AssetRAG seed", "source_url": SELF_URL})
    for p in PLAYBOOKS:
        plays.append({"kind": "playbook", "title": p["title"], "summary": p["benefit"],
                      "data": {k: p.get(k) for k in ("when_to_use", "benefit", "steps", "risks", "verify", "anti_patterns", "benchmark_status", "key")},
                      "tags": ["playbook"], "provenance": "authored", "verification": "unverified", "confidence": "medium",
                      "source_name": "AssetRAG seed", "source_url": SELF_URL})
    return out_bug + rules + plays + out_rec, bad               # same order as before


def seed(log=print, verify: str = "auto", progress=None) -> dict:
    """progress(stage, done, total) with stage in verifying | embedding | saving | re-embedding."""
    cfg = load()
    P = progress or (lambda stage, done, total: None)
    entries, bad = build(cfg, log, verify, lambda d, t: P("verifying", d, t))
    res = {"added": 0, "duplicate": 0, "updated": 0, "other": 0, "rejected": len(bad), "rejected_detail": bad,
           "unverified_here": sum(1 for e in entries if e["kind"] in ("bugfix", "recipe") and e["verification"] == "unverified")}
    L = Library(cfg, lock_wait=300)
    try:
        knowledge.prime(L, entries, lambda d, t: P("embedding", d, t))
        for n, e in enumerate(entries, 1):
            P("saving", n, len(entries))
            try:
                r = knowledge.add(L, e)
            except knowledge.KError as ex:
                res["rejected"] += 1; res["rejected_detail"].append((e["title"][:60], str(ex))); log(f"  x {e['kind']} {e['title'][:50]}: {ex}")
                continue
            res[r["action"] if r["action"] in res else "other"] += 1
        res["resigned"] = knowledge.resig(L)
        res["embedded"] = knowledge.reindex(L, progress=lambda d, t: P("re-embedding", d, t))      # only entries with no vector yet (e.g. after a model switch)
        L.release_vec()
    finally:
        L.close()
    return res


if __name__ == "__main__":
    print(seed(), file=sys.stderr)
