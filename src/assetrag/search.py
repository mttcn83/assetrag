"""Hybrid retrieval: vectors + BM25 keywords + compatibility/licence/quality priors."""
from __future__ import annotations

import json
import re
import time

from . import licenses, rerank
from .embed import EmbedError
from .db import now
from .library import Library
from .vectors import ASSETS

W = dict(sem=0.55, kw=0.20, quality=0.12, usage=0.06, ease=0.07)
# Measured on benchmarks/queries.json (embeddinggemma-2): correct top hits had cosine >= 0.76 (one legitimate hit at 0.69),
# off-topic queries topped out at 0.68. So >= 0.72 is reported as confident, below that as a weak match; candidates under
# 0.60 with no keyword support are dropped. Re-run `assetrag bench retrieval` after changing the embedding model.
CONFIDENT_SIM, DROP_SIM = 0.72, 0.60


GENERAL_KIND_BOOST = {"starter": 0.06, "page-template": 0.04, "library": 0.03, "section-template": 0.02, "module": 0.02}


def _fts_query(q: str) -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9_+#.]{2,}", q.lower()) if t not in STOP]
    return " OR ".join(f'"{t}"' for t in toks[:12])


STOP = {"the", "a", "an", "for", "with", "and", "or", "of", "to", "in", "find", "that", "is", "me", "using", "use", "good", "modern"}


def quality_score(r) -> float:
    q = json.loads(r["quality"])
    checks = q.get("checks", {})
    if not checks:
        return 0.0
    passed = sum(v.startswith("pass") for v in checks.values()) / len(checks)
    return passed


def usage_score(r) -> float:
    ok, bad = r["uses_ok"], r["uses_fail"]
    return 0.5 if ok + bad == 0 else (ok + 1) / (ok + bad + 2)


def compat(r, framework: str | None, language: str | None) -> str:
    fw = [f.lower() for f in json.loads(r["frameworks"])]
    lg = [l.lower() for l in json.loads(r["languages"])]
    if language and lg and language.lower() not in lg:
        return "incompatible"
    if framework:
        f = framework.lower()
        if fw and f not in fw:
            return "incompatible"
        return "match" if f in fw else "agnostic"
    return "agnostic"


def search(lib: Library, query: str, **kw) -> list[dict]:
    return search_ex(lib, query, **kw)["results"]


def search_ex(lib: Library, query: str, *, category: str | None = None, framework: str | None = None,
              language: str | None = None, profile: str | None = None, limit: int = 8,
              min_quality: float = 0.0, project_type: str | None = None) -> dict:
    """-> {"results": [...], "verdict": "good"|"weak"|"no_good_match", "note": str, "degraded": str, "closest": [names]}.
    Semantic + keyword first pass, then rerank.assess() judges whether each candidate is the right *kind* of answer."""
    t0 = time.perf_counter()
    profile = profile or lib.cfg.profile
    degraded = ""
    try:
        qvec = lib.emb.one(query, "query")
        qi0 = rerank.query_intent(query)
        skip = [] if qi0["wants_icon"] else ["icon", "animated-icon"] + ([] if qi0["wants_illustration"] else ["illustration"])   # 13k icons would fill the whole candidate pool
        if skip and lib.vec.lacks_kind():
            skip = []                                    # store not migrated yet (run `assetrag reindex`); the reranker still demotes them
        sem = dict(lib.vec.search(ASSETS, qvec, limit=80, category=category, not_kinds=skip or None))
    except EmbedError as e:                 # embeddings server down: keyword search still answers
        sem, degraded = {}, f"embeddings unavailable ({str(e)[:70]}); keyword matches only"
    qi, host = rerank.query_intent(query), lib.cfg.platform or rerank.host_platform()
    kw: dict[str, float] = {}
    fq = _fts_query(query)
    if fq:
        rows = lib.con.execute(
            "SELECT id, bm25(assets_fts, 0, 6, 2, 3, 2) s FROM assets_fts WHERE assets_fts MATCH ? ORDER BY s LIMIT 80", (fq,)).fetchall()
        if rows:
            best = min(r["s"] for r in rows) or -1
            kw = {r["id"]: min(1.0, r["s"] / best) if best else 0 for r in rows}
    ids = (set(sem) | set(kw)) - {r[0] for r in lib.con.execute("SELECT asset_id FROM asset_flags")}
    out = []
    for aid in ids:
        r = lib.get_row(aid)
        if not r or (category and r["category"] != category):
            continue
        v = licenses.check(r["license_spdx"], profile, dep_licenses=json.loads(r["dependencies"]))
        if not v.ok:
            continue
        c = compat(r, framework, language)
        if c == "incompatible":
            continue
        qs = quality_score(r)
        if qs < min_quality:
            continue
        if sem.get(aid, 0.0) < DROP_SIM and kw.get(aid, 0.0) < 0.5:
            continue          # neither semantically close nor lexically supported
        ease = 1 - (r["difficulty"] - 1) / 4
        score = (W["sem"] * max(0.0, sem.get(aid, 0.0)) + W["kw"] * kw.get(aid, 0.0) + W["quality"] * qs
                 + W["usage"] * usage_score(r) + W["ease"] * ease)
        if c == "match":
            score += 0.08
        score += GENERAL_KIND_BOOST.get(r["kind"], 0.0)   # prefer broad starters over niche demos
        a = rerank.assess(r, qi, host, project_type)
        score += a["delta"]
        if r["uses_ok"]:
            a["why"].append(f"imported and worked {r['uses_ok']}x")
        if r["uses_fail"]:
            a["why"].append(f"reported failing {r['uses_fail']}x")
        out.append({"_major": a["major"], "_cov": a["cov"], "why": a["why"], "verified_at": r["verified_at"][:10], "id": aid, "name": r["name"], "category": r["category"], "kind": r["kind"],
                    "description": r["description"][:140], "license": r["license_spdx"],
                    "frameworks": json.loads(r["frameworks"]), "compat": c, "difficulty": r["difficulty"],
                    "verified": qs >= 1.0, "score": round(score, 4), "similarity": round(sem.get(aid, 0.0), 3),
                    "confidence": "high" if (sem.get(aid, 0.0) >= CONFIDENT_SIM or (not sem and a["cov"] >= 0.67)) else "low"})
    out.sort(key=lambda x: -x["score"])
    clean = [x for x in out if not x["_major"]]
    res = {"results": [], "verdict": "good", "degraded": degraded}
    if clean:
        res["results"] = clean[:limit]
        if rerank.verdict(res["results"][0]["_cov"], res["results"][0]["similarity"], CONFIDENT_SIM) == "weak":
            res["verdict"] = "weak"
    else:
        res["verdict"] = "no_good_match"
        if out:
            res["closest"] = [f"{x['name'][:50]} ({x['_major'][0]})" for x in out[:3]]
    for x in out:
        x.pop("_major", None); x.pop("_cov", None)
    ms = (time.perf_counter() - t0) * 1000
    lib.con.execute("INSERT INTO search_log(ts,query,n_results,top_score,ms) VALUES (?,?,?,?,?)",
                    (now(), query[:300], len(res["results"]), res["results"][0]["score"] if res["results"] else None, ms))
    lib.con.commit()
    res["ms"] = round(ms)
    return res
