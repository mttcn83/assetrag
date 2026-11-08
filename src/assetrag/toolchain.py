"""Versions of the language toolchains installed on this machine (used only to nudge ranking, never to exclude a result)."""
from __future__ import annotations

import functools
import re
import shutil
import subprocess
import sys

_CMD = {"python": None, "node": ["node", "--version"], "javascript": ["node", "--version"], "typescript": ["node", "--version"],
        "go": ["go", "version"], "rust": ["rustc", "--version"]}


@functools.lru_cache(maxsize=None)
def installed(language: str | None) -> str | None:
    lang = (language or "").lower()
    if lang == "python":
        return ".".join(map(str, sys.version_info[:3]))
    cmd = _CMD.get(lang)
    if not cmd or not shutil.which(cmd[0]):
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None
