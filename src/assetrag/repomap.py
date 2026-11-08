"""Repo map: a ranked, token-budgeted outline of a codebase (files -> classes/functions with signatures and line numbers).

Idea from Aider's repo map (Apache-2.0), written from scratch: parse every source file for its definitions, link each file to the files that
define the names it mentions, rank files with personalised PageRank, then print as many of the best files as fit a token budget.

Python is parsed with the stdlib `ast` (no dependency). Other languages use tree-sitter grammars, which are an optional extra
(`pip install 'assetrag[repomap]'`); a language without an installed grammar is counted in the footer instead of silently dropped.
Pure filesystem work: never touches the asset library, never uses the network.
"""
from __future__ import annotations

import ast
import importlib
import math
import os
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILE_BYTES = 400_000
MAX_FILES = 6000
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "third_party", "target", "dist", "build", "out", "__pycache__", ".venv", "venv", "env",
             ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".cache", "coverage", ".idea", ".vscode", "site-packages", "bower_components"}
MANIFESTS = ["pyproject.toml", "setup.py", "package.json", "tsconfig.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "CMakeLists.txt", "Makefile",
             "justfile", "Dockerfile", "docker-compose.yml", "compose.yaml", "README.md", "CLAUDE.md", "AGENTS.md"]
STOP_NAMES = {"main", "init", "new", "get", "set", "run", "test", "setup", "default", "self", "cls", "args", "kwargs", "name", "value", "data", "result",
              "error", "close", "open", "read", "write", "load", "save", "start", "stop", "update", "delete", "create", "build", "parse", "handle", "str", "int"}
TEST_RX = re.compile(r"(^|/)(tests?|__tests__|spec|specs|e2e)/|(^|/)test_[^/]*$|_test\.[a-z]+$|\.(test|spec)\.[a-z]+$|(^|/)conftest\.py$")
IDENT_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
SIG_MAX = 110
_GETTER_RX = re.compile(r"^(@property |@cached_property |static )?(get \w+ ?\(\)|def \w+\(self\)$|def \w+\(self\) ->)|^@(cached_)?property ")
DECORATOR_RX = re.compile(r"(^|\.)(route|get|post|put|delete|patch|tool|command|group|task|fixture|property|cached_property|staticmethod|classmethod|dataclass|abstractmethod)\b")


@dataclass
class Sym:
    name: str
    kind: str
    line: int
    sig: str
    depth: int = 0


@dataclass
class FileInfo:
    path: str
    lang: str
    syms: list = field(default_factory=list)
    refs: Counter = field(default_factory=Counter)
    is_test: bool = False
    rank: float = 0.0
    lines: int = 0


# --------------------------------------------------------------------------------------------- language specs
@dataclass
class Spec:
    module: str                                  # python module exposing the grammar
    loader: str                                  # function returning the language capsule
    defs: dict                                   # node type -> kind
    containers: frozenset = frozenset()          # defs whose body holds nested defs (methods)
    transparent: frozenset = frozenset()         # nodes descended into without emitting (export wrappers, bodies)
    fn_values: frozenset = frozenset()           # for `const f = () => {}` style definitions


_JS_DEFS = {"function_declaration": "function", "generator_function_declaration": "function", "class_declaration": "class", "abstract_class_declaration": "class",
            "method_definition": "method", "interface_declaration": "interface", "type_alias_declaration": "type", "enum_declaration": "enum",
            "abstract_method_signature": "method"}
_JS_T = frozenset({"export_statement", "class_body", "program", "ambient_declaration", "internal_module", "module", "statement_block"})
_JS_FN = frozenset({"arrow_function", "function_expression", "function", "generator_function"})

