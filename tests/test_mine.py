from assetrag import mine


def test_clean_strips_markup_and_trailers():
    assert mine.clean("Fix a crash on {pr}`3805` start. (Pull )") == "Fix a crash on start"
    assert mine.clean("fix(binding): panic on empty value ([#2169](https://x/y/pull/2169))") == "Fix binding: panic on empty value"
    assert mine.clean("Add printable details. PR by") == "Add printable details"


def test_parse_headings_with_prefixes_and_non_fixes():
    text = "\n".join([
        "## Unreleased", "", "- Fix TypeError in nothing", "",
        "## Version 8.3.3", "", "- Fix TypeError when rendering help for an option with a weird default. {pr}`1`",
        "- Add a shiny new option for fun", "",
        "## Gin v1.9.0", "", "### Bug Fixes", "- fix(context): panic on NegotiateFormat index out of range ([#1](https://x))",
    ])
    got = [(f.version, f.text) for f in mine.parse(text)]
    assert ("8.3.3", "Fix TypeError when rendering help for an option with a weird default") in got
    assert any(v == "1.9.0" and "panic" in t for v, t in got)
    assert not any("shiny" in t for _, t in got)
    assert not any("nothing" in t for _, t in got)   # unreleased section has no version to pin
