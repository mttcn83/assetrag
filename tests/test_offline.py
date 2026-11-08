"""Keyword-only mode (embed_mode=off): the whole read/write path works with no embedding server and no vector store."""
import pytest

from assetrag import knowledge as K, ops, search
from assetrag.config import Config
from assetrag.embed import EmbedError
from assetrag.library import Library
from conftest import cand
from test_knowledge import bug


@pytest.fixture
def off(tmp_path):
    L = Library(Config(home=tmp_path / "h", embed_mode="off", embed_url="http://127.0.0.1:1/v1").ensure())   # nothing listens on :1
    yield L
    L.close()


def test_knowledge_round_trip_without_any_server(off):
    assert K.add(off, bug())["action"] == "added"
    assert K.add(off, bug())["action"] == "duplicate"
    hit = K.search(off, "", error="KeyError: 'id'")
    assert hit["results"][0]["match"] == "exact-error"
    kw = K.search(off, "payload lacks id when parsing a KeyError")
    assert kw["results"] and kw["results"][0]["title"].startswith("KeyError")
    assert not K.search(off, "how tall is mount everest")["results"]


def test_asset_search_is_keyword_only_but_honest(off):
    off.admit(cand(name="token-bucket rate limiter", description="rate limiter with a token bucket", category="backend"))
    off.admit(cand(name="markdown renderer", description="render markdown to html", files={"m.py": b"def render(t):\n    return t\n" * 3}))
    r = ops.search_assets(off, "rate limiter token bucket")
    assert r["results"] and "rate limiter" in r["results"][0]["name"] and r["results"][0]["confidence"] == "high"
    miss = ops.search_assets(off, "quantum chromodynamics lattice")
    assert not miss["results"] and miss["verdict"] == "no_good_match"
    assert off.reindex() == 0


def test_server_outage_in_auto_mode_degrades_instead_of_failing(tmp_path):
    L = Library(Config(home=tmp_path / "h2", embed_mode="off").ensure())
    L.admit(cand(name="retry helper", description="retry with exponential backoff"))
    L.cfg.embed_mode = "auto"
    L.cfg.embed_url = "http://127.0.0.1:1/v1"
    L._vec = None
    from assetrag.embed import Embedder
    L.emb = Embedder(L.cfg, L.con)
    r = search.search_ex(L, "retry with exponential backoff")
    assert r["degraded"] and r["results"] and r["results"][0]["name"] == "retry helper"
    L.close()
