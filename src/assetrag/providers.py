"""Embedding providers: where AssetRAG can get embeddings from, how to list their models, and how to test one.

Everything speaks the OpenAI-compatible `/v1/embeddings` shape. Local servers need no key; hosted ones take a bearer key that is read from an
environment variable (preferred, nothing stored) or kept in the 0600 config file.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass

import httpx

GEMMA = "embeddinggemma"                       # substring that identifies EmbeddingGemma in any provider's model list
GEMMA_PREFIXES = {"query_prefix": "task: search result | query: ", "doc_prefix": "title: none | text: "}
_EMBED_NAME = re.compile(r"embed|bge|minilm|e5-|gte|nomic|arctic|mxbai|voyage|titan|sentence|snowflake", re.I)


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    url: str
    local: bool
    key_env: str = ""                          # conventional env var holding the key
    key_required: bool = False
    gemma_model: str = GEMMA                   # model name to use when the provider has EmbeddingGemma
    static_models: tuple[str, ...] = ()        # when the provider has no usable model-list endpoint
    recommended: tuple[str, ...] = ()          # sensible picks, shown first when the provider lacks EmbeddingGemma
    models_path: str = "/models"
    signup: str = ""
    note: str = ""


PROVIDERS: tuple[Provider, ...] = (
    Provider("ollama", "Ollama (local)", "http://127.0.0.1:11434/v1", True, gemma_model="embeddinggemma", note="ollama pull embeddinggemma"),
    Provider("lmstudio", "LM Studio (local)", "http://127.0.0.1:1234/v1", True, gemma_model="text-embedding-embeddinggemma-300m"),
    Provider("llamacpp", "llama.cpp / Unsloth server (local)", "http://127.0.0.1:8888/v1", True, gemma_model="unsloth/embeddinggemma-2-GGUF",
             note="llama-server -hf unsloth/embeddinggemma-300m-GGUF --embedding --port 8888"),
    Provider("openrouter", "OpenRouter (hosted)", "https://openrouter.ai/api/v1", False, "OPENROUTER_API_KEY", True,
             recommended=("google/gemini-embedding-2", "baai/bge-base-en-v1.5", "thenlper/gte-base"), models_path="/embeddings/models",
             signup="https://openrouter.ai/keys"),
    Provider("openai", "OpenAI (hosted)", "https://api.openai.com/v1", False, "OPENAI_API_KEY", True,
             recommended=("text-embedding-3-small", "text-embedding-3-large"), signup="https://platform.openai.com/api-keys"),
    Provider("gemini", "Google Gemini API (hosted)", "https://generativelanguage.googleapis.com/v1beta/openai", False, "GEMINI_API_KEY", True,
             static_models=("gemini-embedding-001",), recommended=("gemini-embedding-001",), signup="https://aistudio.google.com/apikey"),
    Provider("custom", "Other OpenAI-compatible endpoint", "", False, "", False),
)
BY_ID = {p.id: p for p in PROVIDERS}


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"} if key else {}


def list_models(p: Provider, url: str, key: str = "", client: httpx.Client | None = None, timeout: float = 8.0) -> list[str]:
    """Model ids the provider offers for embeddings. Raises ProviderError('unreachable' | 'auth' | ...) with a user-facing message."""
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.get(url.rstrip("/") + p.models_path, headers=_headers(key))
    except httpx.HTTPError as e:
        raise ProviderError(f"could not reach {url}: {e.__class__.__name__}") from e
    finally:
        if client is None:
            c.close()
    if r.status_code in (401, 403):
        raise ProviderError("auth: the server rejected the API key")
    if r.status_code == 404 and p.static_models:
        return list(p.static_models)
    if r.status_code >= 400:
        raise ProviderError(f"{url} answered HTTP {r.status_code}")
    try:
        ids = [m["id"] for m in r.json().get("data", []) if isinstance(m, dict) and "id" in m]
    except ValueError as e:
        raise ProviderError(f"{url} did not return a model list") from e
    if p.static_models:
        ids = list(dict.fromkeys([*p.static_models, *[i.removeprefix("models/") for i in ids if _EMBED_NAME.search(i)]]))
    elif p.models_path == "/models" and len(ids) > 0:
        emb = [i for i in ids if _EMBED_NAME.search(i)]
        ids = emb if (emb or not p.local) else []      # local server with only chat models: nothing embedding-capable
    return ids


def find_gemma(models: list[str]) -> str | None:
    return next((m for m in models if GEMMA in m.lower()), None)


def is_gemma(model: str) -> bool:
    return GEMMA in model.lower()


def prefixes_for(model: str) -> dict:
    """EmbeddingGemma wants task prompts; every other model must get plain text."""
    return dict(GEMMA_PREFIXES) if is_gemma(model) else {"query_prefix": "", "doc_prefix": ""}


def test_embedding(url: str, model: str, key: str = "", client: httpx.Client | None = None, timeout: float = 60.0) -> int:
    """Embed one string; return the vector dimension or raise ProviderError with the server's own message."""
    c = client or httpx.Client(timeout=timeout)
    try:
        r = c.post(url.rstrip("/") + "/embeddings", json={"model": model, "input": ["dimension probe"]}, headers=_headers(key))
    except httpx.HTTPError as e:
        raise ProviderError(f"could not reach {url}: {e.__class__.__name__}") from e
    finally:
        if client is None:
            c.close()
    if r.status_code in (401, 403):
        raise ProviderError("auth: the server rejected the API key")
    if r.status_code >= 400:
        raise ProviderError(f"HTTP {r.status_code}: {r.text.strip()[:160]}")
    try:
        return len(r.json()["data"][0]["embedding"])
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise ProviderError("the server answered, but not with an embedding") from e


