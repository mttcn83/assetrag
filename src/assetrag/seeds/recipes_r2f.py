"""Round-2 recipes, part F: API keys, event batching, pricing UI, gallery/lightbox, scrollspy and cookie consent (TypeScript,
node --test, erasable syntax only). Pure logic with injected clocks; the DOM and network are not exercised."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

LOGIC = "logic only: the DOM, network and storage are stubbed"

R2F = [
RC("ts-api-keys-hashed-prefix", "API keys done right: prefixed, shown once, only the hash stored, constant-time verify, revoke, masked display", "typescript",
   "Customers need API keys; you must never be able to leak them from your own database.",
   "Key format `sk_live_<id>_<secret>` (id is public lookup handle, secret is 32 random bytes), store only SHA-256 of the secret (fast hash is fine for high-entropy random secrets), verify with timingSafeEqual, `lastUsed` and `revokedAt` tracked, masked display `sk_live_ab12…9f`, key shown exactly once at creation.",
   {"keys.ts": r'''import { createHash, randomBytes, timingSafeEqual } from 'node:crypto'

export interface KeyRecord { id: string; prefix: string; hash: string; owner: string; createdAt: number; lastUsedAt?: number; revokedAt?: number }
const sha = (s: string) => createHash('sha256').update(s).digest('hex')

export class KeyStore {
  private byId = new Map<string, KeyRecord>()

  /** The returned `key` is the only time the secret exists in clear text. Show it once and never store it. */
  create(owner: string, now: number, prefix = 'sk_live'): { key: string; record: KeyRecord } {
    const id = randomBytes(6).toString('hex'), secret = randomBytes(32).toString('base64url')
    const record: KeyRecord = { id, prefix, hash: sha(secret), owner, createdAt: now }
    this.byId.set(id, record)
    return { key: `${prefix}_${id}_${secret}`, record: { ...record } }
  }

  verify(key: string, now: number): KeyRecord | null {
    const m = /^([a-z]+_[a-z]+)_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$/.exec(key)
    if (!m) return null
    const rec = this.byId.get(m[2])
    const a = Buffer.from(rec?.hash ?? '0'.repeat(64)), b = Buffer.from(sha(m[3]))        // always hash + compare, even for unknown ids, so timing does not reveal which ids exist
    const same = timingSafeEqual(a, b)
    if (!rec || !same || rec.prefix !== m[1] || rec.revokedAt !== undefined) return null
    rec.lastUsedAt = now
    return { ...rec }
  }

  revoke(id: string, owner: string, now: number): boolean {
    const r = this.byId.get(id)
    if (!r || r.owner !== owner || r.revokedAt !== undefined) return false
    r.revokedAt = now; return true
  }

  list(owner: string): { id: string; masked: string; createdAt: number; lastUsedAt?: number; revoked: boolean }[] {
    return [...this.byId.values()].filter((r) => r.owner === owner).map((r) => ({ id: r.id, masked: mask(r), createdAt: r.createdAt, lastUsedAt: r.lastUsedAt, revoked: r.revokedAt !== undefined }))
  }
}

export const mask = (r: Pick<KeyRecord, 'prefix' | 'id' | 'hash'>) => `${r.prefix}_${r.id.slice(0, 4)}…${r.hash.slice(-2)}`
''', "keys.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { KeyStore } from './keys.ts'

test('create -> verify round trip records last use', () => {
  const s = new KeyStore(), { key } = s.create('u1', 100); assert.match(key, /^sk_live_[0-9a-f]{12}_[A-Za-z0-9_-]{43}$/)
  const rec = s.verify(key, 500); assert.equal(rec?.owner, 'u1'); assert.equal(rec?.lastUsedAt, 500)
})

test('the secret is never stored: no field of the record contains it', () => {
  const s = new KeyStore(), { key, record } = s.create('u1', 1), secret = key.split('_').at(-1)!
  assert.ok(!JSON.stringify([record, s.list('u1'), s.verify(key, 2)]).includes(secret)); assert.match(record.hash, /^[0-9a-f]{64}$/)
})

test('wrong secret, wrong prefix, unknown id, garbage: all null', () => {
  const s = new KeyStore(), { key } = s.create('u1', 1), parts = key.split('_')
  const tampered = [`${parts[0]}_${parts[1]}_${parts[2]}_${'A'.repeat(43)}`, key.replace('sk_live', 'sk_test'), `sk_live_${'0'.repeat(12)}_${parts[3]}`, '', 'sk_live_x', key + 'x', ' ' + key]
  for (const t of tampered) assert.equal(s.verify(t, 2), null, t)
})

test('revocation is immediate, owner-only and idempotent', () => {
  const s = new KeyStore(), { key, record } = s.create('u1', 1)
  assert.equal(s.revoke(record.id, 'intruder', 5), false); assert.ok(s.verify(key, 6)); assert.equal(s.revoke(record.id, 'u1', 7), true); assert.equal(s.verify(key, 8), null); assert.equal(s.revoke(record.id, 'u1', 9), false)
})

test('list shows masked keys only, per owner', () => {
  const s = new KeyStore(); const a = s.create('u1', 1), b = s.create('u2', 2); s.revoke(a.record.id, 'u1', 3)
  const l = s.list('u1'); assert.equal(l.length, 1); assert.match(l[0].masked, /^sk_live_[0-9a-f]{4}…[0-9a-f]{2}$/); assert.equal(l[0].revoked, true); assert.notEqual(s.list('u2')[0].id, l[0].id); assert.ok(b.key)
})

test('keys are unique', () => {
  const s = new KeyStore(); assert.equal(new Set(Array.from({ length: 200 }, () => s.create('u', 0).key)).size, 200)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "SHA-256 is appropriate only because secrets are 256-bit random; user-chosen passwords need argon2/scrypt", "Add per-key scopes and rate limits in the record; webhooks signing secrets follow the same pattern"], compat={"node": ">=22.18"},
   tags=["api-key", "api-keys", "authentication", "hash", "saas", "security", "tokens", "developer-platform", "revoke", "secrets"],
   avoid="Storing keys in plaintext or reversibly; returning the key again from a GET endpoint; comparing hashes with ===; keys without an id prefix (forces a table scan)."),

RC("ts-event-batcher-analytics", "Analytics/event batcher: size + time flush, dedupe, bounded queue, retry with backoff, flush on exit", "typescript",
   "Sending one HTTP request per click melts the server and the user's battery; naive batching loses events on failure or page close.",
   "`track()` queues events (deduped by id), flushes when `maxBatch` is reached or `flushMs` after the first queued event, sends one request per batch, re-queues the batch on failure with exponential backoff up to N tries (then counts it as dropped), caps queue length dropping the oldest, and `flush()` for shutdown / `pagehide` (inject sendBeacon-style transport).",
   {"batcher.ts": r'''export interface Timers { set(fn: () => void, ms: number): unknown; clear(h: unknown): void }
export interface Ev { id: string; name: string; ts: number; props?: Record<string, unknown> }
export interface Options { send: (batch: Ev[]) => Promise<void>; maxBatch?: number; flushMs?: number; maxQueue?: number; maxTries?: number; baseDelayMs?: number; timers?: Timers }

const real: Timers = { set: (f, ms) => setTimeout(f, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) }

export class Batcher {
  queue: Ev[] = []
  stats = { sent: 0, dropped: 0, failures: 0 }
  private ids = new Set<string>()
  private timer: unknown = undefined
  private inflight: Promise<void> | undefined
  private tries = 0
  private o: Required<Options>

  constructor(opts: Options) {
    this.o = { maxBatch: 20, flushMs: 5000, maxQueue: 1000, maxTries: 4, baseDelayMs: 1000, timers: real, ...opts }
  }

  track(e: Ev): void {
    if (this.ids.has(e.id)) return                                   // double-fired handler / retried call
    this.ids.add(e.id); this.queue.push(e)
    while (this.queue.length > this.o.maxQueue) { const old = this.queue.shift()!; this.ids.delete(old.id); this.stats.dropped++ }
    if (this.queue.length >= this.o.maxBatch) void this.flush(); else this.arm(this.o.flushMs)
  }

  private arm(ms: number) {
    if (this.timer !== undefined) return
    this.timer = this.o.timers.set(() => { this.timer = undefined; void this.flush() }, ms)
  }

  /** Send everything queued (in maxBatch-sized requests). Safe to call concurrently: calls share the in-flight send. */
  async flush(): Promise<void> {
    if (this.timer !== undefined) { this.o.timers.clear(this.timer); this.timer = undefined }
    if (this.inflight) return this.inflight
    this.inflight = (async () => {
      try {
        while (this.queue.length) {
          const batch = this.queue.slice(0, this.o.maxBatch)
          try { await this.o.send(batch) } catch {
            this.stats.failures++
            if (++this.tries >= this.o.maxTries) { this.queue.splice(0, batch.length); batch.forEach((e) => this.ids.delete(e.id)); this.stats.dropped += batch.length; this.tries = 0; continue }
            this.arm(this.o.baseDelayMs * 2 ** (this.tries - 1)); return                       // keep the batch queued; try again later
          }
          this.tries = 0; this.queue.splice(0, batch.length); batch.forEach((e) => this.ids.delete(e.id)); this.stats.sent += batch.length
        }
      } finally { this.inflight = undefined }
    })()
    return this.inflight
  }
}
''', "batcher.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Batcher, type Ev, type Timers } from './batcher.ts'

function rig() {
  let now = 0, id = 0; const q = new Map<number, { at: number; fn: () => void }>()
  const timers: Timers = { set: (fn, ms) => { q.set(++id, { at: now + ms, fn }); return id }, clear: (h) => { q.delete(h as number) } }
  const advance = async (ms: number) => { const end = now + ms; for (;;) { const d = [...q.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!d) break; now = d[1].at; q.delete(d[0]); d[1].fn(); await new Promise((r) => setImmediate(r)) } now = end }
  return { timers, advance, pending: () => q.size }
}
const ev = (n: number): Ev => ({ id: 'e' + n, name: 'click', ts: n })
const settle = () => new Promise((r) => setImmediate(r))

test('flushes as soon as the batch is full and drains the rest in order', async () => {
  const r = rig(), sent: Ev[][] = []; const b = new Batcher({ send: async (x) => { sent.push(x) }, maxBatch: 3, timers: r.timers })
  for (let i = 0; i < 7; i++) b.track(ev(i)); await settle(); assert.deepEqual(sent.map((x) => x.length), [3, 3, 1]); assert.equal(b.queue.length, 0)      // a flush drains everything queued, in maxBatch-sized requests
})

test('flushes after flushMs even if the batch is not full, with one timer for many events', async () => {
  const r = rig(), sent: Ev[][] = []; const b = new Batcher({ send: async (x) => { sent.push(x) }, maxBatch: 10, flushMs: 1000, timers: r.timers })
  b.track(ev(1)); b.track(ev(2)); assert.equal(r.pending(), 1); await r.advance(999); assert.equal(sent.length, 0); await r.advance(1); assert.deepEqual(sent.map((x) => x.map((e) => e.id)), [['e1', 'e2']])
})

test('duplicate ids are ignored', () => {
  const b = new Batcher({ send: async () => {}, timers: rig().timers }); b.track(ev(1)); b.track(ev(1)); assert.equal(b.queue.length, 1)
})

test('failure keeps events and retries with exponential backoff, then succeeds', async () => {
  const r = rig(); let fail = 2; const sent: Ev[][] = []
  const b = new Batcher({ send: async (x) => { if (fail-- > 0) throw new Error('503'); sent.push(x) }, maxBatch: 2, baseDelayMs: 1000, timers: r.timers })
  b.track(ev(1)); b.track(ev(2)); await settle(); assert.equal(b.stats.failures, 1); assert.equal(b.queue.length, 2)
  await r.advance(1000); assert.equal(b.stats.failures, 2); await r.advance(1999); assert.equal(sent.length, 0); await r.advance(1); assert.equal(sent.length, 1); assert.deepEqual([b.stats.sent, b.queue.length], [2, 0])
})

test('gives up after maxTries and counts the drop instead of retrying forever', async () => {
  const r = rig(); const b = new Batcher({ send: async () => { throw new Error('down') }, maxBatch: 1, maxTries: 3, baseDelayMs: 10, timers: r.timers })
  b.track(ev(1)); await settle(); await r.advance(1000); assert.deepEqual([b.stats.dropped, b.queue.length, b.stats.failures], [1, 0, 3]); b.track(ev(1)); assert.equal(b.queue.length, 1)
})

test('queue is bounded: oldest events are dropped', () => {
  const b = new Batcher({ send: async () => new Promise<void>(() => {}), maxBatch: 1000, maxQueue: 5, timers: rig().timers }); for (let i = 0; i < 9; i++) b.track(ev(i))
  assert.deepEqual(b.queue.map((e) => e.id), ['e4', 'e5', 'e6', 'e7', 'e8']); assert.equal(b.stats.dropped, 4)
})

test('flush() on shutdown sends the remainder and concurrent flushes share one send', async () => {
  const r = rig(); let calls = 0; const b = new Batcher({ send: async () => { calls++; await settle() }, maxBatch: 10, timers: r.timers }); b.track(ev(1)); b.track(ev(2))
  await Promise.all([b.flush(), b.flush(), b.flush()]); assert.equal(calls, 1); assert.deepEqual([b.queue.length, b.stats.sent, r.pending()], [0, 2, 0])
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "In browsers use `navigator.sendBeacon` (or fetch keepalive) as `send` inside a pagehide/visibilitychange handler, because async work after unload is not guaranteed"], compat={"node": ">=22.18"},
   tags=["analytics", "events", "batching", "telemetry", "tracking", "retry", "backoff", "queue", "saas", "sendbeacon", "frontend"],
   avoid="One request per event; unbounded in-memory queues on offline clients; retrying forever; relying on `beforeunload` + async fetch."),

RC("ts-pricing-table-billing-toggle", "Pricing page logic: monthly/yearly toggle, savings %, per-seat totals, recommended plan, currency formatting (integer cents)", "typescript",
   "Every landing page has a three-tier pricing table with a monthly/yearly switch; the numbers and the 'save 20%' badge must be right and consistent.",
   "Plans defined once in integer cents (yearly price is stored, not derived by guesswork), `displayPrice(plan, interval)` returns the per-month equivalent for yearly, `savingsPercent` rounded honestly, per-seat totals with included seats, Intl formatting with zero decimals only for whole amounts, recommended-plan selection from a seat/usage estimate, and a tiny toggle state machine that is keyboard friendly (radio semantics).",
   {"pricing.ts": r'''export type Interval = 'month' | 'year'
export interface Plan { id: string; name: string; monthlyCents: number; yearlyCents: number; includedSeats: number; extraSeatMonthlyCents: number; extraSeatYearlyCents: number; maxSeats: number }

export const PLANS: Plan[] = [
  { id: 'starter', name: 'Starter', monthlyCents: 900, yearlyCents: 8640, includedSeats: 1, extraSeatMonthlyCents: 0, extraSeatYearlyCents: 0, maxSeats: 1 },
  { id: 'team', name: 'Team', monthlyCents: 2900, yearlyCents: 27840, includedSeats: 5, extraSeatMonthlyCents: 500, extraSeatYearlyCents: 4800, maxSeats: 50 },
  { id: 'business', name: 'Business', monthlyCents: 9900, yearlyCents: 95040, includedSeats: 20, extraSeatMonthlyCents: 400, extraSeatYearlyCents: 3840, maxSeats: 500 },
]

export function format(cents: number, currency = 'USD', locale = 'en-US'): string {
  const whole = cents % 100 === 0
  return new Intl.NumberFormat(locale, { style: 'currency', currency, minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: whole ? 0 : 2 }).format(cents / 100)
}

/** What to print big on the card: yearly plans show the per-month equivalent. */
export function displayPrice(p: Plan, interval: Interval): { cents: number; label: string } {
  const cents = interval === 'month' ? p.monthlyCents : Math.round(p.yearlyCents / 12)
  return { cents, label: `${format(cents)}/mo` }
}

export function savingsPercent(p: Plan): number {
  const full = p.monthlyCents * 12
  return full <= 0 ? 0 : Math.floor(((full - p.yearlyCents) / full) * 100)           // floor: never advertise more than the customer really saves
}

export function total(p: Plan, interval: Interval, seats: number): { cents: number; extraSeats: number } {
  if (!Number.isInteger(seats) || seats < 1 || seats > p.maxSeats) throw new RangeError(`${p.name} supports 1-${p.maxSeats} seats`)
  const extra = Math.max(0, seats - p.includedSeats)
  const base = interval === 'month' ? p.monthlyCents : p.yearlyCents, per = interval === 'month' ? p.extraSeatMonthlyCents : p.extraSeatYearlyCents
  return { cents: base + extra * per, extraSeats: extra }
}

/** Cheapest plan that fits the team; if several are within 20% of the cheapest, prefer the one with more headroom. */
export function recommend(seats: number, interval: Interval = 'month', plans: Plan[] = PLANS): Plan | undefined {
  const fits = plans.filter((p) => seats <= p.maxSeats).map((p) => ({ p, c: total(p, interval, seats).cents })).sort((a, b) => a.c - b.c)
  if (!fits.length) return undefined
  const near = fits.filter((x) => x.c <= fits[0].c * 1.2)
  return near.sort((a, b) => b.p.maxSeats - a.p.maxSeats)[0].p
}

/** Radio-group toggle: arrow keys switch, Home/End jump, anything else is ignored. */
export function nextInterval(cur: Interval, key: string): Interval {
  if (key === 'ArrowLeft' || key === 'ArrowUp' || key === 'Home') return 'month'
  if (key === 'ArrowRight' || key === 'ArrowDown' || key === 'End') return 'year'
  return cur
}
''', "pricing.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PLANS, format, displayPrice, savingsPercent, total, recommend, nextInterval } from './pricing.ts'

const [starter, team, business] = PLANS

test('formatting hides .00 but never drops real cents', () => {
  assert.deepEqual([format(900), format(950), format(123456), format(5, 'EUR', 'de-DE')], ['$9', '$9.50', '$1,234.56', '0,05 €'])
})

test('yearly shows the per-month equivalent', () => {
  assert.equal(displayPrice(team, 'month').label, '$29/mo'); assert.equal(displayPrice(team, 'year').label, '$23.20/mo'); assert.equal(displayPrice(starter, 'year').label, '$7.20/mo')
})

test('savings badge matches the stored prices and is never overstated', () => {
  assert.deepEqual(PLANS.map(savingsPercent), [20, 20, 20]); assert.equal(savingsPercent({ ...team, yearlyCents: 28000 }), 19)
  for (const p of PLANS) assert.ok(p.yearlyCents / 12 <= p.monthlyCents)
})

test('seat totals include the free seats and bill extras', () => {
  assert.deepEqual(total(team, 'month', 5), { cents: 2900, extraSeats: 0 }); assert.deepEqual(total(team, 'month', 8), { cents: 2900 + 3 * 500, extraSeats: 3 })
  assert.equal(total(team, 'year', 6).cents, 27840 + 4800)
})

test('invalid seat counts are rejected loudly', () => {
  for (const s of [0, -1, 1.5, 51, NaN]) assert.throws(() => total(team, 'month', s), RangeError); assert.throws(() => total(starter, 'month', 2), /supports 1-1 seats/)
})

test('recommendation follows team size', () => {
  assert.equal(recommend(1)?.id, 'starter')           // cheapest, and no other plan is within 20% of its price
  assert.equal(recommend(10)?.id, 'team'); assert.equal(recommend(40)?.id, 'business'); assert.equal(recommend(1000), undefined)
})

test('toggle keys', () => {
  assert.deepEqual(['ArrowRight', 'End', 'ArrowLeft', 'Home', 'a'].map((k) => nextInterval('month', k)), ['year', 'year', 'month', 'month', 'month'])
  assert.equal(nextInterval('year', 'Tab'), 'year')
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Tax/VAT display, currency conversion and proration belong server-side (Stripe prices); this is presentation logic", "Intl output (spaces, symbols) depends on the Node ICU build"], compat={"node": ">=22.18"},
   tags=["pricing", "pricing-table", "billing", "toggle", "monthly-yearly", "saas", "landing-page", "currency", "intl", "subscription", "plans"],
   avoid="Floats for money; computing the yearly 'discount' differently in the badge and at checkout; showing the yearly total as if it were monthly."),

RC("ts-lightbox-gallery-logic", "Image gallery lightbox logic: wrap navigation, keyboard map, swipe detection, neighbour preloading, scroll lock", "typescript",
   "A freelancer's portfolio gallery needs a lightbox that behaves: arrows and swipe work, it preloads the next image, ESC closes, and the page behind doesn't scroll.",
   "Pure reducer for `{open, index}` with wrap or clamp, a key->action map, `swipeDirection()` with distance and angle thresholds (ignore mostly-vertical gestures so scrolling still works), `preloadSet(index, count)` for neighbours, and focus/scroll-lock flags the view applies; accessibility notes (dialog role, focus return) in the assumptions.",
   {"lightbox.ts": r'''export interface State { open: boolean; index: number; count: number; wrap: boolean; trigger?: number }
export type Action = { type: 'open'; index: number } | { type: 'close' } | { type: 'next' } | { type: 'prev' } | { type: 'goto'; index: number } | { type: 'first' } | { type: 'last' }

export const init = (count: number, wrap = true): State => ({ open: false, index: 0, count, wrap })

export function reduce(s: State, a: Action): State {
  if (s.count === 0) return s
  const last = s.count - 1, clamp = (i: number) => Math.min(last, Math.max(0, i))
  switch (a.type) {
    case 'open': return { ...s, open: true, index: clamp(a.index), trigger: clamp(a.index) }
    case 'close': return { ...s, open: false }
    case 'next': return !s.open ? s : { ...s, index: s.wrap ? (s.index + 1) % s.count : clamp(s.index + 1) }
    case 'prev': return !s.open ? s : { ...s, index: s.wrap ? (s.index - 1 + s.count) % s.count : clamp(s.index - 1) }
    case 'goto': return s.open ? { ...s, index: clamp(a.index) } : s
    case 'first': return s.open ? { ...s, index: 0 } : s
    case 'last': return s.open ? { ...s, index: last } : s
  }
}

export function keyToAction(key: string): Action | undefined {
  return ({ ArrowRight: { type: 'next' }, ArrowLeft: { type: 'prev' }, Escape: { type: 'close' }, Home: { type: 'first' }, End: { type: 'last' } } as Record<string, Action>)[key]
}

/** Horizontal swipe = navigate; mostly-vertical drags are scrolling and must be left alone. */
export function swipeDirection(dx: number, dy: number, dtMs: number, minDist = 40, maxMs = 800): 'next' | 'prev' | null {
  if (dtMs > maxMs || Math.abs(dx) < minDist || Math.abs(dx) < Math.abs(dy) * 1.5) return null
  return dx < 0 ? 'next' : 'prev'
}

/** Indices worth fetching ahead of time (current first, then neighbours). */
export function preloadSet(index: number, count: number, wrap = true, radius = 1): number[] {
  const out = [index]
  for (let d = 1; d <= radius; d++) for (const i of [index + d, index - d]) {
    const j = wrap ? ((i % count) + count) % count : i
    if (j >= 0 && j < count && !out.includes(j)) out.push(j)
  }
  return out
}

/** What the view must apply for the current state (body scroll lock, aria-hidden on the page, where focus goes). */
export function sideEffects(prev: State, next: State) {
  return { lockScroll: next.open, inertPage: next.open, focus: !prev.open && next.open ? 'dialog' as const : prev.open && !next.open ? 'trigger' as const : null }
}
''', "lightbox.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { init, reduce, keyToAction, swipeDirection, preloadSet, sideEffects } from './lightbox.ts'

test('open, navigate with wrap-around, close', () => {
  let s = reduce(init(3), { type: 'open', index: 2 }); assert.deepEqual([s.open, s.index], [true, 2])
  s = reduce(s, { type: 'next' }); assert.equal(s.index, 0); s = reduce(s, { type: 'prev' }); assert.equal(s.index, 2); s = reduce(s, { type: 'close' }); assert.equal(s.open, false)
})

test('clamp mode stops at the ends', () => {
  let s = reduce(init(3, false), { type: 'open', index: 0 }); s = reduce(s, { type: 'prev' }); assert.equal(s.index, 0)
  s = reduce(reduce(reduce(s, { type: 'next' }), { type: 'next' }), { type: 'next' }); assert.equal(s.index, 2)
})

test('closed lightbox ignores navigation; bad indices are clamped; empty gallery is inert', () => {
  const c = init(3); assert.equal(reduce(c, { type: 'next' }), c); assert.equal(reduce(c, { type: 'open', index: 99 }).index, 2); assert.equal(reduce(c, { type: 'open', index: -5 }).index, 0)
  const e = init(0); assert.equal(reduce(e, { type: 'open', index: 0 }), e)
})

test('keyboard map and home/end', () => {
  assert.deepEqual(['ArrowRight', 'ArrowLeft', 'Escape', 'Home', 'End', 'x'].map((k) => keyToAction(k)?.type), ['next', 'prev', 'close', 'first', 'last', undefined])
  let s = reduce(init(5), { type: 'open', index: 2 }); assert.equal(reduce(s, { type: 'last' }).index, 4); assert.equal(reduce(s, { type: 'first' }).index, 0)
})

test('swipes: horizontal navigates, vertical scroll and slow drags do not', () => {
  assert.equal(swipeDirection(-80, 5, 200), 'next'); assert.equal(swipeDirection(80, -10, 200), 'prev')
  assert.equal(swipeDirection(-80, 70, 200), null); assert.equal(swipeDirection(-20, 0, 100), null); assert.equal(swipeDirection(-200, 0, 2000), null)
})

test('preload neighbours, wrapping, without duplicates', () => {
  assert.deepEqual(preloadSet(0, 5), [0, 1, 4]); assert.deepEqual(preloadSet(4, 5), [4, 0, 3]); assert.deepEqual(preloadSet(0, 5, false), [0, 1]); assert.deepEqual(preloadSet(0, 2), [0, 1]); assert.deepEqual(preloadSet(0, 1), [0])
  assert.deepEqual(preloadSet(2, 9, true, 2), [2, 3, 1, 4, 0])
})

test('side effects: scroll lock while open, focus moves in and back to the trigger', () => {
  const closed = init(3), open = reduce(closed, { type: 'open', index: 1 })
  assert.deepEqual(sideEffects(closed, open), { lockScroll: true, inertPage: true, focus: 'dialog' }); assert.deepEqual(sideEffects(open, reduce(open, { type: 'next' })), { lockScroll: true, inertPage: true, focus: null })
  assert.deepEqual(sideEffects(open, reduce(open, { type: 'close' })), { lockScroll: false, inertPage: false, focus: 'trigger' })
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Markup: use <dialog> (showModal) or role=dialog aria-modal, label it, trap focus, return focus to the thumbnail, and give every image real alt text"], compat={"node": ">=22.18"},
   tags=["lightbox", "gallery", "image-gallery", "carousel", "swipe", "keyboard", "portfolio", "accessibility", "modal", "website", "ui"],
   avoid="Swipe handlers that hijack vertical scrolling; no keyboard support; loading all full-size images up front; body keeps scrolling behind the overlay."),

RC("ts-scrollspy-toc", "Docs table of contents: build nested TOC from headings and scrollspy active-section logic", "typescript",
   "A docs page needs a sidebar TOC that highlights the section being read and handles the top, the bottom and short last sections.",
   "`buildToc(headings)` nests h2/h3 under their parents (tolerating skipped levels and a leading h3), `slugify` with duplicate suffixes, and `activeId(offsets, scrollY, viewport, docHeight, topMargin)` choosing the last heading above the reading line, with an end-of-page override so short final sections can still be activated; pure so any scroll/IntersectionObserver wiring can use it.",
   {"toc.ts": r'''export interface Heading { level: number; text: string; id?: string }
export interface TocNode { id: string; text: string; level: number; children: TocNode[] }

export function slugify(text: string, taken: Set<string>): string {
  const base = text.toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'section'
  let id = base, n = 1
  while (taken.has(id)) id = `${base}-${++n}`
  taken.add(id); return id
}

export function buildToc(headings: Heading[], minLevel = 2, maxLevel = 3): TocNode[] {
  const taken = new Set<string>(), root: TocNode[] = [], stack: TocNode[] = []
  for (const h of headings) {
    if (h.level < minLevel || h.level > maxLevel) continue
    const node: TocNode = { id: h.id ?? slugify(h.text, taken), text: h.text, level: h.level, children: [] }
    if (h.id) taken.add(h.id)
    while (stack.length && stack[stack.length - 1].level >= h.level) stack.pop()
    ;(stack.length ? stack[stack.length - 1].children : root).push(node)
    stack.push(node)
  }
  return root
}

export interface Offset { id: string; top: number }          // absolute document offset of each heading, ascending

/** The section being read: last heading whose top is above the reading line; near the page bottom, the final heading wins. */
export function activeId(offsets: Offset[], scrollY: number, viewport: number, docHeight: number, topMargin = 80): string | null {
  if (!offsets.length) return null
  if (scrollY + viewport >= docHeight - 2) return offsets[offsets.length - 1].id
  const line = scrollY + topMargin
  let current: string | null = null
  for (const o of offsets) { if (o.top <= line) current = o.id; else break }
  return current ?? offsets[0].id
}
''', "toc.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { slugify, buildToc, activeId } from './toc.ts'

test('slugs: accents, punctuation, duplicates', () => {
  const t = new Set<string>(); assert.deepEqual(['Café & Crème!', 'Install', 'Install', '日本語', 'Install'].map((x) => slugify(x, t)), ['cafe-creme', 'install', 'install-2', 'section', 'install-3'])
})

test('h3s nest under the preceding h2; h1/h4 are ignored', () => {
  const toc = buildToc([{ level: 1, text: 'Title' }, { level: 2, text: 'A' }, { level: 3, text: 'A1' }, { level: 3, text: 'A2' }, { level: 4, text: 'deep' }, { level: 2, text: 'B' }, { level: 3, text: 'B1' }])
  assert.deepEqual(toc.map((n) => [n.id, n.children.map((c) => c.id)]), [['a', ['a1', 'a2']], ['b', ['b1']]])
})

test('leading h3 and repeated names still produce a sane tree with unique ids', () => {
  const toc = buildToc([{ level: 3, text: 'Orphan' }, { level: 2, text: 'Usage' }, { level: 3, text: 'Options' }, { level: 2, text: 'Usage' }, { level: 3, text: 'Options' }])
  assert.deepEqual(toc.map((n) => n.id), ['orphan', 'usage', 'usage-2']); assert.deepEqual(toc[2].children.map((c) => c.id), ['options-2'])
})

test('explicit ids from the markup are respected', () => {
  assert.equal(buildToc([{ level: 2, text: 'Hello', id: 'custom' }])[0].id, 'custom')
})

const O = [{ id: 'intro', top: 0 }, { id: 'install', top: 600 }, { id: 'usage', top: 1400 }, { id: 'faq', top: 2000 }]

test('active section follows the reading line', () => {
  const at = (y: number) => activeId(O, y, 800, 3000)
  assert.deepEqual([0, 400, 520, 1319, 1320, 1900].map(at), ['intro', 'intro', 'install', 'install', 'usage', 'usage'])
})

test('above the first heading, the first one is active', () => {
  assert.equal(activeId([{ id: 'a', top: 300 }], 0, 800, 3000), 'a')
})

test('bottom of page activates the last section even if its top never crosses the line', () => {
  assert.equal(activeId(O, 2200, 800, 3000), 'faq'); assert.equal(activeId([...O, { id: 'tiny', top: 2900 }], 2200, 800, 3000), 'tiny')
})

test('no headings -> null', () => {
  assert.equal(activeId([], 100, 800, 3000), null)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "In the browser feed offsets from getBoundingClientRect()+scrollY (recompute on resize/font load) or use IntersectionObserver with rootMargin"], compat={"node": ">=22.18"},
   tags=["scrollspy", "table-of-contents", "toc", "docs", "documentation", "headings", "navigation", "slug", "sidebar", "website"],
   avoid="Highlighting by comparing against 0 (off by the sticky header); duplicate heading ids; last section never highlighted because the page can't scroll further."),

RC("ts-cookie-consent-state", "Cookie/consent banner state: categories, versioned consent, expiry, opt-in default, safe storage", "typescript",
   "A site with analytics or marketing scripts must ask first, remember the answer, re-ask when the policy changes, and not load scripts before consent.",
   "Consent record `{version, decidedAt, categories}` with `necessary` always true and everything else default-off, accept-all / reject-all / custom with equal prominence in the API, `needsPrompt()` when missing, malformed, from an older policy version or older than N days, `allowed(category)` gate used before injecting a script, and storage reads/writes wrapped in try/catch (blocked storage = ask every time).",
   {"consent.ts": r'''export type Category = 'necessary' | 'preferences' | 'analytics' | 'marketing'
export const CATEGORIES: Category[] = ['necessary', 'preferences', 'analytics', 'marketing']
export interface Consent { version: number; decidedAt: number; categories: Record<Category, boolean> }
export interface Storage { getItem(k: string): string | null; setItem(k: string, v: string): void }

const KEY = 'consent'
const off = (): Record<Category, boolean> => ({ necessary: true, preferences: false, analytics: false, marketing: false })

export function decide(version: number, now: number, choice: 'all' | 'none' | Partial<Record<Category, boolean>>): Consent {
  const c = off()
  if (choice === 'all') for (const k of CATEGORIES) c[k] = true
  else if (choice !== 'none') for (const k of CATEGORIES) if (k !== 'necessary' && choice[k] === true) c[k] = true
  return { version, decidedAt: now, categories: c }                  // necessary can never be switched off, nothing else defaults on
}

export function load(store: Storage | undefined): Consent | null {
  try {
    const raw = store?.getItem(KEY); if (!raw) return null
    const v = JSON.parse(raw)
    if (typeof v?.version !== 'number' || typeof v?.decidedAt !== 'number' || typeof v?.categories !== 'object' || v.categories === null) return null
    const c = off(); for (const k of CATEGORIES) if (k !== 'necessary' && v.categories[k] === true) c[k] = true
    return { version: v.version, decidedAt: v.decidedAt, categories: c }
  } catch { return null }
}

export function save(store: Storage | undefined, c: Consent): boolean {
  try { store!.setItem(KEY, JSON.stringify(c)); return true } catch { return false }
}

const DAY = 86_400_000
export function needsPrompt(c: Consent | null, currentVersion: number, now: number, maxAgeDays = 180): boolean {
  return !c || c.version < currentVersion || now - c.decidedAt > maxAgeDays * DAY || now < c.decidedAt
}

/** Gate every non-essential script/pixel through this before injecting it. */
export const allowed = (c: Consent | null, cat: Category): boolean => cat === 'necessary' || c?.categories[cat] === true
''', "consent.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { decide, load, save, needsPrompt, allowed, type Storage } from './consent.ts'

const mem = () => { const m = new Map<string, string>(); return { m, getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => { m.set(k, v) } } as Storage & { m: Map<string, string> } }
const DAY = 86_400_000

test('nothing optional is allowed before a decision', () => {
  assert.deepEqual(['necessary', 'preferences', 'analytics', 'marketing'].map((c) => allowed(null, c as never)), [true, false, false, false])
})

test('accept all / reject all / custom', () => {
  const all = decide(1, 0, 'all'), none = decide(1, 0, 'none'), custom = decide(1, 0, { analytics: true, necessary: false })
  assert.deepEqual(Object.values(all.categories), [true, true, true, true]); assert.deepEqual(Object.values(none.categories), [true, false, false, false])
  assert.deepEqual(custom.categories, { necessary: true, preferences: false, analytics: true, marketing: false })
  assert.equal(allowed(custom, 'analytics'), true); assert.equal(allowed(custom, 'marketing'), false)
})

test('round trip through storage', () => {
  const s = mem(), c = decide(2, 1000, { marketing: true }); assert.equal(save(s, c), true); assert.deepEqual(load(s), c)
})

test('garbage in storage means "ask again", never "allow"', () => {
  for (const raw of ['', '{', 'null', '[]', '{"version":"1"}', JSON.stringify({ version: 1, decidedAt: 1, categories: null })]) { const s = mem(); s.m.set('consent', raw); assert.equal(load(s), null, raw) }
  const s = mem(); s.m.set('consent', JSON.stringify({ version: 1, decidedAt: 1, categories: { necessary: false, analytics: 'yes', marketing: true } }))
  assert.deepEqual(load(s)!.categories, { necessary: true, preferences: false, analytics: false, marketing: true })
})

test('blocked storage: reads null, writes report failure, banner keeps showing', () => {
  const broken = { getItem() { throw new Error('denied') }, setItem() { throw new Error('denied') } } as Storage
  assert.equal(load(broken), null); assert.equal(save(broken, decide(1, 0, 'all')), false); assert.equal(load(undefined), null); assert.equal(needsPrompt(load(broken), 1, 0), true)
})

test('prompt again when missing, policy version bumped, too old, or the clock is nonsense', () => {
  const c = decide(1, 1000, 'all')
  assert.deepEqual([needsPrompt(null, 1, 0), needsPrompt(c, 1, 1000 + 10 * DAY), needsPrompt(c, 2, 1000), needsPrompt(c, 1, 1000 + 181 * DAY), needsPrompt(c, 1, 500)], [true, false, true, true, true])
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Legal requirements vary (GDPR/ePrivacy, CCPA, etc.); this is the state logic, not legal advice. Log consent server-side if you must prove it", "Reject must be as easy as accept; do not pre-tick boxes"], compat={"node": ">=22.18"},
   tags=["cookie-consent", "gdpr", "consent", "privacy", "banner", "analytics", "cookies", "website", "compliance", "opt-in"],
   avoid="Loading analytics before the user decides; a pre-ticked 'accept' with a hidden reject; treating unreadable storage as consent."),
]
