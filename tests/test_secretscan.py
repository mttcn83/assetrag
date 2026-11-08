from assetrag import kops, secretscan

# built from parts so this file itself never contains a token-shaped literal
GH = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def test_rules_loaded_with_source_notice():
    assert secretscan.rule_count() > 150
    d = secretscan._load()
    assert "MIT" in d["notice"] and d["source"].startswith("https://github.com/gitleaks/gitleaks/")


def test_detects_token_and_never_echoes_it():
    hits = secretscan.scan(f'token = "{GH}"\n')
    assert any(h["rule"] == "github-pat" for h in hits)
    assert all(GH not in str(h) for h in hits)


def test_ignores_env_lookups_and_plain_code():
    assert secretscan.scan('password = os.environ["PW"]\nname = "hello world"\nx = 3\n') == []


def test_scan_code_reports_secret_as_must_without_leaking(monkeypatch):
    class L:  # scan_code only needs rule detectors from the store; none needed for this check
        pass
    monkeypatch.setattr(kops, "_rules", lambda lib: [])
    r = kops.scan_code(L(), f'API = "{GH}"\n')
    assert r["count"] >= 1 and all(f["severity"] == "must" and GH not in f["excerpt"] for f in r["findings"])
