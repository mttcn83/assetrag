import subprocess, sys, os

def run(*a, home):
    env = {**os.environ, "ASSETRAG_HOME": str(home), "NO_COLOR": "1", "XDG_CONFIG_HOME": str(home / "cfg")}
    return subprocess.run([sys.executable, "-m", "assetrag.cli", *a], capture_output=True, text=True, env=env)

def test_help_version(tmp_path):
    r = run("--help", home=tmp_path); assert r.returncode == 0 and "examples:" in r.stdout
    r = run("--version", home=tmp_path); assert "1.0.0" in r.stdout

def test_stats_empty_and_json(tmp_path):
    r = run("stats", home=tmp_path); assert r.returncode == 0 and "NOT met" in r.stdout and "\x1b" not in r.stdout
    r = run("stats", "--json", home=tmp_path); import json; assert json.loads(r.stdout)["total"] == 0

def test_bad_args(tmp_path):
    assert run("bogus", home=tmp_path).returncode == 2
    r = run("license", "--spdx", "CC-BY-NC-4.0", home=tmp_path); assert r.returncode == 2
    assert run("license", "--spdx", "MIT", home=tmp_path).returncode == 0
