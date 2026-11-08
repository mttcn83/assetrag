"""Benchmarks. `retrieval` is free and local. `claude-ab` spends real API budget and
only runs with --execute (it never runs implicitly)."""
from __future__ import annotations

import json
import re
import shutil
import statistics as st
import subprocess
import tempfile
import time
from pathlib import Path

from . import kops, ops
from .library import Library

HERE = Path(__file__).resolve().parents[2] / "benchmarks"


def _hit(res: dict, expect: list[str]) -> bool:
    blob = (res["name"] + " " + res["description"]).lower()
    return any(e in blob for e in expect)


def retrieval(lib: Library, out: Path | None = None, none_threshold: float = 0.55) -> dict:
    qs = json.loads((HERE / "queries.json").read_text())
    rows, lat = [], []
    for q in qs:
        t = time.perf_counter()
        r = ops.search_assets(lib, q["q"], category=q.get("category"), limit=8)
        lat.append((time.perf_counter() - t) * 1000)
        res = r["results"]
        rank = next((i + 1 for i, x in enumerate(res) if q["expect"] and _hit(x, q["expect"])), None)
        rows.append({"q": q["q"], "type": q["type"], "rank": rank, "top": res[0]["name"] if res else None,
                     "top_score": res[0]["score"] if res else 0.0, "confidence": res[0]["confidence"] if res else None, "resp_chars": len(json.dumps(r))})
    def agg(t):
        s = [r for r in rows if r["type"] == t and t != "none"]
        if not s: return None
        return {"n": len(s), "hit@1": sum(r["rank"] == 1 for r in s) / len(s), "hit@5": sum(bool(r["rank"] and r["rank"] <= 5) for r in s) / len(s),
                "mrr": round(sum(1 / r["rank"] for r in s if r["rank"]) / len(s), 3)}
    none = [r for r in rows if r["type"] == "none"]
    rep = {"exact": agg("exact"), "partial": agg("partial"),
           "none": {"n": len(none), "top_score_mean": round(st.mean(r["top_score"] for r in none), 3) if none else None,
                    "flagged_low_confidence_or_empty": sum(r["confidence"] != "high" for r in none) if none else 0},
           "confidence_high_on_relevant": sum(r["confidence"] == "high" for r in rows if r["type"] != "none" and r["rank"] == 1),
           "latency_ms": {"p50": round(st.median(lat), 1), "p95": round(sorted(lat)[int(len(lat) * .95) - 1], 1)},
           "avg_response_tokens_est": round(st.mean(r["resp_chars"] for r in rows) / 4), "rows": rows,
           "library_size": lib.stats()["total"]}
    if out:
        out.write_text(json.dumps(rep, indent=2))
    return rep


def judged(lib: Library, out: Path | None = None, limit: int = 3) -> dict:
    """Agent-realistic asset queries (no category hint) judged by regex on name+description instead of keyword presence.
    A top-`limit` result is RIGHT if it matches `good` and not `bad`. `none` queries should come back empty or flagged as no good match."""
    if not (HERE / "judged.json").exists():
        raise SystemExit("benchmark query sets are not shipped with the package; this command is for development checkouts that keep them in benchmarks/")
    qs = json.loads((HERE / "judged.json").read_text())
    rows = []
    for q in qs:
        r = ops.search_assets(lib, q["q"], limit=8)
        top = r["results"][:limit]
        if q.get("none"):
            rows.append({"q": q["q"], "none": True, "returned": len(r["results"]), "flagged": (not r["results"]) or r.get("verdict") in ("no_good_match", "weak"),
                         "top": [x["name"][:60] for x in top]})
            continue
        ok = [bool(re.search(q["good"], x["name"] + " " + x["description"], re.I) and not (q.get("bad") and re.search(q["bad"], x["name"] + " " + x["description"], re.I))) for x in top]
        rows.append({"q": q["q"], "right": ok, "hit": any(ok), "top1": bool(ok and ok[0]), "precision": sum(ok) / limit,
                     "top": [x["name"][:60] for x in top]})
    pos, neg = [r for r in rows if not r.get("none")], [r for r in rows if r.get("none")]
    rep = {"queries": len(pos), f"hit@{limit}": round(sum(r["hit"] for r in pos) / len(pos), 3), "top1_right": round(sum(r["top1"] for r in pos) / len(pos), 3),
           f"precision@{limit}": round(sum(r["precision"] for r in pos) / len(pos), 3),
           "none_queries": len(neg), "none_flagged_or_empty": sum(r["flagged"] for r in neg), "rows": rows}
    if out:
        out.write_text(json.dumps(rep, indent=2))
    return rep


