"""What a project already depends on, read from its manifests (package.json, requirements.txt, pyproject.toml, go.mod, Cargo.toml)."""
from __future__ import annotations

import json
import re
from pathlib import Path


def deps(p: Path) -> list[tuple[str, str]]:
    out = []
    pj = p / "package.json"
    if pj.exists():
        try:
            d = json.loads(pj.read_text())
            out += [("npm", n) for n in {**d.get("dependencies", {})}]
        except ValueError:
            pass
    rq = p / "requirements.txt"
    if rq.exists():
        out += [("pypi", re.split(r"[<>=!~\[ ;]", l.strip())[0]) for l in rq.read_text().splitlines() if l.strip() and not l.startswith(("#", "-"))]
    pp = p / "pyproject.toml"
    if pp.exists():
        import tomllib
        try:
            t = tomllib.loads(pp.read_text())
            out += [("pypi", re.split(r"[<>=!~\[ ;]", x)[0]) for x in t.get("project", {}).get("dependencies", [])]
        except tomllib.TOMLDecodeError:
            pass
    gm = p / "go.mod"
    if gm.exists():
        out += [("go", m.group(1)) for m in re.finditer(r"^\s*(?:require\s+)?([\w.-]+\.[\w.-]+/[^\s]+)\s+v", gm.read_text(), re.M)]
    ct = p / "Cargo.toml"
    if ct.exists():
        import tomllib
        try:
            out += [("crate", n) for n in tomllib.loads(ct.read_text()).get("dependencies", {})]
        except tomllib.TOMLDecodeError:
            pass
    return out


def names(project: str | Path) -> set[str]:
    """Lower-cased dependency names, pypi names normalised (`-`/`_`/`.` equal), go modules in full."""
    out = set()
    for _eco, n in deps(Path(project)):
        n = n.lower()
        out.add(re.sub(r"[-_.]+", "-", n))
        out.add(n.rsplit("/", 1)[-1])
        out.add(n)
    return out
