"""Interactive (and scriptable) choice of an embedding provider, key and model for `assetrag setup`.

Returns config updates; never writes files itself. All prompting goes through `IO` so tests can drive it with scripted answers.
"""
from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys

from . import providers as P


class IO:
    """Prompts on the user's terminal. `interactive` is False when there is nobody to ask (pipes, CI, containers)."""

    def __init__(self, interactive: bool | None = None):
        self.interactive = (sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get("ASSETRAG_NO_PROMPT")) if interactive is None else interactive

    def say(self, msg: str = ""): print(msg)
    def ok(self, msg: str): print(f"  \033[32m✓\033[0m {msg}" if _color() else f"  ✓ {msg}")
    def warn(self, msg: str): print(f"  \033[33m!\033[0m {msg}" if _color() else f"  ! {msg}")

    def ask(self, prompt: str, default: str = "") -> str:
        try:
            s = input(f"  {prompt}" + (f" [{default}]" if default else "") + ": ").strip()
        except EOFError:
            return default
        return s or default

    def secret(self, prompt: str) -> str:
        try:
            return getpass.getpass(f"  {prompt} (input hidden, Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""

    def confirm(self, prompt: str, default: bool = True) -> bool:
        a = self.ask(f"{prompt} [{'Y/n' if default else 'y/N'}]").lower()
        return default if not a else a.startswith("y")

    def choose(self, title: str, options: list[str], default: int = 1) -> int:
        """Numbered menu; returns the 0-based index. Typing a number picks it, Enter takes the default."""
        self.say(f"\n  {title}")
        for i, o in enumerate(options, 1):
            self.say(f"   {i}) {o}")
        while True:
            a = self.ask("Choose", str(default))
            if a.isdigit() and 1 <= int(a) <= len(options):
                return int(a) - 1
            self.warn(f"enter a number from 1 to {len(options)}")

    def progress(self, msg: str): print(f"\r  {msg}   ", end="", flush=True)
    def progress_end(self): print()

    def run(self, cmd: list[str]) -> int:
        return subprocess.run(cmd).returncode


def _color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


def _updates(p: P.Provider, url: str, model: str, key: str, key_env: str) -> dict:
    u = {"embed_provider": p.id, "embed_url": url.rstrip("/"), "embed_model": model, "embed_mode": "auto", **P.prefixes_for(model)}
    u["embed_key"] = key if (key and not key_env) else ""
    u["embed_key_env"] = key_env
    return u


def _get_key(p: P.Provider, io: IO, env: dict) -> tuple[str, str]:
    """(key, env_var_name). A key found in the environment is referenced by name, never copied to disk."""
    if env.get("ASSETRAG_EMBED_KEY"):
        return env["ASSETRAG_EMBED_KEY"], "ASSETRAG_EMBED_KEY"
    if p.key_env and env.get(p.key_env):
        if not io.interactive or io.confirm(f"Use the key in ${p.key_env} from your environment?"):
            return env[p.key_env], p.key_env
    if not io.interactive:
        return "", ""
    if p.signup:
        io.say(f"\n  {p.label} needs an API key. Get one at {p.signup}")
    return io.secret(f"{p.label} API key"), ""


def _pick_model(p: P.Provider, url: str, key: str, models: list[str], io: IO, want: str | None) -> str | None:
    """EmbeddingGemma if the provider has it (offering to download it locally), otherwise let the user choose. None = give up."""
    if want:
        return want
    gem = P.find_gemma(models)
    if gem:
        io.ok(f"EmbeddingGemma is available ({gem})")
        return gem
    if p.local:
        if not io.interactive:
            return None
        io.warn(f"EmbeddingGemma is not on {p.label} yet.")
        gem = _fetch_gemma(p, url, key, io)
        return gem or _choose_from(p, models, io, "Use a model that is already installed instead?")
    io.warn(f"{p.label} does not offer EmbeddingGemma.")
    return _choose_from(p, models, io, None)


def _fetch_gemma(p: P.Provider, url: str, key: str, io: IO) -> str | None:
    """Download the Q4 GGUF from Hugging Face, hand it to the local provider, and return the model name it now lists."""
    if not io.confirm(f"Download EmbeddingGemma (Q4, 278 MB, Gemma licence) from Hugging Face now?"):
        return None
    from . import config as cfgmod
    last = [-1]
    def prog(done, total):
        pct = int(done * 100 / total)
        if pct != last[0] and (pct % 5 == 0 or done == total):
            last[0] = pct
            io.progress(f"downloading {done / 1e6:.0f}/{total / 1e6:.0f} MB  {pct}%")
    try:
        path = P.download_gemma(cfgmod._default_home() / "models", prog)
    except P.ProviderError as e:
        io.warn(str(e)); return None
    io.progress_end()
    io.ok(f"saved {path} (sha256 verified)")
    if P.register_gguf(p, path, io.run):
        gem = P.find_gemma(_safe_models(p, url, key))
        if gem:
            return gem
        io.warn("imported, but the server does not list it yet (LM Studio: load the model in the app)")
    else:
        cmd = f"llama-server -m {path} --embedding --port 8888" if p.id == "llamacpp" else f"load {path} in {p.label}"
        io.say(f"  Start it with:  {cmd}")
    if io.confirm("Retry now that it is started?"):
        return P.find_gemma(_safe_models(p, url, key))
    return None


def _choose_from(p: P.Provider, models: list[str], io: IO, ask_first: str | None) -> str | None:
    cands = list(dict.fromkeys([m for m in p.recommended if not models or m in models] + models))
    if ask_first and (not cands or not io.confirm(ask_first)):
        return None
    shown = cands[:20]
    opts = shown + ["Type a model name myself", "Skip: keyword-only search"]
    i = io.choose("Pick an embedding model:", opts, default=1 if shown else len(opts) - 1)
    if i == len(opts) - 1:
        return None
    if i == len(opts) - 2:
        return io.ask("Model name") or None
    return shown[i]


def configure(provider: str | None = None, url: str | None = None, model: str | None = None, io: IO | None = None, env: dict | None = None) -> dict | None:
    """Choose provider + key + model and prove it works. Returns config updates, or None for keyword-only."""
    io = io or IO()
    env = dict(os.environ if env is None else env)
    p: P.Provider | None = P.BY_ID.get(provider) if provider else None
    if provider and not p:
        raise SystemExit(f"unknown provider {provider!r}; choose one of: {', '.join(P.BY_ID)}")

    if p is None:
        running = P.detect_local()
        if not io.interactive:
            p = next((q for q in running if P.find_gemma(_safe_models(q, q.url))), None)
            if p is None:
                return None                              # nothing to talk to and nobody to ask: keyword-only
        else:
            labels = [f"{q.label}" + ("   ← running" if q in running else "") for q in P.PROVIDERS] + ["No embeddings: keyword-only search (works offline)"]
            default = (P.PROVIDERS.index(running[0]) + 1) if running else (1 if shutil.which("ollama") else len(labels))
            i = io.choose("Where should AssetRAG get embeddings for semantic search? (EmbeddingGemma by default)", labels, default)
            if i == len(labels) - 1:
                return None
            p = P.PROVIDERS[i]

    base = (url or p.url).rstrip("/")
    if p.id == "custom" and not url:
        if not io.interactive:
            raise SystemExit("--provider custom needs --embed-url")
        base = io.ask("Endpoint base URL (ends in /v1)").rstrip("/")
        if not base:
            return None

    key, key_env = ("", "") if p.local and not env.get("ASSETRAG_EMBED_KEY") else _get_key(p, io, env)
    if p.key_required and not key:
        io.warn(f"no API key for {p.label}, so semantic search stays off. Re-run `assetrag setup --reconfigure` to add one.")
        return None

    models: list[str] = []
    for attempt in range(3):
        try:
            models = P.list_models(p, base, key)
            break
        except P.ProviderError as e:
            io.warn(str(e))
            if "auth" in str(e) and io.interactive and attempt < 2:
                key, key_env = io.secret(f"{p.label} API key"), ""
                if key:
                    continue
            if p.local and io.interactive and attempt < 2 and io.confirm(f"Start {p.label}, then retry?"):
                continue
            return None

    chosen = _pick_model(p, base, key, models, io, model)
    if not chosen:
        return None
    try:
        dim = P.test_embedding(base, chosen, key)
    except P.ProviderError as e:
        io.warn(f"{chosen} did not produce an embedding: {e}")
        return None
    io.ok(f"{chosen} works ({dim} dimensions)" + ("" if P.is_gemma(chosen) else "; task prompts are off because this is not EmbeddingGemma"))
    return _updates(p, base, chosen, key, key_env)


def _safe_models(p: P.Provider, url: str, key: str = "") -> list[str]:
    try:
        return P.list_models(p, url, key)
    except P.ProviderError:
        return []
