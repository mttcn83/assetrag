"""Execute a bug-fix claim in the sandbox: the repro must show the error, the fix must clear it."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import sandbox, security
from .config import Config

ENV = {"GOCACHE": "/tmp/gc", "GOPATH": "/tmp/gp", "GOTOOLCHAIN": "local", "GOFLAGS": "-mod=mod", "GO111MODULE": "off", "CGO_ENABLED": "0"}


def _run(cfg: Config, phase: dict, timeout: int = 90) -> tuple[int, str]:
    d = Path(tempfile.mkdtemp(dir=cfg.work))
    try:
        for rel, body in phase["files"].items():
            p = security.safe_join(d, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        return sandbox.run(phase["cmd"], d, timeout, env=ENV)
    finally:
        shutil.rmtree(d, ignore_errors=True)


class VerifyFailed(Exception):
    pass


def verify(cfg: Config, seed: dict) -> dict:
    rp, fx = seed["repro"], seed["fixed"]
    rc1, out1 = _run(cfg, rp)
    if rp.get("contains") and rp["contains"] not in out1:
        raise VerifyFailed(f"repro did not show {rp['contains']!r}; got: {out1[-160:]!r}")
    if rp.get("lacks") and rp["lacks"] in out1:
        raise VerifyFailed(f"repro unexpectedly contains {rp['lacks']!r}")
    rc2, out2 = _run(cfg, fx)
    if rc2 != fx.get("rc", 0):
        raise VerifyFailed(f"fixed version exit {rc2}: {out2[-160:]!r}")
    if fx.get("contains") and fx["contains"] not in out2:
        raise VerifyFailed(f"fixed output lacks {fx['contains']!r}: {out2[-160:]!r}")
    if rp.get("contains") and rp["contains"] in out2:
        raise VerifyFailed("fixed version still shows the error")
    first = next((l for l in out1.splitlines() if (rp.get("contains") or "") in l), out1.strip().splitlines()[-1] if out1.strip() else "")
    return {"method": "executed in bubblewrap sandbox (no network)", "interpreter": sys.version.split()[0] if seed["lang"] == "python" else seed["lang"],
            "repro_exit": rc1, "repro_output": first.strip()[:240], "fixed_exit": rc2,
            "fixed_output": (out2.strip().splitlines()[-1] if out2.strip() else "")[:120]}
