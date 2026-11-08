"""Mine *source-confirmed* bug-fix facts from the changelogs of permissively licensed projects.

What a changelog proves: a defect existed up to some release and the maintainers say it was fixed in the next. It does NOT
prove any code fix, so entries say "upgrade to >= X" with the maintainers' own sentence attached, are marked
`source-confirmed` (never `verified`), carry the project's licence and a commit-pinned URL, and pass the normal knowledge gate
(licence allowlist, injection quarantine, dedupe, secrets)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import ingest, knowledge

# (repo, changelog path, language, package name)
SOURCES = [
    ("psf/requests", "HISTORY.md", "python", "requests"), ("pallets/flask", "CHANGES.rst", "python", "flask"),
    ("pallets/werkzeug", "CHANGES.rst", "python", "werkzeug"), ("pallets/click", "CHANGES.md", "python", "click"),
    ("pallets/jinja", "CHANGES.rst", "python", "jinja2"), ("encode/httpx", "CHANGELOG.md", "python", "httpx"),
    ("pydantic/pydantic", "HISTORY.md", "python", "pydantic"), ("psf/black", "CHANGES.md", "python", "black"),
    ("pypa/pip", "NEWS.rst", "python", "pip"), ("tiangolo/fastapi", "docs/en/docs/release-notes.md", "python", "fastapi"),
    ("expressjs/express", "History.md", "javascript", "express"), ("axios/axios", "CHANGELOG.md", "javascript", "axios"),
    ("gin-gonic/gin", "CHANGELOG.md", "go", "github.com/gin-gonic/gin"), ("labstack/echo", "CHANGELOG.md", "go", "github.com/labstack/echo"),
    ("pytest-dev/pytest", "doc/en/changelog.rst", "python", "pytest"),
]
_VER = re.compile(r"^\s*(?:#{1,3}\s*)?(?:\*\*)?(?:[A-Za-z][\w-]*\s+)?\[?v?(\d+\.\d+(?:\.\d+)?(?:[a-z]+\d*)?)\]?(?:\*\*)?\s*(?:[-–(—:,]|$)", re.I)
_FIXSEC = re.compile(r"^\s*(?:\*\*|#{3,}\s*)?(bug ?fix(?:es)?|fix(?:ed|es)?)(?:\*\*)?:?\s*$", re.I)
_OTHERSEC = re.compile(r"^\s*(?:\*\*|#{3,}\s*)(\w[\w /&-]{1,30})(?:\*\*)?:?\s*$")
_BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+\.)\s+(.*)$")
_ERRTOK = re.compile(r"\b[A-Z][A-Za-z]*(?:Error|Exception)\b|\b(?:panic(?:s|ked)?|crash(?:es|ed)?|deadlock|hang(?:s)?|segfault|infinite loop|memory leak|traceback)\b", re.I)
_FIXSTART = re.compile(r"^(?:\w+:\s*)?(fix(?:ed|es)?|resolve[sd]?|correct(?:ed)?|prevent(?:ed)?|avoid(?:ed)?|handle[sd]?)\b", re.I)
_NOTFIX = re.compile(r"^(?:\w+:\s*)?(add(?:ed|s)?|support|improve[sd]?|updat|upgrad|bump|drop|doc|refactor|enhanc|introduc|allow|make|use|replace|remove|deprecat|rename|new)\w*\b", re.I)
_ROLE = re.compile(r":\w+:`([^`]*)`|`{1,2}([^`]*)`{1,2}")


@dataclass
class Fact:
    version: str
    text: str
    line: int


def clean(t: str) -> str:
    t = re.sub(r":(?:issue|pr|ghissue|ghpr|commit|user|bpo):`[^`]*`", "", t)
    t = re.sub(r"\{(?:issue|pr|user)\}`?[\w#, ]*`?", "", t)
    t = _ROLE.sub(lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r"\(\s*(?:#|:issue:|:pr:)?\d+\s*\)|\[#?\d+\]\([^)]*\)|\(#\d+\)|#\d+", "", t)
    t = re.sub(r"\[([^\]]*)\]\(([^)]*)\)?", lambda m: m.group(1) if not m.group(1).startswith("@") and not re.fullmatch(r"[0-9a-f]{6,40}", m.group(1)) else "", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"\(\s*\)|\(\s*[0-9a-f]{7,40}\s*\)?", "", t)                       # empty parens / dangling commit links
    t = re.sub(r"\s*,?\s*\b(?:PR|Pull request|Thanks|by)\b\s*(?:by\s*)?@\S+.*$", "", t, flags=re.I)  # "PR by @user ..." trailers
    t = re.sub(r"\s+by @\S+.*$", "", t)
    t = re.sub(r"[\s.,;]*(?:see\s+)?(?:pull request|pr)\b(?:\s+by)?\s*$", "", t, flags=re.I)
    t = re.sub(r"\((?:[\W_]*|Pull\s*)\)|\(\s*[<\W_]*$", "", t)                              # parens emptied by the stripping above
    t = re.sub(r"[\s.,;]*\bThanks\b.*$", "", t)
    t = re.sub(r"^(?:fix|bug|hotfix)\(([\w/.,-]+)\):\s*", r"Fix \1: ", t, flags=re.I)         # conventional-commit "fix(scope): "
    t = re.sub(r"^[^\w]+", "", t)                                                       # leading emoji / bullets / ** markers
    t = re.sub(r"^\w+:\*\*\s*", "", t)                                                 # "http:** " scope prefixes
    return re.sub(r"\s+", " ", t).strip(" -*.,;:")


def parse(text: str) -> list[Fact]:
    out: list[Fact] = []
    ver, in_fix, cur, cur_line = None, False, None, 0

    def flush():
        nonlocal cur
        if cur and ver:
            t = clean(cur)
            if (_ERRTOK.search(t) and 25 <= len(t) <= 400 and not _NOTFIX.match(t) and not re.search(r"\b(example|docs?|documentation|readme|typo)\b", t, re.I)
                    and (_FIXSTART.match(t) or (in_fix_at[0] and re.search(r"\b(error|exception|crash|fail|raise|hang|broke)", t, re.I)))):
                out.append(Fact(ver, t, cur_line))
        cur = None
    in_fix_at = [False]
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        m = _VER.match(ln)
        if m and not _BULLET.match(ln) and (ln.lstrip().startswith(("#", "**", "Version", "v")) or re.match(r"^[~=^\-\"]{3,}$", nxt.strip() or "x")
                                              or re.search(r"\(\d{4}-\d\d-\d\d\)|\d{4}-\d\d-\d\d", ln)):
            flush(); ver, in_fix = m.group(1), False; continue
        if _FIXSEC.match(ln):
            flush(); in_fix = True; continue
        if (_OTHERSEC.match(ln) and not _BULLET.match(ln)) or re.match(r"^(added|changed|removed|deprecated|improvements?|features?|security|unreleased)\s*:?\s*$", ln.strip(), re.I):
            flush(); in_fix = bool(_FIXSEC.match(ln)); continue
        b = _BULLET.match(ln)
        if b and len(b.group(1)) <= 4:
            flush(); cur, cur_line = b.group(2), i + 1; in_fix_at[0] = in_fix
        elif cur is not None and ln.startswith((" ", "\t")) and ln.strip():
            cur += " " + ln.strip()
        elif not ln.strip():
            continue
        else:
            flush()
    flush()
    return out


def entry(f: Fact, repo: str, rel: str, ctx, lang: str, pkg: str) -> dict:
    exc = _ERRTOK.search(f.text).group(0)
    url = f"https://github.com/{repo}/blob/{ctx.sha}/{rel}#L{f.line}"
    return {"kind": "bugfix", "title": f"{pkg} {f.version}: {f.text[:120]}", "summary": f.text,
            "data": {"error": f.text[:200], "root_cause": f"Reported by the maintainers as a defect in {pkg} before {f.version}: {f.text}",
                     "fix": f"Upgrade {pkg} to >= {f.version} (maintainer-confirmed in the changelog). Pin or upgrade; no code change is implied.",
                     "attribution": f"(c) {pkg} contributors, {ctx.spdx}; sentence quoted from {repo} changelog, retained for attribution",
                     "exception_or_symptom": exc},
            "languages": [lang], "tags": [pkg, "changelog", exc.lower()], "version_spec": f"<{f.version}", "fixed_in": f.version,
            "provenance": "mined-changelog", "verification": "source-confirmed", "confidence": "medium",
            "source_url": url, "source_name": f"{repo} changelog", "source_license": ctx.spdx}


def mine(lib, only: set[str] | None = None, per_repo: int = 120, log=print) -> dict:
    res = {"seen": 0, "added": 0, "duplicate": 0, "rejected": 0, "quarantined": 0, "repos": {}}
    srcs = [s for s in SOURCES if not only or s[0] in only]
    for repo, rel, lang, pkg in srcs:
        try:
            ctx = ingest.clone(lib.cfg, f"https://github.com/{repo}")
        except Exception as e:  # noqa: BLE001
            log(f"  ! {repo}: clone failed ({str(e)[:60]})"); continue
        if not ctx.spdx:
            log(f"  ! {repo}: licence not verifiable, skipped"); continue
        p = ctx.path / rel
        if not p.exists():
            log(f"  ! {repo}: {rel} not found"); continue
        facts = parse(p.read_text(errors="replace"))[:per_repo]
        n = {"facts": len(facts), "added": 0}
        for f in facts:
            res["seen"] += 1
            try:
                r = knowledge.add(lib, entry(f, repo, rel, ctx, lang, pkg))
            except knowledge.KError:
                res["rejected"] += 1; continue
            k = {"added": "added", "duplicate": "duplicate", "quarantined": "quarantined"}.get(r["action"])
            if k:
                res[k] += 1
            if r["action"] == "added":
                n["added"] += 1
        res["repos"][repo] = n
        lib.release_vec()
        log(f"  {repo:26} {ctx.spdx:12} facts {n['facts']:4}  added {n['added']:4}")
    return res
