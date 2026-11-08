# Architecture

```
 Claude Code ──MCP stdio──▶ mcp_server ─▶ ops ─▶ Library ─┬─ SQLite (metadata, FTS5, versions, lessons, usage, jobs)
                                    CLI ─▶ ops ─┘          ├─ Qdrant local (assets + lessons vectors, cosine)
                                                           ├─ library/<category>/<id>/ (files, LICENSE.evidence, ASSET.json)
 sources.py (catalog) ─┐                                   └─ Embedder ─▶ Unsloth /v1/embeddings (cached in SQLite)
 discover.py (GitHub) ─┴▶ ingest: clone(hooks off) → ClamAV → licence fingerprint → deps → sandboxed checks → Library.admit()
```

* **Admission gate** (`Library.vet/admit`) is the only way in: source, license text fingerprint, declared/detected
  agreement, dependency licenses, provenance rule for visual categories, secrets, dangerous patterns, binaries,
  ≥1 passing quality check and zero failures, exact + simhash near-duplicate rejection.
* **Retrieval** (`search.py`): Qdrant top-80 + FTS5 BM25 top-80 → union → hard filters (license/profile incl.
  dependencies, framework/language compatibility) → score = 0.55 semantic + 0.20 keyword + 0.12 quality evidence
  + 0.06 usage history + 0.07 integration ease (+0.08 explicit framework match). Light results; code only via `get_asset`.
* **Versions**: `asset_versions` keeps (version, content hash, path); assets are immutable per version, a better
  implementation is admitted as a new asset and only replaces after validation (no auto-replacement).
* **Memory**: lessons in SQLite + Qdrant; project lessons mirrored to `.assetrag/knowledge.md`; high-confidence,
  evidence-backed lessons are promoted to global scope; near-duplicates (cosine ≥ 0.93) update in place.
* **Scale**: metadata/FTS in SQLite, vectors in Qdrant (swap `QdrantClient(path=…)` for a server URL to go
  multi-process / 100k+). Embeddings are cached by (model, text) hash; model change rebuilds vectors only.
* **Deviations from the brief**: UMAP/Three.js explorer not built (optional; core works without). Embedded Qdrant
  is single-process (documented limitation).

## Engineering knowledge (BugFixDB, rules, playbooks, failure memory, recipes)

One store, five kinds, one admission gate (`knowledge.add`), same philosophy as the asset library: provenance on
everything, nothing unverified presented as proven.

| kind | what it holds | how it becomes trustworthy |
|---|---|---|
| `bugfix` | error signature, version range, root cause, fix, pitfalls | **executed**: `kverify` runs the repro in the bwrap sandbox (must show the error) and the fix (must exit 0 and not show it) |
| `recipe` | tested pattern + files, declared dependencies/licence/assumptions | its own test-suite is executed in the sandbox at seed time |
| `rule` | engineering rule, severity, optional regex detectors, `overridable` | authored; security/licensing rules are non-overridable |
| `playbook` | when-to-use, steps, risks, verify | authored; all currently `unbenchmarked` (the A/B in `bench claude-ab` is gated behind `--execute`) |
| `failure` | attempted / failed / hypothesis **vs** verified conclusion + evidence | verified only with evidence; never auto-promoted to a rule (`promote_failure_to_rule` needs `confirm`) |

* **Retrieval** (`knowledge.search`): Qdrant semantic + FTS5 + exact **error-signature** match (paths, numbers, quoted
  values, addresses normalised away) + version-range check + verification/reliability priors. Results with no signature
  match must clear a semantic floor (`SEM_FLOOR=0.70`, measured on `benchmarks/kqueries.json`; re-check after changing
  the embedding model).
* **Quality pipeline**: exact-hash dedupe, near-duplicate merge (cos ≥ 0.965, never downgrades verification),
  conflicting fixes for one signature are linked (`conflicts`) not overwritten, every change is versioned
  (`knowledge_versions`, `rollback`), instruction-like text from non-authored sources is quarantined, mined content
  must come from a permissively-licensed source.
* **Changelog miner** (`assetrag mine`, `src/assetrag/mine.py`): clones 15 permissively-licensed libraries (licence verified
  by the same fingerprinting as assets), reads fix entries under a version heading, keeps those that name an error/crash,
  and stores one sentence each as `bugfix` / `source-confirmed` with `version_spec <fixed_in>`, a permalink to the exact
  changelog line (commit-pinned) and attribution. They carry no code change and rank below `verified` entries.
  `knowledge.purge(lib, "mined-changelog")` drops and re-mines them (it refuses authored/user entries).
* **Rules** (`kops.get_rules`): progressive disclosure (compact must-rules + task-relevant ones); `<project>/.assetrag/rules.json`
  may `disable`, re-`severity` or `add` rules, but cannot disable non-overridable ones. `scan_code` applies the detectors.
* **Prompt hook** (`assetrag hook prompt`): on `UserPromptSubmit`, if the prompt contains an error and a *verified* fix
  matches by signature, injects at most two compact, untrusted-labelled hits; silent otherwise; never raises.
* **MCP server** keeps the library open for 20 s between calls (embedded Qdrant is single-process; the first call pays
  ~0.6 s to open it, warm calls ~35 ms) and releases it so `assetrag ingest` can flush.

## Coverage modules (package index, secrets, fix mining)

| module | what it does |
|---|---|
| `pkgindex.py`, `pkgindex_data.py` | `package` knowledge kind. Batches GitHub GraphQL queries, fingerprints each repo's LICENSE (`licenses.detect`), keeps permissive/non-archived/popular repos, pins the commit. Curated guidance (when to use / not / alternatives) lives in `pkgindex_data.P`; awesome lists only supply discovery + categories. |
| `secretscan.py`, `data/gitleaks_rules.json` | gitleaks (MIT) rules converted by `scripts/import_gitleaks.py`; keyword pre-filter, regex, entropy floor, allowlists. Used by `scan_code`. |
| `fixmine.py` | fail-to-pass fix mining in bubblewrap; stores `bugfix` / `verified` / provenance `mined-commit` with the repo licence for attribution. Output goes to `data/fixmine.json`, then `knowledge.add`. |
| `foundation.py`, `sources_cfg.py` | curated Go foundation libs (`discover` kind `go-foundation`) and config/devops sources, both through the normal asset gate (`config` check parses YAML/JSON/TOML/Dockerfile). |
| `seeds/recipes_{go,algo,ts,ts2,ts3,cfg}.py` | tested recipes; `recipes.all_recipes()` is the single list the seeder uses. |

Rebuild the package index: `python -c "from assetrag import pkgindex as p; e,rep=p.build(['awesome-go','awesome-nodejs','awesome-python','awesome-rust']); ..."` then `p.admit(lib, e)` (stop the ingest worker first - Qdrant is single-process).
