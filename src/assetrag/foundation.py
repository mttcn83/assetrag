"""Foundation libraries: small, widely used, permissively licensed building blocks that real projects import
instead of rewriting. They go through the normal admission gate (licence fingerprint, dependency licences,
ClamAV, sandboxed tests); this list only says where to look.

Go repos are admitted as whole-repo assets via discover's curated path. JS/TS libraries are too big for that
path (monorepos, build steps), so they are covered by the package index (see pkgindex.py).
"""
from __future__ import annotations

GO_FOUNDATION = [
    "prometheus/procfs", "shirou/gopsutil", "jaypipes/ghw", "google/renameio", "adrg/xdg", "mattn/go-runewidth",
    "sahilm/fuzzy", "sergi/go-diff", "gofrs/flock",
]

KIND = dict(category="cli", language="go", checks=["gofmt", "gotest"], topics=[])