SPECS = {
    "javascript": Spec("tree_sitter_javascript", "language", _JS_DEFS, frozenset({"class_declaration"}), _JS_T, _JS_FN),
    "typescript": Spec("tree_sitter_typescript", "language_typescript", _JS_DEFS, frozenset({"class_declaration", "abstract_class_declaration"}), _JS_T, _JS_FN),
    "tsx": Spec("tree_sitter_typescript", "language_tsx", _JS_DEFS, frozenset({"class_declaration", "abstract_class_declaration"}), _JS_T, _JS_FN),
    "go": Spec("tree_sitter_go", "language", {"function_declaration": "func", "method_declaration": "method", "type_spec": "type"},
                transparent=frozenset({"type_declaration"})),
    "rust": Spec("tree_sitter_rust", "language", {"function_item": "fn", "struct_item": "struct", "enum_item": "enum", "trait_item": "trait", "impl_item": "impl",
                                                    "mod_item": "mod", "type_item": "type", "function_signature_item": "fn", "union_item": "union"},
                 frozenset({"impl_item", "trait_item", "mod_item"}), frozenset({"declaration_list"})),
    "java": Spec("tree_sitter_java", "language", {"class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum", "record_declaration": "record",
                                                   "method_declaration": "method", "constructor_declaration": "ctor"},
                 frozenset({"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}), frozenset({"class_body", "interface_body", "enum_body", "enum_body_declarations"})),
    "c": Spec("tree_sitter_c", "language", {"function_definition": "func", "struct_specifier": "struct", "enum_specifier": "enum", "union_specifier": "union"},
              transparent=frozenset({"type_definition", "preproc_ifdef", "preproc_if", "preproc_else", "declaration_list", "linkage_specification"})),
    "bash": Spec("tree_sitter_bash", "language", {"function_definition": "func"}),
}
EXT = {".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript", ".mts": "typescript",
       ".cts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".h": "c", ".sh": "bash", ".bash": "bash"}
EXT_NO_GRAMMAR_HINT = {".cpp", ".cc", ".cxx", ".hpp", ".rb", ".php", ".swift", ".kt", ".cs", ".lua", ".dart", ".scala", ".zig"}   # counted in the footer, not parsed

_parsers: dict = {}


def _parser(lang: str):
    """tree-sitter Parser for `lang`, or None when tree-sitter / the grammar is not installed."""
    if lang in _parsers:
        return _parsers[lang]
    p = None
    try:
        import tree_sitter as ts
        spec = SPECS[lang]
        p = ts.Parser(ts.Language(getattr(importlib.import_module(spec.module), spec.loader)()))
    except Exception:  # noqa: BLE001 - missing package, ABI mismatch: treated the same way (no parser)
        p = None
    _parsers[lang] = p
    return p


# ----------------------------------------------------------------------------------------------- extraction
def _clip(s: str, n: int = SIG_MAX) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _py_symbols(tree: ast.Module) -> list[Sym]:
    out: list[Sym] = []

    def deco(node) -> str:
        for d in node.decorator_list:
            t = ast.unparse(d)
            if DECORATOR_RX.search(t.split("(")[0]) and len(t) <= 40:
                return "@" + t + " "
        return ""

    def fn_sig(n, prefix: str) -> str:
        ret = (" -> " + ast.unparse(n.returns)) if n.returns else ""
        return _clip(f"{prefix}{'async ' if isinstance(n, ast.AsyncFunctionDef) else ''}def {n.name}({ast.unparse(n.args)}){ret}")

    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in n.bases)
            out.append(Sym(n.name, "class", n.lineno, _clip(f"{deco(n)}class {n.name}" + (f"({bases})" if bases else "")), 0))
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append(Sym(m.name, "method", m.lineno, fn_sig(m, deco(m)), 1))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(Sym(n.name, "function", n.lineno, fn_sig(n, deco(n)), 0))
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id.isupper() and len(n.targets[0].id) > 2:
            out.append(Sym(n.targets[0].id, "const", n.lineno, n.targets[0].id, 0))
    return out


def _decl_name(node) -> str | None:
    """Name of a definition node across grammars (C declarators and Rust impl headers need special handling)."""
    t = node.type
    if t == "impl_item":
        ty, tr = node.child_by_field_name("type"), node.child_by_field_name("trait")
        if ty is None:
            return None
        return (tr.text.decode() + " for " if tr is not None else "") + ty.text.decode()
    if t == "function_definition" and node.child_by_field_name("declarator") is not None:   # C
        d = node.child_by_field_name("declarator")
        while d is not None and d.type != "identifier":
            d = d.child_by_field_name("declarator") or (d.named_children[0] if d.named_children else None)
        return d.text.decode() if d is not None else None
    n = node.child_by_field_name("name")
    return n.text.decode() if n is not None else None


def _ts_sig(node, name: str, lang: str, src: bytes) -> str:
    if lang == "go" and node.type == "type_spec":
        ty = node.child_by_field_name("type")
        what = {"struct_type": "struct", "interface_type": "interface"}.get(ty.type if ty is not None else "", ty.text.decode() if ty is not None else "")
        return _clip(f"type {name} {what}")
    body = node.child_by_field_name("body")
    end = body.start_byte if body is not None else None
    text = src[node.start_byte:end].decode("utf8", "replace") if end else src[node.start_byte:node.end_byte].decode("utf8", "replace").split("\n", 1)[0]
    return _clip(text.rstrip().rstrip("{").rstrip().removesuffix("=>").rstrip().removesuffix("="))


def _ts_symbols(root, lang: str, src: bytes) -> list[Sym]:
    spec = SPECS[lang]
    out: list[Sym] = []

    def visit(node, depth: int):
        for ch in node.named_children:
            t = ch.type
            if t in spec.transparent:
                visit(ch, depth)
            elif t in spec.defs:
                if t == "mod_item" and (ch.child_by_field_name("body") is None or "test" in (_decl_name(ch) or "")):
                    continue                                        # `mod x;` is a file link, `mod tests` is noise
                name = _decl_name(ch)
                if name:
                    out.append(Sym(name, spec.defs[t], ch.start_point[0] + 1, _ts_sig(ch, name, lang, src), depth))
                    if t in spec.containers:
                        visit(ch, depth + 1)
            elif t in ("lexical_declaration", "variable_declaration") and spec.fn_values and depth == 0:
                for d in ch.named_children:
                    v = d.child_by_field_name("value") if d.type == "variable_declarator" else None
                    nm = d.child_by_field_name("name") if v is not None else None
                    if v is not None and nm is not None and v.type in spec.fn_values and nm.type == "identifier":
                        vb = v.child_by_field_name("body")
                        head = src[ch.start_byte:(vb.start_byte if vb is not None else ch.end_byte)].decode("utf8", "replace")
                        out.append(Sym(nm.text.decode(), "function", ch.start_point[0] + 1, _clip(head.rstrip().removesuffix("=>").rstrip()), depth))
    visit(root, 0)
    return out


def parse_file(rel: str, text: str) -> tuple[FileInfo | None, str | None]:
    """-> (FileInfo, None) or (None, reason it was skipped)."""
    ext = os.path.splitext(rel)[1].lower()
    lang = EXT.get(ext)
    if lang is None:
        return None, ("nogrammar:" + ext) if ext in EXT_NO_GRAMMAR_HINT else None
    if len(text) > 2000 and len(text) / (text.count("\n") + 1) > 300:
        return None, "minified"
    info = FileInfo(rel, lang, is_test=bool(TEST_RX.search(rel)), lines=text.count("\n") + 1)
    if lang == "python":
        try:
            tree = ast.parse(text)
            info.syms = _py_symbols(tree)
        except (SyntaxError, ValueError, RecursionError):
            return None, "syntax:" + rel
        # names actually used in code: strings and comments (recipes, docs, prompts) must not create dependency edges
        info.refs = Counter(n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.name
                            for n in ast.walk(tree) if isinstance(n, (ast.Name, ast.Attribute, ast.alias)) and len(getattr(n, "id", getattr(n, "attr", getattr(n, "name", ""))) or "") > 2)
        return info, None
    else:
        p = _parser(lang)
        if p is None:
            return None, "nogrammar:" + ext
        src = text.encode("utf8", "replace")
        info.syms = _ts_symbols(p.parse(src).root_node, lang, src)
    info.refs = Counter(w for w in IDENT_RX.findall(text))
    return info, None


# ------------------------------------------------------------------------------------------------ discovery
def list_files(root: Path) -> list[str]:
    """Relative paths of candidate source files. `git ls-files` (honours .gitignore) when possible, a pruned walk otherwise."""
    rels: list[str] = []
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"], capture_output=True, timeout=20)
        if r.returncode == 0 and r.stdout:
            rels = [p for p in r.stdout.decode("utf8", "replace").split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        pass
    if not rels:
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith(".git"))
            rels += [os.path.relpath(os.path.join(dp, f), root) for f in sorted(fns)]
    keep = []
    for p in rels:
        parts = p.split("/")
        if any(x in SKIP_DIRS for x in parts[:-1]):
            continue
        ext = os.path.splitext(p)[1].lower()
        if (ext in EXT or ext in EXT_NO_GRAMMAR_HINT) and not p.endswith((".min.js", ".d.ts", ".pb.go", "_pb2.py", ".generated.ts")):
            keep.append(p)
    return keep


def changed_files(root: Path) -> list[str]:
    try:
        r = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z"], capture_output=True, timeout=20)
        if r.returncode != 0:
            return []
        out = []
        for ent in r.stdout.decode("utf8", "replace").split("\0"):
            if len(ent) > 3 and ent[:2].strip() and "D" not in ent[:2]:
                out.append(ent[3:])
        return out
    except (OSError, subprocess.SubprocessError):
        return []


# ---------------------------------------------------------------------------------------------- ranking
def _weight(name: str, n_definers: int) -> float:
    w = 1.0
    if name.startswith("_"):
        w *= 0.1
    if len(name) < 5:
        w *= 0.2                                     # short names (lib, cfg, row) collide with local variables
    if name.lower() in STOP_NAMES:
        w *= 0.05
    if n_definers > 4:                               # a name defined in many files (main, run, __init__) says nothing about a dependency
        w *= 0.1
    if len(name) >= 8 and ("_" in name or any(c.isupper() for c in name[1:])):
        w *= 2.0                                     # long, distinctive identifiers say more about a real dependency
    return w


def rank_files(files: dict[str, FileInfo], focus_files: set[str], focus_syms: set[str]) -> dict[str, list[str]]:
    """Fills FileInfo.rank (personalised PageRank). Returns {symbol name: [files that reference it, excluding definers]} for the printer."""
    definers: dict[str, set[str]] = defaultdict(set)
    for f in files.values():
        for s in f.syms:
            definers[s.name].add(f.path)
    edges: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    users: dict[str, list[str]] = defaultdict(list)
    for f in files.values():
        scale = 0.2 if f.is_test else 1.0
        for ident, cnt in f.refs.items():
            ds = definers.get(ident)
            if not ds:
                continue
            w = _weight(ident, len(ds)) * scale * math.sqrt(cnt) * (10.0 if ident in focus_syms else 1.0)
            for d in ds:
                if d != f.path:
                    edges[f.path][d] += w
                    users[ident].append(f.path)
    if focus_files or focus_syms:                                   # when working on something, callers matter too
        for src, outs in list(edges.items()):
            for dst, w in list(outs.items()):
                edges[dst][src] += 0.25 * w
    names = list(files)
    n = len(names)
    if n == 0:
        return {}
    pers = {p: (1.0 if p in focus_files else 0.0) + 0.02 for p in names}
    for ident in focus_syms:
        for d in definers.get(ident, ()):
            pers[d] += 1.0
    z = sum(pers.values())
    pers = {p: v / z for p, v in pers.items()}
    rank = dict(pers)
    out_sum = {p: sum(edges[p].values()) for p in names}
    for _ in range(40):
        nxt = {p: 0.15 * pers[p] for p in names}
        dangling = sum(rank[p] for p in names if out_sum[p] == 0)
        for p in names:
            if out_sum[p]:
                share = 0.85 * rank[p] / out_sum[p]
                for q, w in edges[p].items():
                    nxt[q] += share * w
        if dangling:
            for p in names:
                nxt[p] += 0.85 * dangling * pers[p]
        delta = sum(abs(nxt[p] - rank[p]) for p in names)
        rank = nxt
        if delta < 1e-7:
            break
    for p in names:
        files[p].rank = rank[p]
    return users


# ---------------------------------------------------------------------------------------------- rendering
def est_tokens(s: str) -> int:
    return int(len(s) / 3.6) + 1


def _file_block(f: FileInfo, tier: int, used_by: dict[str, set[str]], max_tokens: int = 380) -> str:
    """tier 2: signatures with line numbers; tier 1: one line of names; tier 0 is handled by the caller."""
    if tier == 1:
        shown = [s.name for s in f.syms if s.depth == 0 and s.kind not in ("const", "impl") and not s.name.startswith("_")]
        names = ", ".join(dict.fromkeys(shown[:14]))
        more = len(f.syms) - len(shown[:14])
        return f"{f.path}: {names}" + (f" (+{more})" if more > 0 else "") if names else f.path
    if not f.syms:
        return f.path
    lines = [f.path]
    budget = max_tokens
    ranked = sorted(range(len(f.syms)), key=lambda i: (f.syms[i].depth, -len(used_by.get(f.syms[i].name, ())), i))   # top-level first, then most-used members
    keep: set[int] = set()
    for i in ranked:
        if f.syms[i].kind == "const" and not used_by.get(f.syms[i].name):
            continue                                                   # constants nobody else reads are not worth a line
        cost = est_tokens(f.syms[i].sig) + 3
        if budget - cost < 0 and keep:
            continue
        budget -= cost
        keep.add(i)
    props = [i for i in sorted(keep) if f.syms[i].depth and _GETTER_RX.match(f.syms[i].sig)]
    if len(props) < 3:
        props = []
    for i, s in enumerate(f.syms):
        if i in keep and i not in props:
            lines.append(f"  {s.line:>5} {'  ' * s.depth}{s.sig}")
        elif props and i == props[0]:
            lines.append(f"  {s.line:>5}   props: " + ", ".join(dict.fromkeys(f.syms[j].name for j in props[:12])) + (f" (+{len(props) - 12})" if len(props) > 12 else ""))   # a wall of trivial accessors is one line, not forty
    hidden = sum(1 for i, s in enumerate(f.syms) if i not in keep and s.kind != "const")
    if hidden:
        lines.append(f"        … +{hidden} more")
    return "\n".join(lines)


def _group_dirs(paths: list[str]) -> str:
    by: dict[str, list[str]] = defaultdict(list)
    for p in paths:
        d, _, b = p.rpartition("/")
        by[d].append(b)
    return "\n".join(f"{d + '/' if d else ''}: {' '.join(bs)}" if d else " ".join(bs) for d, bs in by.items())


@dataclass
class RepoMap:
    text: str
    files_total: int
    files_shown: int
    tokens: int
    notes: list = field(default_factory=list)
    ranked: list = field(default_factory=list)       # [(path, rank, lang)] best first - for --json


def build(root: str | Path = ".", budget: int = 2000, focus: list[str] | None = None, changed: bool = False, include_tests: bool = True) -> RepoMap:
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    rels = list_files(root)[:MAX_FILES]
    files: dict[str, FileInfo] = {}
    skipped: Counter = Counter()
    for rel in rels:
        full = root / rel
        try:
            if full.stat().st_size > MAX_FILE_BYTES or not full.is_file():
                skipped["too large / not a file"] += 1
                continue
            text = full.read_text("utf8", errors="replace")
        except OSError:
            continue
        info, why = parse_file(rel, text)
        if info:
            files[rel] = info
        elif why:
            skipped[why.split(":")[0] + (":" + why.split(":", 1)[1] if why.startswith("nogrammar") else "")] += 1

    wanted = list(focus or []) + (changed_files(root) if changed else [])
    focus_files = {p for p in files if any(p == w or p.endswith("/" + w.lstrip("./")) or (w.endswith("/") and p.startswith(w.lstrip("./"))) for w in wanted)}
    all_syms = {s.name for f in files.values() for s in f.syms}
    focus_syms = {w for w in wanted if w in all_syms}
    used = rank_files(files, focus_files, focus_syms)
    used_by = {k: set(v) for k, v in used.items()}

    order = sorted(files.values(), key=lambda f: (f.path not in focus_files, f.is_test, -f.rank, f.path))
    if not include_tests:
        order = [f for f in order if not f.is_test or f.path in focus_files]

    lang_count = Counter(f.lang for f in files.values())
    head = f"# repo map: {root.name} - {len(files)} source files ({', '.join(f'{k} {v}' for k, v in lang_count.most_common(4))}); ranked by how much other code depends on them"
    present = [m for m in MANIFESTS if (root / m).exists()]
    tail_lines = []
    if present:
        tail_lines.append("# also at root: " + " ".join(present))
    nog = {k.split(":", 1)[1]: v for k, v in skipped.items() if k.startswith("nogrammar:")}
    if nog:
        tail_lines.append("# no parser for " + ", ".join(f"{v} {k} files" for k, v in sorted(nog.items())) + " (not in the map)" + (
            "; install: pip install 'assetrag[repomap]'" if any(k in {e for e, l in EXT.items() if l != "python"} for k in nog) else ""))
    notes = [f"{k}: {v}" for k, v in skipped.items() if not k.startswith("nogrammar:")]

    fixed = est_tokens(head + "\n" + "\n".join(tail_lines)) + 8
    avail = max(budget - fixed, 100)
    blocks: list[str] = []
    spent = 0
    shown: list[FileInfo] = []
    rest: list[FileInfo] = []
    for f in order:                                                  # pass A: full signatures for the best files (focus files always)
        b = _file_block(f, 2, used_by)
        c = est_tokens(b)
        if (f.path in focus_files) or (f.syms and spent + c <= avail * 0.62 and len(shown) < 60):
            blocks.append(b)
            spent += c
            shown.append(f)
        else:
            rest.append(f)
    rest2: list[FileInfo] = []
    for f in rest:                                                   # pass B: one-line name lists
        b = _file_block(f, 1, used_by)
        c = est_tokens(b)
        if spent + c <= avail * 0.9 and f.syms:
            blocks.append(b)
            spent += c
            shown.append(f)
        else:
            rest2.append(f)
    leftover = []                                                    # pass C: bare paths, grouped by directory, as many as fit
    room = avail - spent
    for f in rest2:
        c = est_tokens(f.path) + 1
        if room - c < 40:
            break
        leftover.append(f.path)
        room -= c
    if leftover:
        blocks.append("# other files\n" + _group_dirs(leftover))
    omitted = len(rest2) - len(leftover)
    if omitted > 0:
        blocks.append(f"# … +{omitted} more files (raise the budget, or pass focus=<path or symbol>)")
    text = "\n".join([head, *blocks, *tail_lines])
    return RepoMap(text, len(files), len(shown) + len(leftover), est_tokens(text), notes, [(f.path, round(f.rank, 5), f.lang) for f in order])
