"""Needs-coverage benchmark: for each concrete need (benchmarks/needs.json), is there a confident, language-correct hit in the
top results of asset search or tested-recipe search whose name/description/tags contain a relevant keyword?
Keyword relevance (name/title/tags, plus purpose for packages; icons excluded for non-icon needs) is a proxy for human judging and is still optimistic; read the printed hits for any need you doubt.
A package-index hit only counts when its licence was fingerprinted from the package's own LICENSE (evidence hash present).
Usage: coverage.py [needs-file-stem]   e.g. `coverage.py needs_heldout` -> benchmarks/coverage-results-needs_heldout.json"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from assetrag import knowledge as K, ops  # noqa: E402
from assetrag.library import Library  # noqa: E402

JS = {"javascript", "typescript"}


def lang_ok(want: str, have: list[str]) -> bool:
    if not want:
        return True
    have = {h.lower() for h in have}
    return bool(have & (JS if want in JS else {want})) or not have      # untagged = language-agnostic


def main(top: int = 4, stem: str = "needs") -> int:
    needs = json.loads((ROOT / "benchmarks" / f"{stem}.json").read_text())["needs"]
    lib = Library()
    rows, by = [], defaultdict(lambda: [0, 0])
    for nd in needs:
        kw, hits = [k.lower() for k in nd["any"]], []
        lang = nd["lang"] or None
        res = ops.search_assets(lib, nd["query"], language=lang if lang not in JS else None, limit=top + 4)["results"]
        for r in res[: top + 4]:
            row = lib.get_row(r["id"])
            text = " ".join([r["name"], r.get("kind", ""), row["tags"]]).lower()
            if (r.get("kind") == "icon" or "icon" in r["name"].lower()) and "icon" not in nd["query"].lower():
                text = ""                                   # an icon never answers a non-icon need
            langs = json.loads(row["languages"])
            hits.append({"src": "asset", "id": r["id"], "name": r["name"][:60], "conf": r["confidence"], "rel": any(k in text for k in kw),
                         "lang_ok": lang_ok(nd["lang"], langs), "lang": langs})
        for h in K.search(lib, nd["query"], kinds=["recipe"], language=lang if lang not in JS else None, limit=3)["results"]:
            e = K.get(lib, h["id"])
            text = " ".join([e["title"], " ".join(e["tags"]), json.dumps(e["data"].get("problem", ""))]).lower()
            hits.append({"src": "recipe", "id": h["id"], "name": e["title"][:60], "conf": "high", "rel": any(k in text for k in kw),
                         "lang_ok": lang_ok(nd["lang"], e["languages"]), "lang": e["languages"]})
        for h in K.search(lib, nd["query"], kinds=["package"], language=lang if lang not in JS else None, limit=3)["results"]:
            e = K.get(lib, h["id"])
            d = e["data"]
            verified = e["verification"] == "source-confirmed" and bool((d.get("license_evidence") or {}).get("sha256")) and bool(d.get("license"))
            text = " ".join([e["title"], str(d.get("purpose", "")), str(d.get("category", "")), " ".join(e["tags"])]).lower()
            hits.append({"src": "package", "id": h["id"], "name": e["title"][:60], "conf": "high" if verified else "low", "rel": any(k in text for k in kw),
                         "lang_ok": lang_ok(nd["lang"], e["languages"]), "lang": e["languages"]})
        ok = [h for h in hits if h["conf"] == "high" and h["rel"] and h["lang_ok"]][:1]
        loose = [h for h in hits if h["rel"]][:1]
        status = "covered" if ok else "partial" if loose else "gap"
        by[nd["group"]][0] += status == "covered"; by[nd["group"]][1] += 1
        rows.append({"group": nd["group"], "lang": nd["lang"], "query": nd["query"], "status": status,
                     "best": (ok or loose or [None])[0]})
    lib.release_vec(); lib.close()
    n = len(rows); cov = sum(r["status"] == "covered" for r in rows); par = sum(r["status"] == "partial" for r in rows)
    srcs = defaultdict(int)
    for r in rows:
        if r["status"] == "covered":
            srcs[r["best"]["src"]] += 1
    print(f"\n  COVERAGE  covered {cov}/{n} = {cov/n:.0%}   partial {par}   gap {n-cov-par}\n")
    print("  covered by:", ", ".join(f"{k} {v}" for k, v in sorted(srcs.items())), "\n")
    for g, (c, t) in sorted(by.items()):
        print(f"  {g:<12} {c:>2}/{t:<2} {'#' * round(20 * c / t):<20} {c/t:.0%}")
    print("\n  uncovered:")
    for r in rows:
        if r["status"] != "covered":
            b = r["best"]
            print(f"   [{r['status']:<7}] {r['group']:<11} {r['query'][:62]:<62} {('~ ' + b['name'][:34]) if b else ''}")
    (ROOT / "benchmarks" / ("coverage-results.json" if stem == "needs" else f"coverage-results-{stem}.json")).write_text(json.dumps({"covered": cov, "partial": par, "total": n, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(stem=sys.argv[1] if len(sys.argv) > 1 else "needs"))
