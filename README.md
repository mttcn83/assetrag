<div align="center">

<img src="docs/images/banner.jpg" alt="AssetRAG" width="100%">

# AssetRAG

**A local-first, licence-aware code library and verified engineering memory for Claude Code** (any MCP client works).

It finds tested building blocks, verified bug fixes and known-good packages **before** your agent reinvents them, and tells it honestly when nothing fits.

[**▶ Watch the 2½ minute film**](docs/media/assetrag-launch-film.mp4) &nbsp;·&nbsp; [Install](#install-linux-one-line) &nbsp;·&nbsp; [Tour](#a-tour-with-real-screenshots) &nbsp;·&nbsp; [Honest numbers](#honest-numbers) &nbsp;·&nbsp; [Disclaimer](DISCLAIMER.md)

<a href="docs/media/assetrag-launch-film.mp4"><img src="docs/media/preview.gif" alt="AssetRAG launch film preview (silent GIF, click for the full video with sound)" width="85%"></a>

<sub>Silent preview. Click it to play the full film (1080p, with sound). Every terminal in it is a real recording of the real tool;
the film's source and the full-quality 1440p file are in [assetrag-launch-film](https://github.com/mttcn83/assetrag-launch-film).</sub>

</div>

```
$ assetrag know "tail a log file without busy-waiting"
$ assetrag search "go cli progress bar" --category cli
$ assetrag repomap .          # ranked outline of a codebase in a token budget
$ assetrag doctor             # health score for the whole setup
```

> **Read [DISCLAIMER.md](DISCLAIMER.md) first.** No warranty, not legal advice, and you (not the author) are responsible for whatever assets or code you
> or your AI agent end up using. AssetRAG ships no third-party assets; it indexes what you ingest, each under its own licence.

## Contents

[Why](#why) · [Install](#install-linux-one-line) · [A tour with real screenshots](#a-tour-with-real-screenshots) · [What you get](#what-you-get) · [Use with Claude Code](#use-with-claude-code) · [Commands](#commands-at-a-glance) · [Safety model](#safety-model) · [Honest numbers](#honest-numbers) · [What is not built](#what-is-not-built) · [Docs](#docs) · [Licence](#licence)

## Why

A coding agent starts every task cold. It rewrites the retry loop you already have a tested version of, picks a package whose licence you cannot ship,
re-derives a fix for an error that has a known cause, and reads half your repository to find one function. AssetRAG is the memory and the shelf it lacks:

- **A shelf of licensed, human-made building blocks.** Whole-repo ingestion with each project's own LICENSE fingerprinted and checked against a permissive allowlist. Importing an asset copies it into your project *and* writes the notices.
- **Verified knowledge.** Recipes and bug fixes whose repro and fix were actually executed in a sandbox, plus rules, playbooks and package guidance with verified licences.
- **A small, honest context footprint.** A ranked repo map and a prompt hook with a hard token cap, so it saves context instead of eating it.
- **Honest empty states.** When nothing relevant exists, it says `no_good_match` rather than guessing.

It runs entirely on your machine: no account, no telemetry, no network required for the happy path.

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
The installer is exercised in containers on Arch, Debian, Ubuntu, Fedora and Alpine (`scripts/container-test.sh`).

<p align="center"><img src="docs/images/install.jpg" alt="assetrag setup output" width="85%"><br>
<sub><code>assetrag setup</code>: real output. Entries that could not be verified here load as <i>unverified</i> and are labelled so.</sub></p>

## A tour with real screenshots

Every image below is a frame from the launch film, which was rendered from real recordings of the CLI and MCP server
(isolated demo directory, keyword-only mode, nothing typed by hand).

### An MCP server your agent can call

18 tools over stdio: asset and knowledge search, licence checks, repo map, rules, secret scan, lessons and failure memory. Results are returned as untrusted data, and the server's instructions tell the client to treat them that way.

<p align="center"><img src="docs/images/mcp.jpg" alt="MCP tool calls with latencies and token counts" width="85%"><br>
<sub>Real MCP calls with measured latency and token counts. The client here is scripted, not an LLM.</sub></p>

### Verified bug-fix memory

Paste the exact error, get a fix whose reproduction and fix were both run in a no-network sandbox (repro exits non-zero, fix exits zero). A hit is a hypothesis to test, and a nonsense error gets an honest "no match".

<p align="center"><img src="docs/images/bugfix.jpg" alt="Bug-fix lookup by exact error" width="85%"></p>

### Playbooks, recipes and package guidance

Pick a method for the situation (root-cause, refactor-safe, perf-optimise and more), grab a recipe with executed tests, or learn which mature library to use and under what licence. Playbooks are checklists and are labelled *unbenchmarked* by the product itself.

<table>
<tr>
<td width="50%"><img src="docs/images/playbooks.jpg" alt="Playbook"></td>
<td width="50%"><img src="docs/images/recipes.jpg" alt="Recipe with executed tests"></td>
</tr>
</table>

### Rules and a secret scan

Engineering rules for a task in a few lines, and `assetrag scan-code` runs the rule detectors over a file (exit code 1 if anything is found).

<p align="center"><img src="docs/images/rules.jpg" alt="scan-code catching a planted example key" width="85%"><br>
<sub>The example key in the demo is AWS's published documentation key.</sub></p>

### Licensed assets, privacy included

Search, read the licence verdict and its evidence hash, import with notices written for you. `assetrag audit` flags hotlinked CDNs, fonts and trackers in a project.

<table>
<tr>
<td width="50%"><img src="docs/images/assets.jpg" alt="Asset search, licence verdict and import"></td>
<td width="50%"><img src="docs/images/privacy.jpg" alt="Privacy audit"></td>
</tr>
</table>

### Small context, on purpose

`assetrag repomap` outlines a codebase in a token budget, and the optional Claude Code prompt hook injects only what is relevant, capped at 520 tokens. `assetrag context "<prompt>"` shows exactly what the hook would inject for a prompt, with scores and drop reasons.

<p align="center"><img src="docs/images/context.jpg" alt="The real prompt hook injection for a bug-fix prompt" width="85%"></p>

### Safe to ingest

Ingestion clones, scans with ClamAV, then applies licence and quality gates before admitting anything. `assetrag scan` is the release gate and writes `security-report.md`.

<p align="center"><img src="docs/images/safety.jpg" alt="ClamAV blocking the harmless EICAR test file" width="85%"><br>
<sub>ClamAV catching the standard, harmless EICAR antivirus test file.</sub></p>

### Upkeep you can trust

`doctor` gives a health score, `snapshot` and `rollback` protect the database, `recheck` re-runs every authored test against today's toolchains,
and `update` is opt-in, never deletes, and can run from a weekly systemd user timer you enable yourself.

<p align="center"><img src="docs/images/upkeep.jpg" alt="doctor health score and checks" width="85%"></p>

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
The hook adds a short, capped note (a ranked hit or two, or a playbook for the situation) to prompts where it is likely to help, and stays silent on trivial edits and plain questions.

Nothing forces the agent to use AssetRAG. The server's instructions say when it is worth calling (first contact with a repo, an exact error, a common pattern, a new dependency, before finishing code that touches secrets or input) and when to skip it.

## Commands at a glance

| Task | Command |
|---|---|
| Find a recipe, fix, rule or package | `assetrag know "<question>"` · `assetrag know --error "<exact error text>"` |
| Show a full entry | `assetrag kshow <id>` |
| Rules for a task | `assetrag rules write an http client` |
| Scan code for secrets and bad patterns | `assetrag scan-code <file>` |
| Find a licensed asset | `assetrag search "<need>" --category cli` · `assetrag show <id>` · `assetrag license <id>` |
| Import with notices | `assetrag import <id> --to <project>` |
| Audit a project | `assetrag audit <project>` |
| Outline a codebase | `assetrag repomap <path> --budget 600` |
| See what the hook would inject | `assetrag context "<prompt>"` |
| Health, gaps, usage | `assetrag doctor` · `assetrag weakest` · `assetrag usage` · `assetrag kstats` |
| Protect and refresh | `assetrag snapshot` · `assetrag rollback` · `assetrag recheck` · `assetrag update` |
| Malware scan | `assetrag scan` |
| Remove | `assetrag uninstall` (dry run) |

Run `assetrag --help` or `assetrag <command> --help` for the full list.

## Safety model

- **Licences.** Only permissive licences on the allowlist are admitted. Each repo's own LICENSE is fingerprinted, and an upstream relicence puts the asset in quarantine. Evidence hashes are stored, and `THIRD_PARTY_NOTICES` is written on import. See [docs/LICENSING.md](docs/LICENSING.md).
- **Untrusted data.** Everything retrieved, including file contents, is treated as untrusted third-party text. Prompt-injection patterns are flagged.
- **Sandbox.** Recipe and bug-fix tests run under `bubblewrap` with no network. Without it, entries load but are labelled *unverified here*.
- **Malware.** ClamAV gate on ingestion and a release scan (optional dependency).
- **No elevation.** Install is user-level. Nothing silently needs `sudo`, and the updater is opt-in and never deletes.
- **Privacy.** Local-first, no telemetry. The audit warns when a project pulls fonts, scripts or trackers from third-party hosts.

More in [docs/SECURITY.md](docs/SECURITY.md).

## Honest numbers

Measured on the author's machine and library (the library holds 16,064 assets and 4,247 knowledge entries there; you build your own by ingesting). Keyword-only mode.

| Measure | Result |
|---|---|
| Repo map of this project | 597 tokens vs 129,107 tokens of source (216× smaller) |
| Prompt hook injection | average 202 tokens, max 295, hard cap 520 (9 test prompts) |
| Asset search latency | median 4.0 ms, p95 9.2 ms |
| Knowledge search latency | median 7.1 ms, p95 9.0 ms |
| Test suite | 132 unit tests passing; `assetrag recheck` re-runs 327 authored recipe and bug-fix tests with 0 failing |
| Retrieval quality | hit@3 = 0.857 on 49 hand-judged queries, and 8 of 8 nonsense queries flagged `no_good_match` |

The retrieval set is kept in the author's development tree (not shipped) and was used while tuning, so treat hit@3 as optimistic.
Whether AssetRAG makes Claude measurably faster in real tasks has **not** been measured yet: the `bench claude-ab` command exists but has never been run, because it spends API budget.

## What is not built

Starter projects, automatic lesson capture, adapted snippets and successor detection are not in the product and are not advertised. Semantic (embedding) search is implemented and unit-tested but was not exercised in the film or the numbers above, which are keyword-only.
The launch film's [feature inventory](https://github.com/mttcn83/assetrag-launch-film/blob/main/FEATURE_INVENTORY.md) lists every claim with its evidence.

## Docs

[Install notes](docs/INSTALL.md) · [Architecture](docs/ARCHITECTURE.md) · [Licensing policy](docs/LICENSING.md) · [Security](docs/SECURITY.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Disclaimer](DISCLAIMER.md)

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Dependencies: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Licensing policy: [docs/LICENSING.md](docs/LICENSING.md). Security: [docs/SECURITY.md](docs/SECURITY.md).
The launch film's visuals are rendered from the real tool; its soundtrack was synthesized for this project (no third-party music).
