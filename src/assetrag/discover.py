"""Automatic discovery of whole-repo assets (small, tested libraries) via GitHub search.

GitHub's own licence field is only a *search hint*; admission still requires the
repository's LICENSE text to fingerprint to an allowed SPDX id (see licenses.detect).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from . import foundation as _foundation
from . import ingest
from .db import jdump, now
from .ingest import DepLicenses, RepoCtx, Spec, run_checks
from .library import Candidate, Library, Rejected

LICS = ["mit", "apache-2.0", "bsd-3-clause", "bsd-2-clause", "isc"]
KINDS = {
    "go-backend": dict(category="backend", language="go", checks=["gofmt", "gotest"], topics=[
        "middleware", "rate-limiter", "jwt", "cache", "retry", "validation", "logging", "router", "cors", "session",
        "queue", "scheduler", "cron", "config", "uuid", "pagination", "websocket", "circuit-breaker", "backoff",
        "encryption", "password-hash", "sql", "orm", "migrations", "health-check", "metrics", "tracing", "pubsub",
        "worker-pool", "csrf", "http-client", "slug", "email", "template-engine", "markdown", "json", "yaml", "toml",
        "env", "feature-flags", "distributed-lock", "bloom-filter", "lru", "ratelimit", "sanitizer", "i18n",
        "graceful-shutdown", "dependency-injection", "event-bus", "state-machine", "tokenizer", "oauth2", "totp"]),
    "py-backend": dict(category="backend", language="python", checks=["py", "pytest"], topics=[
        "middleware", "rate-limiting", "jwt", "cache", "retry", "validation", "logging", "router", "cors", "session",
        "queue", "scheduler", "cron", "config", "uuid", "pagination", "websocket", "circuit-breaker", "backoff",
        "password-hashing", "orm", "migrations", "health-check", "metrics", "pubsub", "csrf", "http-client", "slugify",
        "email", "template-engine", "markdown", "dotenv", "feature-flags", "lru-cache", "sanitize", "i18n",
        "dependency-injection", "event-bus", "state-machine", "tokenizer", "oauth", "totp", "serialization", "diff"]),
    "go-cli": dict(category="cli", language="go", checks=["gofmt", "gotest"], topics=[
        "cli", "tui", "terminal", "progress-bar", "spinner", "prompt", "table", "colors", "command-line", "flags",
        "autocomplete", "pager", "ansi", "terminal-ui", "wizard", "emoji", "markdown-renderer", "tree", "diff-viewer"]),
    "py-cli": dict(category="cli", language="python", checks=["py", "pytest"], topics=[
        "cli", "tui", "terminal", "progress-bar", "spinner", "prompt", "table", "colors", "command-line", "argparse",
        "autocomplete", "ansi", "terminal-ui", "wizard", "tree", "pager"]),
    "py-algo": dict(category="algorithm", language="python", checks=["py", "pytest"], topics=[
        "algorithms", "data-structures", "sorting", "graph", "trie", "bloom-filter", "fuzzy-search", "levenshtein",
        "compression", "hashing", "priority-queue", "interval-tree", "skiplist"]),
    "go-algo": dict(category="algorithm", language="go", checks=["gofmt", "gotest"], topics=[
        "algorithms", "data-structures", "sorting", "graph", "trie", "bloom-filter", "fuzzy-search", "levenshtein",
        "compression", "hashing", "priority-queue", "interval-tree", "skiplist", "consistent-hashing"]),
}
MAX_BYTES = 2_800_000
KINDS["go-foundation"] = _foundation.KIND

# Hand-picked, widely used, permissively licensed libraries per kind. They still go through the full gate
# (licence fingerprint, dependency licences, ClamAV, sandboxed tests); this list only says where to look first.
CURATED = {
    "go-backend": ["rs/cors", "rs/zerolog", "patrickmn/go-cache", "sethvargo/go-retry", "cenkalti/backoff", "golang-jwt/jwt",
                   "google/uuid", "julienschmidt/httprouter", "gorilla/mux", "gorilla/handlers", "gorilla/websocket", "gorilla/sessions",
                   "uber-go/ratelimit", "hashicorp/golang-lru", "sony/gobreaker", "eapache/go-resiliency", "robfig/cron",
                   "kelseyhightower/envconfig", "joho/godotenv", "caarlos0/env", "oklog/ulid", "segmentio/ksuid", "rs/xid",
                   "tidwall/gjson", "allegro/bigcache", "coocood/freecache", "mitchellh/mapstructure", "samber/lo",
                   "sourcegraph/conc", "go-playground/validator", "didip/tollbooth", "go-chi/chi", "go-chi/cors", "go-chi/httprate",
                   "justinas/alice", "ajg/form", "gosimple/slug", "avelino/slugify", "pquerna/otp", "cristalhq/jwt", "alexedwards/argon2id",
                   "benbjohnson/clock", "jonboulle/clockwork", "stretchr/testify", "matryer/is", "go-co-op/gocron"],
    "py-backend": ["tkem/cachetools", "jd/tenacity", "litl/backoff", "pallets/itsdangerous", "more-itertools/more-itertools",
                   "pytoolz/toolz", "python-attrs/attrs", "grantjenks/python-sortedcontainers", "dbader/schedule", "un33k/python-slugify",
                   "hukkin/tomli", "madzak/python-json-logger", "theskumar/python-dotenv", "tomasbasham/ratelimit", "kvesteri/validators",
                   "marshmallow-code/marshmallow", "mahmoud/glom", "pydantic/pydantic-settings", "python-jsonschema/jsonschema",
                   "ActiveState/appdirs", "platformdirs/platformdirs", "jazzband/python-slugify", "ionelmc/python-fields",
                   "ahupp/python-magic", "jaraco/keyring", "pyca/bcrypt", "mpdavis/python-jose", "jpadilla/pyjwt", "kennethreitz/records",
                   "Pylons/webob", "pallets/markupsafe", "pallets/click", "hynek/structlog", "hynek/stamina", "lepture/mistune",
                   "aio-libs/aiocache", "aio-libs/frozenlist", "agronholm/anyio", "encode/httpx", "encode/starlette", "encode/uvicorn"],
    "go-cli": ["spf13/pflag", "urfave/cli", "alecthomas/kong", "fatih/color", "mattn/go-isatty", "muesli/termenv", "olekukonko/tablewriter",
               "schollz/progressbar", "briandowns/spinner", "manifoldco/promptui", "charmbracelet/lipgloss", "charmbracelet/bubbles",
               "charmbracelet/glamour", "charmbracelet/huh", "jedib0t/go-pretty", "mattn/go-runewidth", "rivo/uniseg", "AlecAivazis/survey",
               "cheggaaa/pb", "gookit/color", "pterm/pterm", "jesseduffield/gocui", "rivo/tview", "gdamore/tcell", "mitchellh/cli",
               "peterbourgon/ff", "knadh/koanf", "dustin/go-humanize", "muesli/reflow", "acarl005/stripansi", "k0kubun/pp"],
    "py-cli": ["prompt-toolkit/python-prompt-toolkit", "tqdm/tqdm", "pyfiglet/pyfiglet", "tartley/colorama", "astanin/python-tabulate",
               "jazzband/prettytable", "tmbo/questionary", "pyinvoke/invoke", "docopt/docopt", "jonathanslenders/pymux", "willmcgugan/rich-cli",
               "Textualize/rich", "Textualize/textual", "ppannuto/python-titlecase", "jmcnamara/XlsxWriter", "Rapptz/discord.py",
               "tiangolo/typer", "pallets/click", "ewels/rich-click", "Delgan/loguru", "kvesteri/sqlalchemy-utils", "sdispater/cleo",
               "jaraco/path", "Pylons/venusian", "theacodes/nox", "jonathaneunice/ansiwrap", "kennethreitz/delegator.py"],
    "py-algo": ["keon/algorithms", "grantjenks/python-sortedcontainers", "TheAlgorithms/Python", "eliben/pycparser", "ekzhu/datasketch",
                "jaraco/more-itertools", "seatgeek/thefuzz", "life4/textdistance", "rapidfuzz/RapidFuzz", "ahupp/bloom", "pytries/marisa-trie",
                "scrapinghub/python-crfsuite", "jtauber/rope", "joshkunz/ctypes-skiplist", "networkx/networkx", "scipy/scipy"],
    "go-algo": ["emirpasic/gods", "gammazero/deque", "agnivade/levenshtein", "sahilm/fuzzy", "lithammer/fuzzysearch", "tidwall/btree",
                "huandu/skiplist", "bits-and-blooms/bloom", "bits-and-blooms/bitset", "yourbasic/graph", "zyedidia/generic", "hashicorp/go-immutable-radix",
                "google/btree", "petar/GoLLRB", "ugorji/go", "armon/go-radix", "dgryski/go-farm", "cespare/xxhash", "spaolacci/murmur3",
                "twmb/murmur3", "minio/highwayhash", "klauspost/compress", "golang/snappy", "pierrec/lz4", "mmcloughlin/geohash"],
}

CURATED["go-foundation"] = _foundation.GO_FOUNDATION


def gh_search(q: str, page: int = 1) -> list[dict]:
    r = subprocess.run(["gh", "api", "-X", "GET", "search/repositories", "-f", f"q={q}", "-f", "per_page=50",
                        "-f", f"page={page}", "-f", "sort=stars"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        if "rate limit" in r.stderr.lower():
            time.sleep(60)
        return []
    return json.loads(r.stdout).get("items", [])


def repo_files(root: Path) -> dict[str, bytes] | None:
    out, total = {}, 0
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or p.is_symlink() or ".git" in rel.parts or ".github" in rel.parts or "vendor" in rel.parts \
                or "node_modules" in rel.parts or "docs" in rel.parts:
            continue
        sz = p.stat().st_size
        if sz > 400_000:
            continue
        total += sz
        if total > MAX_BYTES:
            return None
        out[rel.as_posix()] = p.read_bytes()
    return out


def readme_summary(root: Path) -> str:
    for n in ("README.md", "readme.md", "README.rst", "README"):
        p = root / n
        if p.exists():
            t = re.sub(r"```.*?```|<[^>]+>|!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", p.read_text(errors="ignore"), flags=re.S)
            paras = [re.sub(r"\s+", " ", x).strip(" #*_>-") for x in re.split(r"\n\s*\n", t)]
            paras = [x for x in paras if len(x) > 40 and not x.lower().startswith(("build status", "[!["))]
            if paras:
                return paras[0][:320]
    return ""


def go_modfile_deps(files: dict[str, bytes]) -> list[str]:
    gm = files.get("go.mod", b"").decode("utf-8", "ignore")
    return [m.group(1) for m in re.finditer(r"^\s*(?:require\s+)?([\w.-]+\.[\w.-]+/[^\s]+)\s+v[^\s]+\s*$", gm, re.M)]


def prefetch_go(cfg, ctx: RepoCtx) -> tuple[bool, str]:
    """Download module sources (no code execution) into a shared cache the sandbox mounts read-only."""
    gp = cfg.home / "gopath"
    env = {**__import__("os").environ, "GOPATH": str(gp), "GOFLAGS": "-mod=mod", "GOTOOLCHAIN": "local"}
    r = subprocess.run(["go", "mod", "download"], cwd=ctx.path, env=env, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, r.stderr[-200:]


def run_candidate_checks(cfg, kind: dict, ctx: RepoCtx, files: dict[str, bytes]) -> dict[str, str]:
    from . import sandbox
    res = {}
    lang = kind["language"]
    if lang == "go":
        if "go.mod" not in files:
            return {"gotest": "fail: no go.mod"}
        res.update(run_checks(cfg, Spec(id="d", repo="", category="x", kind="x", include=[], checks=["gofmt"]), files))
        ok, err = prefetch_go(cfg, ctx)
        if not ok:
            res["go-mod-download"] = "fail: " + err
            return res
        d = ingest._tmpdir(cfg, files)
        try:
            code, out = sandbox.run(["go", "test", "./...", "-count=1", "-short"], d, 300,
                                    env={"GOCACHE": "/tmp/gc", "GOPATH": "/tmp/gp", "GOMODCACHE": "/gomod", "GOFLAGS": "-mod=mod",
                                         "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "0"},
                                    binds=[(str(cfg.home / "gopath" / "pkg" / "mod"), "/gomod")])
        finally:
            subprocess.run(["rm", "-rf", str(d)])
        if code != 0:
            res["go-test"] = "fail: " + out[-160:].replace("\n", " ")
        elif "no test files" in out and "ok " not in out:
            res["go-test"] = "fail: no tests"
        else:
            oks = len(re.findall(r"^ok\s", out, re.M))
            res["go-test"] = f"pass: {oks} packages ok"
        return res
    spec = Spec(id="d", repo="", category="x", kind="x", include=[], checks=["py", "pytest"], tree=True,
                pip_path=["src"])
    return run_checks(cfg, spec, files)


def py_third_party(files: dict[str, bytes]) -> dict[str, str]:
    import ast, sys
    local = {Path(k).parts[0].replace(".py", "") for k in files} | {Path(k).stem for k in files}
    mods = set()
    for k, b in files.items():
        if not k.endswith(".py") or re.search(r"(^|/)(tests?|docs?|examples?|benchmarks?)/|test_|_test\.py|conftest|setup\.py", k):
            continue
        try:
            tree = ast.parse(b.decode("utf-8", "ignore"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            m = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else \
                [(n.module or "").split(".")[0]] if isinstance(n, ast.ImportFrom) and not n.level else []
            mods |= {x for x in m if x and x not in sys.stdlib_module_names and x not in local}
    return {m: "pypi" for m in mods}


def discover(lib: Library, kind: str, limit: int = 30, progress=print, stop=lambda: False) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    k = KINDS[kind]
    dl = DepLicenses(lib.cfg)
    seen = {r["key"] for r in lib.con.execute("SELECT key FROM ingest_jobs WHERE source=?", (f"discover:{kind}",))}
    stats = {"admitted": 0, "rejected": 0, "tried": 0}
    todo = [u for u in (f"https://github.com/{r}" for r in CURATED.get(kind, [])) if u not in seen]
    for i in range(0, len(todo), 8):
        if stop():
            return stats
        ctxs = []
        for url in todo[i:i + 8]:
            if stop():
                return stats
            try:
                ctxs.append(({"html_url": url, "name": url.rsplit("/", 1)[1], "description": "", "topics": []}, ingest.clone(lib.cfg, url)))
            except Exception:  # noqa: BLE001
                seen.add(url)
                lib.count_reject("clone failed")
                lib.con.execute("INSERT OR REPLACE INTO ingest_jobs VALUES (?,?,?,1,'{}',?)", (url, f"discover:{kind}", "failed", now()))
                lib.con.commit()
        if ctxs:
            infected = ingest.scan_repos([c.path for _, c in ctxs], lib.cfg.home / "clamav-ingest.log")
            _process(lib, kind, k, dl, ctxs, infected, seen, stats, 10**9, progress, stop)
        progress(f"  curated {kind}: {min(i + 8, len(todo))}/{len(todo)} processed, admitted {stats['admitted']}")
    for topic in k["topics"]:
        for lic in LICS:
            if stats["admitted"] >= limit or stop():
                return stats
            q = f"topic:{topic} language:{k['language']} license:{lic} stars:>=30 size:<4000 archived:false fork:false"
            items = gh_search(q)
            time.sleep(2.2)  # search API: 30 req/min
            cand = [i for i in items if i["html_url"] not in seen and re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", i["html_url"])]
            if not cand:
                continue
            ctxs = []
            for it in cand[:12]:
                if stop():
                    break
                try:
                    ctxs.append((it, ingest.clone(lib.cfg, it["html_url"])))
                except Exception as e:  # noqa: BLE001
                    seen.add(it["html_url"])
                    lib.count_reject("clone failed")
            if not ctxs:
                continue
            infected = ingest.scan_repos([c.path for _, c in ctxs], lib.cfg.home / "clamav-ingest.log")
            _process(lib, kind, k, dl, ctxs, infected, seen, stats, limit, progress, stop)
            if stats["admitted"] >= limit or stop():
                return stats
    return stats


def _process(lib: Library, kind: str, k: dict, dl: DepLicenses, ctxs, infected, seen: set, stats: dict, limit: int, progress, stop):
    """Vet and admit each cloned candidate (shared by search-based discovery and curated repo lists)."""
    for it, ctx in ctxs:
        if stop():
            return stats          # graceful stop: unfinished candidates are retried next run (not recorded as seen)
        url = it["html_url"]
        seen.add(url)
        stats["tried"] += 1
        job_state = "done"
        try:
            if ctx.path in infected:
                raise Rejected("ClamAV detection")
            files = repo_files(ctx.path)
            if not files:
                raise Rejected("asset too large (>3MB)")
            if not ctx.spdx:
                raise Rejected("license text missing or not verifiable")
            checks = run_candidate_checks(lib.cfg, k, ctx, files)
            deps: dict[str, str] = {}
            if k["language"] == "go":
                own = re.search(r"^module\s+(\S+)", files.get("go.mod", b"").decode("utf-8", "ignore"), re.M)
                for d in go_modfile_deps(files):
                    if own and d.startswith(own.group(1)):
                        continue
                    deps[d] = dl.go(d) or "UNKNOWN"
            else:
                for d in py_third_party(files):
                    deps[d] = dl.pypi(d) or "UNKNOWN"
            tags = [t for t in it.get("topics", [])][:12] + [k["language"]]
            desc = (it.get("description") or "").strip() or readme_summary(ctx.path)
            c = Candidate(name=it["name"], category=k["category"], kind="library", description=(desc + " " + readme_summary(ctx.path))[:400],
                          source_url=f"{url}/tree/{ctx.sha}", repo=ctx.slug, commit_sha=ctx.sha, files=files,
                          license_text=ctx.license_text, license_url=ctx.license_url, declared_license=ctx.spdx,
                          copyright=ctx.copyright, provenance="human", languages=[k["language"]], dependencies=deps,
                          tags=tags, checks=checks, difficulty=2)
            lib.admit(c)
            stats["admitted"] += 1
            progress(f"  + {ctx.slug} [{kind}] {ctx.spdx} ({stats['admitted']}/{limit})")
        except Rejected as e:
            stats["rejected"] += 1
            lib.count_reject(e.reason)
        except Exception as e:  # noqa: BLE001
            stats["rejected"] += 1
            job_state = "failed"
            lib.count_reject(f"error {type(e).__name__}")
        lib.con.execute("INSERT OR REPLACE INTO ingest_jobs VALUES (?,?,?,1,'{}',?)", (url, f"discover:{kind}", job_state, now()))
        lib.con.commit()
        # keep disk bounded: failed/rejected clones are dropped
        if stats["admitted"] >= limit:
            break
