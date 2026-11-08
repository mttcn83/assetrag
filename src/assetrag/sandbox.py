"""Run untrusted code with no network, a read-only root, and a private tmpfs."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def available() -> bool:
    return shutil.which("bwrap") is not None


def run(cmd: list[str], workdir: Path, timeout: int = 60, env: dict[str, str] | None = None,
        binds: list[tuple[str, str]] | None = None) -> tuple[int, str]:
    """Execute `cmd` inside bubblewrap. workdir is mounted read-only at /w.
    Falls back to refusing (returns 126) when bwrap is missing: untrusted code
    is never run unsandboxed."""
    if not available():
        return 126, "bubblewrap not installed; refusing to run untrusted code"
    bw = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
          "--ro-bind", "/usr", "/usr",
          "--symlink", "usr/bin", "/bin", "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
          "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
          # name resolution files only (no network is reachable): lets tests that start a localhost server resolve "localhost"
          "--ro-bind-try", "/etc/hosts", "/etc/hosts", "--ro-bind-try", "/etc/nsswitch.conf", "/etc/nsswitch.conf",
          "--ro-bind", str(workdir), "/w", "--chdir", "/w",
          "--setenv", "HOME", "/tmp", "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
          "--setenv", "PATH", "/usr/bin"]
    for src, dst in (binds or []):
        bw += ["--ro-bind", src, dst]
    for k, v in (env or {}).items():
        bw += ["--setenv", k, v]
    bw += ["--"] + cmd
    try:
        r = subprocess.run(bw, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return r.returncode, (r.stdout + r.stderr)[-4000:]
