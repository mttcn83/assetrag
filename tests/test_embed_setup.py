"""Embedding provider choice: model listing, key handling, the EmbeddingGemma-first flow and its fallbacks. No network, no real servers."""
import httpx
import pytest

from assetrag import banner, embed_setup as E, providers as P
from assetrag.config import Config, load


class Script(E.IO):
    """Scripted answers instead of a terminal; records what was said and which commands would have run."""
    def __init__(self, answers=(), secrets=(), interactive=True):
        super().__init__(interactive)
        self.answers, self.secrets, self.said, self.ran = list(answers), list(secrets), [], []
    def say(self, m=""): self.said.append(m)
    def ok(self, m): self.said.append(m)
    def warn(self, m): self.said.append(m)
    def ask(self, prompt, default=""): return (self.answers.pop(0) if self.answers else "") or default
    def secret(self, prompt): return self.secrets.pop(0) if self.secrets else ""
    def run(self, cmd): self.ran.append(cmd); return 0


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_models_openrouter_endpoint_and_auth():
    def h(req):
        assert req.url.path == "/api/v1/embeddings/models"
        if req.headers.get("authorization") != "Bearer sk-good":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": [{"id": "google/gemini-embedding-2"}, {"id": "thenlper/gte-base"}]})
    p = P.BY_ID["openrouter"]
    assert P.list_models(p, p.url, "sk-good", client=mock_client(h)) == ["google/gemini-embedding-2", "thenlper/gte-base"]
    with pytest.raises(P.ProviderError, match="auth"):
        P.list_models(p, p.url, "bad", client=mock_client(h))


def test_list_models_filters_chat_models_and_finds_gemma():
    def h(req):
        return httpx.Response(200, json={"data": [{"id": "qwen3:4b"}, {"id": "embeddinggemma:latest"}, {"id": "nomic-embed-text"}]})
    ms = P.list_models(P.BY_ID["ollama"], P.BY_ID["ollama"].url, client=mock_client(h))
    assert ms == ["embeddinggemma:latest", "nomic-embed-text"] and P.find_gemma(ms) == "embeddinggemma:latest"
    only_chat = lambda req: httpx.Response(200, json={"data": [{"id": "qwen3:4b"}]})
    assert P.list_models(P.BY_ID["ollama"], P.BY_ID["ollama"].url, client=mock_client(only_chat)) == []


def test_test_embedding_reports_dim_and_server_errors():
    ok = lambda req: httpx.Response(200, json={"data": [{"embedding": [0.1] * 768, "index": 0}]})
    assert P.test_embedding("http://x/v1", "m", client=mock_client(ok)) == 768
    with pytest.raises(P.ProviderError, match="HTTP 400"):
        P.test_embedding("http://x/v1", "m", client=mock_client(lambda r: httpx.Response(400, text="model not found")))


def test_prefixes_only_for_gemma():
    assert P.prefixes_for("embeddinggemma:latest")["query_prefix"].startswith("task:")
    assert P.prefixes_for("google/gemini-embedding-2") == {"query_prefix": "", "doc_prefix": ""}


def patch(monkeypatch, models, dim=768, running=()):
    monkeypatch.setattr(P, "list_models", lambda p, url, key="", **k: models(key) if callable(models) else models)
    monkeypatch.setattr(P, "test_embedding", lambda url, model, key="", **k: dim)
    monkeypatch.setattr(P, "detect_local", lambda timeout=1.0: [P.BY_ID[i] for i in running])


def test_local_with_gemma_uses_it(monkeypatch):
    patch(monkeypatch, ["embeddinggemma:latest"], running=["ollama"])
    io = Script(answers=["1"])                                   # menu: Ollama
    u = E.configure(io=io, env={})
    assert u["embed_model"] == "embeddinggemma:latest" and u["embed_provider"] == "ollama" and u["embed_mode"] == "auto"
    assert u["query_prefix"].startswith("task:") and u["embed_key"] == "" and io.ran == []


def test_local_without_gemma_downloads_q4_from_hf_and_registers(monkeypatch, tmp_path):
    state = {"registered": False}
    patch(monkeypatch, lambda key: ["embeddinggemma:latest"] if state["registered"] else [], running=["ollama"])
    f = tmp_path / "embeddinggemma-300m-Q4_0.gguf"
    monkeypatch.setattr(P, "download_gemma", lambda dest, progress=None, **k: (progress(10, 10), f)[1])
    monkeypatch.setattr(P, "register_gguf", lambda p, path, run: (state.update(registered=True), run(["ollama", "create"]))[1] == 0)
    io = Script(answers=["1", "y"])
    u = E.configure(io=io, env={})
    assert io.ran == [["ollama", "create"]] and u["embed_model"] == "embeddinggemma:latest"


def test_local_download_declined_falls_back_to_keyword_only(monkeypatch):
    patch(monkeypatch, [], running=["ollama"])
    monkeypatch.setattr(P, "download_gemma", lambda *a, **k: pytest.fail("must not download without a yes"))
    io = Script(answers=["1", "n"])
    assert E.configure(io=io, env={}) is None and io.ran == []


def test_download_failure_is_reported_not_raised(monkeypatch):
    patch(monkeypatch, [], running=["lmstudio"])
    def boom(*a, **k): raise P.ProviderError("download interrupted (ReadTimeout); run setup again to resume")
    monkeypatch.setattr(P, "download_gemma", boom)
    io = Script(answers=["2", "y"])
    assert E.configure(io=io, env={}) is None and any("interrupted" in m for m in io.said)


