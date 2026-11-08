"""More TypeScript building blocks: persistence, text safety, I/O and server plumbing (stdlib only, `node --test`)."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

TS2 = [
# ------------------------------------------------------------------ sqlite migrations
RC("ts-sqlite-migrations", "SQLite access layer with versioned migrations (node:sqlite, TypeScript)", "typescript",
   "A Node tool or bot needs an embedded SQL database whose schema evolves safely across releases.",
   "Use the built-in node:sqlite DatabaseSync; keep the schema version in PRAGMA user_version; apply each pending migration inside one transaction (BEGIN IMMEDIATE) so a failure rolls back and the version never advances; refuse databases newer than the code; prepared statements with bound parameters only.",
   {"db.ts": r'''import { DatabaseSync } from 'node:sqlite'

export interface Migration { version: number; name: string; sql: string }

export function open(path: string): DatabaseSync {
  const db = new DatabaseSync(path)
  db.exec('PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 5000;')
  return db
}

export function version(db: DatabaseSync): number {
  return Number((db.prepare('PRAGMA user_version').get() as { user_version: number }).user_version)
}

/** Apply pending migrations in order; returns the names applied. Throws (and rolls back) on the first failure. */
export function migrate(db: DatabaseSync, migrations: Migration[]): string[] {
  const sorted = [...migrations].sort((a, b) => a.version - b.version)
  sorted.forEach((m, i) => { if (m.version !== i + 1) throw new Error(`migrations must be numbered 1..n without gaps (got ${m.version} at position ${i + 1})`) })
  const current = version(db)
  if (current > sorted.length) throw new Error(`database is at schema ${current}, this build only knows ${sorted.length}: upgrade the app`)
  const applied: string[] = []
  for (const m of sorted.slice(current)) {
    db.exec('BEGIN IMMEDIATE')
    try {
      db.exec(m.sql)
      db.exec(`PRAGMA user_version = ${m.version}`)     // integer from our own list, never user input
      db.exec('COMMIT')
    } catch (e) {
      db.exec('ROLLBACK')
      throw new Error(`migration ${m.version} (${m.name}) failed: ${(e as Error).message}`)
    }
    applied.push(m.name)
  }
  return applied
}

/** Run fn inside a transaction; commits on return, rolls back on throw. */
export function tx<T>(db: DatabaseSync, fn: () => T): T {
  db.exec('BEGIN IMMEDIATE')
  try { const r = fn(); db.exec('COMMIT'); return r } catch (e) { db.exec('ROLLBACK'); throw e }
}
''', "db.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { DatabaseSync } from 'node:sqlite'
import { migrate, version, tx } from './db.ts'

const M = [
  { version: 1, name: 'users', sql: 'CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL);' },
  { version: 2, name: 'email', sql: 'ALTER TABLE users ADD COLUMN email TEXT;' },
]

test('applies pending migrations once and records the version', () => {
  const db = new DatabaseSync(':memory:')
  assert.deepEqual(migrate(db, M), ['users', 'email']); assert.equal(version(db), 2)
  assert.deepEqual(migrate(db, M), [])
  db.prepare('INSERT INTO users (name, email) VALUES (?, ?)').run('ana', 'a@x.io')
  assert.equal((db.prepare('SELECT COUNT(*) AS n FROM users').get() as any).n, 1)
})

test('a failing migration rolls back completely and does not advance the version', () => {
  const db = new DatabaseSync(':memory:')
  const bad = [...M, { version: 3, name: 'broken', sql: 'CREATE TABLE ok (a); INSERT INTO nope VALUES (1);' }]
  assert.throws(() => migrate(db, bad), /migration 3 \(broken\) failed/)
  assert.equal(version(db), 2)
  assert.throws(() => db.prepare('SELECT * FROM ok').get())          // partial DDL was rolled back
})

test('refuses a database from the future and gaps in numbering', () => {
  const db = new DatabaseSync(':memory:'); migrate(db, M)
  assert.throws(() => migrate(db, M.slice(0, 1)), /upgrade the app/)
  assert.throws(() => migrate(new DatabaseSync(':memory:'), [{ version: 2, name: 'x', sql: '' }]), /without gaps/)
})

test('tx commits and rolls back; bound parameters defuse injection', () => {
  const db = new DatabaseSync(':memory:'); migrate(db, M)
  tx(db, () => db.prepare('INSERT INTO users (name) VALUES (?)').run("x'); DROP TABLE users;--"))
  assert.throws(() => tx(db, () => { db.prepare('INSERT INTO users (name) VALUES (?)').run('y'); throw new Error('boom') }), /boom/)
  const rows = db.prepare('SELECT name FROM users').all() as { name: string }[]
  assert.deepEqual(rows.map((r) => r.name), ["x'); DROP TABLE users;--"])
})
'''}, NODE_TS, assumptions=TSA + ["Node >= 22.5 with node:sqlite (prints an ExperimentalWarning on some versions)", "forward-only migrations; write a new migration to undo one"],
   compat={"node": ">=22.18"},
   tags=["sqlite", "migrations", "database", "schema", "user_version", "transaction", "node-sqlite", "sql", "versioned", "typescript", "javascript"],
   avoid="CREATE TABLE IF NOT EXISTS sprinkled through startup code; string-building SQL; migrations that run outside a transaction."),

# ------------------------------------------------------------------ word filter
RC("ts-word-filter", "Word filter that normalizes leetspeak, homoglyphs and spacing (TypeScript)", "typescript",
   "Chat and usernames need a blocklist that survives 'b4dw0rd', Cyrillic look-alikes, zero-width characters and 'b a d w o r d' without flagging innocent words that merely contain a blocked string.",
   "Normalize (NFKD, strip marks and zero-width, homoglyph + leet map, lowercase, collapse repeats), tokenize on non-letters, match whole tokens plus runs of single-letter tokens re-joined; embedded matches are only flagged when the token *starts or ends* with a long-enough blocked word, so 'classic' passes.",
   {"filter.ts": r'''const HOMOGLYPHS: Record<string, string> = {
  'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'у': 'y', 'і': 'i', 'ѕ': 's', 'ј': 'j', 'ԁ': 'd', 'ɡ': 'g',
  'α': 'a', 'ο': 'o', 'ν': 'v', 'ρ': 'p', 'ı': 'i', 'ƒ': 'f',
}
const LEET: Record<string, string> = { '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '8': 'b', '@': 'a', '$': 's' }
const INVISIBLE = /[​-‏⁠﻿­]/g

export function normalize(s: string): string {
  let out = ''
  for (const ch of s.normalize('NFKD').replace(/\p{M}/gu, '').replace(INVISIBLE, '').toLowerCase()) out += HOMOGLYPHS[ch] ?? LEET[ch] ?? ch
  return out.replace(/(.)\1{2,}/g, '$1$1')              // heeellll -> heell
}

export interface Hit { word: string; index: number }

export class WordFilter {
  private words: Set<string>
  private minAffix: number
  constructor(blocked: string[], opts: { minAffix?: number } = {}) {
    this.words = new Set(blocked.map((w) => normalize(w).replace(/[^a-z]/g, '')).filter(Boolean))
    this.minAffix = opts.minAffix ?? 5
  }

  private hitToken(tok: string): string | undefined {
    const t = tok.replace(/(.)\1/g, '$1')                  // also try the fully de-duplicated form
    for (const cand of new Set([tok, t])) {
      if (this.words.has(cand)) return cand
      for (const w of this.words) {
        if (w.length >= this.minAffix && cand.length > w.length && (cand.startsWith(w) || cand.endsWith(w))) return w
      }
    }
    return undefined
  }

  check(text: string): Hit[] {
    const norm = normalize(text), hits: Hit[] = []
    const toks = [...norm.matchAll(/[a-z]+/g)].map((m) => ({ t: m[0], i: m.index! }))
    for (const k of toks) { const w = this.hitToken(k.t); if (w) hits.push({ word: w, index: k.i }) }
    for (let a = 0; a < toks.length; a++) {                 // "b a d w o r d": consecutive single letters re-joined
      if (toks[a].t.length !== 1) continue
      let j = a, joined = ''
      while (j < toks.length && toks[j].t.length === 1) joined += toks[j++].t
      if (j - a >= 3) { const w = this.hitToken(joined); if (w) hits.push({ word: w, index: toks[a].i }) }
      a = j - 1
    }
    return hits.sort((x, y) => x.index - y.index)
  }

  isClean(text: string): boolean { return this.check(text).length === 0 }
  /** Replace flagged tokens with asterisks (word granularity). */
  mask(text: string): string {
    return text.replace(/[\p{L}\p{N}@$]+/gu, (tok) => (this.check(tok).length ? '*'.repeat(tok.length) : tok))
  }
}
''', "filter.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { WordFilter, normalize } from './filter.ts'

const f = new WordFilter(['heck', 'darnation', 'scunthorpe'])

test('normalize folds leet, homoglyphs, marks and invisibles', () => {
  assert.equal(normalize('H3CK'), 'heck'); assert.equal(normalize('hеck'), 'heck')       // Cyrillic е
  assert.equal(normalize('he​ck'), 'heck'); assert.equal(normalize('Ｈéｃｋ'), 'heck')
  assert.equal(normalize('heeeeck'), 'heeck')
})

test('catches obfuscated variants', () => {
  for (const s of ['what the h3ck', 'HECK!', 'hеck', 'h e c k', 'he​ck', 'd4rn4tion', 'hhheeeccckkk']) assert.equal(f.isClean(s), false, s)
})

test('does not flag innocent words (Scunthorpe problem) or short embedded matches', () => {
  for (const s of ['checkpoint', 'the hecklers', 'a perfectly fine sentence', 'recheck later']) assert.equal(f.isClean(s), true, s)
  assert.equal(f.isClean('Scunthorpe united'), false)           // an actual blocked token still hits
})

test('mask replaces only the flagged tokens', () => {
  assert.equal(f.mask('oh h3ck no'), 'oh **** no')
  assert.deepEqual(f.check('heck and darnation').map((h) => h.word), ['heck', 'darnation'])
})
'''}, NODE_TS, assumptions=TSA + ["blocklists are language- and community-specific; combine with human moderation", "test words are deliberately mild"],
   compat={"node": ">=22.18"},
   tags=["filter", "profanity", "moderation", "blocklist", "leetspeak", "homoglyph", "unicode", "normalize", "chat", "username", "typescript", "javascript"],
   avoid="Plain `text.includes(word)` (Scunthorpe problem, trivially bypassed); regex per word on raw text; lowercase-only normalization."),

# ------------------------------------------------------------------ burst detector
RC("ts-burst-detector", "Detect bursts of events such as raid joins (TypeScript)", "typescript",
   "A server must notice when many events (joins, signups, failed logins, messages) arrive in a short window and react once, not on every event.",
   "Keep a ring of recent timestamps per key; a burst fires when the count in the window reaches the threshold; after firing, stay quiet for a cooldown and report the burst size when it ends; optional distinct-actor counting so one noisy user is not a raid.",
   {"burst.ts": r'''export interface BurstEvent { key: string; count: number; actors: number; startedAt: number }

export class BurstDetector {
  private seen = new Map<string, { t: number; actor: string }[]>()
  private quietUntil = new Map<string, number>()
  private threshold: number
  private windowMs: number
  private cooldownMs: number
  private minActors: number
  private now: () => number

  constructor(o: { threshold: number; windowMs: number; cooldownMs?: number; minActors?: number; now?: () => number }) {
    if (o.threshold < 2 || o.windowMs <= 0) throw new RangeError('threshold >= 2 and windowMs > 0 required')
    this.threshold = o.threshold; this.windowMs = o.windowMs; this.cooldownMs = o.cooldownMs ?? o.windowMs
    this.minActors = o.minActors ?? 1; this.now = o.now ?? Date.now
  }

  /** Record an event; returns a BurstEvent exactly once per burst (null otherwise). */
  record(key: string, actor = 'anon'): BurstEvent | null {
    const t = this.now()
    const live = (this.seen.get(key) ?? []).filter((e) => e.t > t - this.windowMs)
    live.push({ t, actor })
    this.seen.set(key, live)
    if (t < (this.quietUntil.get(key) ?? 0)) return null
    const actors = new Set(live.map((e) => e.actor)).size
    if (live.length >= this.threshold && actors >= this.minActors) {
      this.quietUntil.set(key, t + this.cooldownMs)
      return { key, count: live.length, actors, startedAt: live[0].t }
    }
    return null
  }

  /** Events currently inside the window (for a status command). */
  load(key: string): number {
    const t = this.now()
    return (this.seen.get(key) ?? []).filter((e) => e.t > t - this.windowMs).length
  }
}
''', "burst.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { BurstDetector } from './burst.ts'

test('fires once when the threshold is reached inside the window', () => {
  let t = 0; const d = new BurstDetector({ threshold: 3, windowMs: 10_000, now: () => t })
  assert.equal(d.record('g', 'a'), null); t = 1000; assert.equal(d.record('g', 'b'), null)
  t = 2000; const e = d.record('g', 'c')
  assert.deepEqual(e, { key: 'g', count: 3, actors: 3, startedAt: 0 })
  t = 3000; assert.equal(d.record('g', 'd'), null)                  // cooldown: no second alert
})

test('slow trickle never fires; cooldown ends and a new burst fires again', () => {
  let t = 0; const d = new BurstDetector({ threshold: 3, windowMs: 1000, cooldownMs: 2000, now: () => t })
  for (let i = 0; i < 10; i++) { t = i * 600; assert.equal(d.record('k'), null) }
  t = 100_000; d.record('k'); d.record('k'); assert.ok(d.record('k'))
  t = 100_500; assert.equal(d.record('k'), null)
  t = 110_000; d.record('k'); d.record('k'); assert.ok(d.record('k'))
})

test('minActors ignores one noisy user; keys are independent', () => {
  let t = 0; const d = new BurstDetector({ threshold: 3, windowMs: 1000, minActors: 2, now: () => t })
  assert.equal(d.record('g', 'spam'), null); assert.equal(d.record('g', 'spam'), null); assert.equal(d.record('g', 'spam'), null)
  assert.ok(d.record('g', 'other'))
  assert.equal(d.record('h', 'x'), null); assert.equal(d.load('h'), 1); assert.equal(d.load('g'), 4)
  assert.throws(() => new BurstDetector({ threshold: 1, windowMs: 1 }), RangeError)
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["burst", "raid", "spike", "anomaly", "detect", "joins", "abuse", "rate", "cooldown", "discord", "bot", "typescript", "javascript"],
   avoid="Alerting on every event over the threshold (alert storms); counting events forever instead of per window; treating one spammer as a raid."),

# ------------------------------------------------------------------ csv
RC("ts-csv", "CSV import and export (RFC 4180) with formula-injection protection (TypeScript)", "typescript",
   "Apps import user CSV (quotes, embedded newlines, BOM, CRLF) and export CSV that spreadsheets will open safely.",
   "Single-pass state machine for quotes/escapes/newlines, BOM stripped, ragged rows reported with line numbers, header mode returns objects; stringify quotes only when needed and neutralises cells starting with = + - @ so Excel/Sheets do not run them as formulas.",
   {"csv.ts": r'''export class CsvError extends Error {
  line: number
  constructor(message: string, line: number) { super(`line ${line}: ${message}`); this.line = line }
}

export function parse(text: string, o: { delimiter?: string } = {}): string[][] {
  const d = o.delimiter ?? ','
  if (d.length !== 1 || d === '"' || d === '\n' || d === '\r') throw new RangeError('bad delimiter')
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)
  const rows: string[][] = []
  let row: string[] = [], field = '', quoted = false, line = 1, fieldStart = 1, wasQuoted = false
  const endField = () => { row.push(field); field = ''; wasQuoted = false }
  const endRow = () => { if (row.length === 0 && field === '' && !wasQuoted) return; endField(); rows.push(row); row = [] }
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (quoted) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++ } else quoted = false }
      else { if (c === '\n') line++; field += c }
    } else if (c === '"') {
      if (field !== '') throw new CsvError('quote in the middle of an unquoted field', line)
      quoted = true; wasQuoted = true; fieldStart = line
    } else if (c === d) endField()
    else if (c === '\n' || c === '\r') { if (c === '\r' && text[i + 1] === '\n') i++; endRow(); line++ }
    else field += c
  }
  if (quoted) throw new CsvError('unterminated quoted field', fieldStart)
  if (field !== '' || row.length || wasQuoted) endRow()
  return rows
}

/** Header mode: first row names the columns; every data row must have the same width. */
export function parseObjects(text: string): Record<string, string>[] {
  const [head, ...rest] = parse(text)
  if (!head) return []
  return rest.map((r, i) => {
    if (r.length !== head.length) throw new CsvError(`expected ${head.length} columns, got ${r.length}`, i + 2)
    return Object.fromEntries(head.map((h, j) => [h, r[j]]))
  })
}

const FORMULA = /^[=+\-@\t\r]/

export function cell(v: unknown, o: { delimiter?: string; safe?: boolean } = {}): string {
  const d = o.delimiter ?? ','
  let s = v === null || v === undefined ? '' : String(v)
  if ((o.safe ?? true) && typeof v === 'string' && FORMULA.test(s)) s = "'" + s
  return /["\r\n]/.test(s) || s.includes(d) ? '"' + s.replaceAll('"', '""') + '"' : s
}

export function stringify(rows: unknown[][], o: { delimiter?: string; safe?: boolean; bom?: boolean } = {}): string {
  const body = rows.map((r) => r.map((v) => cell(v, o)).join(o.delimiter ?? ',')).join('\r\n') + (rows.length ? '\r\n' : '')
  return (o.bom ? '﻿' : '') + body
}
''', "csv.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, parseObjects, stringify, CsvError } from './csv.ts'

test('quotes, escaped quotes, embedded newline and comma, CRLF and BOM', () => {
  const t = '﻿a,b,c\r\n1,"he said ""hi""","x,y"\r\n"multi\nline",,3\r\n'
  assert.deepEqual(parse(t), [['a', 'b', 'c'], ['1', 'he said "hi"', 'x,y'], ['multi\nline', '', '3']])
})

