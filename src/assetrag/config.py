"""Configuration: env vars > ~/.config/assetrag/config.toml-style JSON > defaults."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

def _default_home() -> Path:
    """ASSETRAG_HOME > an existing ~/assetrag/data (the pre-1.0 location) > $XDG_DATA_HOME/assetrag."""
    if os.environ.get("ASSETRAG_HOME"):
        return Path(os.environ["ASSETRAG_HOME"]).expanduser()
    legacy = Path.home() / "assetrag" / "data"
    if (legacy / "assetrag.sqlite").exists():
        return legacy
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "assetrag"


DEFAULT_HOME = _default_home()


@dataclass
class Config:
    home: Path = DEFAULT_HOME
    embed_url: str = "http://127.0.0.1:8888/v1"
    embed_model: str = "unsloth/embeddinggemma-2-GGUF"   # BF16 quant, 768 dims
    embed_key: str = ""
    embed_key_env: str = ""          # name of an env var holding the key (preferred: nothing secret is stored)
    embed_provider: str = ""         # informational: ollama | lmstudio | llamacpp | openrouter | openai | gemini | custom
    embed_batch: int = 64
    # auto: use the embedding server and fall back to keyword search when it is down; off: never contact one (keyword-only, fully offline).
    embed_mode: str = "auto"
    # EmbeddingGemma task prompts (asymmetric retrieval). Empty strings disable.
    query_prefix: str = "task: search result | query: "
    doc_prefix: str = "title: none | text: "
    # Distribution profile used by default license checks.
    profile: str = "closed_source"  # closed_source | open_source | saas
    # Target platform for result ranking (linux|macos|windows); empty = this machine.
    platform: str = ""
    # Extra roots the ingester may read from (path-traversal allowlist).
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path: return self.home / "assetrag.sqlite"
    @property
    def qdrant_path(self) -> Path: return self.home / "qdrant"
    @property
    def library(self) -> Path: return self.home / "library"
    @property
    def quarantine(self) -> Path: return self.home / "quarantine"
    @property
    def work(self) -> Path: return self.home / "work"

    def ensure(self) -> "Config":
        for p in (self.home, self.library, self.quarantine, self.work):
            p.mkdir(parents=True, exist_ok=True)
        return self


def _config_file() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "assetrag" / "config.json"


def load() -> Config:
    cfg = Config()
    f = _config_file()
    if f.exists():
        data = json.loads(f.read_text())
        for k, v in data.items():
            if k == "home":
                v = Path(v).expanduser()
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    env = os.environ
    if env.get("ASSETRAG_HOME"):
        cfg.home = Path(env["ASSETRAG_HOME"]).expanduser()
    cfg.embed_url = env.get("ASSETRAG_EMBED_URL", cfg.embed_url).rstrip("/")
    cfg.embed_model = env.get("ASSETRAG_EMBED_MODEL", cfg.embed_model)
    cfg.embed_key = env.get("ASSETRAG_EMBED_KEY") or (env.get(cfg.embed_key_env, "") if cfg.embed_key_env else "") or cfg.embed_key
    cfg.embed_mode = env.get("ASSETRAG_EMBED_MODE", cfg.embed_mode)
    if cfg.embed_mode not in ("auto", "off"):
        raise ValueError(f"embed_mode must be 'auto' or 'off', got {cfg.embed_mode!r}")
    cfg.query_prefix = env.get("ASSETRAG_QUERY_PREFIX", cfg.query_prefix)
    cfg.doc_prefix = env.get("ASSETRAG_DOC_PREFIX", cfg.doc_prefix)
    cfg.profile = env.get("ASSETRAG_PROFILE", cfg.profile)
    return cfg.ensure()
