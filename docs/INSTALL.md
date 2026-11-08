# Install

Requirements: Linux, Python >= 3.11, `git`. Optional: `bubblewrap` (sandbox for executing recipe tests), `clamav` (malware scan on ingest),
`gh` (licence checks and `assetrag update`), `go` / `node` / `rustc` (to execute recipes in those languages), and a local OpenAI-compatible
embedding server (semantic search; without one AssetRAG runs keyword-only and says so).

## One line
```bash
curl -fsSL https://raw.githubusercontent.com/mttcn83/assetrag/main/scripts/install.sh | sh
```
User-level only (pipx or uv, else a private venv): no sudo, no system files. It ends by running `assetrag setup`, which writes the config (mode 0600),
loads the built-in knowledge, and prints the `claude mcp add` command. Tested end to end in containers on Arch, Debian 12, Ubuntu 24.04,
Fedora and Alpine (`scripts/container-test.sh`).

Entries whose tests cannot be executed on your machine (no working bubblewrap, no Go/Rust/Node) are loaded as **unverified** and labelled so;
install the tool and run `assetrag seed` to verify them.

## From a clone (development)
```bash
git clone https://github.com/mttcn83/assetrag && cd assetrag
uv venv .venv && uv pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```
Uninstall: `assetrag uninstall` (dry run), `assetrag uninstall --yes` (config and timers; your data is kept), add `--purge` to delete the data dir, then `pipx uninstall assetrag`.

## Embeddings (optional)
Semantic search needs an embedding model. `assetrag setup` (and the installer) asks where to get one and **defaults to EmbeddingGemma**:

| Provider | Key | Notes |
|---|---|---|
| Ollama (local) | none | if EmbeddingGemma is missing, offers to download the Q4 GGUF (278 MB) from Hugging Face, sha256-checked, and `ollama create`s it |
| LM Studio (local) | none | same download, then `lms import`; the model must be loaded in the app |
| llama.cpp / Unsloth server (local) | none | same download (kept in `<data dir>/models/`), then prints the `llama-server -m ... --embedding` command to start it |
| OpenRouter, OpenAI, Google Gemini (hosted) | yes | no EmbeddingGemma there, so it lists the provider's embedding models and asks which you want (or type a name) |
| Any other OpenAI-compatible endpoint | optional | give the base URL ending in `/v1` |

The key is read from `$OPENROUTER_API_KEY` / `$OPENAI_API_KEY` / `$GEMINI_API_KEY` (or `ASSETRAG_EMBED_KEY`) if set, and the config then stores only the *name* of the variable.
Otherwise you are asked for it (input hidden) and it is saved in `~/.config/assetrag/config.json`, which is created with mode 0600. Prompts go to your terminal even under `curl | sh`.
The model is `unsloth/embeddinggemma-300m-GGUF` (`Q4_0`, no Hugging Face login needed) under Google's Gemma terms; an interrupted download resumes on the next run.
Task prompts are enabled only for EmbeddingGemma; any other model gets plain text. Every choice is verified with a real test embedding before it is saved.

```bash
assetrag setup --reconfigure                                   # choose again
assetrag setup --provider openrouter --embed-model google/gemini-embedding-2   # no questions (key from $OPENROUTER_API_KEY)
ASSETRAG_SETUP_ARGS='--provider ollama' curl -fsSL .../install.sh | sh          # same, through the installer
```

Resulting config (written for you):

