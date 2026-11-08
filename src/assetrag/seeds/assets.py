"""Publish the tested recipes as importable library assets, through the same `harvest_code` gate user code goes through.
Provenance is recorded as `generated` (allowed for backend/cli/algorithm only); the recipe's own tests must pass."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .. import ops
from ..library import Library
from .recipes import all_recipes

CATEGORY = {"py-cli-skeleton": "cli", "py-term-table": "cli", "go-cli-skeleton": "cli", "py-xdg-config": "cli",
            **{k: "cli" for k in ("go-atomic-write", "go-xdg-dirs", "go-proc-net-sockets", "go-proc-stats", "go-pidfd-terminate", "go-flock-single-instance",
                                  "go-termcolor", "go-sysctl-sysfs", "go-journaled-apply", "go-hw-detect", "go-privileged-helper-allowlist", "go-table-width")},
            **{k: "algorithm" for k in ("go-permutation-test", "go-fuzzy-match", "go-trie", "go-ring-buffer", "go-glob-match", "go-parse-duration", "go-semver", "go-myers-diff")}}
SKIP = {"js-fetch-retry", "js-reactive-store"}   # harvest supports python/go; JS recipes stay knowledge-only


def seed_assets(lib: Library, log=print) -> dict:
    res = {"harvested": 0, "skipped": 0, "failed": []}
    for r in all_recipes():
        if r["key"] in SKIP or r["lang"] not in ("python", "go"):
            res["skipped"] += 1
            continue
        d = Path(tempfile.mkdtemp(prefix="ar-harvest-"))
        try:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.name", "Mert"], cwd=d, check=True)
            files = dict(r["files"])
            if r["lang"] == "go":
                files["go.mod"] = "module example.com/" + r["key"].replace("-", "") + "\n\ngo 1.21\n"
            for name, body in files.items():
                (d / name).write_text(body)
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "-c", "user.email=a@b.c", "commit", "-qm", "recipe"], cwd=d, check=True)
            names = sorted(files)
            tests = [n for n in names if n.startswith("test_") or n.endswith("_test.go") or ".test." in n]
            code = [n for n in names if n not in tests and n != "go.mod"]
            out = ops.harvest_code(lib, code + (["go.mod"] if r["lang"] == "go" else []), str(d), r["title"].split(" (")[0],
                                   f"{r['problem']} {r['approach']}", CATEGORY.get(r["key"], "backend"), True, True, tests,
                                   r["tags"] + [r["lang"], "recipe", "stdlib-only"])
            if out.get("harvested"):
                res["harvested"] += 1
                log(f"  + asset {r['key']} -> {out['asset_id']}")
            else:
                res["failed"].append((r["key"], out.get("reason") or out.get("note") or str(out)[:160]))
                log(f"  x asset {r['key']}: {res['failed'][-1][1]}")
        finally:
            shutil.rmtree(d, ignore_errors=True)
    lib.flush_index()
    return res