def reachable(p: Provider, url: str | None = None, timeout: float = 1.0) -> bool:
    """Any HTTP answer, even 401, means something is listening."""
    try:
        httpx.get((url or p.url).rstrip("/") + p.models_path, timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


def detect_local(timeout: float = 1.0) -> list[Provider]:
    return [p for p in PROVIDERS if p.local and reachable(p, timeout=timeout)]


GGUF_REPO = "unsloth/embeddinggemma-300m-GGUF"
GGUF_FILE = "embeddinggemma-300m-Q4_0.gguf"          # Q4, ~278 MB, ungated; Google's Gemma terms apply to the weights
HF = "https://huggingface.co"


def download_gemma(dest_dir, progress=None, client: httpx.Client | None = None, timeout: float = 60.0):
    """Fetch the Q4 EmbeddingGemma GGUF from Hugging Face into dest_dir, resuming a partial file and checking the published sha256.

    Returns the final Path. Raises ProviderError with a user-facing message on any failure; a bad file is deleted, never kept.
    """
    import hashlib
    from pathlib import Path
    dest_dir = Path(dest_dir); dest_dir.mkdir(parents=True, exist_ok=True)
    final, part = dest_dir / GGUF_FILE, dest_dir / (GGUF_FILE + ".part")
    c = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        try:
            meta = c.get(f"{HF}/api/models/{GGUF_REPO}/tree/main")
            meta.raise_for_status()
            info = next(f for f in meta.json() if f["path"] == GGUF_FILE)
            want_sha, want_size = info["lfs"]["oid"], int(info["size"])
        except (httpx.HTTPError, StopIteration, KeyError, ValueError) as e:
            raise ProviderError(f"could not read the file listing for {GGUF_REPO} on Hugging Face ({e.__class__.__name__})") from e
        if final.exists() and final.stat().st_size == want_size and _sha256(final) == want_sha:
            return final
        have = part.stat().st_size if part.exists() else 0
        if have > want_size:
            part.unlink(); have = 0
        try:
            with c.stream("GET", f"{HF}/{GGUF_REPO}/resolve/main/{GGUF_FILE}", headers={"Range": f"bytes={have}-"} if have else {}) as r:
                if r.status_code == 416:
                    pass                                              # already complete, verified below
                else:
                    r.raise_for_status()
                    if have and r.status_code != 206:
                        have = 0                                      # server ignored Range: start over
                    with open(part, "ab" if have else "wb") as f:
                        done = have
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk); done += len(chunk)
                            if progress:
                                progress(done, want_size)
        except httpx.HTTPError as e:
            raise ProviderError(f"download interrupted ({e.__class__.__name__}); run setup again to resume") from e
        if part.stat().st_size != want_size or _sha256(part) != want_sha:
            part.unlink(missing_ok=True)
            raise ProviderError("downloaded file failed the size/sha256 check and was deleted; run setup again")
        part.replace(final)
        return final
    finally:
        if client is None:
            c.close()


def _sha256(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def register_gguf(p: Provider, path, run) -> bool:
    """Make a downloaded GGUF visible to the local provider. `run(cmd) -> exit code`. False when we cannot do it for them."""
    from pathlib import Path
    path = Path(path)
    if p.id == "ollama" and shutil.which("ollama"):
        mf = path.parent / "Modelfile.embeddinggemma"
        mf.write_text(f"FROM {path}\n")
        return run(["ollama", "create", GEMMA, "-f", str(mf)]) == 0
    if p.id == "lmstudio" and shutil.which("lms"):
        return run(["lms", "import", "--copy", "-y", str(path)]) == 0
    return False