test('no trailing newline, blank lines skipped, quoted empty field kept', () => {
  assert.deepEqual(parse('a,b\n\n1,2'), [['a', 'b'], ['1', '2']])
  assert.deepEqual(parse('""'), [['']])
  assert.deepEqual(parse('a;b', { delimiter: ';' }), [['a', 'b']])
})

test('malformed input reports line numbers', () => {
  assert.throws(() => parse('a,b\n1,"oops'), (e: any) => e instanceof CsvError && e.line === 2)
  assert.throws(() => parse('a,b"c"'), CsvError)
  assert.throws(() => parseObjects('a,b\n1,2,3'), /line 2: expected 2 columns/)
})

test('header mode', () => { assert.deepEqual(parseObjects('n,v\nx,1\ny,2\n'), [{ n: 'x', v: '1' }, { n: 'y', v: '2' }]) })

test('stringify round-trips and neutralises formulas', () => {
  const rows = [['name', 'note'], ['a,b', 'say "x"'], ['multi\nline', 5], ['=HYPERLINK("http://evil")', '@SUM(A1)'], ['-5', null]]
  const out = stringify(rows)
  assert.match(out, /'=HYPERLINK/); assert.match(out, /'@SUM/)
  assert.deepEqual(parse(out)[1], ['a,b', 'say "x"'])
  assert.equal(parse(out)[4][0], "'-5")
  assert.equal(stringify([['=1']], { safe: false }), '=1\r\n')
  assert.ok(stringify([['a']], { bom: true }).startsWith('﻿'))
})
'''}, NODE_TS, assumptions=TSA + ["whole-text parser (not streaming); use papaparse for huge files or type inference"], compat={"node": ">=22.18"},
   tags=["csv", "parse", "stringify", "import", "export", "rfc4180", "spreadsheet", "formula-injection", "excel", "typescript", "javascript"],
   avoid="`line.split(',')` (breaks on quotes/newlines); exporting user text unescaped (CSV injection); assuming LF-only line endings."),

# ------------------------------------------------------------------ turnstile
RC("ts-turnstile-verify", "Verify a Cloudflare Turnstile token on the server (TypeScript)", "typescript",
   "A form protected by Turnstile must verify the widget's token server-side before trusting the submission.",
   "POST secret + token (+ remoteip, idempotency_key) as form data to the siteverify endpoint with a timeout; fail closed on network errors, non-200 and malformed JSON; surface error-codes; check hostname/action when provided; fetch is injected so tests need no network.",
   {"turnstile.ts": r'''export const VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'