def knowledge(lib: Library, out: Path | None = None) -> dict:
    """Labelled lookups for the knowledge store: exact error text, symptom paraphrases, task descriptions, off-topic negatives."""
    qs = json.loads((HERE / "kqueries.json").read_text())
    key_of = {r["id"]: json.loads(r["data"]).get("key") for r in lib.con.execute("SELECT id, data FROM knowledge")}
    rows, lat = [], []
    for q in qs:
        t = time.perf_counter()
        # error-looking queries go through the error channel exactly as an agent/hook would pass them
        is_err = q["type"] == "exact"
        r = kops.find_knowledge(lib, "" if is_err else q["q"], error=q["q"] if is_err else None, limit=5)
        lat.append((time.perf_counter() - t) * 1000)
        keys = [key_of.get(h["id"]) for h in r["results"]]
        rank = next((i + 1 for i, k in enumerate(keys) if q["expect"] and k == q["expect"]), None)
        rows.append({"q": q["q"][:70].replace("\n", " "), "type": q["type"], "rank": rank, "top": keys[0] if keys else None,
                     "n_results": len(keys), "tokens": len(json.dumps(r)) // 4, "match": r["results"][0]["match"] if r["results"] else None})

    def agg(t):
        s = [r for r in rows if r["type"] == t]
        return {"n": len(s), "hit@1": round(sum(r["rank"] == 1 for r in s) / len(s), 3), "hit@3": round(sum(bool(r["rank"] and r["rank"] <= 3) for r in s) / len(s), 3),
                "mrr": round(sum(1 / r["rank"] for r in s if r["rank"]) / len(s), 3)}
    none = [r for r in rows if r["type"] == "none"]
    rep = {t: agg(t) for t in ("exact", "symptom", "task")}
    rep["none"] = {"n": len(none), "returned_something": sum(r["n_results"] > 0 for r in none)}
    rep["latency_ms"] = {"p50": round(st.median(lat), 1), "p95": round(sorted(lat)[max(0, int(len(lat) * .95) - 1)], 1)}
    rep["avg_response_tokens_est"] = round(st.mean(r["tokens"] for r in rows))
    rep["misses"] = [{"q": r["q"], "type": r["type"], "top": r["top"], "rank": r["rank"]} for r in rows if r["type"] != "none" and r["rank"] != 1]
    if out:
        out.write_text(json.dumps({**rep, "rows": rows}, indent=2))
    return rep


def claude_ab(execute: bool, repeats: int = 1, max_turns: int = 12, budget_usd: float = 0.50, out: Path | None = None) -> dict:
    """A/B: same prompt with and without the AssetRAG MCP server, verified by running the task's check."""
    tasks = json.loads((HERE / "tasks.json").read_text())
    plan = [{"task": t["id"], "arm": a, "rep": r} for t in tasks for a in ("baseline", "assetrag") for r in range(repeats)]
    if not execute:
        return {"dry_run": True, "runs_planned": len(plan), "max_budget_usd_per_run": budget_usd,
                "worst_case_usd": round(len(plan) * budget_usd, 2), "note": "pass --execute to spend API budget"}
    if not shutil.which("claude"):
        return {"error": "claude CLI not found"}
    results = []
    cfg_empty = json.dumps({"mcpServers": {}})
    cfg_ar = json.dumps({"mcpServers": {"assetrag": {"command": str(Path(shutil.which("assetrag-mcp") or "assetrag-mcp"))}}})
    for t in tasks:
        for arm in ("baseline", "assetrag"):
            for rep in range(repeats):
                d = Path(tempfile.mkdtemp(prefix="arbench-"))
                for f in (t.get("setup") or "").split(";"):
                    if ":" in f:
                        n, body = f.split(":", 1); (d / n).write_text(body)
                cmd = ["claude", "-p", t["prompt"], "--output-format", "json", "--max-turns", str(max_turns),
                       "--permission-mode", "acceptEdits", "--max-budget-usd", str(budget_usd), "--strict-mcp-config",
                       "--mcp-config", cfg_ar if arm == "assetrag" else cfg_empty]
                t0 = time.time()
                p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=900)
                wall = time.time() - t0
                try:
                    j = json.loads(p.stdout)
                except ValueError:
                    j = {}
                ok = subprocess.run(t["verify"], shell=True, cwd=d, capture_output=True).returncode == 0  # trusted: our own fixed check strings
                u = j.get("usage", {})
                results.append({"task": t["id"], "match": t["match"], "kind": t["kind"], "arm": arm, "rep": rep, "wall_s": round(wall, 1),
                                "cost_usd": j.get("total_cost_usd"), "turns": j.get("num_turns"),
                                "tokens": (u.get("input_tokens", 0) + u.get("output_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)),
                                "passed": ok})
    rep = {"results": results, "summary": _summ(results)}
    if out:
        out.write_text(json.dumps(rep, indent=2))
    return rep


def _summ(rs):
    out = {}
    for arm in ("baseline", "assetrag"):
        s = [r for r in rs if r["arm"] == arm]
        if s:
            out[arm] = {"n": len(s), "pass_rate": sum(r["passed"] for r in s) / len(s),
                        "median_wall_s": st.median(r["wall_s"] for r in s),
                        "median_cost_usd": st.median([r["cost_usd"] for r in s if r["cost_usd"] is not None] or [0]),
                        "median_tokens": st.median(r["tokens"] for r in s)}
    return out
