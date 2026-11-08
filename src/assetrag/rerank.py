"""Second pass over search candidates: does this result answer what was asked?

First-pass retrieval (embeddings + BM25) finds things that are *about* the query. This pass judges whether they are the *right kind of thing*:
  * an icon when nobody asked for an icon (13k icons swamp everything else),
  * a template for a named subject nobody named (`gitignore: HIP` for "gitignore patterns"),
  * a result for another platform (a macOS-only notifier for a Linux app),
  * a data file when the request wants code, and weak lexical coverage of what was asked.
Pure functions, no model and no network, so every decision can be explained in one line (`why`) and tested.
Candidates with a *major* problem are dropped when something clean exists; if nothing clean exists the honest answer is "no good match".
"""
from __future__ import annotations

import re
import sys

STOP = {"a", "an", "the", "for", "with", "in", "on", "of", "to", "and", "or", "from", "using", "use", "that", "my", "our", "your", "is", "it", "as", "by", "at",
        "simple", "basic", "good", "modern", "nice", "small", "new", "app", "code", "build", "make", "need", "want", "like", "some", "any", "me", "find",
        "project", "thing", "something", "write", "create", "add", "get", "set", "give"}
SYN = {"colour": "color", "colours": "color", "cmd": "command", "dir": "directory", "dirs": "directory", "env": "environment", "golang": "go", "py": "python",
       "js": "javascript", "ts": "typescript", "nodejs": "node", "postgresql": "postgres", "k8s": "kubernetes", "regexp": "regex", "tui": "terminal", "cli": "command"}
ICON_WORDS = {"icon", "icons", "glyph", "glyphs", "pictogram", "svg", "symbol"}
ILLUSTRATION_WORDS = {"illustration", "illustrations", "artwork", "mascot", "hero image", "spot illustration"}
VISUAL_KINDS = {"icon", "illustration", "animated-icon"}
NAMED_TEMPLATE_KINDS = {"gitignore", "ci-workflow", "compose-stack", "release-config"}      # "<kind>: <subject>"; useless unless the subject is the one asked for
KIND_WORDS = {"gitignore": {"gitignore", "ignore", "git"}, "ci-workflow": {"workflow", "ci", "action", "pipeline", "github", "cicd"},
              "compose-stack": {"compose", "docker", "stack", "container"}, "release-config": {"release", "goreleaser", "changelog", "publish"}}
DATA_KINDS = NAMED_TEMPLATE_KINDS | VISUAL_KINDS | {"section-template", "page-template"}
CODE_WORDS = {"parse", "parser", "parsing", "library", "function", "implement", "middleware", "server", "daemon", "command", "api", "algorithm", "class", "module",
              "decoder", "encoder", "queue", "cache", "client", "worker", "watch", "watcher", "scheduler", "handler", "engine", "clipboard", "notification", "process",
              "processes", "socket", "thread", "hash", "compress", "serialize", "validate", "validator", "migration", "migrations", "logging", "router", "limiter"}
PLATFORMS = {
    "macos": re.compile(r"\b(macos|mac os|os x|osx|darwin|cocoa|appkit|swiftui|osascript|launchd|homebrew|nsuser\w*|uikit)\b", re.I),
    "windows": re.compile(r"\b(windows|win32|winapi|powershell|msvc|wsh|\.net framework|registry key)\b", re.I),
    "linux": re.compile(r"\b(linux|wayland|x11|xorg|systemd|posix|unix|freedesktop|dbus|inotify|procfs|/proc|epoll|bwrap)\b", re.I),
}
CROSS = re.compile(r"\b(cross[- ]platform|portable|all platforms|linux, macos|macos, linux|windows, macos|multi[- ]platform)\b", re.I)
QUERY_PLATFORM = {"macos": "macos", "osx": "macos", "mac": "macos", "darwin": "macos", "windows": "windows", "win32": "windows", "linux": "linux", "wayland": "linux",
                  "x11": "linux", "systemd": "linux", "arch": "linux", "ubuntu": "linux", "debian": "linux"}
