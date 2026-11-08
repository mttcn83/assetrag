"""assetrag command line."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import __doc__ as _d  # noqa: F401
from . import knowledge, kops, ops, security
from .config import load
from .embed import EmbedError
from .library import MINIMUMS, Library

VERSION = "1.0.0"
USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def c(s, code): return f"\033[{code}m{s}\033[0m" if USE_COLOR else str(s)
def dim(s): return c(s, "2")
def acc(s): return c(s, "36")
def bold(s): return c(s, "1")


def bar(have, need, w=24):
    f = min(w, int(w * have / need)) if need else w
    return "█" * f + "░" * (w - f)


def cmd_stats(a, lib: Library):
    s = ops.library_stats(lib)
    if a.json:
        print(json.dumps(s, indent=2)); return 0
    print(f"\n  {bold(acc('ASSETRAG'))}  /  library   {dim(str(lib.cfg.home))}\n")
    print(f"  {'CATEGORY':<11}{'HAVE':>6}{'NEED':>7}  PROGRESS")
    for k, need in MINIMUMS.items():
        have = s["by_category"].get(k, 0)
        mark = c("✓", "32") if have >= need else c("·", "33")
        print(f"  {k:<11}{have:>6}{need:>7}  {bar(have, need)} {mark}")
    print(f"  {'-'*52}\n  {bold(s['total'])} assets  |  {s['lessons']} lessons  |  launch target {'MET' if s['launch_target_met'] else 'NOT met'}")
    print("  licenses: " + ", ".join(f"{k} {v}" for k, v in list(s["licenses"].items())[:8]))
    if s["search_gaps"]:
        print("  search gaps: " + "; ".join(g["query"] for g in s["search_gaps"][:4]))
    return 0


def cmd_search(a, lib):
    r = ops.search_assets(lib, " ".join(a.query), a.category, a.framework, a.language, a.profile, a.limit)
    if a.json:
        print(json.dumps(r, indent=2)); return 0
    if not r["results"]:
        print(r["note"]); return 1
    w = shutil.get_terminal_size((100, 20)).columns
    print(f"\n  {'ID':<15}{'SCORE':>6}  {'CAT':<10}{'LICENSE':<13}NAME")
    for x in r["results"]:
        name = (x["name"] + (" - " + x["description"] if x["description"] else ""))
        line = f"  {x['id']:<15}{x['score']:>6.2f}  {x['category']:<10}{x['license']:<13}"
        print(line + name[: max(10, w - len(line) - 1)])
    print(dim(f"\n  assetrag show <id>   |   assetrag import <id> --to <project>"))
    return 0


def cmd_show(a, lib):
    r = ops.get_asset(lib, a.id, include_code=a.code)
    print(json.dumps(r, indent=2) if a.json or a.code else _fmt(r))
    return 0


def _fmt(r):
    return "\n".join(f"{k:>14}: {v}" for k, v in r.items() if k not in ("files", "quality", "notice")) + \
        f"\n{'checks':>14}: " + "; ".join(f"{k} {v}" for k, v in r["quality"].get("checks", {}).items())


def cmd_license(a, lib):
    r = ops.check_license(lib, a.id, a.spdx, a.profile, not a.no_notices)
    print(json.dumps(r, indent=2))
    return 0 if r["ok"] else 2


def cmd_import(a, lib):
    r = ops.import_asset(lib, a.id, a.to, a.dest, a.profile, not a.no_notices, a.overwrite)
    print(json.dumps(r, indent=2))
    return 0 if r["imported"] else 2


def cmd_populate(a, lib):
    from .populate import populate
    t = populate(lib, set(a.only) if a.only else None, a.rerun)
    print(t); return 0


def cmd_discover(a, lib):
    from .discover import discover
    print(discover(lib, a.kind, a.limit)); return 0


def cmd_reindex(a, lib):
    print(f"indexed {lib.reindex(a.force)} assets"); return 0


def cmd_audit(a, lib):
    r = ops.audit_project(lib, a.project, a.profile)
    print(json.dumps(r, indent=2))
    return 0 if r["clean"] else 2


def cmd_lesson(a, lib):
    if a.sub == "add":
        r = ops.save_lesson(lib, a.project, title=a.title, category=a.category, solution=a.solution, framework=a.framework,
                            root_cause=a.root_cause, symptoms=a.symptoms, prevention=a.prevention, evidence=a.evidence,
                            confidence=a.confidence)
    else:
        r = ops.search_lessons(lib, " ".join(a.query), a.framework, a.project)
    print(json.dumps(r, indent=2)); return 0


def cmd_outcome(a, lib):
    print(ops.record_outcome(lib, a.id, a.result == "ok", a.detail)); return 0


def cmd_scan(a, lib):
    """ClamAV gate. Writes security-report.md."""
    from .report import security_report
    return security_report(lib.cfg, Path(a.path).resolve(), Path(a.report))


def cmd_bench(a, lib):
    from . import bench
    if a.which == "retrieval":
        r = bench.retrieval(lib, Path(a.out) if a.out else None)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=2))
    elif a.which == "judged":
        r = bench.judged(lib, Path(a.out) if a.out else None)
        print(json.dumps({k: v for k, v in r.items() if k != "rows"}, indent=2))
        if a.verbose:
            for x in r["rows"]:
                mark = ("FLAG" if x["flagged"] else "ANSWERED") if x.get("none") else ("ok  " if x["hit"] else "MISS")
                print(f"{mark} {x['q']}\n       " + " | ".join(x["top"]))
    elif a.which == "knowledge":
        print(json.dumps(bench.knowledge(lib, Path(a.out) if a.out else None), indent=2))
    else:
        print(json.dumps(bench.claude_ab(a.execute, a.repeats, out=Path(a.out) if a.out else None), indent=2))
    return 0


def cmd_ingest(a, lib):
    from . import service
    svc = service.Service(lib.cfg)
    if a.sub == "status":
        lib.close()
        s = service.write_status(lib.cfg)
        print(json.dumps({k: v for k, v in s.items() if k != "per_source"}, indent=2)); return 0
    lib.close()
    if a.sub == "start":
        fwd = list(a.args)
        if a.only:
            fwd += ["--only", *a.only]
        if a.discover:
            fwd += ["--discover", *a.discover]
        print("started pid", svc.start(fwd)); return 0
    if a.sub == "stop":
        print("stopped" if svc.stop() else "not running / did not stop in time"); return 0
    disc = [(k, int(n)) for k, n in (x.split(":") for x in a.discover)] if a.discover else []
    try:
        svc.run(set(a.only) if a.only else None, disc)
    except service.Busy as e:
        print(f"error: {e}", file=sys.stderr); return 3
    return 0


def cmd_context(a, lib):
    from . import context as C
    p = C.plan(lib, a.prompt, str(Path(a.cwd).resolve()), "explain")
    print(f"  intent: {p.intent}   project: {p.project or 'unknown'}")
    for tag, items in (("USE ", p.chosen), ("skip", p.dropped)):
        for c in items:
            print(f"  {tag} {c.final:5.2f} (raw {c.raw:.2f}) {c.kind:<8} {c.id[:22]:<22} ~{c.cost:>3}t  {c.why}")
    print(f"  tokens: {sum(c.cost for c in p.chosen)}/{C.BUDGET_TOKENS}   license line: {'yes' if p.license_line else 'no'}")
    return 0


def cmd_doctor(a, lib):
    from . import maint
    h = maint.health(lib)
    if a.json:
        print(json.dumps(h, indent=1)); return 0 if h["score"] >= 60 else 1
    mark = {"ok": c("✓", "32"), "warn": c("!", "33"), "fail": c("✗", "31")}
    print(f"\n  {bold('AssetRAG health')}  {bar(h['score'], 100, 20)} {h['score']}/100\n")
    for k in h["checks"]:
        print(f"  {mark[k['status']]} {k['name']:<16}{k['detail']}")
    for t in ("git", "go", "node", "gh"):
        print(f"  {dim('·')} {t:<16}{shutil.which(t) or dim('not installed (optional: needed for some checks / the updater)')}")
    print()
    return 0 if h["score"] >= 60 else 1


def cmd_weakest(a, lib):
    from . import maint
    w = maint.weakest(lib, a.days)
    if a.json:
        print(json.dumps(w, indent=1)); return 0
    print(f"\n  {bold('Weakest spots')}\n")
    for x in w["categories"][:6]:
        print(f"  {x['category']:<11}{bar(x['have'], x['target'], 18)} {x['have']}/{x['target']} ({x['pct']}%)")
    if w["thin_asset_kinds"]:
        print("\n  thin asset kinds: " + ", ".join(f"{k} ({n})" for k, n in w["thin_asset_kinds"]))
    if w["thin_languages"]:
        print("  thinnest languages (recipes+packages): " + ", ".join(f"{k} ({n})" for k, n in w["thin_languages"]))
    if w["unanswered_asset_queries"] or w["unanswered_knowledge_queries"]:
        print(f"\n  real searches in the last {a.days} days that found little:")
        for x in w["unanswered_asset_queries"][:10]:
            print(f"    asset  {x['q'][:70]}")
        for q in w["unanswered_knowledge_queries"][:10]:
            print(f"    know   {q[:70]}")
    print()
    return 0


def cmd_usage(a, lib):
    from . import maint
    u = maint.usage(lib, a.days)
    if a.json:
        print(json.dumps(u, indent=1)); return 0
    print(f"\n  {bold('Usage')} (last {u['days']} days)")
    print(f"  asset searches: {u['asset_searches']['n']} ({u['asset_searches']['no_result']} found nothing, avg {u['asset_searches']['avg_ms']} ms)")
    k = u["knowledge_searches"]
    print(f"  knowledge searches: {k['n']} ({k['no_result']} found nothing, avg {k['avg_ms']} ms, ~{k['avg_tokens']} tokens each)")
    print("  events: " + (", ".join(f"{e} {n}" for e, n in u["events"].items()) or "none"))
    if u["top_imports"]:
        print("  most imported: " + ", ".join(f"{n} ({c_})" for n, c_ in u["top_imports"][:5]))
    bad = [o for o in u["asset_outcomes"] if o["fail"]]
    if bad:
        print("  reported failures: " + ", ".join(f"{o['name']} ({o['fail']} fail/{o['ok']} ok)" for o in bad[:5]))
    print()
    return 0


def cmd_snapshot(a, lib):
    from . import maint
    print(maint.snapshot(lib.cfg, a.label)); return 0


def cmd_rollback(a, lib):
    from . import maint
    if a.list:
        for s in maint.list_snapshots(lib.cfg):
            print(s.name)
        return 0
    lib.close()
    try:
        t, safety = maint.rollback(lib.cfg, a.which)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr); return 1
    print(f"restored {t.name}\n(previous state saved as {safety.name}; the vector index rebuilds on next use: run `assetrag reindex`)")
    return 0


def cmd_recheck(a, lib):
    from . import maint
    r = maint.recheck(lib.cfg)
    if r.get("skipped"):
        print(r["skipped"]); return 2
    print(f"re-tested {r['checked']} authored recipes/bug fixes against the toolchains installed now: {len(r['failed'])} failing")
    for f in r["failed"]:
        print(f"  FAIL {f['title']}: {f['reason']}")
    return 1 if r["failed"] else 0


def cmd_update(a, lib):
    from . import maint
    if a.install_timer:
        files = maint.install_timer(shutil.which("assetrag") or sys.argv[0], a.osv)
        print("wrote " + ", ".join(str(f) for f in files) + "\nenable it yourself with:\n  systemctl --user daemon-reload && systemctl --user enable --now assetrag-update.timer")
        return 0
    if a.remove_timer:
        gone = maint.remove_timer()
        print("removed " + ", ".join(str(g) for g in gone) + "\n(then: systemctl --user disable --now assetrag-update.timer)" if gone else "no timer installed")
        return 0
    if a.report:
        p = lib.cfg.home / "update-report.md"
        print(p.read_text() if p.exists() else "no report yet: run `assetrag update`")
        return 0
    rep = maint.update(lib, seed=not a.no_seed, licenses_=not a.no_licenses, osv=a.osv, dry_run=a.dry_run)
    print(maint.render_report(rep))
    return 0


def _kline(h):
    tag = {"verified": "✔", "source-confirmed": "◐", "hypothesis": "?", "unverified": "·"}.get(h["verification"], "·")
    body = h.get("fix") or h.get("rule") or h.get("problem") or h.get("use_when") or h.get("correction") or ""
    return f"{tag} {acc(h['kind']):<18} {h['score']:.2f}  {h['title'][:70]}\n      {dim(h['id'])}  {body[:150]}"


def cmd_know(a, lib):
    r = kops.find_knowledge(lib, " ".join(a.query), kinds=a.kind, language=a.language, framework=a.framework, version=a.version,
                            error=a.error, project_dir=a.project, limit=a.limit)
    if a.json:
        print(json.dumps(r, indent=1)); return
    if not r["results"]:
        print("No matching knowledge. (Nothing above the relevance floor; try --error with the exact message.)"); return
    for h in r["results"]:
        print(_kline(h))
    print(dim(f"  {len(r['results'])} shown | show one: assetrag kshow <id>"))


def cmd_kshow(a, lib):
    print(json.dumps(kops.get_knowledge(lib, a.id), indent=1))


def cmd_rules(a, lib):
    r = kops.get_rules(lib, " ".join(a.task), a.language, a.framework, a.project)
    if a.json:
        print(json.dumps(r, indent=1)); return
    print(bold("must")); [print(f"  - {x['rule']}") for x in r["must"]]
    if r["relevant"]:
        print(bold("relevant")); [print(f"  - [{x.get('severity')}] {x['rule']}") for x in r["relevant"]]
    print(dim(f"  {r['more']} more rules available (assetrag know --kind rule ...)"))


def cmd_codescan(a, lib):
    text = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(errors="replace")
    r = kops.scan_code(lib, text, a.language)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for f in r["findings"]:
            print(f"{a.file}:{f['line']}: [{f['severity']}] {f['message']} ({f['rule']})")
        print(dim(r["note"]))
    return 1 if r["count"] else 0


def cmd_kstats(a, lib):
    print(json.dumps(knowledge.stats(lib), indent=1))


def cmd_fail(a, lib):
    r = kops.record_failure(lib, a.attempted, a.failed, a.hypothesis, a.verified, a.evidence, a.correction, a.detect_earlier,
                            a.prevention, a.bugfix, a.language, None, a.project)
    print(json.dumps(r))


def cmd_mine(a, lib):
    from . import mine
    lib.release_vec()
    lib.lock_wait = 300
    r = mine.mine(lib, set(a.only) if a.only else None, a.per_repo)
    print(json.dumps({k: v for k, v in r.items() if k != "repos"}))


def cmd_repomap(a):
    from . import repomap
    try:
        m = repomap.build(a.path, a.budget, a.focus, a.changed, not a.no_tests)
    except NotADirectoryError:
        print(f"error: not a directory: {a.path}", file=sys.stderr); return 1
    if a.json:
        print(json.dumps({"files": m.files_total, "shown": m.files_shown, "tokens": m.tokens, "map": m.text, "ranked": m.ranked[:200], "notes": m.notes}, indent=1))
    else:
        print(m.text)
        if m.notes:
            print(dim("# skipped: " + "; ".join(m.notes)), file=sys.stderr)
    return 0


def cmd_seed(a, lib):
    from .seeds import load as seed_load
    lib.release_vec()
    print(seed_load.seed(verify="off" if a.no_verify else "auto"))


def build_parser():
    p = argparse.ArgumentParser(prog="assetrag", description="License-aware local asset library + engineering memory for Claude Code.",
                                epilog="examples:\n  assetrag search 'go cli progress bar' --category cli\n  assetrag import a_0123456789ab --to ~/proj\n  assetrag stats",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"assetrag {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_); s.set_defaults(fn=fn); return s
    s = add("stats", cmd_stats, "inventory vs launch minimums"); s.add_argument("--json", action="store_true")
    s = add("search", cmd_search, "hybrid search"); s.add_argument("query", nargs="+")
    s.add_argument("-c", "--category"); s.add_argument("-f", "--framework"); s.add_argument("-l", "--language")
    s.add_argument("--profile", choices=["closed_source", "saas", "open_source"]); s.add_argument("-n", "--limit", type=int, default=8)
    s.add_argument("--json", action="store_true")
    s = add("show", cmd_show, "asset details"); s.add_argument("id"); s.add_argument("--code", action="store_true"); s.add_argument("--json", action="store_true")
    s = add("license", cmd_license, "license verdict (exit 2 if not usable)"); s.add_argument("id", nargs="?"); s.add_argument("--spdx")
    s.add_argument("--profile"); s.add_argument("--no-notices", action="store_true")
    s = add("import", cmd_import, "copy an asset into a project + notices"); s.add_argument("id"); s.add_argument("--to", required=True)
    s.add_argument("--dest", default="vendor/assetrag"); s.add_argument("--profile"); s.add_argument("--no-notices", action="store_true"); s.add_argument("--overwrite", action="store_true")
    s = add("populate", cmd_populate, "ingest the curated source catalog (resumable)"); s.add_argument("only", nargs="*"); s.add_argument("--rerun", action="store_true")
    s = add("discover", cmd_discover, "find new whole-repo candidates via GitHub search and ingest them"); s.add_argument("kind"); s.add_argument("--limit", type=int, default=30)
    s = add("ingest", cmd_ingest, "background ingestion service: run|start|stop|status"); s.add_argument("sub", choices=["run", "start", "stop", "status"])
    s.add_argument("--only", nargs="*"); s.add_argument("--discover", nargs="*", help="kind:limit e.g. go-backend:40"); s.add_argument("args", nargs="*")
    s = add("reindex", cmd_reindex, "(re)embed assets missing from the vector store"); s.add_argument("--force", action="store_true")
    s = add("audit", cmd_audit, "license/secret audit of a project"); s.add_argument("project"); s.add_argument("--profile")
    s = add("lesson", cmd_lesson, "engineering memory"); ls = s.add_subparsers(dest="sub", required=True)
    a_ = ls.add_parser("add"); [a_.add_argument(f"--{x.replace('_','-')}", default="") for x in ("framework", "root_cause", "symptoms", "prevention", "evidence")]
    a_.add_argument("--title", required=True); a_.add_argument("--category", required=True); a_.add_argument("--solution", required=True)
    a_.add_argument("--confidence", default="medium"); a_.add_argument("--project")
    q_ = ls.add_parser("search"); q_.add_argument("query", nargs="+"); q_.add_argument("--framework"); q_.add_argument("--project")
    for x in (a_, q_): x.set_defaults(fn=cmd_lesson)
    s = add("outcome", cmd_outcome, "record whether an imported asset worked"); s.add_argument("id"); s.add_argument("result", choices=["ok", "fail"]); s.add_argument("--detail", default="")
    s = add("scan", cmd_scan, "ClamAV scan + security-report.md"); s.add_argument("path", nargs="?", default="."); s.add_argument("--report", default="security-report.md")
    s = add("bench", cmd_bench, "benchmarks: retrieval (free) or claude-ab (spends API budget, needs --execute)")
    s.add_argument("which", choices=["retrieval", "knowledge", "judged", "claude-ab"]); s.add_argument("--execute", action="store_true"); s.add_argument("-v", "--verbose", action="store_true"); s.add_argument("--repeats", type=int, default=1); s.add_argument("--out")
    s = add("know", cmd_know, "search bug fixes, rules, playbooks, failures, recipes"); s.add_argument("query", nargs="*")
    s.add_argument("-k", "--kind", action="append", choices=sorted(knowledge.KINDS)); s.add_argument("-l", "--language"); s.add_argument("-f", "--framework")
    s.add_argument("--version"); s.add_argument("--error", help="exact error text (matched by normalised signature)"); s.add_argument("--project")
    s.add_argument("-n", "--limit", type=int, default=5); s.add_argument("--json", action="store_true")
    s = add("kshow", cmd_kshow, "full knowledge entry"); s.add_argument("id")
    s = add("rules", cmd_rules, "engineering rules for a task (compact)"); s.add_argument("task", nargs="*"); s.add_argument("-l", "--language")
    s.add_argument("-f", "--framework"); s.add_argument("--project"); s.add_argument("--json", action="store_true")
    s = add("scan-code", cmd_codescan, "run rule detectors over a file (exit 1 if findings)"); s.add_argument("file", help="path or - for stdin")
    s.add_argument("-l", "--language"); s.add_argument("--json", action="store_true")
    add("kstats", cmd_kstats, "knowledge inventory, quality and retrieval stats")
    s = add("fail", cmd_fail, "record a failure memory (hypothesis vs verified conclusion)"); s.add_argument("--attempted", required=True)
    s.add_argument("--failed", required=True); s.add_argument("--hypothesis", default=""); s.add_argument("--verified", default=""); s.add_argument("--evidence", default="")
    s.add_argument("--correction", default=""); s.add_argument("--detect-earlier", default=""); s.add_argument("--prevention", default="")
    s.add_argument("--bugfix", default=""); s.add_argument("--language", default=""); s.add_argument("--project")
    s = add("mine", cmd_mine, "mine source-confirmed bug fixes from permissively licensed changelogs"); s.add_argument("--only", nargs="*", metavar="OWNER/REPO", help="mine just these repos (default: all 15)"); s.add_argument("--per-repo", type=int, default=120, help="max fixes kept per repo (default 120)")
    s = add("context", cmd_context, "show what the prompt hook would inject for a prompt, with scores and drop reasons"); s.add_argument("prompt")
    s.add_argument("--cwd", default=".", help="project directory used for language/framework detection (default: .)")
    s = add("repomap", cmd_repomap, "ranked outline of a codebase (files, classes, function signatures) within a token budget"); s.add_argument("path", nargs="?", default=".")
    s.add_argument("-b", "--budget", type=int, default=2000, help="approximate token budget (default 2000)"); s.add_argument("-f", "--focus", action="append", default=[], metavar="PATH|SYMBOL", help="file or symbol you are working on (repeatable): shown in full, and its neighbours rank higher")
    s.add_argument("--changed", action="store_true", help="focus on files with uncommitted changes (git)"); s.add_argument("--no-tests", action="store_true", help="leave test files out"); s.add_argument("--json", action="store_true")
    s = add("weakest", cmd_weakest, "where the library is thinnest, plus real searches that found little"); s.add_argument("--days", type=int, default=30); s.add_argument("--json", action="store_true")
    s = add("usage", cmd_usage, "what gets searched, imported and reported as failing"); s.add_argument("--days", type=int, default=30); s.add_argument("--json", action="store_true")
    s = add("snapshot", cmd_snapshot, "copy the database to <home>/snapshots (keeps the newest 5)"); s.add_argument("label", nargs="?", default="manual")
    s = add("rollback", cmd_rollback, "restore the newest (or a named) snapshot; the current state is snapshotted first"); s.add_argument("which", nargs="?"); s.add_argument("--list", action="store_true")
    add("recheck", cmd_recheck, "re-run every authored recipe/bug-fix test against the toolchains installed now (no writes)")
    s = add("update", cmd_update, "opt-in upkeep: snapshot, re-test recipes, re-check upstream licences (needs `gh`), flag archived repos; never deletes")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--no-seed", action="store_true"); s.add_argument("--no-licenses", action="store_true")
    s.add_argument("--osv", action="store_true", help="also fetch advisory counts from api.osv.dev (sends package names only)"); s.add_argument("--report", action="store_true", help="show the last report")
    s.add_argument("--install-timer", action="store_true", help="write a weekly systemd USER timer (you enable it yourself)"); s.add_argument("--remove-timer", action="store_true")
    s = add("seed", cmd_seed, "(re)verify and load the built-in bug fixes, rules, playbooks and recipes")
    s.add_argument("--no-verify", action="store_true", help="load without executing the tests (entries are marked unverified)")
    s = add("setup", None, "first-run setup: config, built-in knowledge, MCP registration hint (safe to re-run)")
    s.add_argument("--embed-url", help="OpenAI-compatible embedding endpoint, e.g. http://127.0.0.1:8888/v1")
    s.add_argument("--keyword-only", action="store_true", help="never use an embedding server (fully offline)")
    s.add_argument("--no-verify", action="store_true", help="do not execute recipe tests (faster; entries are marked unverified)")
    s.add_argument("--register", action="store_true", help="also run `claude mcp add assetrag` if the claude CLI is installed")
    s = add("uninstall", None, "remove config and timer units; --purge also deletes the data dir (dry run unless --yes)")
    s.add_argument("--yes", action="store_true"); s.add_argument("--purge", action="store_true", help="also delete the data dir (library, knowledge, snapshots)")
    s = add("hook", lambda a, lib: 0, "Claude Code hook entry points"); s.add_argument("event", choices=["prompt"])
    s = add("doctor", cmd_doctor, "health score: database, embeddings, sandbox, ClamAV, licences, staleness, speed"); s.add_argument("--json", action="store_true")
    return p


def _hook(event: str) -> int:
    """Never raises, never blocks the prompt: prints hook JSON or nothing."""
    from contextlib import contextmanager

    @contextmanager
    def factory():
        L = Library(lock_wait=2.0)
        try:
            L.vec
            yield L
        finally:
            L.close()
    try:
        out = kops.hook_prompt(sys.stdin.read(), factory)
        if out:
            print(out)
    except Exception:  # noqa: BLE001
        pass
    return 0


def main(argv=None):
    a = build_parser().parse_args(argv)
    if a.cmd == "hook":
        return _hook(a.event)
    if a.cmd in ("setup", "uninstall"):         # must work before any library or embedding server exists
        from . import setup_cmd
        try:
            if a.cmd == "setup":
                return setup_cmd.run_setup(a.embed_url, a.keyword_only, "off" if a.no_verify else "auto", a.register)
            return setup_cmd.run_uninstall(a.yes, a.purge)
        except (OSError, ValueError, EmbedError, knowledge.KError) as e:
            print(f"error: {e}", file=sys.stderr); return 1
        except KeyboardInterrupt:
            return 130
    if a.cmd == "repomap":                      # pure filesystem work: no library, no vector-store lock
        try:
            return cmd_repomap(a)
        except BrokenPipeError:
            return 0
    try:
        cfg = load()
        lib = Library(cfg)
        try:
            return a.fn(a, lib) or 0
        finally:
            lib.close()
    except (ops.OpError, EmbedError, knowledge.KError) as e:
        print(f"error: {e}", file=sys.stderr); return 1
    except BrokenPipeError:
        try: sys.stdout.close()
        except Exception: pass
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
