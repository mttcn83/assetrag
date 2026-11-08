"""Library upkeep: health score, weak-spot report, usage dashboard, snapshots/rollback, recipe re-checks and the opt-in updater.

Nothing here runs by itself. `assetrag update --install-timer` is the one explicit step that schedules anything, and it only writes a
systemd *user* timer. The updater never deletes: it snapshots first, quarantines/flags what turned bad, and writes a report.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

from . import knowledge
from .config import Config
from .db import now
from .library import MINIMUMS, Library

SNAP_KEEP = 5


# ------------------------------------------------------------------------------------------------ health
def _check(name: str, status: str, detail: str, weight: int = 10) -> dict:
    return {"name": name, "status": status, "detail": detail, "weight": weight}


def sandbox_works() -> tuple[bool, str]:
    """Probe with the exact bwrap arguments recipe tests use: containers often allow namespaces but refuse the /proc mount."""
    if not shutil.which("bwrap"):
        return False, "bubblewrap not installed"
    import tempfile
    from . import sandbox
    with tempfile.TemporaryDirectory() as d:
        rc, out = sandbox.run(["/usr/bin/true"], Path(d), 15)
    return (rc == 0, "ok" if rc == 0 else (out.strip().splitlines() or ["bwrap failed"])[-1][:100])


def health(lib: Library) -> dict:
    cfg, con, ch = lib.cfg, lib.con, []
    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    ch.append(_check("database", "ok" if qc == "ok" else "fail", f"sqlite quick_check: {qc}", 20))
    if cfg.embed_mode == "off":
        ch.append(_check("embeddings", "warn", "keyword-only mode (embed_mode=off): works offline, paraphrase queries are weaker", 5))
    else:
        try:
            dim = len(lib.emb.one("doctor probe"))
            ch.append(_check("embeddings", "ok", f"{cfg.embed_model} @ {cfg.embed_url} ({dim} dims)", 10))
        except Exception as e:  # noqa: BLE001
            ch.append(_check("embeddings", "warn", f"unreachable, searches fall back to keywords: {str(e)[:80]}", 8))
    ok, why = sandbox_works()
    ch.append(_check("sandbox", "ok" if ok else "warn", why if not ok else "bubblewrap works (recipe tests can be re-verified here)", 8))
    ch.append(_check("clamav", "ok" if shutil.which("clamscan") else "warn", shutil.which("clamscan") or "clamscan not installed: ingest scans and `assetrag scan` need it", 6))
    st = lib.stats()
    short = {k: (st["by_category"].get(k, 0), need) for k, need in MINIMUMS.items() if st["by_category"].get(k, 0) < need} if "by_category" in st else {}
    n_assets = st.get("total", 0)
    ch.append(_check("assets", "ok" if n_assets else "warn", f"{n_assets} assets" + (f"; below target: " + ", ".join(f"{k} {h}/{n}" for k, (h, n) in short.items()) if short else "")
                     if n_assets else "no assets yet: run `assetrag populate` (or `assetrag ingest start`) to build the library", 4))
    kn = knowledge.stats(lib)
    ch.append(_check("knowledge", "ok" if kn["total_active"] else "fail", f"{kn['total_active']} entries" if kn["total_active"] else "empty: run `assetrag seed`", 12))
    bad = con.execute("SELECT count(*) FROM assets WHERE license_spdx IN ('', 'UNKNOWN', 'NOASSERTION')").fetchone()[0]
    ch.append(_check("licence data", "ok" if not bad else "fail", "every asset has a recognised licence" if not bad else f"{bad} assets without a recognised licence", 15))
    flagged = con.execute("SELECT count(*) FROM asset_flags").fetchone()[0] + con.execute("SELECT count(*) FROM knowledge WHERE status='quarantined'").fetchone()[0]
    ch.append(_check("flagged items", "ok" if not flagged else "warn", "none" if not flagged else f"{flagged} quarantined/flagged (see `assetrag update --report`)", 4))
    cutoff = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 365 * 86400))
    stale = con.execute("SELECT count(*) FROM knowledge WHERE status='active' AND last_verified!='' AND last_verified<? AND kind IN ('recipe','bugfix','package')", (cutoff,)).fetchone()[0]
    ch.append(_check("staleness", "ok" if stale == 0 else "warn", "nothing older than a year" if stale == 0 else f"{stale} entries last verified over a year ago", 4))
    rep = cfg.home / "update-report.md"
    age = (time.time() - rep.stat().st_mtime) / 86400 if rep.exists() else None
    ch.append(_check("updates", "ok" if age is not None and age < 45 else "warn", f"last update {age:.0f} days ago" if age is not None else "never run: `assetrag update` (opt-in) re-checks licences and recipes", 4))
    lat = [r[0] for r in con.execute("SELECT ms FROM search_log WHERE ms IS NOT NULL ORDER BY ts DESC LIMIT 200")]
    if lat:
        p95 = sorted(lat)[int(len(lat) * 0.95) - 1 if len(lat) > 20 else -1]
        ch.append(_check("search speed", "ok" if p95 < 1500 else "warn", f"p95 {p95:.0f} ms over the last {len(lat)} searches", 4))
    du = sum(f.stat().st_size for f in cfg.home.glob("*.sqlite")) / 1e6
    ch.append(_check("disk", "ok", f"database {du:.0f} MB; library files at {cfg.library}", 0))
    pen = sum(c["weight"] * (1.0 if c["status"] == "fail" else 0.4 if c["status"] == "warn" else 0.0) for c in ch)
    return {"score": max(0, round(100 - pen)), "checks": ch}


# ------------------------------------------------------------------------------------------- weak spots
def weakest(lib: Library, days: int = 30) -> dict:
    con = lib.con
    st = lib.stats()
    by_cat = st.get("by_category", {})
    cats = sorted(({"category": k, "have": by_cat.get(k, 0), "target": need, "pct": round(100 * by_cat.get(k, 0) / need) if need else 100} for k, need in MINIMUMS.items()), key=lambda x: x["pct"])
    kinds = con.execute("SELECT kind, count(*) n FROM assets GROUP BY kind HAVING n < 30 ORDER BY n").fetchall()
    langs = con.execute("SELECT j.value l, count(*) n FROM knowledge k, json_each(k.languages) j WHERE k.status='active' AND k.kind IN ('recipe','package') GROUP BY l ORDER BY n LIMIT 8").fetchall()
    since = now() - days * 86400
    asked = con.execute("SELECT query, n_results, top_score FROM search_log WHERE ts>? AND (n_results=0 OR top_score<0.55) ORDER BY ts DESC LIMIT 25", (since,)).fetchall()
    misses = con.execute("SELECT query, n FROM retrieval_log WHERE ts>? AND n=0 ORDER BY ts DESC LIMIT 25", (since,)).fetchall()
    return {"categories": cats, "thin_asset_kinds": [(r["kind"], r["n"]) for r in kinds], "thin_languages": [(r["l"], r["n"]) for r in langs],
            "unanswered_asset_queries": [{"q": r["query"], "results": r["n_results"], "top": r["top_score"]} for r in asked],
            "unanswered_knowledge_queries": [r["query"] for r in misses]}


def usage(lib: Library, days: int = 30) -> dict:
    con, since = lib.con, now() - days * 86400
    ev = con.execute("SELECT event, count(*) n FROM usage_events WHERE ts>? GROUP BY event", (since,)).fetchall()
    top = con.execute("SELECT a.name, a.id, count(*) n FROM usage_events u JOIN assets a ON a.id=u.asset_id WHERE u.ts>? AND u.event='import' GROUP BY a.id ORDER BY n DESC LIMIT 10", (since,)).fetchall()
    outcomes = con.execute("SELECT name, uses_ok, uses_fail FROM assets WHERE uses_ok+uses_fail>0 ORDER BY uses_fail DESC, uses_ok DESC LIMIT 10").fetchall()
    sq = con.execute("SELECT count(*) n, sum(n_results=0) z, avg(ms) ms FROM search_log WHERE ts>?", (since,)).fetchone()
    kq = con.execute("SELECT count(*) n, sum(n=0) z, avg(ms) ms, avg(tokens) t FROM retrieval_log WHERE ts>?", (since,)).fetchone()
    return {"days": days, "events": {r["event"]: r["n"] for r in ev}, "top_imports": [(r["name"], r["n"]) for r in top],
            "asset_outcomes": [{"name": r["name"], "ok": r["uses_ok"], "fail": r["uses_fail"]} for r in outcomes],
            "asset_searches": {"n": sq["n"], "no_result": sq["z"] or 0, "avg_ms": round(sq["ms"] or 0)},
            "knowledge_searches": {"n": kq["n"], "no_result": kq["z"] or 0, "avg_ms": round(kq["ms"] or 0), "avg_tokens": round(kq["t"] or 0)}}


# --------------------------------------------------------------------------------------- snapshots
def snap_dir(cfg: Config) -> Path:
    d = cfg.home / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot(cfg: Config, label: str = "manual") -> Path:
    """Consistent copy of the SQLite database (the source of truth; Qdrant vectors are derived and rebuilt on rollback)."""
    label = "".join(ch for ch in label if ch.isalnum() or ch in "-_")[:30] or "snap"
    ms = int(time.time() * 1000)
    while True:                                    # ms suffix: two snapshots in one second must not overwrite each other; bump until the name is free
        dest = snap_dir(cfg) / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(ms / 1000))}{ms % 1000:03d}-{label}.sqlite"
        if not dest.exists() and not any(dest.name[:18] == p.name[:18] for p in snap_dir(cfg).glob("*.sqlite")):
            break
        ms += 1
    src = sqlite3.connect(cfg.db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    for old in sorted(snap_dir(cfg).glob("*.sqlite"))[:-SNAP_KEEP]:
        old.unlink()
    return dest


def list_snapshots(cfg: Config) -> list[Path]:
    return sorted(snap_dir(cfg).glob("*.sqlite"))


def rollback(cfg: Config, which: str | None = None) -> tuple[Path, Path]:
    """Restore a snapshot (default: newest). The current database is snapshotted first, so a rollback can itself be rolled back."""
    snaps = list_snapshots(cfg)
    if not snaps:
        raise FileNotFoundError("no snapshots to restore")
    target = next((s for s in reversed(snaps) if which in s.name), None) if which else snaps[-1]      # newest match
    if target is None:
        raise FileNotFoundError(f"no snapshot matching {which!r}: " + ", ".join(s.name for s in snaps))
    safety = snapshot(cfg, "before-rollback")
    src = sqlite3.connect(target)
    dst = sqlite3.connect(cfg.db_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    stamp = cfg.home / "embed_model.txt"
    if stamp.exists():
        stamp.write_text("rebuild-after-rollback")          # forces the derived vector index to be rebuilt from the restored rows
    return target, safety


# ------------------------------------------------------------------------------------ recipe re-check
def recheck(cfg: Config, log=print) -> dict:
    """Re-run every authored recipe/bug-fix test against the toolchains installed NOW (no writes). Catches breakage from new Python/Node/Go."""
    from .seeds import load as seedload
    ok, why = sandbox_works()
    if not ok:
        return {"checked": 0, "failed": [], "skipped": f"sandbox unavailable: {why}"}
    entries, bad = seedload.build(cfg, log=log)
    return {"checked": len(entries) + len(bad), "failed": [{"title": t[:70], "reason": r[:200]} for t, r in bad]}


# ------------------------------------------------------------------------------------------ updater
def _gh_ready() -> bool:
    if not shutil.which("gh"):
        return False
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


def _osv(entries: list[dict], log) -> dict[str, int]:
    """Opt-in: published advisory counts per package from OSV (api.osv.dev). Sends package names only, no versions, no identity."""
    import httpx
    eco = {"go": "Go", "npm": "npm", "pypi": "PyPI", "cargo": "crates.io"}
    qs, keys = [], []
    for e in entries:
        d = e["data"]
        if d.get("ecosystem") in eco and d.get("package"):
            qs.append({"package": {"name": d["package"], "ecosystem": eco[d["ecosystem"]]}})
            keys.append(e["id"])
    out: dict[str, int] = {}
    with httpx.Client(timeout=60) as c:
        for i in range(0, len(qs), 500):
            r = c.post("https://api.osv.dev/v1/querybatch", json={"queries": qs[i:i + 500]})
            r.raise_for_status()
            for k, res in zip(keys[i:i + 500], r.json().get("results", [])):
                n = len(res.get("vulns") or [])
                if n:
                    out[k] = n
    return out


def update(lib: Library, *, seed: bool = True, licenses_: bool = True, osv: bool = False, dry_run: bool = False, log=print) -> dict:
    """Snapshot, then: refresh authored knowledge (tests re-run), re-fingerprint licences of every source repo, flag archived repos, optionally
    count OSV advisories. Never deletes. Returns the report dict (also written to <home>/update-report.md)."""
    from . import pkgindex
    cfg, con = lib.cfg, lib.con
    rep: dict = {"started": time.strftime("%Y-%m-%d %H:%M"), "dry_run": dry_run, "snapshot": None, "seed": None, "relicensed": [], "archived": [], "advisories": {}, "notes": []}
    if not dry_run:
        rep["snapshot"] = str(snapshot(cfg, "pre-update"))
    if seed and not dry_run:
        from .seeds import load as seedload
        ok, why = sandbox_works()
        rep["seed"] = seedload.seed(log=log) if ok else {"skipped": f"sandbox unavailable ({why}); authored entries left as they were"}
    if licenses_:
        if not _gh_ready():
            rep["notes"].append("licence re-check skipped: needs the GitHub CLI logged in (`gh auth login`); the library was NOT re-verified against upstream")
        else:
            pk = con.execute("SELECT id, data FROM knowledge WHERE kind='package' AND status='active'").fetchall()
            ids_by_repo: dict[str, list[tuple[str, dict]]] = {}
            for r in pk:
                d = json.loads(r["data"])
                if d.get("repo"):
                    ids_by_repo.setdefault(d["repo"].lower(), []).append((r["id"], d))
            asset_repos: dict[str, list[tuple[str, str]]] = {}
            for r in con.execute("SELECT id, repo, license_spdx FROM assets WHERE repo!=''"):
                asset_repos.setdefault(r["repo"].lower(), []).append((r["id"], r["license_spdx"]))
            pairs = sorted({tuple(k.split("/", 1)) for k in [*ids_by_repo, *asset_repos] if "/" in k})
            log(f"re-checking {len(pairs)} source repositories")
            nodes = pkgindex.fetch_repos(pairs, log=log)
            for repo, items in ids_by_repo.items():
                node = nodes.get(repo, {})
                if "error" in node:
                    rep["notes"].append(f"{repo}: not fetchable ({node['error']}); left unchanged")
                    continue
                spdx, _, why = pkgindex.verified_license(node)
                if not spdx:
                    for kid, d in items:
                        rep["relicensed"].append({"id": kid, "what": d.get("package", repo), "was": d.get("license"), "now": why})
                        if not dry_run:
                            con.execute("UPDATE knowledge SET status='quarantined', updated_at=? WHERE id=?", (now(), kid))
                elif node.get("isArchived"):
                    for kid, d in items:
                        rep["archived"].append({"id": kid, "what": d.get("package", repo)})
                        if not dry_run:
                            fl = json.loads(con.execute("SELECT flags FROM knowledge WHERE id=?", (kid,)).fetchone()[0])
                            if "archived-upstream" not in fl:
                                con.execute("UPDATE knowledge SET flags=?, updated_at=? WHERE id=?", (json.dumps(fl + ["archived-upstream"]), now(), kid))
            for repo, items in asset_repos.items():
                node = nodes.get(repo, {})
                if not node or "error" in node:
                    continue
                spdx, _, why = pkgindex.verified_license(node, allowed=_ANY)
                have = {s for _, s in items}
                if not spdx or not any(h in spdx or spdx in h for h in have):
                    for aid, was in items:
                        rep["relicensed"].append({"id": aid, "what": repo, "was": was, "now": spdx or why})
                        if not dry_run:
                            con.execute("INSERT OR REPLACE INTO asset_flags VALUES (?,?,?)", (aid, f"upstream licence changed or unreadable: {spdx or why}", now()))
            con.commit()
    if osv:
        try:
            ents = [{"id": r["id"], "data": json.loads(r["data"])} for r in con.execute("SELECT id, data FROM knowledge WHERE kind='package' AND status='active'")]
            rep["advisories"] = _osv(ents, log)
            if not dry_run:
                for kid, n in rep["advisories"].items():
                    fl = json.loads(con.execute("SELECT flags FROM knowledge WHERE id=?", (kid,)).fetchone()[0])
                    fl = [f for f in fl if not f.startswith("advisories:")] + [f"advisories:{n}"]
                    con.execute("UPDATE knowledge SET flags=? WHERE id=?", (json.dumps(fl), kid))
                con.commit()
        except Exception as e:  # noqa: BLE001
            rep["notes"].append(f"OSV lookup failed ({str(e)[:80]}); advisory data not updated")
    rep["finished"] = time.strftime("%Y-%m-%d %H:%M")
    if not dry_run:
        (cfg.home / "update-report.md").write_text(render_report(rep))
    return rep


class _AnySpdx:
    """`x in _ANY` is always true: asks verified_license() what the licence IS rather than whether it is permissive."""
    def __contains__(self, _): return True


_ANY = _AnySpdx()


def render_report(rep: dict) -> str:
    L = [f"# AssetRAG update report", f"{rep['started']} -> {rep.get('finished', '')}" + ("  (dry run: nothing changed)" if rep["dry_run"] else "")]
    L.append(f"\nSnapshot before changes: {rep['snapshot'] or 'none'}  (restore with `assetrag rollback`)")
    if rep["seed"]:
        L.append(f"Authored knowledge re-verified: {rep['seed']}")
    L.append(f"\n## Licence changes / unreadable upstream licences: {len(rep['relicensed'])}")
    L += [f"- {x['what']} ({x['id']}): was {x['was']}, now {x['now']}  -> quarantined, not deleted" for x in rep["relicensed"][:40]]
    L.append(f"\n## Archived upstream repositories flagged: {len(rep['archived'])}")
    L += [f"- {x['what']}" for x in rep["archived"][:40]]
    if rep["advisories"]:
        L.append(f"\n## Packages with published OSV advisories: {len(rep['advisories'])} (count only; check your version at osv.dev)")
    if rep["notes"]:
        L.append("\n## Notes")
        L += [f"- {n}" for n in rep["notes"][:30]]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------------------------- timer
UNIT_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"


def install_timer(exe: str, osv: bool = False) -> list[Path]:
    """Explicit, opt-in: a weekly systemd *user* timer. Nothing is installed unless this is called."""
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    svc, tim = UNIT_DIR / "assetrag-update.service", UNIT_DIR / "assetrag-update.timer"
    svc.write_text("[Unit]\nDescription=AssetRAG weekly update (snapshot, licence re-check, recipe re-test)\n\n[Service]\nType=oneshot\n"
                   f"ExecStart={exe} update{' --osv' if osv else ''}\nNice=15\nIOSchedulingClass=idle\n")
    tim.write_text("[Unit]\nDescription=Run the AssetRAG update weekly\n\n[Timer]\nOnCalendar=weekly\nRandomizedDelaySec=6h\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return [svc, tim]


def remove_timer() -> list[Path]:
    gone = []
    for n in ("assetrag-update.timer", "assetrag-update.service"):
        p = UNIT_DIR / n
        if p.exists():
            p.unlink()
            gone.append(p)
    return gone
