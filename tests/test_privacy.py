from assetrag import privacy


def test_flags_google_fonts_and_trackers(tmp_path):
    (tmp_path / "index.html").write_text('<link href="https://fonts.googleapis.com/css2?family=Inter" rel="stylesheet">')
    (tmp_path / "app.js").write_text("import x from 'https://unpkg.com/lib'; // https://www.googletagmanager.com/gtag/js")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "skip.js").write_text("https://cdn.jsdelivr.net/x")
    kinds = {(r["file"], r["kind"]) for r in privacy.external_refs(tmp_path)}
    assert ("index.html", "font") in kinds
    assert ("app.js", "cdn") in kinds or ("app.js", "tracker") in kinds
    assert not any("node_modules" in f for f, _ in kinds)


def test_clean_project_reports_nothing(tmp_path):
    (tmp_path / "a.css").write_text("@font-face { src: url('./inter.woff2'); }")
    assert privacy.external_refs(tmp_path) == []
