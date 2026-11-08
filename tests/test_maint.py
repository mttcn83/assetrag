import json
import sqlite3

import pytest

from assetrag import knowledge as K, maint, ops, pkgindex, search
from assetrag.config import Config
from assetrag.library import Library
from conftest import cand
from test_knowledge import bug


@pytest.fixture
def L(tmp_path):
    lib = Library(Config(home=tmp_path / "h", embed_mode="off").ensure())
    yield lib
    lib.close()


def test_snapshot_and_rollback_round_trip_with_safety_copy(L):
    K.add(L, bug())
    snap = maint.snapshot(L.cfg, "before")
    K.add(L, bug(title="IndexError: list index out of range", data={**bug()["data"], "error": "IndexError: list index out of range"}))
    assert K.stats(L)["total_active"] == 2
    L.con.close()
    target, safety = maint.rollback(L.cfg, "before")
    assert target == snap and safety.exists() and "before-rollback" in safety.name
    con = sqlite3.connect(L.cfg.db_path)
    assert con.execute("SELECT count(*) FROM knowledge").fetchone()[0] == 1
    con.close()
    # the rollback itself can be undone
    maint.rollback(L.cfg, "before-rollback")
    con = sqlite3.connect(L.cfg.db_path)
    assert con.execute("SELECT count(*) FROM knowledge").fetchone()[0] == 2
    con.close()


def test_rollback_with_nothing_to_restore_or_unknown_name(L):
    with pytest.raises(FileNotFoundError):
        maint.rollback(L.cfg)
    maint.snapshot(L.cfg, "a")
    with pytest.raises(FileNotFoundError):
        maint.rollback(L.cfg, "nope")


def test_only_the_newest_snapshots_are_kept(L):
    import time
    for i in range(maint.SNAP_KEEP + 3):
        maint.snapshot(L.cfg, f"s{i}")
        time.sleep(0.01)
    assert len(maint.list_snapshots(L.cfg)) == maint.SNAP_KEEP


def test_health_flags_an_empty_library_and_scores_a_populated_one_higher(L):
    empty = maint.health(L)
    assert any(c["name"] == "knowledge" and c["status"] == "fail" for c in empty["checks"])
    K.add(L, bug())
    L.admit(cand())
    better = maint.health(L)
    assert better["score"] > empty["score"] and 0 <= better["score"] <= 100
    assert any(c["name"] == "embeddings" and c["status"] == "warn" for c in better["checks"])      # keyword-only is called out, not hidden


def test_weakest_and_usage_run_on_a_small_library(L):
    L.admit(cand())
    ops.search_assets(L, "quantum chromodynamics")
    w = maint.weakest(L)
    assert w["categories"] and any("quantum" in q["q"] for q in w["unanswered_asset_queries"])
    assert maint.usage(L)["asset_searches"]["n"] == 1


def test_flagged_assets_disappear_from_search_but_stay_on_disk(L):
    aid = L.admit(cand(name="token bucket limiter", description="rate limiter"))
    assert search.search_ex(L, "token bucket limiter")["results"]
    L.con.execute("INSERT INTO asset_flags VALUES (?,?,?)", (aid, "licence changed", 0)); L.con.commit()
    assert not search.search_ex(L, "token bucket limiter")["results"]
    assert L.get_row(aid) is not None


def test_update_quarantines_relicensed_packages_and_flags_archived(L, monkeypatch):
    def pkg(repo):
        return {"kind": "package", "title": f"{repo} (go, MIT)", "summary": "x library", "provenance": "authored", "verification": "source-confirmed", "confidence": "high",
                "languages": ["go"], "tags": ["package-index"], "source_url": f"https://github.com/{repo}", "source_license": "MIT", "source_name": repo,
                "data": {"package": repo, "ecosystem": "go", "repo": repo, "license": "MIT", "purpose": "x library for tests", "when_to_use": "tests", "key": f"pkg:go:{repo}",
                         "license_evidence": {"file": "LICENSE", "sha256": "0" * 64, "commit": "abc", "method": "m"}}}
    for r in ("o/relicensed", "o/archived", "o/fine"):
        assert K.add(L, pkg(r))["action"] == "added"
    MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the \"Software\"), to deal in the Software without restriction"
    nodes = {"o/relicensed": {"f0": {"text": "GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007"}},
             "o/archived": {"isArchived": True, "f0": {"text": MIT}}, "o/fine": {"f0": {"text": MIT}}}
    monkeypatch.setattr(pkgindex, "fetch_repos", lambda pairs, batch=20, log=print: {f"{o}/{r}": nodes[f"{o}/{r}"] for o, r in pairs})
    monkeypatch.setattr(pkgindex, "verified_license", lambda node, allowed=pkgindex.PERMISSIVE: ("MIT", "LICENSE", "") if "GNU" not in node["f0"]["text"] else (None, "LICENSE", "not permissive (GPL-3.0)"))
    monkeypatch.setattr(maint, "_gh_ready", lambda: True)
    dry = maint.update(L, seed=False, dry_run=True)
    assert len(dry["relicensed"]) == 1 and len(dry["archived"]) == 1 and not maint.list_snapshots(L.cfg)
    assert L.con.execute("SELECT count(*) FROM knowledge WHERE status='quarantined'").fetchone()[0] == 0          # dry run changed nothing
    rep = maint.update(L, seed=False)
    assert rep["relicensed"][0]["what"] == "o/relicensed" and rep["archived"][0]["what"] == "o/archived" and rep["snapshot"]
    assert L.con.execute("SELECT count(*) FROM knowledge WHERE status='quarantined'").fetchone()[0] == 1
    assert (L.cfg.home / "update-report.md").exists() and "quarantined, not deleted" in (L.cfg.home / "update-report.md").read_text()
    hit = K.search(L, "x library for tests", kinds=["package"], limit=5)["results"]
    names = {h["package"] for h in hit}
    assert "o/relicensed" not in names and "o/fine" in names
    arch = next(h for h in hit if h["package"] == "o/archived")
    assert "archived" in arch["maintenance"]


def test_update_without_github_cli_says_so_instead_of_pretending(L, monkeypatch):
    monkeypatch.setattr(maint, "_gh_ready", lambda: False)
    rep = maint.update(L, seed=False, dry_run=True)
    assert any("NOT re-verified" in n for n in rep["notes"])


def test_timer_units_are_user_level_and_removable(tmp_path, monkeypatch):
    monkeypatch.setattr(maint, "UNIT_DIR", tmp_path / "systemd" / "user")
    svc, tim = maint.install_timer("/usr/bin/assetrag", osv=True)
    assert "ExecStart=/usr/bin/assetrag update --osv" in svc.read_text() and "OnCalendar=weekly" in tim.read_text() and "WantedBy=timers.target" in tim.read_text()
    assert len(maint.remove_timer()) == 2 and not list((tmp_path / "systemd" / "user").iterdir())
