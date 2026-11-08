"""First-run flow through the real CLI entry point, in a throwaway HOME. Uninstall is only ever exercised as a dry run."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from assetrag import setup_cmd


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "ASSETRAG_"))}
    e.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"), NO_COLOR="1")
    return e


def cli(env, *args):
    return subprocess.run([sys.executable, "-m", "assetrag.cli", *args], env=env, capture_output=True, text=True, timeout=300)


def test_setup_then_search_then_doctor(env):
    r = cli(env, "setup", "--keyword-only", "--no-verify")
    assert r.returncode == 0, r.stderr
    assert "AS IS" in r.stdout and "knowledge:" in r.stdout
    cfg = Path(env["XDG_CONFIG_HOME"]) / "assetrag" / "config.json"
    assert json.loads(cfg.read_text())["embed_mode"] == "off"
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"
    k = cli(env, "know", "file watch inotify systemd timer")
    assert k.returncode == 0, k.stderr
    assert "Traceback" not in k.stderr and "inotify" in k.stdout.lower()       # unverified-here seeds must stay findable
    assert "No matching" in cli(env, "know", "unrelated flurble quux").stdout
    d = cli(env, "doctor", "--json")
    assert json.loads(d.stdout)["score"] >= 60
    again = cli(env, "setup", "--no-verify")                     # idempotent
    assert again.returncode == 0 and "0 added" in again.stdout


def test_uninstall_dry_run_removes_nothing(env):
    data = Path(env["XDG_DATA_HOME"]) / "assetrag"
    r = cli(env, "uninstall")
    assert r.returncode == 0 and "re-run with --yes" in r.stdout
    assert "keeping your data dir" in r.stdout and (data / "assetrag.sqlite").exists()     # data is only listed for deletion with --purge
    p = cli(env, "uninstall", "--purge")
    assert str(data) in p.stdout and "re-run with --yes" in p.stdout and (data / "assetrag.sqlite").exists()


def test_uninstall_refuses_foreign_dirs(tmp_path):
    (tmp_path / "docs").mkdir()
    assert not setup_cmd._is_ours(tmp_path)
    assert not setup_cmd._is_ours(Path.home())
    (tmp_path / "assetrag.sqlite").write_text("")
    assert setup_cmd._is_ours(tmp_path)
