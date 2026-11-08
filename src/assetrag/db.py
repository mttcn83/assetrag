"""SQLite metadata store (+FTS5 keyword index)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,          -- ui | template | backend | cli | animation | algorithm
  kind TEXT NOT NULL DEFAULT '',   -- icon, illustration, component, page-template, module, ...
  description TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL,
  repo TEXT NOT NULL DEFAULT '',
  commit_sha TEXT NOT NULL DEFAULT '',
  license_spdx TEXT NOT NULL,
  license_evidence_hash TEXT NOT NULL,
  license_evidence_url TEXT NOT NULL DEFAULT '',
  copyright TEXT NOT NULL DEFAULT '',
  attribution_required INTEGER NOT NULL DEFAULT 0,
  copyleft TEXT NOT NULL DEFAULT 'none',   -- none | weak | strong | network
  provenance TEXT NOT NULL DEFAULT 'human', -- human | generated (AI) | harvested
  frameworks TEXT NOT NULL DEFAULT '[]',
  languages TEXT NOT NULL DEFAULT '[]',
  dependencies TEXT NOT NULL DEFAULT '{}',  -- name -> spdx
  tags TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL,
  simhash INTEGER NOT NULL DEFAULT 0,
  rel_path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  quality TEXT NOT NULL DEFAULT '{}',       -- evidence: checks run + results
  difficulty INTEGER NOT NULL DEFAULT 2,    -- 1 drop-in .. 5 heavy integration
  version INTEGER NOT NULL DEFAULT 1,
  verified_at TEXT NOT NULL,
  uses_ok INTEGER NOT NULL DEFAULT 0,
  uses_fail INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_assets_hash ON assets(content_hash);
CREATE INDEX IF NOT EXISTS ix_assets_cat ON assets(category);

CREATE VIRTUAL TABLE IF NOT EXISTS assets_fts USING fts5(
  id UNINDEXED, name, description, tags, kind, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS asset_versions (
  asset_id TEXT NOT NULL, version INTEGER NOT NULL, content_hash TEXT NOT NULL,
  rel_path TEXT NOT NULL, created_at REAL NOT NULL, note TEXT DEFAULT '',
  PRIMARY KEY (asset_id, version)
);

CREATE TABLE IF NOT EXISTS lessons (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL, category TEXT NOT NULL, framework TEXT DEFAULT '',
  versions TEXT DEFAULT '', symptoms TEXT DEFAULT '', root_cause TEXT DEFAULT '',
  solution TEXT DEFAULT '', prevention TEXT DEFAULT '', evidence TEXT DEFAULT '',
  confidence TEXT DEFAULT 'medium', limitations TEXT DEFAULT '',
  scope TEXT NOT NULL DEFAULT 'project',   -- project | global
  project TEXT DEFAULT '', content_hash TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
  id UNINDEXED, title, symptoms, root_cause, solution, prevention, tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS embed_cache (
  key TEXT PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, asset_id TEXT,
  event TEXT NOT NULL, detail TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS search_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, query TEXT NOT NULL,
  n_results INTEGER NOT NULL, top_score REAL, ms REAL
);
-- assets the updater found problematic (upstream licence changed/unreadable): hidden from search, kept on disk, listed in the update report
CREATE TABLE IF NOT EXISTS asset_flags (
  asset_id TEXT PRIMARY KEY, reason TEXT NOT NULL, ts REAL NOT NULL
);

-- Resumable ingestion. Only *pending/in-flight* work lives here; rejected
-- candidates are counted (reject_stats) but never kept for review.
CREATE TABLE IF NOT EXISTS ingest_jobs (
  key TEXT PRIMARY KEY, source TEXT NOT NULL, state TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL DEFAULT '{}',
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reject_stats (
  reason TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0
);
-- Engineering knowledge (bug fixes, rules, playbooks, failures, recipes). Assets keep their own tables.
CREATE TABLE IF NOT EXISTS knowledge (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,                 -- bugfix | rule | playbook | failure | recipe
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',   -- human-readable
  data TEXT NOT NULL DEFAULT '{}',    -- structured per-kind payload (json)
  languages TEXT NOT NULL DEFAULT '[]',
  frameworks TEXT NOT NULL DEFAULT '[]',
  tags TEXT NOT NULL DEFAULT '[]',
  scope TEXT NOT NULL DEFAULT 'universal',   -- universal | language | framework | project
  project TEXT NOT NULL DEFAULT '',
  version_spec TEXT NOT NULL DEFAULT '',     -- affected range, e.g. ">=1.0,<2.3"
  fixed_in TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  source_name TEXT NOT NULL DEFAULT '',
  source_license TEXT NOT NULL DEFAULT '',
  provenance TEXT NOT NULL DEFAULT 'authored', -- authored | mined-changelog | user | harvested
  verification TEXT NOT NULL DEFAULT 'unverified', -- verified | source-confirmed | hypothesis | unverified
  confidence TEXT NOT NULL DEFAULT 'medium',
  reliability REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'active',      -- active | quarantined | superseded | deprecated | proposed
  flags TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  occurrences INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, last_verified TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_hash ON knowledge(kind, content_hash);
CREATE INDEX IF NOT EXISTS ix_knowledge_kind ON knowledge(kind, status);
CREATE TABLE IF NOT EXISTS knowledge_sigs (sig TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY (sig, id));
CREATE INDEX IF NOT EXISTS ix_sig ON knowledge_sigs(sig);
CREATE TABLE IF NOT EXISTS knowledge_versions (
  id TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL, note TEXT DEFAULT '', ts REAL NOT NULL,
  PRIMARY KEY (id, version));
CREATE TABLE IF NOT EXISTS knowledge_links (
  src TEXT NOT NULL, dst TEXT NOT NULL, rel TEXT NOT NULL, PRIMARY KEY (src, dst, rel));
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
  id UNINDEXED, title, summary, body, sigs, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS kstats (event TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS retrieval_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, query TEXT, kinds TEXT, n INTEGER, ms REAL, tokens INTEGER);

CREATE TABLE IF NOT EXISTS index_queue (asset_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS harvest_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL, attempts INTEGER DEFAULT 0,
  last_error TEXT DEFAULT '', created_at REAL NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    # one Library is only ever used by one caller at a time (the MCP server serialises with a lock), but possibly from different threads
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(search_log)")}
    if "ms" not in cols:                                   # databases created before latency was logged
        con.execute("ALTER TABLE search_log ADD COLUMN ms REAL")
    con.commit()
    return con


def jdump(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def now() -> float:
    return time.time()
