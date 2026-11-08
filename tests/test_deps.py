import httpx

from assetrag import ingest
from assetrag.config import Config


def mk(tmp_path, handler):
    d = ingest.DepLicenses(Config(home=tmp_path / "home").ensure())
    d.http = httpx.Client(transport=httpx.MockTransport(handler))
    d.github = lambda repo: {"uber-go/zap": "MIT", "go-yaml/yaml": "Apache-2.0"}.get(repo)
    return d


def meta(prefix, url):
    return httpx.Response(200, text=f'<html><head><meta name="go-import" content="{prefix} git {url}"></head></html>')


def test_vanity_modules_resolve_through_go_import(tmp_path):
    def h(req):
        host = str(req.url).split("?")[0]
        if host.endswith("go.uber.org/zap/zapcore"):
            return httpx.Response(404)
        if "go.uber.org/zap" in host:
            return meta("go.uber.org/zap", "https://github.com/uber-go/zap")
        if "gopkg.in/yaml.v3" in host:
            return meta("gopkg.in/yaml.v3", "https://github.com/go-yaml/yaml")
        return httpx.Response(404)
    d = mk(tmp_path, h)
    assert d.go("go.uber.org/zap") == "MIT"
    assert d.go("go.uber.org/zap/zapcore") == "MIT"          # falls back to a shorter prefix
    assert d.go("gopkg.in/yaml.v3") == "Apache-2.0"
    assert d.cache["go:go.uber.org/zap"] == "MIT"


def test_unknown_and_non_github_hosts_stay_unknown_and_errors_are_not_cached(tmp_path):
    calls = []

    def h(req):
        calls.append(str(req.url))
        if "gitlab-hosted" in str(req.url):
            return meta("example.org/gitlab-hosted", "https://gitlab.com/o/r")
        if "flaky" in str(req.url):
            raise httpx.ConnectError("boom")
        return httpx.Response(404)
    d = mk(tmp_path, h)
    assert d.go("example.org/gitlab-hosted") is None
    assert d.go("example.org/flaky") is None and "go:example.org/flaky" not in d.cache      # transient failure: retry next time
    assert d.go("nowhere.example/x/y") is None and d.cache.get("go:nowhere.example/x/y", 0) is None


def test_js_deps_ignores_minified_junk():
    src = b'import a from "+u+"; import b from "left-pad"; import c from "@scope/pkg/sub"; const x = require("a b");'
    assert ingest.js_deps({"x.js": src}) == ["@scope/pkg", "left-pad"]
