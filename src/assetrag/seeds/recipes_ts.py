"""TypeScript web/server building blocks (stdlib only, tested with `node --test`). Erasable syntax only (no enums or
parameter properties); types are not checked by tsc in the sandbox. Where a mature library exists the recipe says so."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

TSX = ["javascript", "typescript"]

TS = [
# ------------------------------------------------------------------ rate limiting
RC("ts-sliding-window-rate-limiter", "Sliding-window rate limiter for an HTTP API (TypeScript)", "typescript",
   "An API, login route or bot command needs per-key request limits that do not allow 2x bursts at window edges.",
   "Keep a sorted list of request timestamps per key; allow when fewer than `limit` fall inside the window; return remaining and retryAfterMs for the 429/Retry-After header; sweep idle keys so memory stays bounded; clock injected for tests.",
   {"limiter.ts": r'''export interface Decision { allowed: boolean; remaining: number; retryAfterMs: number }

export class SlidingWindowLimiter {
  private hits = new Map<string, number[]>()
  private limit: number
  private windowMs: number
  private now: () => number

  constructor(limit: number, windowMs: number, now: () => number = Date.now) {
    if (!Number.isInteger(limit) || limit < 1) throw new RangeError('limit must be a positive integer')
    if (!(windowMs > 0)) throw new RangeError('windowMs must be > 0')
    this.limit = limit; this.windowMs = windowMs; this.now = now
  }

  check(key: string): Decision {
    const t = this.now()
    const live = (this.hits.get(key) ?? []).filter((x) => x > t - this.windowMs)
    if (live.length >= this.limit) {
      this.hits.set(key, live)
      return { allowed: false, remaining: 0, retryAfterMs: live[0] + this.windowMs - t }
    }
    live.push(t)
    this.hits.set(key, live)
    return { allowed: true, remaining: this.limit - live.length, retryAfterMs: 0 }
  }

  /** Drop keys with no hits inside the window; call from a timer to bound memory. */
  sweep(): number {
    const t = this.now(); let n = 0
    for (const [k, v] of this.hits) if (!v.some((x) => x > t - this.windowMs)) { this.hits.delete(k); n++ }
    return n
  }

  get size(): number { return this.hits.size }
}

/** Seconds for a Retry-After header (rounded up, at least 1). */
export const retryAfterSeconds = (d: Decision): number => Math.max(1, Math.ceil(d.retryAfterMs / 1000))
''', "limiter.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SlidingWindowLimiter, retryAfterSeconds } from './limiter.ts'

test('allows up to limit then blocks with retryAfter', () => {
  let t = 1000
  const l = new SlidingWindowLimiter(3, 10_000, () => t)
  assert.deepEqual([l.check('a').allowed, l.check('a').allowed, l.check('a').allowed], [true, true, true])
  t = 4000
  const d = l.check('a')
  assert.equal(d.allowed, false); assert.equal(d.remaining, 0)
  assert.equal(d.retryAfterMs, 7000); assert.equal(retryAfterSeconds(d), 7)
})

test('window slides: no 2x burst at the boundary', () => {
  let t = 0
  const l = new SlidingWindowLimiter(2, 1000, () => t)
  l.check('k'); t = 900; l.check('k')
  t = 1001                       // first hit expired, second (t=900) still counts
  assert.equal(l.check('k').allowed, true)
  assert.equal(l.check('k').allowed, false)
})

test('keys are independent and sweep frees idle keys', () => {
  let t = 0
  const l = new SlidingWindowLimiter(1, 1000, () => t)
  assert.equal(l.check('a').allowed, true); assert.equal(l.check('b').allowed, true)
  assert.equal(l.check('a').allowed, false)
  t = 5000
  assert.equal(l.sweep(), 2); assert.equal(l.size, 0)
})

test('validates arguments', () => {
  assert.throws(() => new SlidingWindowLimiter(0, 1000), RangeError)
  assert.throws(() => new SlidingWindowLimiter(1, 0), RangeError)
})
'''}, NODE_TS, assumptions=TSA + ["in-process only; use rate-limiter-flexible with Redis for multiple instances"], compat={"node": ">=22.18"},
   tags=["rate-limit", "rate-limiter", "sliding-window", "throttle", "api", "429", "http", "typescript", "javascript"],
   avoid="Fixed-window counters (allow 2x bursts at the edge); unbounded per-key maps; Date.now() hard-coded so tests need sleeps."),

# ------------------------------------------------------------------ offline outbox
RC("ts-offline-outbox", "Offline queue (outbox) that syncs when back online (TypeScript)", "typescript",
   "A web app must accept user actions while offline and deliver them later, in order, exactly once from the server's view.",
   "Persist each action with a client-generated idempotency key; flush in order through an injected send(); stop at the first transient failure and back off; drop to a dead-letter list after max attempts or on a permanent (4xx) error; flush on the 'online' event.",
   {"outbox.ts": r'''export interface Item<T> { id: string; payload: T; attempts: number; createdAt: number }
export interface Store<T> { load(): Item<T>[]; save(items: Item<T>[]): void }
export type SendResult = 'ok' | 'retry' | 'drop'
export type Send<T> = (item: Item<T>) => Promise<SendResult>

export class MemoryStore<T> implements Store<T> {
  private items: Item<T>[] = []
  load() { return this.items.map((x) => ({ ...x })) }
  save(items: Item<T>[]) { this.items = items.map((x) => ({ ...x })) }
}

export class Outbox<T> {
  dead: Item<T>[] = []
  private flushing: Promise<number> | null = null
  private store: Store<T>
  private send: Send<T>
  private maxAttempts: number
  private newId: () => string
  private now: () => number

  constructor(store: Store<T>, send: Send<T>, opts: { maxAttempts?: number; newId?: () => string; now?: () => number } = {}) {
    this.store = store; this.send = send
    this.maxAttempts = opts.maxAttempts ?? 5
    this.newId = opts.newId ?? (() => crypto.randomUUID())
    this.now = opts.now ?? Date.now
  }

  /** The id doubles as the server-side idempotency key (send it as `Idempotency-Key`). */
  enqueue(payload: T): string {
    const id = this.newId()
    this.store.save([...this.store.load(), { id, payload, attempts: 0, createdAt: this.now() }])
    return id
  }

  get pending(): number { return this.store.load().length }

  /** Deliver in order. Resolves with the number delivered. Concurrent calls share one run. */
  flush(): Promise<number> {
    if (!this.flushing) this.flushing = this.run().finally(() => { this.flushing = null })
    return this.flushing
  }

  private async run(): Promise<number> {
    let sent = 0
    for (;;) {
      const items = this.store.load()
      const head = items[0]
      if (!head) return sent
      let res: SendResult
      try { res = await this.send(head) } catch { res = 'retry' }
      if (res === 'ok') { this.store.save(items.slice(1)); sent++; continue }
      if (res === 'drop' || head.attempts + 1 >= this.maxAttempts) {
        this.dead.push({ ...head, attempts: head.attempts + 1 }); this.store.save(items.slice(1)); continue
      }
      this.store.save([{ ...head, attempts: head.attempts + 1 }, ...items.slice(1)])
      return sent                       // keep order: do not skip past a transient failure
    }
  }

  /** Browser wiring: flush on reconnect. Returns an unsubscribe function. */
  attach(target: { addEventListener(t: string, f: () => void): void; removeEventListener(t: string, f: () => void): void }): () => void {
    const f = () => { void this.flush() }
    target.addEventListener('online', f)
    return () => target.removeEventListener('online', f)
  }
}

/** Map an HTTP status to a SendResult: 2xx ok, 408/429/5xx retry, other 4xx are permanent. */
export function classify(status: number): SendResult {
  if (status >= 200 && status < 300) return 'ok'
  if (status === 408 || status === 429 || status >= 500) return 'retry'
  return 'drop'
}
''', "outbox.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Outbox, MemoryStore, classify } from './outbox.ts'

const mk = (send: any, opts: any = {}) => { let n = 0; return new Outbox<string>(new MemoryStore(), send, { newId: () => 'id' + ++n, ...opts }) }

test('delivers in order and empties the queue', async () => {
  const seen: string[] = []
  const o = mk(async (i: any) => { seen.push(i.payload); return 'ok' })
  o.enqueue('a'); o.enqueue('b'); o.enqueue('c')
  assert.equal(await o.flush(), 3); assert.deepEqual(seen, ['a', 'b', 'c']); assert.equal(o.pending, 0)
})

test('transient failure stops the run, keeps order, then resumes', async () => {
  let fail = true; const seen: string[] = []
  const o = mk(async (i: any) => { if (i.payload === 'b' && fail) return 'retry'; seen.push(i.payload); return 'ok' })
  o.enqueue('a'); o.enqueue('b'); o.enqueue('c')
  assert.equal(await o.flush(), 1); assert.equal(o.pending, 2)
  fail = false
  assert.equal(await o.flush(), 2); assert.deepEqual(seen, ['a', 'b', 'c'])
})

test('permanent errors and exhausted retries go to dead letters without blocking the rest', async () => {
  const o = mk(async (i: any) => (i.payload === 'bad' ? 'drop' : i.payload === 'flaky' ? 'retry' : 'ok'), { maxAttempts: 2 })
  o.enqueue('bad'); o.enqueue('flaky'); o.enqueue('good')
  await o.flush(); await o.flush()
  assert.deepEqual(o.dead.map((d) => d.payload), ['bad', 'flaky']); assert.equal(o.pending, 0)
})

test('thrown send counts as retry; concurrent flushes share one run', async () => {
  let calls = 0
  const o = mk(async () => { calls++; throw new Error('offline') })
  o.enqueue('x')
  const [a, b] = await Promise.all([o.flush(), o.flush()])
  assert.equal(a, 0); assert.equal(b, 0); assert.equal(calls, 1); assert.equal(o.pending, 1)
})

test('ids are idempotency keys and classify maps statuses', () => {
  const o = mk(async () => 'ok')
  assert.equal(o.enqueue('x'), 'id1')
  assert.deepEqual([200, 204, 408, 429, 503, 400, 404].map(classify), ['ok', 'ok', 'retry', 'retry', 'retry', 'drop', 'drop'])
})

test('attach flushes on online and unsubscribes', async () => {
  let sent = 0; const o = mk(async () => { sent++; return 'ok' })
  const listeners = new Set<() => void>()
  const target = { addEventListener: (_: string, f: () => void) => listeners.add(f), removeEventListener: (_: string, f: () => void) => listeners.delete(f) }
  const off = o.attach(target)
  o.enqueue('x'); listeners.forEach((f) => f()); await o.flush()
  assert.equal(sent, 1); off(); assert.equal(listeners.size, 0)
})
'''}, NODE_TS, assumptions=TSA + ["store is injected: back it with localStorage/IndexedDB in the browser", "server must honour the Idempotency-Key"],
   compat={"node": ">=22.18"},
   tags=["offline", "outbox", "queue", "sync", "pwa", "idempotency", "retry", "service-worker", "typescript", "javascript"],
   avoid="Fire-and-forget fetch on submit; retrying without an idempotency key (duplicate writes); skipping ahead past a failed item (reorders edits)."),

# ------------------------------------------------------------------ Intl money
RC("ts-intl-money", "Format and compute money per locale with Intl, in integer minor units (TypeScript)", "typescript",
   "Prices and balances must display correctly per locale and currency (decimals, grouping, symbol position) and never use floats for arithmetic.",
   "Store amounts as integer minor units; take the currency's fraction digits from Intl.NumberFormat so JPY has 0 and BHD has 3; parse decimal strings without floats; split totals with largest-remainder allocation so parts sum exactly.",
   {"money.ts": r'''export interface Money { minor: bigint; currency: string }

export function digits(currency: string): number {
  return new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2
}

export function money(minor: number | bigint, currency: string): Money {
  if (typeof minor === 'number' && !Number.isInteger(minor)) throw new RangeError('minor units must be an integer')
  return { minor: BigInt(minor), currency: currency.toUpperCase() }
}

/** '12.34' -> 1234n for USD. Exact: no floating point involved; rejects more decimals than the currency has. */
export function fromDecimal(s: string, currency: string): Money {
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(s.trim())
  if (!m) throw new SyntaxError(`not a decimal amount: ${s}`)
  const d = digits(currency), frac = m[3] ?? ''
  if (frac.length > d) throw new RangeError(`${currency} has ${d} decimal places`)
  const v = BigInt(m[2] + frac.padEnd(d, '0'))
  return money(m[1] ? -v : v, currency)
}

export function add(a: Money, b: Money): Money {
  if (a.currency !== b.currency) throw new TypeError(`currency mismatch ${a.currency}/${b.currency}`)
  return { minor: a.minor + b.minor, currency: a.currency }
}

export function format(m: Money, locale = 'en-US'): string {
  const d = digits(m.currency), neg = m.minor < 0n, abs = neg ? -m.minor : m.minor
  const base = 10n ** BigInt(d)
  const num = `${abs / base}${d ? '.' + String(abs % base).padStart(d, '0') : ''}`
  // formatToParts on the decimal *string* keeps bigint precision; Intl accepts numeric strings
  const out = new Intl.NumberFormat(locale, { style: 'currency', currency: m.currency }).format(num as unknown as number)
  return neg ? new Intl.NumberFormat(locale, { style: 'currency', currency: m.currency }).format(('-' + num) as unknown as number) : out
}

/** Split an amount by integer weights; remainders go to the largest fractional parts so the parts sum exactly. */
export function allocate(m: Money, weights: number[]): Money[] {
  if (!weights.length || weights.some((w) => !Number.isInteger(w) || w < 0)) throw new RangeError('weights must be non-negative integers')
  const total = weights.reduce((a, b) => a + b, 0)
  if (total === 0) throw new RangeError('weights sum to zero')
  const neg = m.minor < 0n, abs = neg ? -m.minor : m.minor, W = BigInt(total)
  const parts = weights.map((w) => (abs * BigInt(w)) / W)
  let rest = abs - parts.reduce((a, b) => a + b, 0n)
  const order = weights.map((w, i) => ({ i, r: (abs * BigInt(w)) % W })).sort((a, b) => (a.r === b.r ? a.i - b.i : a.r > b.r ? -1 : 1))
  for (const { i } of order) { if (rest === 0n) break; parts[i] += 1n; rest-- }
  return parts.map((p) => ({ minor: neg ? -p : p, currency: m.currency }))
}
''', "money.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { money, fromDecimal, add, format, allocate, digits } from './money.ts'

test('currency digits come from Intl', () => {
  assert.equal(digits('USD'), 2); assert.equal(digits('JPY'), 0); assert.equal(digits('BHD'), 3)
})

test('exact decimal parsing, no float drift', () => {
  assert.equal(fromDecimal('0.10', 'USD').minor, 10n)
  assert.equal(add(fromDecimal('0.1', 'USD'), fromDecimal('0.2', 'USD')).minor, 30n)
  assert.equal(fromDecimal('1234', 'JPY').minor, 1234n)
  assert.equal(fromDecimal('-5.5', 'USD').minor, -550n)
  assert.throws(() => fromDecimal('1.234', 'USD'), RangeError)
  assert.throws(() => fromDecimal('abc', 'USD'), SyntaxError)
  assert.throws(() => add(money(1, 'USD'), money(1, 'EUR')), TypeError)
})

test('locale-aware formatting', () => {
  assert.equal(format(money(123456, 'USD'), 'en-US'), '$1,234.56')
  assert.match(format(money(123456, 'EUR'), 'de-DE'), /^1\.234,56\s€$/)
  assert.match(format(money(1234, 'JPY'), 'ja-JP'), /1,234/)
  assert.equal(format(money(-5, 'USD'), 'en-US'), '-$0.05')
  assert.match(format(money(1500, 'BHD'), 'en-US'), /1\.500/)
})

test('allocate sums exactly and is deterministic', () => {
  const parts = allocate(money(100, 'USD'), [1, 1, 1])
  assert.deepEqual(parts.map((p) => p.minor), [34n, 33n, 33n])
  assert.equal(allocate(money(5, 'USD'), [3, 7]).reduce((a, p) => a + p.minor, 0n), 5n)
  assert.deepEqual(allocate(money(-100, 'USD'), [1, 1, 1]).map((p) => p.minor), [-34n, -33n, -33n])
  assert.throws(() => allocate(money(1, 'USD'), [0, 0]), RangeError)
})
'''}, NODE_TS, assumptions=TSA + ["Node built with full ICU (default since 13)", "for rich money types use dinero.js"], compat={"node": ">=22.18"},
   tags=["money", "currency", "intl", "numberformat", "locale", "decimal", "bigint", "format", "typescript", "javascript"],
   avoid="Floating-point amounts (0.1 + 0.2); toFixed(2) for display; hard-coded 2 decimals for every currency; naive amount/3 splits that lose cents."),

# ------------------------------------------------------------------ i18n plurals
RC("ts-i18n-plurals", "Small i18n translator with CLDR plurals and fallback locales (TypeScript)", "typescript",
   "Translations need plural forms that differ per language (Russian few/many, Arabic zero..many), interpolation and a fallback chain.",
   "Messages are strings or {zero,one,two,few,many,other} objects; Intl.PluralRules picks the category (exact `=0` keys win); {name} interpolation; fallback chain de-AT -> de -> en; missing keys return the key and are reported.",
   {"i18n.ts": r'''export type Message = string | Record<string, string>
export type Catalog = Record<string, Record<string, Message>>

export function createI18n(catalog: Catalog, locale: string, opts: { fallback?: string[]; onMissing?: (key: string, locale: string) => void } = {}) {
  const chain = [...new Set([locale, locale.split('-')[0], ...(opts.fallback ?? ['en'])])]
  const rules = new Map<string, Intl.PluralRules>()
  const pr = (l: string) => { let r = rules.get(l); if (!r) rules.set(l, (r = new Intl.PluralRules(l))); return r }

  function find(key: string): { msg: Message; locale: string } | undefined {
    for (const l of chain) { const m = catalog[l]?.[key]; if (m !== undefined) return { msg: m, locale: l } }
    return undefined
  }

  function t(key: string, params: Record<string, string | number> = {}): string {
    const hit = find(key)
    if (!hit) { opts.onMissing?.(key, locale); return key }
    let text: string
    if (typeof hit.msg === 'string') text = hit.msg
    else {
      const n = Number(params.count)
      if (!Number.isFinite(n)) throw new TypeError(`'${key}' is plural: pass params.count`)
      text = hit.msg[`=${n}`] ?? hit.msg[pr(hit.locale).select(n)] ?? hit.msg.other
      if (text === undefined) throw new Error(`'${key}' has no 'other' form`)
    }
    return text.replace(/\{(\w+)\}/g, (m, name) => (name in params ? String(params[name]) : m))
  }
  return { t, locale, chain }
}
''', "i18n.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createI18n } from './i18n.ts'

const cat = {
  en: { files: { one: '{count} file', other: '{count} files' }, hello: 'Hello {name}', onlyen: 'English only', inbox: { '=0': 'Inbox is empty', one: '1 message', other: '{count} messages' } },
  ru: { files: { one: '{count} файл', few: '{count} файла', many: '{count} файлов', other: '{count} файла' } },
  de: { hello: 'Hallo {name}' },
}

test('english plurals and exact =0', () => {
  const { t } = createI18n(cat, 'en')
  assert.equal(t('files', { count: 1 }), '1 file'); assert.equal(t('files', { count: 2 }), '2 files')
  assert.equal(t('inbox', { count: 0 }), 'Inbox is empty'); assert.equal(t('inbox', { count: 5 }), '5 messages')
})

test('russian one/few/many', () => {
  const { t } = createI18n(cat, 'ru')
  assert.equal(t('files', { count: 1 }), '1 файл'); assert.equal(t('files', { count: 3 }), '3 файла')
  assert.equal(t('files', { count: 5 }), '5 файлов'); assert.equal(t('files', { count: 21 }), '21 файл')
})

test('fallback chain and regional locales', () => {
  const seen: string[] = []
  const { t, chain } = createI18n(cat, 'de-AT', { onMissing: (k) => seen.push(k) })
  assert.deepEqual(chain, ['de-AT', 'de', 'en'])
  assert.equal(t('hello', { name: 'Ana' }), 'Hallo Ana'); assert.equal(t('onlyen'), 'English only')
  assert.equal(t('nope'), 'nope'); assert.deepEqual(seen, ['nope'])
})

test('missing params stay visible, plural needs count', () => {
  const { t } = createI18n(cat, 'en')
  assert.equal(t('hello'), 'Hello {name}')
  assert.throws(() => t('files'), TypeError)
})
'''}, NODE_TS, assumptions=TSA + ["for ICU MessageFormat features (select, nested plurals, dates) use i18next or formatjs"], compat={"node": ">=22.18"},
   tags=["i18n", "translation", "plural", "locale", "intl", "pluralrules", "localization", "l10n", "typescript", "javascript"],
   avoid="`count === 1 ? 'file' : 'files'` (wrong for most languages); string concatenation of translated fragments."),

# ------------------------------------------------------------------ recurring dates
RC("ts-recurring-dates", "Generate recurring payday / schedule dates (TypeScript)", "typescript",
   "Apps need the next N paydays, due dates or renewal dates: weekly, every N weeks, semi-monthly, monthly on the 31st, last business day.",
   "Work in UTC calendar dates (no local-time DST traps); monthly rules clamp to the month's last day; optional roll-back from weekends to Friday; every-N-weeks counts from an anchor date; generator yields ISO dates >= from.",
   {"recur.ts": r'''export type Rule =
  | { kind: 'weekly'; every?: number; anchor: string }            // every N weeks from anchor (same weekday)
  | { kind: 'monthly'; day: number }                               // day 1..31, clamped to month length
  | { kind: 'semi-monthly'; days?: [number, number] }              // default 15th and last day
  | { kind: 'last-business-day' }

const DAY = 86_400_000
const iso = (d: Date) => d.toISOString().slice(0, 10)
const parse = (s: string) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s); if (!m) throw new SyntaxError(`bad date ${s}`); return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) }
const dim = (y: number, m: number) => new Date(Date.UTC(y, m + 1, 0)).getUTCDate()

function rollBack(d: Date): Date {                                 // Saturday/Sunday -> previous Friday
  const w = d.getUTCDay()
  return w === 6 ? new Date(d.getTime() - DAY) : w === 0 ? new Date(d.getTime() - 2 * DAY) : d
}

function lastBusiness(y: number, m: number): Date { return rollBack(new Date(Date.UTC(y, m, dim(y, m)))) }

export function* occurrences(rule: Rule, from: string, opts: { weekendRollBack?: boolean } = {}): Generator<string> {
  const start = parse(from), adj = (d: Date) => (opts.weekendRollBack ? rollBack(d) : d)
  if (rule.kind === 'weekly') {
    const every = rule.every ?? 1; if (!Number.isInteger(every) || every < 1) throw new RangeError('every must be >= 1')
    const step = 7 * every * DAY, a = parse(rule.anchor).getTime()
    let t = a + Math.max(0, Math.ceil((start.getTime() - a) / step)) * step
    for (;;) { yield iso(new Date(t)); t += step }
  }
  let y = start.getUTCFullYear(), m = start.getUTCMonth()
  for (;;) {
    let days: Date[]
    if (rule.kind === 'monthly') {
      if (rule.day < 1 || rule.day > 31) throw new RangeError('day must be 1..31')
      days = [new Date(Date.UTC(y, m, Math.min(rule.day, dim(y, m))))]
    } else if (rule.kind === 'semi-monthly') {
      const [a, b] = rule.days ?? [15, 31]
      days = [a, b].map((d) => new Date(Date.UTC(y, m, Math.min(d, dim(y, m)))))
    } else days = [lastBusiness(y, m)]
    for (const d of days) { const x = rule.kind === 'last-business-day' ? d : adj(d); if (x >= start) yield iso(x) }
    if (++m > 11) { m = 0; y++ }
  }
}

export function next(rule: Rule, from: string, count: number, opts: { weekendRollBack?: boolean } = {}): string[] {
  const out: string[] = []
  for (const d of occurrences(rule, from, opts)) { out.push(d); if (out.length >= count) break }
  return out
}
''', "recur.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { next } from './recur.ts'

test('every other friday from an anchor', () => {
  assert.deepEqual(next({ kind: 'weekly', every: 2, anchor: '2026-01-02' }, '2026-01-10', 3), ['2026-01-16', '2026-01-30', '2026-02-13'])
  assert.deepEqual(next({ kind: 'weekly', anchor: '2026-01-02' }, '2026-01-02', 2), ['2026-01-02', '2026-01-09'])
})

test('monthly on the 31st clamps to month end, leap years included', () => {
  assert.deepEqual(next({ kind: 'monthly', day: 31 }, '2024-01-01', 4), ['2024-01-31', '2024-02-29', '2024-03-31', '2024-04-30'])
  assert.deepEqual(next({ kind: 'monthly', day: 31 }, '2025-02-01', 1), ['2025-02-28'])
})

test('semi-monthly 15th and last day, with weekend roll-back to Friday', () => {
  assert.deepEqual(next({ kind: 'semi-monthly' }, '2026-02-01', 3), ['2026-02-15', '2026-02-28', '2026-03-15'])
  assert.deepEqual(next({ kind: 'semi-monthly' }, '2026-02-01', 3, { weekendRollBack: true }), ['2026-02-13', '2026-02-27', '2026-03-13'])
})

test('last business day and starting mid-month', () => {
  assert.deepEqual(next({ kind: 'last-business-day' }, '2026-02-01', 3), ['2026-02-27', '2026-03-31', '2026-04-30'])
  assert.deepEqual(next({ kind: 'monthly', day: 5 }, '2026-03-06', 2), ['2026-04-05', '2026-05-05'])
})

test('year rollover and bad input', () => {
  assert.deepEqual(next({ kind: 'monthly', day: 15 }, '2026-12-20', 2), ['2027-01-15', '2027-02-15'])
  assert.throws(() => next({ kind: 'monthly', day: 0 }, '2026-01-01', 1), RangeError)
  assert.throws(() => next({ kind: 'monthly', day: 1 }, 'tomorrow', 1), SyntaxError)
})
'''}, NODE_TS, assumptions=TSA + ["dates are calendar dates in UTC (no time zone arithmetic)", "for full RFC 5545 rules use rrule"], compat={"node": ">=22.18"},
   tags=["recurring", "recurrence", "payday", "schedule", "dates", "calendar", "monthly", "weekly", "billing", "due-date", "typescript", "javascript"],
   avoid="Adding 30 days for 'a month'; local-time Date math across DST; setMonth overflow (Jan 31 + 1 month = March 3)."),

# ------------------------------------------------------------------ schema validation
RC("ts-schema-validate", "Validate config and request bodies with a tiny schema library (TypeScript)", "typescript",
   "Untrusted JSON (request bodies, config files, env) must be validated once at the boundary with all errors reported and unknown keys stripped.",
   "Composable validators (string/number/boolean/enum/array/object/optional) returning {ok,value} or {ok:false,errors:[{path,message}]}; collects every error, coerces nothing silently, strips unknown keys, never mutates input.",
   {"schema.ts": r'''export interface Issue { path: string; message: string }
export type Result<T> = { ok: true; value: T } | { ok: false; errors: Issue[] }
export interface Schema<T> { parse(v: unknown, path?: string): Result<T> }

const ok = <T>(value: T): Result<T> => ({ ok: true, value })
const bad = (path: string, message: string): Result<never> => ({ ok: false, errors: [{ path: path || '$', message }] })

export const string = (o: { min?: number; max?: number; pattern?: RegExp; trim?: boolean } = {}): Schema<string> => ({
  parse(v, p = '') {
    if (typeof v !== 'string') return bad(p, 'expected string')
    const s = o.trim ? v.trim() : v
    if (o.min !== undefined && s.length < o.min) return bad(p, `must be at least ${o.min} characters`)
    if (o.max !== undefined && s.length > o.max) return bad(p, `must be at most ${o.max} characters`)
    if (o.pattern && !o.pattern.test(s)) return bad(p, 'has an invalid format')
    return ok(s)
  },
})

export const number = (o: { int?: boolean; min?: number; max?: number } = {}): Schema<number> => ({
  parse(v, p = '') {
    if (typeof v !== 'number' || !Number.isFinite(v)) return bad(p, 'expected finite number')
    if (o.int && !Number.isInteger(v)) return bad(p, 'expected integer')
    if (o.min !== undefined && v < o.min) return bad(p, `must be >= ${o.min}`)
    if (o.max !== undefined && v > o.max) return bad(p, `must be <= ${o.max}`)
    return ok(v)
  },
})

export const boolean = (): Schema<boolean> => ({ parse: (v, p = '') => (typeof v === 'boolean' ? ok(v) : bad(p, 'expected boolean')) })

export const oneOf = <T extends string>(...values: T[]): Schema<T> => ({
  parse: (v, p = '') => (values.includes(v as T) ? ok(v as T) : bad(p, `expected one of ${values.join(', ')}`)),
})

export const array = <T>(item: Schema<T>, o: { max?: number } = {}): Schema<T[]> => ({
  parse(v, p = '') {
    if (!Array.isArray(v)) return bad(p, 'expected array')
    if (o.max !== undefined && v.length > o.max) return bad(p, `at most ${o.max} items`)
    const out: T[] = [], errors: Issue[] = []
    v.forEach((x, i) => { const r = item.parse(x, `${p}[${i}]`); r.ok ? out.push(r.value) : errors.push(...r.errors) })
    return errors.length ? { ok: false, errors } : ok(out)
  },
})

export const optional = <T>(s: Schema<T>, fallback?: T): Schema<T | undefined> => ({
  parse: (v, p = '') => (v === undefined ? ok(fallback) : s.parse(v, p)),
})

type Shape = Record<string, Schema<any>>
type Out<S extends Shape> = { [K in keyof S]: S[K] extends Schema<infer T> ? T : never }

export const object = <S extends Shape>(shape: S): Schema<Out<S>> => ({
  parse(v, p = '') {
    if (typeof v !== 'object' || v === null || Array.isArray(v)) return bad(p, 'expected object')
    const out: Record<string, unknown> = {}, errors: Issue[] = []
    for (const [k, s] of Object.entries(shape)) {
      const r = s.parse((v as Record<string, unknown>)[k], p ? `${p}.${k}` : k)
      if (!r.ok) errors.push(...r.errors)
      else if (r.value !== undefined) out[k] = r.value
    }
    return errors.length ? { ok: false, errors } : ok(out as Out<S>)
  },
})
''', "schema.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { object, string, number, boolean, oneOf, array, optional } from './schema.ts'

const User = object({
  name: string({ min: 1, max: 20, trim: true }),
  age: number({ int: true, min: 0, max: 150 }),
  role: oneOf('admin', 'member'),
  tags: optional(array(string({ max: 8 }), { max: 3 }), []),
  newsletter: optional(boolean(), false),
})

test('valid input: strips unknown keys, applies defaults, does not mutate', () => {
  const input = { name: '  Ana ', age: 30, role: 'member', admin: true }
  const r = User.parse(input)
  assert.ok(r.ok)
  assert.deepEqual(r.value, { name: 'Ana', age: 30, role: 'member', tags: [], newsletter: false })
  assert.deepEqual(input, { name: '  Ana ', age: 30, role: 'member', admin: true })
})

test('reports every error with a path', () => {
  const r = User.parse({ name: '', age: 1.5, role: 'root', tags: ['ok', 3, 'waytoolongtag'] })
  assert.ok(!r.ok)
  assert.deepEqual(r.errors.map((e) => e.path).sort(), ['age', 'name', 'role', 'tags[1]', 'tags[2]'])
})

test('rejects wrong shapes at the root and nested', () => {
  assert.ok(!User.parse(null).ok); assert.ok(!User.parse([]).ok); assert.ok(!User.parse('x').ok)
  const r = User.parse({ name: 'a', age: NaN, role: 'admin' }); assert.ok(!r.ok)
  const many = array(number(), { max: 2 }).parse([1, 2, 3]); assert.ok(!many.ok)
})
'''}, NODE_TS, assumptions=TSA + ["for production prefer zod/valibot/typebox; this shows the pattern in ~100 lines"], compat={"node": ">=22.18"},
   tags=["validation", "schema", "zod", "request-body", "config", "parse", "input", "sanitize", "typescript", "javascript"],
   avoid="Trusting `req.body as User`; stopping at the first error; copying unknown keys into your model (mass assignment)."),
]
