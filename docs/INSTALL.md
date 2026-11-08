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
Any OpenAI-compatible `/v1/embeddings` server works (tested: `embeddinggemma`, 768-d, via a local server). Put the endpoint in `~/.config/assetrag/config.json`
or run `assetrag setup --embed-url http://127.0.0.1:8888/v1`:

```json
{ "embed_url": "http://127.0.0.1:8888/v1",
  "embed_model": "unsloth/embeddinggemma-2-GGUF",
  "embed_key": "",
  "embed_mode": "auto",
  "profile": "closed_source" }
```
`embed_mode: "off"` (or `assetrag setup --keyword-only`) never contacts an embedding server. Embedding model weights are not included and have their own licence terms.
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
