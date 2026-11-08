#!/usr/bin/env python3
"""Convert gitleaks' default rule set (MIT) into src/assetrag/data/gitleaks_rules.json.

Usage: import_gitleaks.py [path/to/gitleaks.toml]   (default: fetch at HEAD via `gh`, verify the repo LICENSE first)
Go (RE2) regexes are translated to Python `re`; rules that do not compile after translation are listed and skipped.
"""
import base64, json, re, subprocess, sys, tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assetrag import licenses  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "src/assetrag/data/gitleaks_rules.json"
POSIX = {"[:alpha:]": "a-zA-Z", "[:digit:]": "0-9", "[:alnum:]": "a-zA-Z0-9", "[:upper:]": "A-Z", "[:lower:]": "a-z", "[:space:]": r"\s",
         "[:word:]": r"\w", "[:xdigit:]": "0-9a-fA-F", "[:punct:]": r"!-/:-@\[-`{-~"}


def gh(*a):
    return subprocess.run(["gh", *a], capture_output=True, text=True, check=True).stdout


def translate(rx: str) -> str:
    for k, v in POSIX.items():
        rx = rx.replace(k, v)
    rx = rx.replace(r"\z", r"\Z").replace("(?U)", "")
    # Python only allows global flags at the very start: turn a mid-pattern (?i) into a scoped (?i:...)
    out, i, stack, in_cls = "", 0, [], False
    while i < len(rx):
        ch = rx[i]
        if ch == "\\":
            out += rx[i:i + 2]; i += 2; continue
        if in_cls:
            in_cls = ch != "]"; out += ch; i += 1; continue
        if ch == "[":
            in_cls = True; out += ch; i += 1
            if rx[i:i + 1] == "^": out += "^"; i += 1
            if rx[i:i + 1] == "]": out += r"\]"; i += 1
            continue
        if ch == "(":
            m = re.match(r"\(\?([a-z]+)\)", rx[i:])
            if m and i > 0:
                out += f"(?{m.group(1)}:"; stack.append("scoped"); i += m.end(); continue
            stack.append("g")
        elif ch == ")":
            while stack and stack[-1] == "scoped":
                stack.pop(); out += ")"
            if stack: stack.pop()
        out += ch; i += 1
    return out + ")" * sum(1 for s in stack if s == "scoped")


def main():
    if len(sys.argv) > 1:
        toml_text, sha, lic = Path(sys.argv[1]).read_text(), "local", "MIT"
    else:
        lic_text = base64.b64decode(gh("api", "repos/gitleaks/gitleaks/license", "--jq", ".content").strip()).decode()
        lic = licenses.detect(lic_text)
        if lic != "MIT":
            sys.exit(f"gitleaks licence did not verify as MIT (got {lic}); not importing")
        sha = gh("api", "repos/gitleaks/gitleaks/commits/HEAD", "--jq", ".sha").strip()
        toml_text = gh("api", "repos/gitleaks/gitleaks/contents/config/gitleaks.toml", "-H", "Accept: application/vnd.github.raw")
    cfg = tomllib.loads(toml_text)
    rules, skipped = [], []
    for r in cfg["rules"]:
        if "regex" not in r:
            skipped.append((r["id"], "path-only rule")); continue
        try:
            tr = translate(r["regex"])
            re.compile(tr)
        except re.error as e:
            skipped.append((r["id"], f"regex: {e}")); continue
        rules.append({"id": r["id"], "description": r["description"], "regex": tr, "entropy": r.get("entropy", 0),
                      "keywords": [k.lower() for k in r.get("keywords", [])], "group": r.get("secretGroup", 0),
                      "stopwords": [w.lower() for a in r.get("allowlists", []) for w in a.get("stopwords", [])]})
    al = cfg.get("allowlist", {})
    value_allow = []
    for x in al.get("regexes", []):
        try:
            value_allow.append(re.compile(translate(x)).pattern)
        except re.error:
            skipped.append(("allowlist", x[:40]))
    doc = {"_source": f"https://github.com/gitleaks/gitleaks/blob/{sha}/config/gitleaks.toml", "_license": lic,
           "_notice": "Rules derived from gitleaks, Copyright (c) 2019 Zachary Rice, MIT License. Regexes translated from Go RE2 to Python re.",
           "stopwords": [w.lower() for w in al.get("stopwords", [])], "value_allow": value_allow, "rules": rules}
    OUT.write_text(json.dumps(doc, indent=0))
    print(f"wrote {OUT}: {len(rules)} rules, skipped {len(skipped)}")
    for s in skipped: print("  skip", *s)


main()
