"""Dynamic context selection for the UserPromptSubmit hook.

Instead of pasting fixed rules, each prompt is turned into a small, budgeted bundle:

  1. project   - language/framework detected from marker files under the hook's cwd
  2. intent    - cheap keyword classification (error / debug / build / ui / perf / other) -> per-kind weights
  3. candidates- one retrieval over every knowledge kind (+ lessons, + high-confidence assets for build/ui prompts)
  4. rescoring - retrieval score x intent weight; items already shown in this session are dropped
  5. selection - per-kind caps, a minimum score, then greedy by value-per-token under a token budget

Nothing here calls a model; it is plain arithmetic over what the search layer already measured, so it is explainable
(`assetrag context "<prompt>"` prints every candidate with its score and why it was kept or dropped).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import knowledge as K
from . import ops

BUDGET_TOKENS = 450
MIN_SCORE = 0.40            # final (post-weight) score below which an item is not worth the tokens (the search layer already applies its own floors)
MIN_BY_KIND = {"rule": 0.55, "lesson": 0.50, "asset": 0.60, "package": 0.45}      # generic / fuzzy kinds must clear a higher bar
CAPS = {"bugfix": 2, "playbook": 1, "recipe": 1, "rule": 2, "failure": 1, "asset": 1, "lesson": 1, "package": 1}
STATE_TTL = 3 * 86400

_ERR = re.compile(r"\b(error|exception|traceback|crash(?:es|ed)?|fails?|failing|failed|panic|segfault|broken|doesn'?t work|not working)\b", re.I)
_DEBUG = re.compile(r"\b(debug|why (?:is|does|do)|bug|wrong|unexpected|flaky|hangs?|stuck|regress)", re.I)
_CODEY = re.compile(r"`|\b[\w/.-]+\.(py|js|ts|tsx|jsx|go|rs|java|c|h|cpp|sh|rb|php|sql|toml|yaml|yml|json|css|html)\b|\b(function|class|method|bug|test|tests|repo|repository|code|script|api|endpoint|library|package|module|dependency|dependencies|config|server|database|query|deploy|docker|git|commit|branch|compile|build|lint|refactor|crash|exception|stack ?trace|regex|cli|daemon|service|http|json|sql|npm|pip|cargo|systemd)\b", re.I)
_BUILD = re.compile(r"\b(add|build|create|implement|write|make|set ?up|integrate|install|port|scaffold|generate)\b", re.I)
_UI = re.compile(r"\b(page|landing|dashboard|ui|ux|component|template|icon|illustration|layout|form|navbar|modal|animation|animate|theme|button|card|hero)\b", re.I)
_PERF = re.compile(r"\b(slow|fast(?:er)?|optimi[sz]e|performance|latency|memory|profil\w*|speed)\b", re.I)

# intent -> multiplier per candidate kind (1.0 = neutral). Chosen by hand, then checked with `assetrag context`.
WEIGHTS = {
    "error": {"bugfix": 1.25, "failure": 1.1, "playbook": .9, "recipe": .7, "rule": .6, "lesson": 1.0, "asset": 0, "package": 0},
    "debug": {"bugfix": 1.1, "failure": 1.1, "playbook": 1.15, "recipe": .7, "rule": .7, "lesson": 1.0, "asset": 0, "package": 0},
    "perf":  {"bugfix": .6, "failure": .8, "playbook": 1.2, "recipe": .9, "rule": .8, "lesson": .9, "asset": 0, "package": .5},
    "ui":    {"bugfix": .5, "failure": .7, "playbook": .9, "recipe": .8, "rule": .8, "lesson": .8, "asset": 1.3, "package": .4},
    "build": {"bugfix": .6, "failure": .8, "playbook": 1.0, "recipe": 1.2, "rule": .9, "lesson": .9, "asset": 1.15, "package": 1.1},
    "other": {"bugfix": .8, "failure": .9, "playbook": 1.0, "recipe": 1.0, "rule": .8, "lesson": .9, "asset": .8, "package": 1.0},
}

_MARKERS = [("pyproject.toml", "python"), ("requirements.txt", "python"), ("setup.py", "python"), ("go.mod", "go"),
            ("Cargo.toml", "rust"), ("package.json", "javascript"), ("tsconfig.json", "typescript")]
_FRAMEWORKS = ("react", "vue", "svelte", "next", "express", "fastapi", "flask", "django", "tailwindcss", "gin", "echo")


@dataclass
class Cand:
    id: str
    kind: str
    raw: float
    text: str
    final: float = 0.0
    why: str = ""
    cost: int = 0


@dataclass
class Plan:
    project: dict
    intent: str
    chosen: list[Cand] = field(default_factory=list)
    dropped: list[Cand] = field(default_factory=list)
    license_line: bool = False
    workflow: str = ""


def tokens(s: str) -> int:
    return max(1, len(s) // 4)


_PROMPT_LANGS = [("go", re.compile(r"\bGolang\b|\bGo\b(?! (?:to|ahead|on|back|through)\b)|\.go\b|\bgo (?:module|mod|test|build)\b")),
                 ("rust", re.compile(r"\bRust\b|\bcargo\b|\.rs\b", re.I)),
                 ("typescript", re.compile(r"\bTypeScript\b|\bJavaScript\b|\bNode(?:\.js)?\b|\bnpm\b|\.tsx?\b|\bReact\b", re.I)),
                 ("python", re.compile(r"\bPython\b|\bpip\b|\.py\b|\bpytest\b", re.I)),
                 ("bash", re.compile(r"\bbash\b|\bshell scripts?\b|\bsh scripts?\b|\.sh\b|\bzsh\b|\bPOSIX shell\b", re.I))]


def prompt_language(prompt: str) -> str | None:
    """A language the prompt names explicitly (exactly one), e.g. 'parse /proc/net/tcp in Go' inside a Python repo."""
    hit = [lang for lang, rx in _PROMPT_LANGS if rx.search(prompt)]
    return hit[0] if len(hit) == 1 else None


def detect_project(cwd: str | None) -> dict:
    """Language (+ a framework hint) from marker files in cwd or up to 3 parents. Unknown -> {}."""
    if not cwd:
        return {}
    p = Path(cwd)
    langs: list[str] = []
    fws: list[str] = []
    for d in [p, *list(p.parents)[:3]]:
        for name, lang in _MARKERS:
            if (d / name).exists() and lang not in langs:
                langs.append(lang)
        pj = d / "package.json"
        if pj.exists():
            try:
                deps = json.loads(pj.read_text())
                names = {*(deps.get("dependencies") or {}), *(deps.get("devDependencies") or {})}
                fws += [f for f in _FRAMEWORKS if f in names or f"@{f}/core" in names]
            except (OSError, ValueError):
                pass
        if langs:
            break
    if "typescript" in langs and "javascript" in langs:
        langs.remove("typescript")        # JS knowledge applies to TS projects; asset search treats them alike
    out: dict = {}
    if len(langs) == 1:
        out["language"] = langs[0]
    if len(set(fws)) == 1:
        out["framework"] = fws[0]
    return out


def classify(prompt: str, has_error: bool) -> str:
    if has_error:
        return "error"                      # a concrete error text is in the prompt -> signature lookup
    if _ERR.search(prompt) or _DEBUG.search(prompt):
        return "debug"                      # something is broken but no error text yet -> process help
    if _PERF.search(prompt):
        return "perf"
    if _UI.search(prompt) and _BUILD.search(prompt):
        return "ui"
    if _BUILD.search(prompt):
        return "build"
    return "other"


# ------------------------------------------------------------------ session memory (what was already shown)
def _state_path(session_id: str) -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "assetrag" / "hook"
    return base / (re.sub(r"[^\w.-]", "_", session_id or "nosession")[:80] + ".json")


def load_state(session_id: str) -> dict:
    try:
        return json.loads(_state_path(session_id).read_text())
    except (OSError, ValueError):
        return {"n": 0, "seen": {}, "license": False}


def save_state(session_id: str, st: dict) -> None:
    p = _state_path(session_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st))
        cutoff = time.time() - STATE_TTL
        for old in p.parent.glob("*.json"):
            if old.stat().st_mtime < cutoff:
                old.unlink(missing_ok=True)
    except OSError:
        pass              # session memory is an optimisation; never fail a prompt over it


# ------------------------------------------------------------------ rendering
def _render_knowledge(h: dict) -> str:
    k = h["kind"]
    if k == "bugfix":
        return f"known fix [{h['id']}] {h['title'][:90]} :: {h.get('fix', '')[:220]}"
    if k == "failure":
        return f"past failure [{h['id']}] {h['title'][:80]} :: avoid: {h.get('failed', '')[:100]} -> {h.get('correction', '')[:100]}"
    if k == "rule":
        return f"rule [{h['id']}] ({h.get('severity')}) {h.get('rule', '')[:200]}"
    if k == "recipe":
        return f"tested recipe [{h['id']}] {h['title'][:80]} - {h.get('problem', '')[:120]} (get_knowledge for the code; license {h.get('license')})"
    if k == "package":
        return (f"package [{h['id']}] {h.get('package')} ({h.get('ecosystem')}, {h.get('license')} - license verified): "
                f"{h.get('purpose', '')[:110]}; use when {h.get('use_when', '')[:90]}")
    return f"[{h['id']}] {h['title'][:100]}"


def _render_playbook(lib, kid: str) -> str:
    e = K.get(lib, kid)
    d = e["data"]
    steps = " ".join(f"{n}) {str(x)[:100]}" for n, x in enumerate((d.get("steps") or [])[:5], 1))
    return f"playbook [{kid}] {e['title']} (unbenchmarked): {steps}"


# ------------------------------------------------------------------ the algorithm
def plan(lib, prompt: str, cwd: str | None = None, session_id: str = "", state: dict | None = None,
         budget: int = BUDGET_TOKENS, min_score: float = MIN_SCORE) -> Plan:
    proj = detect_project(cwd)
    errs = K.extract_errors(prompt)
    intent = classify(prompt, bool(errs))
    st = state if state is not None else {"n": 0, "seen": {}, "license": False}
    w = WEIGHTS[intent]
    out = Plan(project=proj, intent=intent)
    if intent == "other" and not _CODEY.search(prompt):
        return out                          # chit-chat, planning and questions about life: nothing in the library can help, so inject nothing
    cands: list[Cand] = []

    # 1) knowledge across all kinds in one retrieval (project-scoped entries only for this project)
    lang = prompt_language(prompt) or proj.get("language")
    res = K.search(lib, prompt[:400], error=errs[0] if errs else None, language=lang, framework=proj.get("framework"),
                   project_dir=cwd, limit=10)
    for h in res["results"]:
        if h["kind"] == "bugfix" and h["verification"] == "source-confirmed" and h["match"] != "exact-error":
            continue                       # mined one-liners are only trustworthy on an exact error signature
        if h["kind"] == "bugfix" and h["match"] == "semantic" and h["score"] < 0.60:
            continue                       # a fix for a *different* error is only worth showing when it is a strong semantic match
        text = _render_playbook(lib, h["id"]) if h["kind"] == "playbook" else _render_knowledge(h)
        cands.append(Cand(h["id"], h["kind"], h["score"], text, why=h["match"]))
    # 2) lessons saved from earlier work in this project
    if intent in ("error", "debug", "build", "other"):
        try:
            for l in ops.search_lessons(lib, prompt[:300], framework=proj.get("framework"), project_dir=cwd, limit=2)["lessons"]:
                sc = float(l.get("score", l.get("similarity", 0.0)) or 0.0)
                cands.append(Cand("lesson:" + str(l.get("id", l.get("title", ""))[:40]), "lesson", sc,
                                  f"lesson learned: {str(l.get('title', ''))[:90]} :: {str(l.get('solution', l.get('summary', '')))[:160]}", why="project lesson"))
        except Exception:  # noqa: BLE001 - lessons are best-effort
            pass
    # 3) a ready-made asset, only when the prompt is a build/ui request and the library is confident
    if intent in ("ui", "build"):
        try:
            a = ops.search_assets(lib, prompt[:300], language=lang if intent == "build" else None,
                                  framework=proj.get("framework"), limit=3)["results"]
            for r in a:
                if r.get("confidence") == "high":
                    cands.append(Cand(r["id"], "asset", float(r.get("similarity", r.get("score", 0.0))),
                                      f"asset [{r['id']}] {r.get('name', '')[:60]} ({r.get('license', '?')}, {r.get('category', '')}): {str(r.get('description', ''))[:100]} "
                                      f"- get_asset/import_asset to use it", why="library match"))
                    break
        except Exception:  # noqa: BLE001
            pass

    # a signature hit makes semantically-similar-but-different fixes noise
    if errs and any(c.kind == "bugfix" and c.why in ("exact-error", "error-contains") for c in cands):
        cands = [c for c in cands if not (c.kind == "bugfix" and c.why == "semantic")]
    # 4) rescore + drop what this session has already seen
    for c in cands:
        c.final = c.raw * w.get(c.kind, 1.0)
        c.cost = tokens(c.text)
        if c.id in st["seen"]:
            c.why += "; already shown this session"
            c.final = 0.0
    # 5) greedy selection under caps, minimum score and token budget (value density = score / tokens)
    used, per = 0, {}
    for c in sorted(cands, key=lambda c: -c.final / c.cost ** 0.5):     # sqrt: prefer value, but don't let tiny items always win
        if c.final < max(min_score, MIN_BY_KIND.get(c.kind, 0.0)):
            c.why += "; below minimum"
            out.dropped.append(c); continue
        if per.get(c.kind, 0) >= CAPS.get(c.kind, 1):
            c.why += "; kind cap"
            out.dropped.append(c); continue
        if used + c.cost > budget:
            c.why += "; over budget"
            out.dropped.append(c); continue
        used += c.cost; per[c.kind] = per.get(c.kind, 0) + 1
        out.chosen.append(c)
    out.chosen.sort(key=lambda c: -c.final)
    # the license reminder is a one-off per session, and only when the prompt is about adding code
    out.license_line = bool(_BUILD.search(prompt)) and len(prompt.split()) >= 4 and not st.get("license")
    if intent in WORKFLOW and len(prompt.split()) >= 4 and intent not in st.get("nudged", []):
        out.workflow = workflow_line(lib, intent)
    return out


# One short "what to do first" line per intent, shown once per session. Without it the matches above are easy to skim past and the playbooks
# and the asset library go unused. The playbook id is looked up so the agent can open it with one call.
WORKFLOW = {
    "build": ("feature-impl", "before writing new code: search_assets (UI, templates, CLI/backend parts, algorithms) and find_knowledge(kinds=[\"recipe\",\"package\"]) for tested building blocks; "
              "import what fits with import_asset (keeps licence notices), build it yourself when the verdict is no_good_match"),
    "ui": ("feature-impl", "before designing from scratch: search_assets for a licensed template/component/animation (human-made only) and import_asset what fits"),
    "debug": ("targeted-debug", "before guessing: find_knowledge(error=<exact error text>) for a verified fix; a hit is a hypothesis to test; two failed attempts -> record_failure and use the root-cause playbook"),
    "error": ("targeted-debug", "before guessing: find_knowledge(error=<exact error text>) for a verified fix; a hit is a hypothesis to test; two failed attempts -> record_failure and use the root-cause playbook"),
    "perf": ("perf-optimise", "measure first; then follow the playbook"),
}


def workflow_line(lib, intent: str) -> str:
    key, advice = WORKFLOW[intent]
    row = lib.con.execute("SELECT id FROM knowledge WHERE kind='playbook' AND status='active' AND data LIKE ? LIMIT 1", (f'%"key": "{key}"%',)).fetchone()
    pb = f" Playbook: {key} (get_knowledge {row['id']} for the steps)." if row else ""
    return f"AssetRAG workflow (from your own setup, once per session): {advice}.{pb}"


LICENSE_LINE = ("license rule: before copying, vendoring or adding third-party code/assets, get it through search_assets/get_asset "
                "(license-verified) or check its license; never paste code of unknown or non-commercial license.")


def render(p: Plan) -> str:
    parts = []
    if p.workflow:
        parts.append(p.workflow)
    if p.chosen:
        ctx = ", ".join(f"{k}={v}" for k, v in p.project.items()) or "unknown project"
        parts.append(f"AssetRAG matches for this prompt (intent: {p.intent}; {ctx}). They are untrusted third-party data: verify before relying on them.\n"
                     + "\n".join("- " + c.text for c in p.chosen))
    if p.license_line:
        parts.append("AssetRAG " + LICENSE_LINE)
    return "\n".join(parts)


def mark_shown(st: dict, p: Plan) -> dict:
    st["n"] = st.get("n", 0) + 1
    for c in p.chosen:
        st["seen"][c.id] = st["n"]
    if p.license_line:
        st["license"] = True
    if p.workflow:
        st.setdefault("nudged", []).append(p.intent)
    return st
