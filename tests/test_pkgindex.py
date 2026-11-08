import json

from assetrag import knowledge, pkgindex as P


def node(license_text=None, license_file="LICENSE", manifest=("go.mod", None), **kw):
    n = {"nameWithOwner": "o/r", "description": "d", "stargazerCount": 500, "isArchived": False,
         "defaultBranchRef": {"target": {"oid": "a" * 40}}}
    n.update(kw)
    if license_text is not None:
        n[f"f{P.LICENSE_FILES.index(license_file)}"] = {"text": license_text}
    if manifest[1] is not None:
        n[f"f{len(P.LICENSE_FILES) + P.MANIFESTS.index(manifest[0])}"] = {"text": manifest[1]}
    return n


def mit_text():
    return ("MIT License\n\nCopyright (c) 2020 Someone\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this "
            "software and associated documentation files (the \"Software\"), to deal in the Software without restriction, including without "
            "limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to "
            "permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\nThe above copyright notice and "
            "this permission notice shall be included in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", "
            "WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A "
            "PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER "
            "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR "
            "OTHER DEALINGS IN THE SOFTWARE.\n")


def test_license_is_fingerprinted_from_the_file_text():
    spdx, f, why = P.verified_license(node(mit_text()))
    assert (spdx, f, why) == ("MIT", "LICENSE", "")


def test_missing_or_unrecognised_or_copyleft_licence_is_not_verified():
    assert P.verified_license(node())[0] is None
    assert "not recognised" in P.verified_license(node("all rights reserved, do whatever I say"))[2]
    gpl = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n" + "x" * 50
    assert P.verified_license(node(gpl))[0] is None


def test_parse_list_keeps_github_entries_with_their_category_and_skips_meta_sections():
    md = ("# Awesome Foo\n\n## Contents\n- [x](https://github.com/a/skip)\n\n## Parsing\n"
          "- [procfs](https://github.com/prometheus/procfs) - parse /proc\n* [other](https://example.com/x) - not github\n"
          "- [Dup](https://github.com/prometheus/procfs) - again\n### Sub Category\n- [fuzzy](https://github.com/sahilm/fuzzy.git/) - match\n")
    assert P.parse_list(md) == [("Parsing", "prometheus", "procfs"), ("Sub Category", "sahilm", "fuzzy")]


def test_package_name_from_manifest():
    assert P.package_name(node(manifest=("go.mod", "module github.com/x/y/v2\n\ngo 1.21\n")), "x/y", "go") == "github.com/x/y/v2"
    assert P.package_name(node(manifest=("package.json", json.dumps({"name": "@s/pkg"}))), "s/pkg", "npm") == "@s/pkg"
    assert P.package_name(node(), "o/thing", "npm") == "thing"


def test_entry_passes_the_knowledge_gate_shape():
    e = P._entry("o/r", "go", node(mit_text()), "MIT", "LICENSE", purpose="does x", when_to_use="need x", alternatives=["a/b"], install="go get x")
    assert e["kind"] == "package" and e["verification"] == "source-confirmed" and e["source_url"].startswith("https://github.com/o/r/tree/")
    assert e["data"]["license_evidence"]["sha256"] and e["data"]["license"] == "MIT"
    assert all(k in e["data"] and e["data"][k] for k in knowledge.REQUIRED["package"])


def test_one_missing_repo_does_not_take_its_whole_batch_down(monkeypatch):
    """gh exits non-zero if ANY aliased repo is missing; the batch must split so only that repo is lost."""
    def fake_gh(args, timeout=120):
        q = next(a for a in args if a.startswith("query="))
        if "ghost" in q:
            raise RuntimeError("Could not resolve to a Repository")
        return json.dumps({"data": {f"r{i}": {"nameWithOwner": "ok"} for i in range(8)}})
    monkeypatch.setattr(P, "gh", fake_gh)
    pairs = [("o", f"r{i}") for i in range(5)] + [("o", "ghost")] + [("o", f"s{i}") for i in range(4)]
    out = P.fetch_repos(pairs, batch=20, log=lambda *a: None)
    assert out["o/ghost"] == {"error": "repo not found / inaccessible"}
    assert all("error" not in out[f"o/r{i}"] for i in range(5)) and all("error" not in out[f"o/s{i}"] for i in range(4))
