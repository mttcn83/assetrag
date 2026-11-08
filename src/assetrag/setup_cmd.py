"""`assetrag setup` (first-run, idempotent) and `assetrag uninstall`. Neither needs an existing library or an embedding server."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from . import config as cfgmod

NOTICE = ("AssetRAG is provided AS IS, with no warranty and no legal advice. You (not the author) are responsible for the licences of anything you or your "
          "AI agent copy into a project. Read DISCLAIMER.md.")


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb" else s


def _ok(msg: str): print(f"  {_c('32', '✓')} {msg}")
def _note(msg: str): print(f"  {_c('33', '!')} {msg}")


def embed_reachable(url: str, key: str = "", timeout: float = 2.0) -> bool:
    """Any HTTP answer (even 401) means a server is there; connection errors mean nothing is listening."""
    try:
        httpx.get(url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


def _write_config(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)                       # it can hold an embedding key


def run_setup(embed_url: str | None = None, keyword_only: bool = False, verify: str = "auto", register: bool = False,
              provider: str | None = None, embed_model: str | None = None, reconfigure: bool = False, io=None) -> int:
    from . import banner, embed_setup
    banner.show()
    print(f"  {_c('1', 'AssetRAG setup')}\n  {NOTICE}\n")
    io = io or embed_setup.IO()
    cf = cfgmod._config_file()
    data = json.loads(cf.read_text()) if cf.exists() else {}
    asked = bool(provider or embed_url or embed_model or reconfigure)
    if keyword_only:
        data["embed_mode"] = "off"
    elif asked or "embed_mode" not in data:                   # first run, or the user asked to change it
        updates = embed_setup.configure(provider, embed_url, embed_model, io)
        if updates:
            data.update(updates)
        else:
            data["embed_mode"] = "off"
    _write_config(cf, data)
    mode = data["embed_mode"]
    _ok(f"config {cf} (embed_mode={mode})")
    if mode == "off":
        _note("search is keyword-only (works fully offline, less fuzzy for paraphrased queries). "
              "Turn on semantic search any time: `assetrag setup --reconfigure`.")
    else:
        _ok(f"embeddings: {data.get('embed_model')} via {data.get('embed_provider') or data.get('embed_url')}"
            + (f" (key from ${data['embed_key_env']})" if data.get("embed_key_env") else ""))
    cfg = cfgmod.load()
    _ok(f"data dir {cfg.home}")

    from .seeds import load as seed_load
    from .library import Library
    lib = Library(cfg, lock_wait=60)
    lib.release_vec()
    lib.close()
    print("  loading built-in knowledge (recipes, bug fixes, rules, playbooks) ...")
    res = seed_load.seed(log=lambda m: None, verify=verify)
    _ok(f"knowledge: {res['added']} added, {res['updated']} updated, {res['duplicate']} already there, {res['rejected']} rejected")
    if res["rejected"]:
        _note("rejected = a test really failed on this machine: " + "; ".join(f"{k} ({v[:60]})" for k, v in res["rejected_detail"][:3]))
    if res.get("unverified_here"):
        _note(f"{res['unverified_here']} entries loaded as 'unverified' because this machine could not execute their tests "
              f"(no usable sandbox, a missing toolchain, or --no-verify; `assetrag doctor` says which). Fix that and re-run `assetrag seed`.")

    for tool, why in (("bwrap", "sandbox for running tests"), ("clamscan", "malware scan on ingest"), ("gh", "licence checks and `assetrag update`"), ("git", "ingest")):
        (_ok if shutil.which(tool) else _note)(f"{tool}: {'found' if shutil.which(tool) else 'not found'} ({why})")

    mcp = shutil.which("assetrag-mcp") or "assetrag-mcp"
    cmd = ["claude", "mcp", "add", "assetrag", "--", mcp]
    if register and shutil.which("claude"):
        r = subprocess.run(cmd, capture_output=True, text=True)
        (_ok if r.returncode == 0 else _note)("registered with Claude Code" if r.returncode == 0 else f"claude mcp add failed: {(r.stderr or r.stdout).strip()[:120]}")
    else:
        print(f"\n  Register with Claude Code:\n    {' '.join(cmd)}")
    print("\n  Try:  assetrag know 'retry with backoff'     assetrag doctor     assetrag repomap .\n")
    return 0


def _is_ours(home: Path) -> bool:
    return home != Path.home() and len(home.parts) >= 3 and ((home / "assetrag.sqlite").exists() or (home / "library").is_dir())


def run_uninstall(yes: bool = False, purge: bool = False) -> int:
    from . import maint
    cfg = cfgmod.Config()                      # defaults only: load() would create the dirs we are about to remove
    cf = cfgmod._config_file()
    if cf.exists():
        d = json.loads(cf.read_text())
        if d.get("home"):
            cfg.home = Path(d["home"]).expanduser()
    if os.environ.get("ASSETRAG_HOME"):
        cfg.home = Path(os.environ["ASSETRAG_HOME"]).expanduser()
    targets = [p for p in (maint.UNIT_DIR / "assetrag-update.timer", maint.UNIT_DIR / "assetrag-update.service", cf.parent) if p.exists()]
    if purge and cfg.home.exists():
        if _is_ours(cfg.home):
            targets.append(cfg.home)
        else:
            _note(f"{cfg.home} does not look like an AssetRAG data dir, so it will not be touched")
    elif cfg.home.exists():
        print(f"keeping your data dir {cfg.home} (library, knowledge, snapshots). Add --purge to delete it too.")
    if not targets:
        print("nothing to remove"); return 0
    print("will remove:" if yes else "would remove (re-run with --yes):")
    for t in targets:
        print(f"  {t}")
    if not yes:
        print("then run:  pipx uninstall assetrag   (or: uv tool uninstall assetrag)\nand:  claude mcp remove assetrag")
        return 0
    if (maint.UNIT_DIR / "assetrag-update.timer").exists() and shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", "assetrag-update.timer"], capture_output=True)
    for t in targets:
        shutil.rmtree(t) if t.is_dir() else t.unlink()
    print("removed. Finish with:  pipx uninstall assetrag   (or: uv tool uninstall assetrag)   and   claude mcp remove assetrag")
    return 0