export interface Verdict { success: boolean; errors: string[]; hostname?: string; action?: string }
type FetchLike = (url: string, init: { method: string; body: URLSearchParams; signal: AbortSignal }) => Promise<{ ok: boolean; status: number; json(): Promise<any> }>

export async function verifyTurnstile(
  token: string | null | undefined,
  secret: string,
  o: { ip?: string; expectedHostname?: string; expectedAction?: string; idempotencyKey?: string; timeoutMs?: number; fetch?: FetchLike } = {},
): Promise<Verdict> {
  if (!secret) throw new Error('TURNSTILE_SECRET is not configured')       // misconfiguration, not a user error
  if (!token || token.length > 2048) return { success: false, errors: ['missing-input-response'] }
  const body = new URLSearchParams({ secret, response: token })
  if (o.ip) body.set('remoteip', o.ip)
  if (o.idempotencyKey) body.set('idempotency_key', o.idempotencyKey)
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), o.timeoutMs ?? 5000)
  try {
    const res = await (o.fetch ?? (fetch as unknown as FetchLike))(VERIFY_URL, { method: 'POST', body, signal: ctl.signal })
    if (!res.ok) return { success: false, errors: [`http-${res.status}`] }
    const j = await res.json()
    if (typeof j?.success !== 'boolean') return { success: false, errors: ['malformed-response'] }
    const v: Verdict = { success: j.success, errors: Array.isArray(j['error-codes']) ? j['error-codes'] : [], hostname: j.hostname, action: j.action }
    if (v.success && o.expectedHostname && v.hostname !== o.expectedHostname) return { ...v, success: false, errors: [...v.errors, 'hostname-mismatch'] }
    if (v.success && o.expectedAction && v.action !== o.expectedAction) return { ...v, success: false, errors: [...v.errors, 'action-mismatch'] }
    return v
  } catch {
    return { success: false, errors: ['verification-unavailable'] }        // fail closed
  } finally {
    clearTimeout(timer)
  }
}
''', "turnstile.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { verifyTurnstile, VERIFY_URL } from './turnstile.ts'

const reply = (j: any, status = 200) => async () => ({ ok: status < 400, status, json: async () => j })

test('sends secret, token and ip as form data to siteverify', async () => {
  let seen: any
  const fetch = async (url: string, init: any) => { seen = { url, init }; return { ok: true, status: 200, json: async () => ({ success: true, hostname: 'x.dev' }) } }
  const v = await verifyTurnstile('tok', 's3cret', { ip: '1.2.3.4', fetch })
  assert.equal(v.success, true); assert.equal(seen.url, VERIFY_URL); assert.equal(seen.init.method, 'POST')
  assert.equal(seen.init.body.get('secret'), 's3cret'); assert.equal(seen.init.body.get('response'), 'tok'); assert.equal(seen.init.body.get('remoteip'), '1.2.3.4')
})

test('failed verification surfaces error codes', async () => {
  const v = await verifyTurnstile('tok', 's', { fetch: reply({ success: false, 'error-codes': ['invalid-input-response'] }) })
  assert.deepEqual(v, { success: false, errors: ['invalid-input-response'], hostname: undefined, action: undefined })
})

test('fails closed on network error, http error, bad JSON, timeout', async () => {
  assert.deepEqual((await verifyTurnstile('t', 's', { fetch: async () => { throw new Error('down') } })).errors, ['verification-unavailable'])
  assert.deepEqual((await verifyTurnstile('t', 's', { fetch: reply({}, 503) })).errors, ['http-503'])
  assert.deepEqual((await verifyTurnstile('t', 's', { fetch: reply({ nope: 1 }) })).errors, ['malformed-response'])
  const slow = (_u: string, init: any) => new Promise<any>((_, rej) => init.signal.addEventListener('abort', () => rej(new Error('aborted'))))
  assert.equal((await verifyTurnstile('t', 's', { fetch: slow as any, timeoutMs: 20 })).success, false)
})

test('missing token never reaches the network; hostname/action are enforced; missing secret throws', async () => {
  let calls = 0; const fetch = async () => { calls++; return { ok: true, status: 200, json: async () => ({ success: true, hostname: 'evil.dev', action: 'login' }) } }
  assert.deepEqual((await verifyTurnstile('', 's', { fetch })).errors, ['missing-input-response']); assert.equal(calls, 0)
  assert.deepEqual((await verifyTurnstile('t', 's', { fetch, expectedHostname: 'mine.dev' })).errors, ['hostname-mismatch'])
  assert.deepEqual((await verifyTurnstile('t', 's', { fetch, expectedAction: 'signup' })).errors, ['action-mismatch'])
  await assert.rejects(() => verifyTurnstile('t', '', { fetch }), /not configured/)
})
'''}, NODE_TS, assumptions=TSA + ["endpoint and field names follow Cloudflare's documented siteverify API; not exercised against the live service here"],
   compat={"node": ">=22.18"},
   tags=["turnstile", "cloudflare", "captcha", "bot", "verify", "siteverify", "form", "server", "workers", "typescript", "javascript"],
   avoid="Trusting the widget's token without server-side verification; failing open when Cloudflare is unreachable; sending the secret to the browser."),

