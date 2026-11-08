"""Fix-commit mining (SWE-bench style): turn real upstream bug fixes into *reproduced* knowledge.

For a permissively-licensed repo we look at commits whose subject says "fix", that change source AND tests, and are
small. For each candidate we build two trees from git objects only:
    parent+tests : the parent commit's sources with the fix commit's test files overlaid  -> must FAIL
    fixed        : the fix commit itself                                                    -> must PASS
Both runs happen in the bubblewrap sandbox (no network). Only candidates that fail at the parent *because of a test
assertion* (not a compile / collection error) and pass at the fix are stored, as bugfix entries with
verification=verified, the failing assertion text as the error signature, the source diff as the fix, and a
commit-pinned source link plus the repo's fingerprinted licence for attribution.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import ingest, licenses, sandbox
from .config import Config

REPOS = [("pallets/click", "python"), ("python-attrs/attrs", "python"), ("tkem/cachetools", "python"), ("mahmoud/boltons", "python"),
         ("google/uuid", "go"), ("gorilla/mux", "go"), ("patrickmn/go-cache", "go"), ("julienschmidt/httprouter", "go"),
         ("cenkalti/backoff", "go"), ("mitchellh/mapstructure", "go")]
PERMISSIVE = {"MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "0BSD", "Unlicense", "CC0-1.0"}
FIXISH = re.compile(r"\b(fix(?:es|ed)?|bug|regression|crash(?:es)?|panic|incorrect(?:ly)?|wrong(?:ly)?|broken|fail(?:s|ed|ure)?)\b", re.I)
GENERIC = re.compile(r"^(?:typo|docs?|ci|chore|release|bump|merge|revert|lint|format)", re.I)


def git(repo: Path, *a: str, text=True, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=text, timeout=300)
    if check and r.returncode:
        raise RuntimeError(r.stderr[-200:] if text else "git failed")
    return r.stdout


def is_test(path: str, lang: str) -> bool:
    n = Path(path).name
    return n.endswith("_test.go") if lang == "go" else (n.startswith("test_") or n.endswith("_test.py") or "/tests/" in "/" + path or n == "conftest.py")


def is_source(path: str, lang: str) -> bool:
    return path.endswith(".go" if lang == "go" else ".py") and not is_test(path, lang)


def candidates(repo: Path, lang: str, scan=2500, max_files=6, max_lines=200):
    out = git(repo, "log", "--no-merges", f"-n{scan}", "--format=%H%x09%P%x09%s").splitlines()
    for line in out:
        sha, parents, subject = (line.split("\t") + ["", ""])[:3]
        if not parents or " " in parents or not FIXISH.search(subject) or GENERIC.match(subject):
            continue
        files = git(repo, "show", "--name-only", "--format=", sha).split()
        if not (1 <= len(files) <= max_files):
            continue
        tests, srcs = [f for f in files if is_test(f, lang)], [f for f in files if is_source(f, lang)]
        if not tests or not srcs:
            continue
        stat = git(repo, "show", "--shortstat", "--format=", sha)
        n = sum(int(x) for x in re.findall(r"(\d+) (?:insertion|deletion)", stat))
        if n <= max_lines:
            yield sha, parents, subject, tests, srcs


def export(repo: Path, sha: str, dest: Path) -> None:
    p = subprocess.Popen(["git", "-C", str(repo), "archive", sha], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(dest)], stdin=p.stdout, check=True)
    p.wait()


def overlay_tests(repo: Path, sha: str, dest: Path, tests: list[str]) -> None:
    for t in tests:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{sha}:{t}"], capture_output=True)
        if r.returncode == 0:
            (dest / t).parent.mkdir(parents=True, exist_ok=True)
            (dest / t).write_bytes(r.stdout)


def run_tests(cfg: Config, tree: Path, lang: str, tests: list[str]) -> tuple[int, str]:
    if lang == "python":
        sp = str(Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
        return sandbox.run(["python3", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--rootdir=/w", *tests], tree, 150,
                           env={"PYTHONPATH": "/sp:/w:/w/src"}, binds=[(sp, "/sp")])
    gp = cfg.home / "gopath"
    env = {**os.environ, "GOPATH": str(gp), "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"}
    if (tree / "go.mod").exists():
        r = subprocess.run(["go", "mod", "download"], cwd=tree, env=env, capture_output=True, text=True, timeout=300)
        if r.returncode:
            return 125, "go mod download failed: " + r.stderr[-150:]
    else:
        (tree / "go.mod").write_text("module m\n\ngo 1.21\n")
    pkgs = sorted({"./" + str(Path(t).parent) if str(Path(t).parent) != "." else "." for t in tests})
    return sandbox.run(["go", "test", *pkgs, "-count=1", "-short"], tree, 240,
                       env={"GOCACHE": "/tmp/gc", "GOPATH": "/tmp/gp", "GOMODCACHE": "/gomod", "GOFLAGS": "-mod=mod", "GOPROXY": "off",
                            "GOSUMDB": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "0"},
                       binds=[(str(gp / "pkg" / "mod"), "/gomod")])


def failure_signature(out: str, lang: str) -> str | None:
    """The assertion that failed, or None when the failure is a compile/collection error (not a behavioural bug)."""
    if lang == "python":
        if re.search(r"error(s)? during collection|ImportError|ModuleNotFoundError|SyntaxError|fixture '.*' not found", out):
            return None
        m = re.search(r"^E\s+(.+)$", out, re.M)
        f = re.search(r"^FAILED \S+ - (.+)$", out, re.M)
        sig = (m.group(1) if m else f.group(1) if f else "").strip()
        return sig[:240] if len(sig) >= 8 and "failed" in out.lower() else None
    if re.search(r"\[build failed\]|undefined:|cannot use|too many arguments|not enough arguments|missing return|has no field or method", out):
        return None
    m = re.search(r"--- FAIL: (\w+)[^\n]*\n\s+([\w./-]+_test\.go:\d+: .+)", out)
    if m:
        return re.sub(r"^[\w./-]+_test\.go:\d+:\s*", "", m.group(2)).strip()[:240]
    m = re.search(r"^panic: (.+)$", out, re.M)
    return m.group(1)[:240] if m else None


def try_commit(cfg: Config, repo: Path, lang: str, sha: str, parent: str, tests: list[str]) -> dict:
    work = Path(tempfile.mkdtemp(dir=cfg.work))
    try:
        a, b = work / "parent", work / "fixed"
        a.mkdir(); b.mkdir()
        export(repo, parent, a); overlay_tests(repo, sha, a, tests)
        export(repo, sha, b)
        rc_a, out_a = run_tests(cfg, a, lang, tests)
        if rc_a == 0:
            return {"ok": False, "why": "tests already pass before the fix"}
        sig = failure_signature(out_a, lang)
        if not sig:
            return {"ok": False, "why": "fails to build/collect before the fix (not a behavioural reproduction)"}
        rc_b, out_b = run_tests(cfg, b, lang, tests)
        if rc_b != 0:
            return {"ok": False, "why": "tests still fail after the fix in the sandbox"}
        return {"ok": True, "sig": sig, "before": out_a.strip().splitlines()[-1][:160], "after": out_b.strip().splitlines()[-1][:160]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def build_entry(repo: Path, slug: str, lang: str, sha: str, parent: str, subject: str, tests: list[str], srcs: list[str], res: dict, spdx: str) -> dict:
    body = git(repo, "show", "-s", "--format=%b", sha).strip()
    body = re.sub(r"(?im)^(signed-off-by|co-authored-by|reviewed-by|change-id).*$", "", body).strip()
    cause = (subject + (". " + re.split(r"\n\s*\n", body)[0].replace("\n", " ") if body else ""))[:400]
    diff = git(repo, "show", "-U2", "--format=", sha, "--", *srcs)[:1800]
    name = slug.split("/")[1]
    return {"kind": "bugfix", "title": f"{name}: {subject}"[:200], "summary": cause[:300],
            "data": {"error": res["sig"], "root_cause": cause, "fix": "Apply the upstream change (source diff below):\n" + diff,
                     "repro": {"test_files": tests, "command": "pytest" if lang == "python" else "go test", "run_against": f"{parent[:12]} + tests from {sha[:12]}"},
                     "verification_evidence": {"method": "fail-to-pass in bubblewrap sandbox (no network): parent sources + fix-commit tests fail, fix commit passes",
                                               "before_fix": res["before"], "after_fix": res["after"], "parent": parent, "fix_commit": sha},
                     "key": f"fixmine:{slug}:{sha[:12]}"},
            "languages": [lang], "tags": [name, "mined-commit", "reproduced"], "provenance": "mined-commit", "verification": "verified",
            "confidence": "high", "source_name": f"{slug}@{sha[:12]} ({spdx})", "source_url": f"https://github.com/{slug}/commit/{sha}",
            "source_license": spdx}


def mine(cfg: Config, repos=None, tries=15, log=print) -> tuple[list[dict], dict]:
    entries, report = [], {}
    for slug, lang in (repos or REPOS):
        rep = report[slug] = {"candidates": 0, "reproduced": 0, "rejected": {}}
        dest = cfg.work / "fixmine" / slug.replace("/", "__")
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "-q", "--no-tags", f"https://github.com/{slug}.git", str(dest)], capture_output=True, text=True, timeout=900)
            if r.returncode:
                rep["error"] = "clone failed"; log(f"x {slug}: clone failed"); continue
        _, text, spdx = ingest.find_license(dest)
        if spdx not in PERMISSIVE:
            rep["error"] = f"licence not permissive/verified ({spdx})"; log(f"x {slug}: {rep['error']}"); continue
        for sha, parent, subject, tests, srcs in candidates(dest, lang):
            if rep["candidates"] >= tries:
                break
            rep["candidates"] += 1
            try:
                res = try_commit(cfg, dest, lang, sha, parent, tests)
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "why": f"error {type(e).__name__}"}
            if res["ok"]:
                rep["reproduced"] += 1
                entries.append(build_entry(dest, slug, lang, sha, parent, subject, tests, srcs, res, spdx))
                log(f"  + {slug} {sha[:8]} {subject[:60]}  [{res['sig'][:60]}]")
            else:
                rep["rejected"][res["why"]] = rep["rejected"].get(res["why"], 0) + 1
        log(f"{slug}: {rep['reproduced']}/{rep['candidates']} reproduced; rejected {rep['rejected']}")
    return entries, report


if __name__ == "__main__":
    from .config import load
    cfg = load()
    only = set(sys.argv[2:])
    ents, rep = mine(cfg, [r for r in REPOS if not only or r[0] in only], tries=int(sys.argv[1]) if len(sys.argv) > 1 else 15)
    out = cfg.home / "fixmine.json"
    out.write_text(json.dumps({"entries": ents, "report": rep}, indent=1))
    print(json.dumps(rep, indent=1)); print("wrote", out, len(ents), "entries")