def hf_client(payload: bytes, good_sha=True, support_range=True):
    import hashlib
    sha = hashlib.sha256(payload).hexdigest() if good_sha else "0" * 64
    def h(req):
        if req.url.path.startswith("/api/models/"):
            return httpx.Response(200, json=[{"path": P.GGUF_FILE, "size": len(payload), "lfs": {"oid": sha}}])
        rng = req.headers.get("range")
        if rng and support_range:
            start = int(rng.split("=")[1].split("-")[0])
            return httpx.Response(206, content=payload[start:])
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(h))


def test_download_verifies_sha_and_resumes(tmp_path):
    data = b"GGUF" + bytes(range(256)) * 40
    (tmp_path / (P.GGUF_FILE + ".part")).write_bytes(data[:1000])            # an interrupted earlier attempt
    seen = []
    out = P.download_gemma(tmp_path, lambda d, t: seen.append((d, t)), client=hf_client(data))
    assert out.read_bytes() == data and not (tmp_path / (P.GGUF_FILE + ".part")).exists() and seen[-1] == (len(data), len(data))
    assert P.download_gemma(tmp_path, client=hf_client(data)) == out         # already there and verified: no re-download


def test_download_rejects_corrupt_file_and_deletes_it(tmp_path):
    with pytest.raises(P.ProviderError, match="sha256"):
        P.download_gemma(tmp_path, client=hf_client(b"GGUF" + b"x" * 100, good_sha=False))
    assert list(tmp_path.iterdir()) == []


def test_hosted_without_gemma_asks_which_model_and_uses_env_key(monkeypatch):
    patch(monkeypatch, ["thenlper/gte-base", "google/gemini-embedding-2"])
    io = Script(answers=["4", "y", "1"])                         # OpenRouter, use $OPENROUTER_API_KEY, first listed model
    u = E.configure(io=io, env={"OPENROUTER_API_KEY": "sk-or-xyz"})
    assert u["embed_model"] == "google/gemini-embedding-2"       # recommended model is listed first
    assert u["embed_key"] == "" and u["embed_key_env"] == "OPENROUTER_API_KEY"   # the secret is referenced, not stored
    assert u["query_prefix"] == "" and any("does not offer EmbeddingGemma" in s for s in io.said)


def test_hosted_prompts_for_key_and_retries_bad_one(monkeypatch):
    def models(key):
        if key != "sk-right":
            raise P.ProviderError("auth: the server rejected the API key")
        return ["text-embedding-3-small"]
    patch(monkeypatch, models)
    io = Script(answers=["5", "1"], secrets=["sk-wrong", "sk-right"])       # OpenAI, then pick model 1
    u = E.configure(io=io, env={})
    assert u["embed_key"] == "sk-right" and u["embed_key_env"] == "" and u["embed_model"] == "text-embedding-3-small"


def test_hosted_without_key_stays_keyword_only(monkeypatch):
    patch(monkeypatch, ["x"])
    assert E.configure(io=Script(answers=["4"], secrets=[""]), env={}) is None


def test_hosted_user_types_their_own_model(monkeypatch):
    patch(monkeypatch, ["a/one"])
    io = Script(answers=["4", "y", "2", "my/embedder"])            # menu entry 2 after 1 listed model is "type a name"
    assert E.configure(io=io, env={"OPENROUTER_API_KEY": "k"})["embed_model"] == "my/embedder"


def test_noninteractive_picks_running_local_gemma_or_nothing(monkeypatch):
    patch(monkeypatch, ["embeddinggemma:latest"], running=["ollama"])
    assert E.configure(io=Script(interactive=False), env={})["embed_provider"] == "ollama"
    patch(monkeypatch, [], running=["ollama"])
    assert E.configure(io=Script(interactive=False), env={}) is None
    patch(monkeypatch, ["m"], running=[])
    assert E.configure(io=Script(interactive=False), env={}) is None


def test_flags_skip_the_menu(monkeypatch):
    patch(monkeypatch, ["whatever"])
    u = E.configure("openrouter", None, "baai/bge-base-en-v1.5", Script(interactive=False), {"OPENROUTER_API_KEY": "k"})
    assert u["embed_model"] == "baai/bge-base-en-v1.5" and u["embed_key_env"] == "OPENROUTER_API_KEY"


def test_config_resolves_key_from_named_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c")); monkeypatch.setenv("ASSETRAG_HOME", str(tmp_path / "h"))
    monkeypatch.delenv("ASSETRAG_EMBED_KEY", raising=False)
    from assetrag import config
    cf = config._config_file(); cf.parent.mkdir(parents=True); cf.write_text('{"embed_key_env": "MY_KEY_VAR", "embed_key": "stale"}')
    monkeypatch.setenv("MY_KEY_VAR", "from-env")
    assert load().embed_key == "from-env"


def test_banner_fits_and_degrades():
    art = banner.art_lines()
    assert len({len(l) for l in art}) == 1 and len(art) == 6
    wide = banner.render(color=False, width=100)
    assert "██" in wide and all(len(l) <= 80 for l in wide.splitlines())
    assert "\033" not in wide and banner.render(color=True, width=100).count("\033[38;5;") == 6
    assert banner.render(color=False, width=40) == "ASSETRAG" and "█" not in banner.render(color=False, width=40)