# ------------------------------------------------------------------ claude hook
RC("ts-claude-hook-handler", "Claude Code hook that reads JSON from stdin and replies with a decision (TypeScript)", "typescript",
   "A Claude Code hook (PreToolUse / UserPromptSubmit) must parse the event JSON on stdin and answer with the documented JSON on stdout, never crashing the session.",
   "Read all of stdin, JSON.parse defensively, route by hook_event_name; PreToolUse answers with hookSpecificOutput.permissionDecision allow|deny|ask plus a reason; UserPromptSubmit may add additionalContext; any internal error exits 0 with no output (fail open) and logs to stderr only.",
   {"hook.ts": r'''export interface HookInput { hook_event_name?: string; tool_name?: string; tool_input?: Record<string, unknown>; prompt?: string; cwd?: string; session_id?: string }

const DENY = [/\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(\/|~|\$HOME)(\s|$)/, /\bgit\s+push\s+[^|;&]*--force(?!-with-lease)/, /:\(\)\s*\{\s*:\|:&\s*\};:/]

export function decide(input: HookInput): Record<string, unknown> | null {
  switch (input.hook_event_name) {
    case 'PreToolUse': {
      if (input.tool_name !== 'Bash') return null
      const cmd = String(input.tool_input?.command ?? '')
      if (DENY.some((r) => r.test(cmd))) {
        return { hookSpecificOutput: { hookEventName: 'PreToolUse', permissionDecision: 'deny', permissionDecisionReason: 'Blocked by project hook: destructive command' } }
      }
      return null
    }
    case 'UserPromptSubmit': {
      if (/\bdeploy\b/i.test(input.prompt ?? '')) {
        return { hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: 'Reminder: run the test suite before deploying.' } }
      }
      return null
    }
    default:
      return null
  }
}

export async function readAll(stream: AsyncIterable<Buffer | string>): Promise<string> {
  const chunks: Buffer[] = []
  for await (const c of stream) chunks.push(typeof c === 'string' ? Buffer.from(c) : c)
  return Buffer.concat(chunks).toString('utf8')
}

/** Entry point: never throws; malformed input or a bug must not block the user's session. */
export async function main(stdin: AsyncIterable<Buffer | string>, out: { write(s: string): unknown }, err: { write(s: string): unknown }): Promise<number> {
  try {
    const raw = await readAll(stdin)
    const input = JSON.parse(raw) as HookInput
    const reply = decide(input)
    if (reply) out.write(JSON.stringify(reply) + '\n')
    return 0
  } catch (e) {
    err.write(`hook error (ignored): ${(e as Error).message}\n`)
    return 0
  }
}

if (import.meta.main) process.exitCode = await main(process.stdin, process.stdout, process.stderr)
''', "hook.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { decide } from './hook.ts'

test('denies destructive Bash, allows normal commands', () => {
  const d: any = decide({ hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'rm -rf /' } })
  assert.equal(d.hookSpecificOutput.permissionDecision, 'deny'); assert.match(d.hookSpecificOutput.permissionDecisionReason, /destructive/)
  assert.ok(decide({ hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'git push origin main --force' } }))
  assert.equal(decide({ hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'rm -rf ./build' } }), null)
  assert.equal(decide({ hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'git push --force-with-lease' } }), null)
  assert.equal(decide({ hook_event_name: 'PreToolUse', tool_name: 'Read', tool_input: {} }), null)
})

