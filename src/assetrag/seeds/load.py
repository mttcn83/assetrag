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


def build(cfg: Config, log=print, verify: str = "auto") -> tuple[list[dict], list[tuple[str, str]]]:
    """Verify everything first (no vector-store lock held), return entries ready to add.
    verify='auto' executes whatever this machine can and loads the rest as `unverified`; 'off' executes nothing."""
    out, bad = [], []
    for s in SEEDS:
        skip = _skip_reason(s["repro"]["cmd"], verify, s["lang"])
        try:
            ev = {} if skip else kverify.verify(cfg, s)
        except Exception as e:  # noqa: BLE001
            bad.append((s["key"], str(e)[:200])); log(f"  x bugfix {s['key']}: {e}"); continue
        out.append({"kind": "bugfix", "title": s["title"], "summary": s["root_cause"],
                    "data": {"error": s["error"], "root_cause": s["root_cause"], "fix": s["fix"], "pitfalls": s.get("pitfalls", []),
                             "repro": s["repro"], "fixed_example": s["fixed"], "verification_evidence": ev, "key": s["key"]},
                    "languages": [s["lang"]], "frameworks": s.get("frameworks", []), "tags": s.get("tags", []),
                    "version_spec": s.get("version_spec", ""), "provenance": "authored", "verification": "verified",
                    "confidence": "high", "source_name": "AssetRAG seed (executed)", "source_url": SELF_URL})
        if skip:
            _unverified(out[-1], skip)
    for r in RULES:
        out.append({"kind": "rule", "title": r["statement"][:120], "summary": r["rationale"],
                    "data": {k: r.get(k) for k in ("statement", "rationale", "severity", "domain", "applies_when", "detect", "overridable", "refs", "key")},
                    "languages": r.get("languages", []), "frameworks": r.get("frameworks", []), "tags": [r["domain"]],
                    "scope": r.get("scope", "universal"), "provenance": "authored", "verification": "unverified", "confidence": "high",
                    "source_name": "AssetRAG seed", "source_url": SELF_URL})
    for p in PLAYBOOKS:
        out.append({"kind": "playbook", "title": p["title"], "summary": p["benefit"],
                    "data": {k: p.get(k) for k in ("when_to_use", "benefit", "steps", "risks", "verify", "anti_patterns", "benchmark_status", "key")},
                    "tags": ["playbook"], "provenance": "authored", "verification": "unverified", "confidence": "medium",
                    "source_name": "AssetRAG seed", "source_url": SELF_URL})
    for r in all_recipes():
        skip = _skip_reason(r["cmd"], verify, r["lang"])
        try:
            ev = {} if skip else verify_recipe(cfg, r)
        except Exception as e:  # noqa: BLE001
            bad.append((r["key"], str(e)[:200])); log(f"  x recipe {r['key']}: {e}"); continue
        out.append({"kind": "recipe", "title": r["title"], "summary": r["problem"],
                    "data": {"problem": r["problem"], "approach": r["approach"], "files": r["files"], "dependencies": [],
                             "license": "MIT (authored for AssetRAG; stdlib-only)", "assumptions": r["assumptions"],
                             "compatibility": r["compat"], "avoid": r["avoid"], "test_command": " ".join(r["cmd"]),
                             "verification_evidence": ev, "key": r["key"]},
                    "languages": [] if r["lang"] in AGNOSTIC else [r["lang"]], "tags": r["tags"] + ([r["lang"]] if r["lang"] in AGNOSTIC else []),
                    "provenance": "authored", "verification": "verified",
                    "confidence": "high", "source_name": "AssetRAG seed (executed)", "source_url": SELF_URL})
        if skip:
            _unverified(out[-1], skip)
    return out, bad


def seed(log=print, verify: str = "auto") -> dict:
    cfg = load()
    entries, bad = build(cfg, log, verify)
    res = {"added": 0, "duplicate": 0, "updated": 0, "other": 0, "rejected": len(bad), "rejected_detail": bad,
           "unverified_here": sum(1 for e in entries if e["kind"] in ("bugfix", "recipe") and e["verification"] == "unverified")}
    L = Library(cfg, lock_wait=300)
    try:
        for e in entries:
            try:
                r = knowledge.add(L, e)
            except knowledge.KError as ex:
                res["rejected"] += 1; res["rejected_detail"].append((e["title"][:60], str(ex))); log(f"  x {e['kind']} {e['title'][:50]}: {ex}")
                continue
            res[r["action"] if r["action"] in res else "other"] += 1
        res["resigned"] = knowledge.resig(L)
        L.release_vec()
    finally:
        L.close()
    return res


if __name__ == "__main__":
    print(seed(), file=sys.stderr)
