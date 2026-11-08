"""Discovery -> verification -> isolation -> checks -> admission, resumable.

A `Spec` declares *where* assets live inside a source repository and how to
check them; everything legal/safety related happens in Library.admit().
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from . import licenses, sandbox, security
from .config import Config
from .db import now
from .library import Candidate, Library, Rejected

STDLIB = set(sys.stdlib_module_names)
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}


# ------------------------------------------------------------------ repo ctx
@dataclass
class RepoCtx:
    url: str
    path: Path
    sha: str
    license_text: str
    license_url: str
    spdx: str | None
    copyright: str

    @property
    def slug(self) -> str: return "/".join(self.url.rstrip("/").split("/")[-2:])


_LIC_NAME = re.compile(r"^(licen[cs]e|copying|unlicense)([-_.].*)?$", re.I)


def find_license(root: Path) -> tuple[str, str, str | None]:
    """Return (relative name, text, spdx) for the first license file we can fingerprint."""
    cands = sorted(p for p in root.iterdir() if p.is_file() and _LIC_NAME.match(p.name))
    for p in cands:
        t = p.read_text(errors="ignore")
        s = licenses.detect(t)
        if s:
            return p.name, t, s
    return (cands[0].name, cands[0].read_text(errors="ignore"), None) if cands else ("", "", None)


def clone(cfg: Config, url: str, ref: str | None = None, retries: int = 3) -> RepoCtx:
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", url):
        raise ValueError(f"only https://github.com/<owner>/<repo> sources are supported: {url}")
    dest = cfg.work / "repos" / url.split("github.com/")[1].replace("/", "__")
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=never",
               "-c", "core.symlinks=false", "clone", "--depth", "1", "--no-tags", "--single-branch", "-q"]
        if ref:
            cmd += ["--branch", ref]
        err = ""
        for i in range(retries):
            r = subprocess.run(cmd + [url + ".git", str(dest)], capture_output=True, text=True, env=GIT_ENV, timeout=900)
            if r.returncode == 0:
                break
            err = r.stderr[-300:]
            subprocess.run(["rm", "-rf", str(dest)])
            time.sleep(2 * (i + 1))
        else:
            raise RuntimeError(f"clone failed for {url}: {err}")
    sha = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    name, text, spdx = find_license(dest)
    m = re.search(r"(?im)^\s*(copyright[^\n]{5,120})", text)
    return RepoCtx(url, dest, sha, text, f"{url}/blob/{sha}/{name}" if name else "", spdx,
                   m.group(1).strip() if m else "")


def scan_repos(paths: list[Path], log: Path, batch: int = 6) -> dict[Path, str]:
    """ClamAV pass over fresh clones, in resumable batches. Clean repos get a stamp
    keyed to the commit so they are not rescanned. Returns {path: finding} for infected ones."""
    bad: dict[Path, str] = {}

    def stamp(p: Path) -> Path:
        sha = subprocess.run(["git", "-C", str(p), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        return p / ".git" / f"assetrag-clam-{sha[:12]}"

    todo = [p for p in paths if not stamp(p).exists()]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        code, out = security.clamscan_many(chunk, log)
        if code == 2:
            raise RuntimeError(f"ClamAV unavailable: {out[-200:]}")
        hits = {}
        for line in out.splitlines():
            if line.endswith("FOUND"):
                f = line.split(":")[0]
                for p in chunk:
                    if f.startswith(str(p)):
                        hits[p] = line
        for p in chunk:
            if p in hits:
                bad[p] = hits[p]
            else:
                stamp(p).write_text("clean\n")
    return bad


# ------------------------------------------------------- dependency licences
class DepLicenses:
    """Resolve dependency licence -> SPDX using registries / GitHub, cached."""

    def __init__(self, cfg: Config):
        self.f = cfg.home / "deplic.json"
        self.cache: dict[str, str | None] = json.loads(self.f.read_text()) if self.f.exists() else {}
        self.http = httpx.Client(timeout=30, follow_redirects=True)

    def _save(self): self.f.write_text(json.dumps(self.cache, indent=1, sort_keys=True))

    def npm(self, name: str) -> str | None:
        k = f"npm:{name}"
        if k not in self.cache:
            try:
                r = self.http.get(f"https://registry.npmjs.org/{name.replace('/', '%2f')}/latest")
                lic = r.json().get("license") if r.status_code == 200 else None
                if isinstance(lic, dict): lic = lic.get("type")
                self.cache[k] = licenses.normalize_spdx(lic)
            except (httpx.HTTPError, ValueError):
                return None
            self._save()
        return self.cache[k]

    def pypi(self, name: str) -> str | None:
        k = f"pypi:{name}"
        if k not in self.cache:
            try:
                r = self.http.get(f"https://pypi.org/pypi/{name}/json")
                info = r.json()["info"] if r.status_code == 200 else {}
                cand = [info.get("license_expression"), info.get("license")] + [
                    c.split("::")[-1].replace("License", "").strip() for c in info.get("classifiers", []) if "License ::" in c]
                spdx = next((s for s in (licenses.normalize_spdx(c) for c in cand if c and len(c) < 60) if s), None)
                if not spdx and info.get("license") and len(info["license"]) > 200:
                    spdx = licenses.detect(info["license"])
                self.cache[k] = spdx
            except (httpx.HTTPError, ValueError, KeyError):
                return None
            self._save()
        return self.cache[k]

    def github(self, owner_repo: str) -> str | None:
        """Licence from the repo's LICENSE file text, fingerprinted by us (not GitHub's guess)."""
        k = f"gh:{owner_repo}"
        if k not in self.cache:
            r = subprocess.run(["gh", "api", f"repos/{owner_repo}/license", "--jq", ".content"],
                               capture_output=True, text=True, timeout=60)
            spdx = None
            if r.returncode == 0:
                try:
                    spdx = licenses.detect(base64.b64decode(r.stdout.strip()).decode("utf-8", "ignore"))
                except ValueError:
                    pass
            self.cache[k] = spdx
            self._save()
        return self.cache[k]

    def crate(self, name: str) -> str | None:
        k = f"crate:{name}"
        if k not in self.cache:
            try:
                r = self.http.get(f"https://crates.io/api/v1/crates/{name}", headers={"User-Agent": "assetrag (local license audit)"})
                lic = r.json()["versions"][0].get("license") if r.status_code == 200 else None
                self.cache[k] = licenses.normalize_spdx(lic)
            except (httpx.HTTPError, ValueError, KeyError, IndexError):
                return None
            self._save()
        return self.cache[k]

    _GO_META = re.compile(r"""<meta\s+name=["']go-import["']\s+content=["']([^"']+)["']""", re.I)

    def _go_import(self, module: str) -> tuple[bool, str | None]:
        """Ask the module's own domain where its code lives (the `go-import` meta tag `go get` uses). Returns
        (answered, owner/repo): answered=False on a network error so a transient failure is never cached as 'no licence'."""
        parts = module.split("/")
        for n in range(len(parts), 1, -1):                    # the vanity root may be a prefix of the import path
            cand = "/".join(parts[:n])
            try:
                r = self.http.get(f"https://{cand}?go-get=1", headers={"User-Agent": "assetrag (local license audit)"})
            except httpx.HTTPError:
                return False, None
            for m in self._GO_META.finditer(r.text if r.status_code == 200 else ""):
                f = m.group(1).split()
                if len(f) == 3 and (module == f[0] or module.startswith(f[0] + "/")):
                    g = re.match(r"https?://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$", f[2])
                    return True, g.group(1) if g else None   # only GitHub-hosted repos can be fingerprinted via gh
        return True, None

    def go(self, module: str) -> str | None:
        module = re.sub(r"/v\d+$", "", module)
        m = re.match(r"github\.com/([\w.-]+/[\w.-]+)", module)
        if m:
            return self.github(m.group(1))
        m = re.match(r"charm\.land/(\w+)", module)   # vanity domain for github.com/charmbracelet/<name>
        if m:
            return self.github(f"charmbracelet/{m.group(1)}")
        m = re.match(r"gopkg\.in/(?:([\w.-]+)/)?([\w-]+)\.v\d+", module)   # gopkg.in/pkg.vN -> github.com/go-pkg/pkg ; gopkg.in/user/pkg.vN -> github.com/user/pkg
        if m:
            return self.github(f"{m.group(1) or 'go-' + m.group(2)}/{m.group(2)}")
        if re.match(r"golang\.org/x/", module):
            return "BSD-3-Clause"   # Go Authors BSD-3 (verified in x/* LICENSE files)
        k = f"go:{module}"
        if k not in self.cache:
            answered, repo = self._go_import(module)
            if not answered:
                return None
            self.cache[k] = self.github(repo) if repo else None   # the licence text is still fingerprinted by us, never taken from the registry
            self._save()
        return self.cache[k]


# ------------------------------------------------------------------- spec
@dataclass
class Spec:
    id: str
    repo: str
    category: str
    kind: str
    include: list[str]
    exclude: list[str] = field(default_factory=list)
    unit: str = "file"                 # file | dir | html
    root: str = ""                     # subdirectory that owns the licence (default repo root)
    license_path: str | None = None    # licence file elsewhere in repo
    frameworks: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    cap: int = 100000
    skip: int = 0
    difficulty: int = 2
    support: list[str] = field(default_factory=list)   # extra repo files bundled with each unit
    describe: Callable | None = None   # (ctx, rel, files)->(description, tags)
    pair_tests: bool = False           # bundle sibling test file(s)
    allow_imports: dict[str, str] = field(default_factory=dict)  # py import -> pypi dist
    prefix: str = ""
    min_bytes: int = 120
    max_bytes: int = 400_000
    name_fn: Callable | None = None
    stride: int = 1                    # take every Nth unit (diverse sampling of big sets)
    siblings: list[str] = field(default_factory=list)   # same-stem companion extensions (.tcss)
    tree: bool = False                 # keep repo-relative paths (python packages / go modules)
    tests: list[str] = field(default_factory=list)       # test file patterns: {dir} {stem}
    npm_deps: list[str] = field(default_factory=list)    # runtime deps not visible in imports
    pip_path: list[str] = field(default_factory=list)    # extra PYTHONPATH entries for tests


def _matches(rel: str, pats: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in pats)


IGNORED_PARTS = {"node_modules", ".git", "__pycache__", ".github", ".next"}


def iter_units(spec: Spec, ctx: RepoCtx):
    base = ctx.path / spec.root
    if spec.unit == "dir":
        seen = []
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(ctx.path).as_posix()
            if p.is_dir() and not set(p.parts) & IGNORED_PARTS and _matches(rel, spec.include) and not _matches(rel, spec.exclude):
                seen.append(p)
        for d in seen:
            files = {}
            for f in sorted(d.rglob("*")):
                if f.is_file() and not f.is_symlink() and not set(f.parts) & IGNORED_PARTS:
                    files[f.relative_to(d).as_posix()] = f.read_bytes()
            yield d.relative_to(ctx.path).as_posix(), files
        return
    for p in sorted(base.rglob("*")):
        if not p.is_file() or p.is_symlink() or set(p.relative_to(ctx.path).parts) & IGNORED_PARTS:
            continue
        rel = p.relative_to(ctx.path).as_posix()
        if _matches(rel, spec.include) and not _matches(rel, spec.exclude):
            key = p.relative_to(base).as_posix() if spec.tree else p.name
            files = {key: p.read_bytes()}
            for ext in spec.siblings:
                sib = p.with_suffix(ext)
                if sib.exists():
                    files[(sib.relative_to(base).as_posix() if spec.tree else sib.name)] = sib.read_bytes()
            yield rel, files


# --------------------------------------------------------------- checks
def _tmpdir(cfg: Config, files: dict[str, bytes]) -> Path:
    d = Path(tempfile.mkdtemp(dir=cfg.work))
    for rel, b in files.items():
        p = security.safe_join(d, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
    return d


def _check_config(text: dict[str, str]) -> None:
    """Config/devops files must at least parse in their own format (YAML/JSON/TOML) and Dockerfiles must start from an image."""
    import tomllib
    import yaml
    for k, t in text.items():
        name = Path(k).name
        if not t.strip():
            raise ValueError(f"{k} is empty")
        if k.endswith((".yml", ".yaml")):
            list(yaml.safe_load_all(t))
        elif k.endswith(".json"):
            json.loads(t)
        elif k.endswith(".toml"):
            tomllib.loads(t)
        elif name.startswith("Dockerfile") and not re.search(r"(?im)^\s*FROM\s+\S+", t):
            raise ValueError(f"{k} has no FROM")


def run_checks(cfg: Config, spec: Spec, files: dict[str, bytes]) -> dict[str, str]:
    import ast
    import xml.etree.ElementTree as ET
    from html.parser import HTMLParser
    res: dict[str, str] = {}
    text = {k: v.decode("utf-8", "ignore") for k, v in files.items()}
    for chk in spec.checks:
        try:
            if chk == "svg":
                for k, t in text.items():
                    if k.endswith(".svg"):
                        root = ET.fromstring(t)
                        if not root.tag.endswith("svg"):
                            raise ValueError("root is not <svg>")
                        if re.search(r"<script|onload=|onclick=|javascript:", t, re.I):
                            raise ValueError("active content in SVG")
                res["svg-valid-no-script"] = "pass"
            elif chk == "html":
                for k, t in text.items():
                    if k.endswith((".html", ".htm")):
                        p = _TagBalance(); p.feed(t); p.close()
                        if p.errors > 3:
                            raise ValueError(f"{p.errors} unbalanced tags in {k}")
                        if not re.search(r"<(body|section|div|main|nav|header|form)\b", t, re.I):
                            raise ValueError("no markup structure")
                res["html-wellformed"] = "pass"
            elif chk == "htmldoc":
                k = next((k for k in text if k.endswith((".html", ".htm"))), None)
                if not k or not re.search(r"<html[\s>]", text[k], re.I) or "<body" not in text[k].lower():
                    raise ValueError("not a complete HTML document")
                res["html-complete-document"] = "pass"
            elif chk == "rustfmt":
                for k, t in text.items():
                    if k.endswith(".rs"):
                        r = subprocess.run(["rustfmt", "--edition", "2021", "--emit", "stdout", "--config", "skip_children=true"],
                                           input=t, capture_output=True, text=True, timeout=30)
                        if r.returncode not in (0,) and "error" in r.stderr.lower():
                            raise ValueError(r.stderr[-150:])
                res["rustfmt-parse"] = "pass"
            elif chk == "css":
                for k, t in text.items():
                    if k.endswith((".css", ".scss")):
                        c = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
                        if c.count("{") != c.count("}"):
                            raise ValueError(f"unbalanced braces in {k}")
                res["css-balanced"] = "pass"
            elif chk == "config":
                _check_config(text)
                res["config-parses"] = "pass"
            elif chk == "py":
                for k, t in text.items():
                    if k.endswith(".py"):
                        ast.parse(t)
                res["python-ast-parse"] = "pass"
            elif chk == "pyimports":
                for k, t in text.items():
                    if k.endswith(".py"):
                        _check_py_imports(t, files, spec)
                res["python-imports-resolved"] = "pass"
            elif chk in ("doctest", "pytest"):
                d = _tmpdir(cfg, files)
                try:
                    targets = [k for k in files if k.endswith(".py") and not Path(k).name.startswith("test_")]
                    if chk == "doctest":
                        code, out = sandbox.run(["python3", "-I", "-m", "doctest", *targets], d, 60)
                    else:
                        sp = str(Path(sys.prefix) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
                        code, out = sandbox.run(["python3", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "--rootdir=/w"], d, 120,
                                                env={"PYTHONPATH": ":".join(["/sp", "/w", *("/w/" + x for x in spec.pip_path)])},
                                                binds=[(sp, "/sp")])
                finally:
                    subprocess.run(["rm", "-rf", str(d)])
                if code != 0:
                    raise ValueError(out[-200:])
                res[chk] = "pass: " + (out.strip().splitlines()[-1] if out.strip() else "all examples passed")
            elif chk == "node":
                d = _tmpdir(cfg, files)
                try:
                    for k in files:
                        if k.endswith((".js", ".mjs", ".cjs")):
                            code, out = sandbox.run(["node", "--check", k], d, 30)
                            if code != 0:
                                raise ValueError(f"{k}: {out[-150:]}")
                finally:
                    subprocess.run(["rm", "-rf", str(d)])
                res["node-syntax-check"] = "pass"
            elif chk == "ts":
                d = _tmpdir(cfg, files)
                try:
                    errs = ts_syntax(cfg, d, [k for k in files if k.endswith((".ts", ".tsx", ".jsx", ".js", ".mts"))])
                finally:
                    subprocess.run(["rm", "-rf", str(d)])
                if errs:
                    raise ValueError(errs[:200])
                res["typescript-syntax"] = "pass"
            elif chk == "gofmt":
                d = _tmpdir(cfg, files)
                try:
                    code, out = sandbox.run(["gofmt", "-e", "-l", "."], d, 30)
                finally:
                    subprocess.run(["rm", "-rf", str(d)])
                if "error" in out.lower() or ":" in out and "rror" in out:
                    raise ValueError(out[-200:])
                res["gofmt-syntax"] = "pass"
            elif chk == "gotest":
                d = _tmpdir(cfg, files)
                try:
                    (d / "go.mod").write_text("module asset\n\ngo 1.21\n")
                    code, out = sandbox.run(["go", "test", "./..."], d, 180,
                                            env={"GOCACHE": "/tmp/gc", "GOFLAGS": "-mod=mod", "GOPROXY": "off",
                                                 "GOTOOLCHAIN": "local", "GOPATH": "/tmp/gp", "CGO_ENABLED": "0"})
                finally:
                    subprocess.run(["rm", "-rf", str(d)])
                if code != 0:
                    raise ValueError(out[-200:])
                res["go-test"] = "pass: " + out.strip().splitlines()[-1][:80]
            elif chk == "json":
                for k, t in text.items():
                    if k.endswith(".json"):
                        json.loads(t)
                res["json-valid"] = "pass"
            else:
                raise ValueError(f"unknown check {chk}")
        except Exception as e:  # noqa: BLE001 - any failure is a failed check
            res[chk] = f"fail: {str(e)[:160]}"
    return res


class _TagBalance:
    """Cheap well-formedness counter (avoids bs4 dependency)."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr",
            "param", "command", "keygen", "path", "circle", "rect", "line", "polyline", "polygon", "use", "stop"}

    def __init__(self):
        from html.parser import HTMLParser

        outer = self
        outer.stack, outer.errors = [], 0

        class P(HTMLParser):
            def handle_starttag(s, tag, attrs):
                if tag not in outer.VOID:
                    outer.stack.append(tag)
            def handle_startendtag(s, tag, attrs): pass
            def handle_endtag(s, tag):
                if tag in outer.VOID: return
                if tag in outer.stack:
                    while outer.stack and outer.stack[-1] != tag:
                        if outer.stack.pop() not in ("li", "p", "td", "tr", "th", "option", "dd", "dt", "tbody", "thead"):
                            outer.errors += 1
                    outer.stack.pop()
                else:
                    outer.errors += 1
        self.p = P(convert_charrefs=True)

    def feed(self, t): self.p.feed(t)
    def close(self): self.p.close(); self.errors += sum(1 for t in self.stack if t not in ("li", "p", "html", "body", "td", "tr"))


def _check_py_imports(src: str, files: dict[str, bytes], spec: Spec):
    import ast
    tree = ast.parse(src)
    local = {Path(k).stem for k in files}
    for n in ast.walk(tree):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name.split(".")[0] for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                raise ValueError("relative import to a file not bundled")
            mods = [(n.module or "").split(".")[0]]
        for m in mods:
            if m and m not in STDLIB and m not in local and m not in spec.allow_imports:
                raise ValueError(f"unbundled third-party import '{m}'")


def py_deps(files: dict[str, bytes], spec: Spec) -> dict[str, str]:
    import ast
    deps = {}
    for k, b in files.items():
        if k.endswith(".py"):
            for n in ast.walk(ast.parse(b.decode("utf-8", "ignore"))):
                mods = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else \
                       [(n.module or "").split(".")[0]] if isinstance(n, ast.ImportFrom) and not n.level else []
                for m in mods:
                    if m in spec.allow_imports:
                        deps[spec.allow_imports[m]] = "pypi"
    return deps


_TS_HELPER = Path(__file__).with_name("ts_syntax.cjs")


def ts_syntax(cfg: Config, d: Path, files: list[str]) -> str:
    tsdir = cfg.home / "tools" / "node_modules" / "typescript"
    if not tsdir.exists():
        subprocess.run(["npm", "install", "--prefix", str(cfg.home / "tools"), "typescript@5.6.3", "--ignore-scripts",
                        "--no-audit", "--no-fund", "--silent"], check=True, capture_output=True, timeout=300)
    r = subprocess.run(["node", str(_TS_HELPER), str(tsdir), str(d), *files], capture_output=True, text=True, timeout=120)
    return r.stdout.strip() if r.returncode else ""


NPM_NAME = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]*/)?[a-z0-9-~][a-z0-9-._~]*$")


def js_deps(files: dict[str, bytes]) -> list[str]:
    out = set()
    for k, b in files.items():
        if k.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".vue", ".svelte")):
            t = b.decode("utf-8", "ignore")
            for m in re.finditer(r"""(?:from\s+|import\s+|require\()\s*['"]([^'"]+)['"]""", t):
                s = m.group(1)
                if s.startswith((".", "/", "@/", "~", "#", "node:")) or s in ("fs", "path", "os", "http", "https", "crypto", "util", "events", "stream", "url", "child_process", "zlib", "net", "buffer", "assert", "querystring", "readline", "tty", "dns", "string_decoder", "cluster", "module", "timers", "vm"):
                    continue
                parts = s.split("/")
                name = "/".join(parts[:2]) if s.startswith("@") else parts[0]
                if NPM_NAME.match(name):      # minified bundles contain strings like "+u+" that are not packages
                    out.add(name)
    return sorted(out)


def go_deps(files: dict[str, bytes]) -> list[str]:
    """Third-party module roots imported by the Go files (import blocks only)."""
    out = set()
    for k, b in files.items():
        if not k.endswith(".go"):
            continue
        t = b.decode("utf-8", "ignore")
        paths = []
        for blk in re.finditer(r"(?m)^import\s*\((.*?)^\)", t, re.S):
            paths += re.findall(r'"([^"]+)"', blk.group(1))
        paths += re.findall(r'(?m)^import\s+(?:\w+\s+)?"([^"]+)"', t)
        for p in paths:
            if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+/", p):
                out.add(module_root(p))
    return sorted(out)


def module_root(p: str) -> str:
    parts = p.split("/")
    if parts[0] in ("github.com", "gitlab.com", "bitbucket.org") and len(parts) >= 3:
        return "/".join(parts[:3])
    if parts[0] == "golang.org" and len(parts) >= 3:
        return "/".join(parts[:3])
    if parts[0] == "charm.land" and len(parts) >= 2:
        return "/".join(parts[:2])
    return "/".join(parts[:3])


# ------------------------------------------------------------- description
def default_describe(ctx: RepoCtx, rel: str, files: dict[str, bytes]) -> tuple[str, list[str]]:
    """Deterministic metadata from the content itself (no model)."""
    main = next((k for k in files if k.endswith((".html", ".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".svg", ".rs", ".vue"))), next(iter(files)))
    t = files[main].decode("utf-8", "ignore")
    desc, tags = "", []
    if main.endswith(".py"):
        import ast
        try:
            doc = ast.get_docstring(ast.parse(t))
            if doc: desc = doc.strip().split("\n\n")[0].replace("\n", " ")
        except SyntaxError:
            pass
    elif main.endswith((".html", ".htm")):
        m = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
        d = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', t, re.I)
        desc = " ".join(x.strip() for x in (m.group(1) if m else "", d.group(1) if d else "") if x)
        tags = sorted({w for w in ("dashboard", "pricing", "login", "blog", "portfolio", "landing", "admin", "shop", "form", "table", "chart", "calendar", "gallery", "contact", "resume", "sidebar", "navbar", "hero", "footer", "carousel", "modal", "invoice", "profile", "kanban", "chat", "mail", "faq", "404", "register") if w in t.lower()})
    elif main.endswith(".svg"):
        m = re.search(r"<title>(.*?)</title>", t)
        desc = m.group(1) if m else ""
    elif main.endswith((".go", ".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".rs", ".vue")):
        m = re.search(r"(?s)^\s*(?://\s?(.*?)\n(?!//)|/\*+\s*(.*?)\*/)", t)
        if m:
            desc = re.sub(r"\s*\n\s*\*?\s*", " ", (m.group(1) or m.group(2) or "")).strip()
        else:
            m = re.search(r"(?m)((?://[^\n]*\n)+)\s*package\s+\w+", t)
            if m: desc = re.sub(r"//\s?", "", m.group(1)).replace("\n", " ").strip()
    desc = re.sub(r"\s+", " ", re.sub(r"(?i)copyright.*|license.*|SPDX.*", "", desc)).strip()[:300]
    return desc, tags


def humanize(rel: str) -> str:
    stem = Path(rel).stem if "." in Path(rel).name else Path(rel).name
    return re.sub(r"[-_.]+", " ", stem).strip()


# --------------------------------------------------------------- runner
def run_spec(lib: Library, spec: Spec, ctx: RepoCtx, deps: DepLicenses, progress: Callable[[str], None] = print,
             stop: Callable[[], bool] = lambda: False) -> dict:
    cfg = lib.cfg
    res = {"admitted": 0, "rejected": 0, "seen": 0, "skipped": 0, "interrupted": False}
    lic_text, lic_url, lic_spdx = ctx.license_text, ctx.license_url, ctx.spdx
    if spec.license_path:
        p = ctx.path / spec.license_path
        lic_text = p.read_text(errors="ignore")
        lic_url = f"{ctx.url}/blob/{ctx.sha}/{spec.license_path}"
        lic_spdx = licenses.detect(lic_text)
    support = {s: (ctx.path / s).read_bytes() for s in spec.support if (ctx.path / s).exists()}
    skipped = 0
    for rel, files in iter_units(spec, ctx):
        if res["admitted"] >= spec.cap:
            break
        if stop():
            res["interrupted"] = True
            break
        if skipped < spec.skip:
            skipped += 1; continue
        res["seen"] += 1
        if (res["seen"] - 1) % spec.stride:
            continue
        files = {**files}
        try:
            size = sum(len(b) for b in files.values())
            if size < spec.min_bytes or size > spec.max_bytes:
                raise Rejected("size out of range")
            if spec.unit == "html":
                files = bundle_html(ctx, rel, files)
            if spec.pair_tests or spec.tests:
                files.update(find_tests(ctx, rel, spec))
            files.update({k: v for k, v in support.items() if k not in files})
            from .library import content_hash
            if lib.con.execute("SELECT 1 FROM assets WHERE content_hash=?", (content_hash(files),)).fetchone():
                res["skipped"] += 1          # already admitted: skip checks/deps/embedding entirely
                continue
            describe = spec.describe or default_describe
            desc, tags = describe(ctx, rel, files)
            name = spec.name_fn(ctx, rel) if spec.name_fn else f"{spec.prefix}{humanize(rel)}".strip()
            c = Candidate(
                name=name, category=spec.category, kind=spec.kind,
                description=desc or f"{spec.kind} from {ctx.slug}: {humanize(rel)}",
                source_url=f"{ctx.url}/blob/{ctx.sha}/{rel}", repo=ctx.slug, commit_sha=ctx.sha, files=files,
                license_text=lic_text, license_url=lic_url, declared_license=lic_spdx, copyright=ctx.copyright,
                provenance="human", frameworks=list(spec.frameworks), languages=list(spec.languages),
                tags=sorted(set(spec.tags + tags)), difficulty=spec.difficulty)
            c.checks = run_checks(cfg, spec, files)
            c.dependencies = resolve_deps(spec, files, deps)
            lib.admit(c)
            res["admitted"] += 1
        except Rejected as e:
            res["rejected"] += 1
            if not e.reason.startswith("duplicate (identical"):
                lib.count_reject(e.reason)
        except Exception as e:  # noqa: BLE001
            res["rejected"] += 1
            lib.count_reject(f"error {type(e).__name__}")
        if res["seen"] % 50 == 0:
            progress(f"  {spec.id}: seen {res['seen']} admitted {res['admitted']}")
    return res


def resolve_deps(spec: Spec, files: dict[str, bytes], deps: DepLicenses) -> dict[str, str]:
    out: dict[str, str] = {}
    langs = set(spec.languages)
    if "python" in langs:
        for d in py_deps(files, spec):
            lic = deps.pypi(d)
            out[d] = lic or "UNKNOWN"
    if langs & {"typescript", "javascript"}:
        for d in js_deps(files):
            if d in spec.allow_imports:  # explicitly-bundled aliases
                continue
            lic = deps.npm(d)
            out[d] = lic or "UNKNOWN"
    for d in spec.npm_deps:
        out[d] = deps.npm(d) or "UNKNOWN"
    if "rust" in langs:
        import tomllib
        for k, b in files.items():
            if k.endswith("Cargo.toml"):
                try:
                    t = tomllib.loads(b.decode())
                except tomllib.TOMLDecodeError:
                    continue
                for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
                    for name, v in t.get(sect, {}).items():
                        if isinstance(v, dict) and "path" in v:
                            continue
                        out[name] = deps.crate(name) or "UNKNOWN"
    if "go" in langs:
        for d in go_deps(files):
            lic = deps.go(d)
            out[d] = lic or "UNKNOWN"
    return out


def find_tests(ctx: RepoCtx, rel: str, spec: Spec) -> dict[str, bytes]:
    p = ctx.path / rel
    base = ctx.path / spec.root
    pats = spec.tests or ["{dir}/test_{stem}{ext}", "{dir}/{stem}_test{ext}", "{dir}/tests/test_{stem}{ext}",
                          "{dir}/../tests/test_{stem}{ext}", "tests/test_{stem}{ext}"]
    out = {}
    for pat in pats:
        c = (p.parent / pat.format(dir=".", stem=p.stem, ext=p.suffix)) if pat.startswith("{dir}") else \
            (ctx.path / pat.format(dir=".", stem=p.stem, ext=p.suffix))
        c = c.resolve()
        if c.is_file() and ctx.path.resolve() in c.parents:
            key = c.relative_to(base.resolve()).as_posix() if spec.tree else c.name
            out[key] = c.read_bytes()
    if spec.tree:
        init = p.parent / "__init__.py"
        if init.exists():
            out[init.relative_to(base).as_posix()] = init.read_bytes()
    return out


def bundle_html(ctx: RepoCtx, rel: str, files: dict[str, bytes]) -> dict[str, bytes]:
    """HTML page + the local css/js/img it references, laid out relative to their common ancestor
    so ../css/x.css still resolves inside the bundle."""
    import os
    page = ctx.path / rel
    html = page.read_text(errors="ignore")
    refs: dict[str, Path] = {}
    total = page.stat().st_size
    for m in re.finditer(r"""(?:href|src)=["']([^"'#?:]+?\.(?:css|js|png|jpg|jpeg|svg|webp|gif|ico|woff2?))["']""", html, re.I):
        ref = m.group(1)
        if ref.startswith(("//", "/", "http")):
            continue
        f = (page.parent / ref).resolve()
        if ctx.path.resolve() not in f.parents or not f.is_file() or f in refs.values():
            continue
        if f.suffix.lower() == ".js" and f.stat().st_size > 400_000:
            continue
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif") and f.stat().st_size > 300_000:
            continue
        total += f.stat().st_size
        if len(refs) > 40 or total > 2_500_000:
            raise Rejected("template bundle too large")
        refs[ref] = f
    paths = [page.resolve(), *refs.values()]
    root = Path(os.path.commonpath([str(p.parent) for p in paths]))
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in paths}