test('UserPromptSubmit adds context only when relevant', () => {
  const d: any = decide({ hook_event_name: 'UserPromptSubmit', prompt: 'please deploy to prod' })
  assert.match(d.hookSpecificOutput.additionalContext, /test suite/)
  assert.equal(decide({ hook_event_name: 'UserPromptSubmit', prompt: 'refactor' }), null)
})

const run = (input: string) => spawnSync(process.execPath, ['hook.ts'], { input, encoding: 'utf8' })

test('real process: JSON in on stdin, JSON out on stdout, exit 0', () => {
  const r = run(JSON.stringify({ hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_input: { command: 'rm -rf ~' } }))
  assert.equal(r.status, 0); assert.equal(JSON.parse(r.stdout).hookSpecificOutput.permissionDecision, 'deny')
})

test('real process: garbage input fails open (exit 0, no stdout, message on stderr)', () => {
  const r = run('not json')
  assert.equal(r.status, 0); assert.equal(r.stdout, ''); assert.match(r.stderr, /hook error/)
})
'''}, NODE_TS, assumptions=TSA + ["output shapes follow the documented Claude Code hook protocol (hookSpecificOutput); check your Claude Code version's docs for new fields"],
   compat={"node": ">=22.18"},
   tags=["claude-code", "hook", "pretooluse", "userpromptsubmit", "stdin", "json", "permissiondecision", "additionalcontext", "automation", "typescript", "javascript"],
   avoid="Printing debug text to stdout (it is parsed as the reply); exit code 2 on internal errors (blocks the user); reading stdin with readFileSync('/dev/stdin') on Windows."),
]
