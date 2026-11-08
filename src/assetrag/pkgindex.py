"""Package index: "what library do I import instead of rewriting this?", with a *verified* licence.

Sources
  * curated   hand-written guidance (pkgindex_data.P) for foundation packages
  * awesome   awesome-go / awesome-nodejs / awesome-python / awesome-rust, used only to *discover* repos and
              categories (names, URLs and headings are facts). Descriptions come from each package's own repo.
Every entry's licence is fingerprinted from the package repo's own LICENSE text at a pinned commit
(licenses.detect) - never taken from a registry, a README badge or the awesome list. Anything that is not
permissive is dropped. A list whose own licence does not verify as allowed is not used at all.
Entries are stored as knowledge kind "package" (verification=source-confirmed).
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict

from . import knowledge, licenses
from .library import Library
from .pkgindex_data import P
from .pkgindex_data2 import P2
from .pkgindex_data3 import P3
from .pkgindex_data4 import P4
from .pkgindex_data5 import P5

PERMISSIVE = {"MIT", "MIT-0", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "0BSD", "Unlicense", "CC0-1.0", "Zlib",
              "BSL-1.0", "BlueOak-1.0.0", "Python-2.0", "PSF-2.0"}
LIST_OK = PERMISSIVE | {"CC-BY-4.0"}        # an awesome list may be CC-BY (attribution recorded); packages may not
LISTS = {
    "awesome-go": dict(repo="avelino/awesome-go", eco="go", lang="go"),
    "awesome-nodejs": dict(repo="sindresorhus/awesome-nodejs", eco="npm", lang="typescript"),
    "awesome-python": dict(repo="vinta/awesome-python", eco="pypi", lang="python"),
    "awesome-rust": dict(repo="rust-unofficial/awesome-rust", eco="cargo", lang="rust"),
}
LANGS = {"go": ["go"], "npm": ["typescript", "javascript"], "pypi": ["python"], "cargo": ["rust"], "dart": ["dart"], "ruby": ["ruby"], "swift": ["swift"], "kotlin": ["kotlin", "java"], "c": ["c", "cpp"], "cpp": ["cpp", "c"]}
LICENSE_FILES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "UNLICENSE", "LICENSE-MIT", "LICENSE-APACHE", "license", "License.md", "LICENCE", "LICENSE.markdown",
                 "LICENCE.md", "LICENCE.txt", "license.txt", "license.md", "License.txt", "LICENSE.rst"]       # British spelling and lowercase variants exist in the wild
MANIFESTS = ["go.mod", "package.json", "Cargo.toml", "pyproject.toml"]
SKIP_HEADINGS = re.compile(r"contribut|table of contents|^contents|resources|license|^awesome|related|^lists?$|^other$|tutorials|books|newsletter|podcast|conferences|^meta", re.I)
CATCHALL = re.compile(r"^(utilit|misc|other|general|tools?$|libraries|helpers?|command.line|uncategori)", re.I)     # peers in these headings are not alternatives
ENTRY = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?\)")
HEAD = re.compile(r"^#{2,4}\s+(.*?)\s*$")


def gh(args: list[str], timeout=120) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:200])
    return r.stdout


# ------------------------------------------------------------------ repo facts (licence fingerprinted by us)
# monorepos whose licence/manifest live in a package directory rather than the repo root
SUBDIR = {"date-fns/date-fns": "pkgs/core"}


def _query(batch: list[tuple[str, str]]) -> str:
    parts = []
    for n, (o, r) in enumerate(batch):
        sub = SUBDIR.get(f"{o}/{r}".lower(), "")
        pre = f"{sub}/" if sub else ""
        objs = " ".join(f'f{i}: object(expression:"HEAD:{pre}{f}"){{... on Blob{{text}}}}' for i, f in enumerate(LICENSE_FILES + MANIFESTS))
        parts.append(f'r{n}: repository(owner:{json.dumps(o)}, name:{json.dumps(r)}) {{ nameWithOwner description stargazerCount isArchived isFork '
                     f'defaultBranchRef{{target{{... on Commit{{oid}}}}}} {objs} }}')
    return "query{" + " ".join(parts) + "}"


def _fetch_chunk(chunk: list[tuple[str, str]], log) -> dict[str, dict]:
    """One GraphQL call; if it fails (a single missing repo makes gh exit non-zero) split and retry so only the bad repo is lost."""
    try:
        data = json.loads(gh(["api", "graphql", "-f", f"query={_query(chunk)}"], timeout=180)).get("data", {})
    except (RuntimeError, ValueError, subprocess.TimeoutExpired):
        if len(chunk) == 1:
            log(f"  not fetchable: {chunk[0][0]}/{chunk[0][1]}")
            return {f"{chunk[0][0]}/{chunk[0][1]}".lower(): {"error": "repo not found / inaccessible"}}
        mid = len(chunk) // 2
        return {**_fetch_chunk(chunk[:mid], log), **_fetch_chunk(chunk[mid:], log)}
    return {f"{o}/{r}".lower(): data.get(f"r{n}") or {"error": "repo not found / inaccessible"} for n, (o, r) in enumerate(chunk)}


def fetch_repos(pairs: list[tuple[str, str]], batch=20, log=print) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(pairs), batch):
        out.update(_fetch_chunk(pairs[i:i + batch], log))
        if (i // batch) % 10 == 0:
            log(f"  fetched {min(i + batch, len(pairs))}/{len(pairs)}")
    return out


def verified_license(node: dict, allowed: set[str] = PERMISSIVE) -> tuple[str | None, str, str]:
    """-> (spdx or None, evidence file, reason). Fingerprints every licence file present; dual MIT/Apache files -> 'A OR B'."""
    found = []
    for i, f in enumerate(LICENSE_FILES):
        b = node.get(f"f{i}")
        if b and b.get("text"):
            try:
                spdx = licenses.detect(b["text"])
            except ValueError:
                spdx = None
            found.append((f, spdx))
    if not found:
        return None, "", "no LICENSE file at HEAD"
    ids = [s for _, s in found if s]
    if not ids:
        return None, found[0][0], "licence text not recognised"
    if any(s not in allowed for s in ids):
        return None, found[0][0], f"not permissive ({','.join(sorted(set(ids)))})"
    files = {f for f, s in found if s}
    if files <= {"LICENSE-MIT", "LICENSE-APACHE", "LICENSE", "LICENSE.md"} and len(set(ids)) > 1:
        return " OR ".join(sorted(set(ids))), ",".join(sorted(files)), ""
    return ids[0], next(f for f, s in found if s), ""


def package_name(node: dict, repo: str, eco: str) -> str:
    m = {name: (node.get(f"f{len(LICENSE_FILES) + i}") or {}).get("text") or "" for i, name in enumerate(MANIFESTS)}
    if eco == "go" and m["go.mod"]:
        mod = re.search(r"^module\s+(\S+)", m["go.mod"], re.M)
        if mod:
            return mod.group(1)
    if eco == "npm" and m["package.json"]:
        try:
            n = json.loads(m["package.json"]).get("name")
            if isinstance(n, str) and n:
                return n
        except ValueError:
            pass
    if eco == "cargo" and m["Cargo.toml"]:
        mm = re.search(r'^\[package\][^\[]*?^name\s*=\s*"([^"]+)"', m["Cargo.toml"], re.M | re.S)
        if mm:
            return mm.group(1)
    if eco == "pypi" and m["pyproject.toml"]:
        mm = re.search(r'^name\s*=\s*"([^"]+)"', m["pyproject.toml"], re.M)
        if mm:
            return mm.group(1)
    return f"github.com/{repo}" if eco == "go" else repo.split("/")[1]


# ------------------------------------------------------------------ awesome lists
def parse_list(text: str) -> list[tuple[str, str, str]]:
    """-> [(category, owner, repo)] for GitHub-hosted entries, in list order."""
    cat, out, seen = "", [], set()
    for line in text.splitlines():
        h = HEAD.match(line)
        if h:
            cat = re.sub(r"[\[\]*`]|\(.*?\)", "", h.group(1)).strip()
            continue
        m = ENTRY.match(line)
        if m and cat and not SKIP_HEADINGS.search(cat):
            key = f"{m.group(2)}/{m.group(3)}".lower()
            if key not in seen:
                seen.add(key)
                out.append((cat, m.group(2), m.group(3)))
    return out


def load_list(name: str, log=print) -> dict:
    L = LISTS[name]
    node = fetch_repos([tuple(L["repo"].split("/"))], log=lambda *_: None)[L["repo"].lower()]
    spdx, _, why = verified_license(node, LIST_OK)
    if spdx not in LIST_OK:
        return {"name": name, "ok": False, "why": f"list licence not verified as allowed ({spdx or why})", "entries": []}
    text = gh(["api", f"repos/{L['repo']}/readme", "-H", "Accept: application/vnd.github.raw"], timeout=120)
    sha = node["defaultBranchRef"]["target"]["oid"]
    return {"name": name, "ok": True, "license": spdx, "sha": sha, "entries": parse_list(text), **L}


# ------------------------------------------------------------------ entries
def _entry(repo: str, eco: str, node: dict, spdx: str, lic_file: str, *, purpose: str, when_to_use: str, when_not="",
           alternatives=None, install="", category="", source="curated", stars=0, from_list: dict | None = None) -> dict:
    sha = (node.get("defaultBranchRef") or {}).get("target", {}).get("oid", "")
    name = package_name(node, repo, eco)
    ev = licenses.evidence_hash(next((node[f"f{i}"]["text"] for i, f in enumerate(LICENSE_FILES) if f == lic_file.split(",")[0] and node.get(f"f{i}")), ""))
    data = {"package": name, "ecosystem": eco, "repo": repo, "license": spdx, "purpose": purpose, "when_to_use": when_to_use,
            "license_evidence": {"file": lic_file, "sha256": ev, "commit": sha, "method": "LICENSE text fingerprinted by AssetRAG"},
            "stars": stars, "key": f"pkg:{eco}:{repo.lower()}"}
    if when_not: data["when_not"] = when_not
    if alternatives: data["alternatives"] = alternatives
    if install: data["install"] = install
    if category: data["category"] = category
    if from_list: data["found_in"] = f"{from_list['name']} @ {from_list['sha'][:8]} ({from_list['license']}; discovery only)"
    return {"kind": "package", "title": f"{name} ({eco}, {spdx})", "summary": purpose[:300], "data": data,
            "languages": LANGS[eco], "tags": ["package-index", eco, source] + ([re.sub(r"\W+", "-", category.lower()).strip("-")] if category else []),
            "provenance": "authored" if source == "curated" else "indexed", "verification": "source-confirmed",
            "confidence": "high" if source == "curated" else "medium", "source_name": f"{repo} LICENSE @ {sha[:8]}",
            "source_url": f"https://github.com/{repo}/tree/{sha}", "source_license": spdx}


def build(lists: list[str], min_stars=150, log=print) -> tuple[list[dict], dict]:
    rep = defaultdict(lambda: defaultdict(int))
    entries: list[dict] = []
    # curated first (they win over list-derived entries for the same repo)
    cur = {(r.lower()): (r, eco, *rest) for r, eco, *rest in P + P2 + P3 + P4 + P5}
    nodes = fetch_repos([tuple(c[0].split("/")) for c in cur.values()], log=log)
    for key, (repo, eco, purpose, use, not_when, alts, install) in cur.items():
        node = nodes.get(key, {})
        spdx, f, why = verified_license(node) if "error" not in node else (None, "", node["error"])
        if not spdx or node.get("isArchived"):
            rep["curated"]["rejected: " + (why or "archived")] += 1; log(f"  x curated {repo}: {why or 'archived'}"); continue
        entries.append(_entry(repo, eco, node, spdx, f, purpose=purpose, when_to_use=use, when_not=not_when, alternatives=alts,
                              install=install, stars=node.get("stargazerCount", 0)))
        rep["curated"]["admitted"] += 1
    have = set(cur)
    for name in lists:
        L = load_list(name, log)
        if not L["ok"]:
            rep[name]["list rejected: " + L["why"]] += 1; log(f"  x {name}: {L['why']}"); continue
        by_cat = defaultdict(list)
        for cat, o, r in L["entries"]:
            if f"{o}/{r}".lower() not in have:
                by_cat[cat].append((o, r))
        pairs = [p for v in by_cat.values() for p in v]
        log(f"{name}: {len(L['entries'])} entries listed, license {L['license']}")
        nodes = fetch_repos(pairs, log=log)
        good: dict[str, list[tuple[int, str, str, dict, str, str]]] = defaultdict(list)
        for cat, ps in by_cat.items():
            for o, r in ps:
                node = nodes.get(f"{o}/{r}".lower(), {})
                rep[name]["listed"] += 1
                if "error" in node: rep[name]["rejected: repo not found"] += 1; continue
                if node.get("isArchived"): rep[name]["rejected: archived"] += 1; continue
                if node.get("stargazerCount", 0) < min_stars: rep[name][f"rejected: <{min_stars} stars"] += 1; continue
                if not (node.get("description") or "").strip(): rep[name]["rejected: no description"] += 1; continue
                spdx, f, why = verified_license(node)
                if not spdx:
                    rep[name]["rejected: " + re.sub(r"\(.*\)", "", why).strip()] += 1; continue
                good[cat].append((node["stargazerCount"], f"{o}/{r}", spdx, node, f, cat))
        for cat, items in good.items():
            items.sort(key=lambda x: -x[0])
            for stars, repo, spdx, node, f, _ in items:
                names = [] if CATCHALL.search(cat) else [package_name(x[3], x[1], L["eco"]) for x in items if x[1] != repo][:4]
                desc = node["description"].strip()
                entries.append(_entry(repo, L["eco"], node, spdx, f, purpose=desc[:300], when_to_use=f"{cat}: {desc[:160]}",
                                      alternatives=names, category=cat, source=name, stars=stars, from_list=L))
                rep[name]["admitted"] += 1
    return entries, {k: dict(v) for k, v in rep.items()}


def admit(lib: Library, entries: list[dict], log=print) -> dict:
    res = defaultdict(int)
    for i, e in enumerate(entries):
        try:
            res[knowledge.add(lib, e)["action"]] += 1
        except knowledge.KError as ex:
            res["rejected"] += 1; log(f"  x {e['title'][:60]}: {ex}")
        if i and i % 250 == 0:
            log(f"  admitted {i}/{len(entries)}")
    return dict(res)