PROJECT_TYPES = {"cli": {"cli": 0.05, "backend": 0.02, "algorithm": 0.02}, "daemon": {"backend": 0.05, "cli": 0.03, "algorithm": 0.02},
                 "library": {"backend": 0.04, "algorithm": 0.04, "cli": 0.02}, "web": {"ui": 0.04, "template": 0.04, "animation": 0.03, "backend": 0.03}}


def host_platform() -> str:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


def tokens(text: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9][a-z0-9+#.]*", text.lower()):
        t = SYN.get(t, t)
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        if t not in STOP and len(t) > 1:
            out.append(t)
    return out


def query_intent(query: str) -> dict:
    low = query.lower()
    words = set(re.findall(r"[a-z0-9+#]+", low))
    plat = next((QUERY_PLATFORM[w] for w in re.findall(r"[a-z0-9]+", low) if w in QUERY_PLATFORM), None)
    return {"tokens": list(dict.fromkeys(tokens(query))), "wants_icon": bool(words & ICON_WORDS), "wants_illustration": bool(words & {"illustration", "illustrations", "artwork", "mascot"}),
            "code": bool(words & CODE_WORDS), "platform": plat}


def _subject(name: str) -> str:
    return name.split(":", 1)[1].strip().lower() if ":" in name else name.lower()


def platform_clash(text: str, query_platform: str | None, host: str) -> list[str]:
    """Platforms the text is tied to when none of them is the target (and it does not claim to be cross-platform or Linux-capable)."""
    marks = {p for p, rx in PLATFORMS.items() if rx.search(text)}
    target = query_platform or host
    return sorted(marks) if marks and target not in marks and "linux" not in marks and not CROSS.search(text) else []


def assess(row, query_info: dict, host: str, project_type: str | None = None) -> dict:
    """-> {delta, major: [reasons it is the wrong kind of result], why: [short reasons it fits], cov: lexical coverage 0..1}."""
    qt = query_info["tokens"]
    name, desc, kind = row["name"], row["description"], row["kind"]
    blob_tokens = set(tokens(f"{name} {desc} {row['tags']}"))
    name_tokens = set(tokens(name))
    hit = [t for t in qt if t in blob_tokens]
    cov = len(hit) / len(qt) if qt else 1.0
    delta, major, why = 0.0, [], []
    if hit:
        why.append("matches " + ", ".join(hit[:4]))
    delta += 0.14 * (cov - 0.5)
    if qt and all(t in name_tokens for t in qt):
        delta += 0.05
    if kind in VISUAL_KINDS:
        asked = query_info["wants_icon"] if kind != "illustration" else query_info["wants_illustration"]
        if asked:
            delta += 0.03
        else:
            delta -= 0.30
            major.append("an %s, but you did not ask for one" % kind.replace("-", " "))
    if kind in NAMED_TEMPLATE_KINDS:
        subj = set(tokens(_subject(name)))
        if not (KIND_WORDS.get(kind, set()) & set(qt)):
            delta -= 0.45
            major.append("a %s template, but you did not ask for one" % kind)
        elif subj & set(qt):
            delta += 0.06
            why.append("template for " + _subject(name))
        else:
            delta -= 0.45
            major.append("a %s template for '%s', which you did not name" % (kind, _subject(name)))
    if query_info["code"] and kind in DATA_KINDS and kind not in VISUAL_KINDS and kind not in NAMED_TEMPLATE_KINDS:
        delta -= 0.10
        why.append("template, request looks like code")
    text = f"{name} {desc} {row['tags']}"
    clash = platform_clash(text, query_info["platform"], host) if kind not in VISUAL_KINDS else []
    target = query_info["platform"] or host
    if clash:
        delta -= 0.25
        major.append("%s-only; target is %s" % ("/".join(clash), target))
    elif query_info["platform"] and PLATFORMS[target].search(text):
        delta += 0.04
        why.append(target)
    if project_type and project_type in PROJECT_TYPES:
        delta += PROJECT_TYPES[project_type].get(row["category"], 0.0)
    return {"delta": delta, "major": major, "why": why, "cov": cov}


def verdict(top_cov: float, top_sim: float, confident_sim: float) -> str:
    """'good' | 'weak'. Weak means the best surviving result neither covers the request's words nor is semantically close."""
    if top_cov < 0.34 and top_sim < confident_sim:
        return "weak"
    return "good"
