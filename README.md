# AssetRAG

A local-first, licence-aware code library and engineering memory for Claude Code (any MCP client works).
It finds tested building blocks, verified bug fixes and known-good packages **before** your agent reinvents them, and tells it honestly when nothing fits.

```
$ assetrag know "tail a log file without busy-waiting"
$ assetrag search "go cli progress bar" --category cli
$ assetrag repomap .          # ranked outline of a codebase in a token budget
$ assetrag doctor             # health score for the whole setup
```

> **Read [DISCLAIMER.md](DISCLAIMER.md) first.** No warranty, not legal advice, and you (not the author) are responsible for whatever assets or code you
> or your AI agent end up using. AssetRAG ships no third-party assets; it indexes what you ingest, each under its own licence.

## Install (Linux, one line)

```bash
curl -fsSL https://raw.githubusercontent.com/mttcn83/assetrag/main/scripts/install.sh | sh
```

Prefer to read it first? `scripts/install.sh` is ~100 lines. It installs with `pipx`/`uv` (no sudo, no system changes), then runs `assetrag setup`.
Or by hand: `pipx install git+https://github.com/mttcn83/assetrag && assetrag setup`.

`assetrag setup` is idempotent and asks nothing it can detect: it creates the data dir, picks keyword-only mode if no embedding server is found,
loads the built-in verified knowledge, and prints the exact command to register the MCP server with Claude Code.
Remove everything with `assetrag uninstall` (dry run; `--yes` removes config and timers, `--purge` also deletes the data dir).

Requirements: Python 3.11+, git. Optional: `bubblewrap` (sandbox for running recipe tests), `clamav` (malware scan), `gh` (licence checks, updater),
a local OpenAI-compatible embedding server (semantic search; without one it runs keyword-only and says so).

## What you get

| | |
|---|---|
| **Knowledge** | Authored, executed-in-a-sandbox recipes, bug fixes, rules and playbooks (Python, Go, Rust, TypeScript, shell) plus curated package guidance |
| **Reranker** | Second pass that drops icons nobody asked for, wrong-platform results and named templates for the wrong subject; says `no_good_match` instead of guessing |
| **Licence gate** | Fingerprints each repo's own LICENSE; permissive allowlist; relicensed upstream => quarantined, never silently kept |
| **Repo map** | `assetrag repomap` outlines a codebase (Python via `ast`, other languages via tree-sitter) within a token budget |
| **Memory** | Lessons and failure notes, with outcomes tracked |
| **Upkeep** | `snapshot`, `rollback`, `recheck` (re-run recipe tests against today's toolchains), `update` (opt-in; never deletes), `weakest`, `usage` |
| **Safety** | ClamAV gate on ingest, bubblewrap sandbox for untrusted code, secret scan, prompt-injection flagging, third-party-host warning on web assets |

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Use with Claude Code

```bash
claude mcp add assetrag -- assetrag-mcp
```

Retrieved text is wrapped as untrusted data, and the server tells the client to treat it that way. Optional prompt hook: `integrations/settings.hook.json`.

## Honest numbers

On a 57-query hand-judged set kept in the author's development tree (not shipped): hit@3 85.7%, and 8/8 "nothing here" queries are flagged. The set was used while tuning, so treat it as optimistic.
Whether AssetRAG makes Claude measurably faster in real tasks has **not** been measured yet.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Dependencies: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Licensing policy: [docs/LICENSING.md](docs/LICENSING.md). Security: [docs/SECURITY.md](docs/SECURITY.md).