```json
{ "embed_provider": "openrouter",
  "embed_url": "https://openrouter.ai/api/v1",
  "embed_model": "google/gemini-embedding-2",
  "embed_key_env": "OPENROUTER_API_KEY",
  "embed_mode": "auto" }
```
No terminal (CI, containers) means no questions: setup uses a running local server that has EmbeddingGemma, otherwise keyword-only.
`embed_mode: "off"` (or `assetrag setup --keyword-only`) never contacts an embedding server. Embedding model weights are not included and have their own licence terms.
Hosted providers receive the text being embedded (asset descriptions and your search queries): use a local provider if that matters.
Env overrides: `ASSETRAG_EMBED_URL`, `ASSETRAG_EMBED_MODEL`, `ASSETRAG_EMBED_KEY`, `ASSETRAG_EMBED_MODE`, `ASSETRAG_QUERY_PREFIX`,
`ASSETRAG_DOC_PREFIX`, `ASSETRAG_PROFILE`, `ASSETRAG_HOME` (data dir; default `$XDG_DATA_HOME/assetrag`, or an existing `~/assetrag/data`).
Changing the model/dimension/prefix rebuilds the vector index from SQLite (`assetrag reindex`).

```bash
assetrag doctor      # health score: database, embeddings, sandbox, ClamAV, licences, staleness, speed
```

## Load the engineering knowledge (bug fixes, rules, playbooks, recipes)
```bash
assetrag seed        # executes every bug-fix repro and recipe test in the sandbox, then loads them (~1 min)
assetrag know --error "TypeError: 'NoneType' object is not iterable"
```

## Build the library
```bash
assetrag ingest start          # supervised background ingest of the curated catalog (resumable; `ingest status|stop`)
assetrag ingest start --discover go-backend:40 py-backend:40   # GitHub search discovery
assetrag populate            # curated catalog, resumable (ClamAV → license → sandboxed checks → admit)
assetrag discover go-backend --limit 60   # GitHub search discovery of tested libraries
assetrag stats
```

## Claude Code (MCP)
```bash
claude mcp add assetrag -- assetrag-mcp
```
or copy `integrations/mcp.json.example` to `.mcp.json`. Requires `mcp<2` (pinned in `pyproject.toml`; the 2.x SDK renamed `FastMCP`).
Optional error-fix hook: merge `integrations/settings.hook.json` into `~/.claude/settings.json`. Append `integrations/CLAUDE.snippet.md` to your
CLAUDE.md. **Embedded Qdrant is single-process**: the ingest service and the MCP server take turns (each releases the
vector store within seconds; callers wait up to 60 s / 15 s before reporting "library busy").

## Release gate (ClamAV)
```bash
sudo freshclam                                   # system DB; `assetrag scan` also updates a user-owned DB without sudo
clamscan -r -i --log=clamav-report.log ./assetrag
assetrag scan .                        # same scan + writes security-report.md
```

## Mined bug fixes (optional)
```bash
assetrag mine            # clones 15 libraries, adds ~350 source-confirmed fixes; safe to re-run (deduped)
assetrag mine --help     # restrict to repos
```
Stop the ingest worker first (`assetrag ingest stop`) or let `mine` wait for the vector-store lock (up to 5 minutes).

## Automatic behaviour
* **MCP instructions** (no `CLAUDE.md` needed): the server advertises a "situation -> tool/playbook" decision guide on connect, so the agent chooses what to use and when (and when to skip it).
  Source: `INSTRUCTIONS` in `src/assetrag/mcp_server.py`, so it travels with the repo; `integrations/CLAUDE.snippet.md` is a generated copy.
* **Prompt hook** (optional, `integrations/settings.hook.json`, add it to `~/.claude/settings.json`): a small selection algorithm
  (`src/assetrag/context.py`), not fixed text. Per prompt it detects the project (language/framework from marker files in the
  hook's cwd), classifies the intent (error / debug / perf / ui / build / other), retrieves across fixes, playbooks, recipes,
  rules, failures, lessons and high-confidence assets, rescores by intent, drops what this session already saw, and picks what
  fits a ~450-token budget. Silent when nothing clears the bar. No session id -> stateless. It works without the embeddings
  server (exact-error matches only) and never blocks a prompt. Inspect or tune it with
  `assetrag context "<prompt>" --cwd .` (prints every candidate, its score and why it was kept or dropped).
