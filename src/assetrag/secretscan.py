"""Secret detection with the gitleaks default rule set (MIT; see data/gitleaks_rules.json for source and notice).

Same approach as gitleaks: a cheap keyword pre-filter, the rule regex, an entropy floor on the captured secret, and
allowlists for obvious non-secrets (placeholders, template variables). Findings never contain the secret itself.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "gitleaks_rules.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    doc = json.loads(DATA.read_text())
    rules = []
    for r in doc["rules"]:
        try:
            rules.append({**r, "rx": re.compile(r["regex"])})
        except re.error:
            continue
    allow = []
    for x in doc.get("value_allow", []):
        try:
            allow.append(re.compile(x))
        except re.error:
            continue
    return {"rules": rules, "allow": allow, "stop": set(doc.get("stopwords", [])), "source": doc["_source"], "notice": doc["_notice"]}


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def rule_count() -> int:
    return len(_load()["rules"])


def scan(text: str, max_findings: int = 50) -> list[dict]:
    """-> [{"rule", "description", "line", "redacted"}] ordered by line."""
    d = _load()
    low = text.lower()
    out = []
    for r in d["rules"]:
        if r["keywords"] and not any(k in low for k in r["keywords"]):
            continue
        for m in r["rx"].finditer(text):
            g = r["group"]
            sec = m.group(g) if g and g <= (m.re.groups or 0) else (next((x for x in m.groups() if x), m.group(0)) if m.re.groups else m.group(0))
            if not sec:
                continue
            if r["entropy"] and entropy(sec) < r["entropy"]:
                continue
            sl = sec.lower()
            if sl in d["stop"] or any(w and w in sl for w in r["stopwords"]) or any(a.search(sec) for a in d["allow"]):
                continue
            out.append({"rule": r["id"], "description": r["description"], "line": text.count("\n", 0, m.start()) + 1,
                        "redacted": (sec[:4] + "…" + f"({len(sec)} chars)") if len(sec) > 8 else "…"})
            break                                       # one finding per rule is enough for a hint
    out.sort(key=lambda f: (f["line"], f["rule"]))
    return out[:max_findings]
