"""Project-aware ranking, installed-toolchain nudge, asset/package de-duplication, manifest parsing."""
import json

from assetrag import knowledge as K, kops, manifest, toolchain
from conftest import cand
from test_knowledge import bug


def pkg(name, repo, purpose):
    return {"kind": "package", "title": f"{name} (pypi, MIT)", "summary": purpose, "languages": ["python"], "tags": ["package-index", "python"],
            "data": {"package": name, "repo": repo, "ecosystem": "pypi", "license": "MIT", "purpose": purpose, "when_to_use": purpose, "when_not": "never",
                     "alternatives": [], "install": f"pip install {name}", "key": f"pkg:pypi:{name}",
                     "license_evidence": {"method": "LICENSE text fingerprinted", "file": "LICENSE", "commit": "abc", "sha256": "0" * 64}},
            "verification": "source-confirmed", "provenance": "third-party", "confidence": "medium", "source_url": f"https://github.com/{repo}/tree/abc"}


def test_manifest_names_normalise(tmp_path):
    (tmp_path / "requirements.txt").write_text("Typing_Extensions>=4\nrich[extra]==13\n")
    (tmp_path / "go.mod").write_text("module m\n\nrequire github.com/spf13/cobra v1.8.0\n")
    n = manifest.names(tmp_path)
    assert {"typing-extensions", "rich", "cobra", "github.com/spf13/cobra"} <= n


def test_package_already_in_project_ranks_first(lib, tmp_path):
    K.add(lib, pkg("colorlib", "a/colorlib", "terminal colour output and ansi styling helper"))
    K.add(lib, pkg("rich", "b/rich", "terminal colour output and ansi styling helper"))
    (tmp_path / "requirements.txt").write_text("rich\n")
    plain = K.search(lib, "terminal colour output ansi styling", kinds=["package"], language="python", min_score=0.0)["results"]
    aware = K.search(lib, "terminal colour output ansi styling", kinds=["package"], language="python", project_dir=str(tmp_path), min_score=0.0)["results"]
    assert aware[0]["package"] == "rich" and "already a dependency" in aware[0]["in_project"]
    assert "in_project" not in plain[0] or plain[0]["package"] != "colorlib"


def test_installed_toolchain_is_detected_and_only_nudges(lib):
    assert toolchain.installed("python").count(".") == 2 and toolchain.installed("cobol") is None
    K.add(lib, bug(version_spec=">=99.0"))                    # affects a Python we do not run: must still be returned
    assert K.search(lib, "", error="KeyError: 'id'", language="python")["results"]


def test_asset_and_package_for_same_repo_collapse(lib):
    aid = lib.admit(cand(name="colorlib helper", repo="a/colorlib", description="terminal colour output and ansi styling helper"), embed=False)
    K.add(lib, pkg("colorlib", "a/colorlib", "terminal colour output and ansi styling helper"))
    r = kops.find_knowledge(lib, "terminal colour output ansi styling", kinds=["package"], language="python", include_assets=True)
    pk = next(x for x in r["results"] if x["kind"] == "package")
    assert pk["repo"] == "a/colorlib"
    assert aid in pk.get("also_in_library", []) and all(a["id"] != aid for a in r["assets"])
