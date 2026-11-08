import pytest

from assetrag import rerank as R


def row(name, kind="library", category="backend", desc="", tags="[]"):
    return {"name": name, "kind": kind, "category": category, "description": desc, "tags": tags}


def judge(q, r, host="linux", **kw):
    return R.assess(r, R.query_intent(q), host, **kw)


def test_icons_are_wrong_kind_unless_asked_for():
    icon = row("Lucide icon: clipboard copy", "icon", "ui", "Lucide SVG icon")
    assert judge("copy text to the clipboard from a cli", icon)["major"]
    assert not judge("clipboard copy icon", icon)["major"]
    assert judge("clipboard copy icon", icon)["delta"] > judge("copy text to the clipboard", icon)["delta"]


def test_named_templates_need_both_the_kind_and_the_subject():
    ig = lambda s: row("gitignore: " + s, "gitignore", "template", f"gitignore template for {s}")      # noqa: E731
    assert judge("gitignore patterns", ig("HIP"))["major"]                       # nobody named HIP
    assert not judge("gitignore for a rust project", ig("Rust"))["major"]
    assert judge("gitignore for a rust project", ig("Python"))["major"]
    assert judge("desktop notification for a linux app", ig("Linux"))["major"]    # right subject, but nobody asked for a gitignore
    assert not judge("github actions workflow to test a go project", row("GitHub Actions: go", "ci-workflow", "template"))["major"]
    assert judge("go service", row("GitHub Actions: go", "ci-workflow", "template"))["major"]


def test_platform_mismatch_is_major_unless_cross_platform_or_asked_for():
    mac = row("notifier", desc="Send notifications using osascript on macOS")
    assert judge("desktop notification for a linux app", mac)["major"]
    assert not judge("macos notification", mac)["major"]
    assert not judge("desktop notification", mac, host="macos")["major"]
    assert not judge("notification", row("notify", desc="cross-platform notifications for macOS, Windows and Linux"))["major"]
    assert not judge("notification", row("dbus-notify", desc="notifications on Linux and macOS"))["major"]


def test_lexical_coverage_orders_otherwise_equal_candidates():
    full = judge("retry with exponential backoff", row("retry", desc="retry with exponential backoff and jitter"))
    part = judge("retry with exponential backoff", row("retry", desc="retries things"))
    assert full["cov"] > part["cov"] and full["delta"] > part["delta"]
    assert full["why"] and full["why"][0].startswith("matches ")


def test_platform_and_synonym_parsing():
    assert R.query_intent("wayland clipboard")["platform"] == "linux"
    assert R.query_intent("macOS menu bar")["platform"] == "macos"
    assert R.query_intent("colour output")["tokens"] == ["color", "output"]
    assert R.query_intent("go cli for golang")["tokens"] == ["go", "command"] or "go" in R.query_intent("golang")["tokens"]


def test_project_type_nudges_category():
    assert judge("parser", row("p", category="cli"), project_type="cli")["delta"] > judge("parser", row("p", category="cli"))["delta"]


def test_verdict_needs_words_or_semantic_closeness():
    assert R.verdict(0.2, 0.6, 0.72) == "weak" and R.verdict(0.2, 0.8, 0.72) == "good" and R.verdict(0.9, 0.5, 0.72) == "good"


@pytest.mark.parametrize("q,expected", [("icon", True), ("an svg glyph", True), ("cli tool", False)])
def test_wants_icon(q, expected):
    assert R.query_intent(q)["wants_icon"] is expected
