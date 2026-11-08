"""Round-3 recipes, part B (TypeScript, node --test, erasable syntax only): scheduling/calendar, realtime messaging, accessibility.
Pure logic with injected clocks and sockets; no browser, network or calendar service is involved."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

LOGIC = "logic only: no real browser, socket or calendar server is exercised"

R3B = [
RC("ts-availability-slots-booking", "Scheduling: merge busy intervals, find free slots, prevent double booking, count business days with holidays", "typescript",
   "A booking page or meeting finder must compute free time across calendars correctly and never allow two bookings to overlap.",
   "Half-open intervals [start, end) as epoch ms, `mergeIntervals`, `freeSlots(window, busy, duration, step)` that respects working hours, overlap test used both for display and as the final guard on `book()`, and `businessDays(from, to, holidays)` in UTC dates; all pure so the same code runs in the browser and the API.",
   {"slots.ts": r'''export interface Interval { start: number; end: number }       // half-open [start, end), epoch ms

export const overlaps = (a: Interval, b: Interval) => a.start < b.end && b.start < a.end

export function mergeIntervals(list: Interval[]): Interval[] {
  const s = list.filter((i) => i.end > i.start).map((i) => ({ ...i })).sort((a, b) => a.start - b.start), out: Interval[] = []
  for (const i of s) {
    const last = out[out.length - 1]
    if (last && i.start <= last.end) last.end = Math.max(last.end, i.end)           // touching intervals merge: 9-10 and 10-11 is one busy block
    else out.push(i)
  }
  return out
}

/** Start times (every `step` ms) in `window` where `duration` fits between busy blocks. */
export function freeSlots(window: Interval, busy: Interval[], duration: number, step = duration): Interval[] {
  if (duration <= 0 || step <= 0) throw new RangeError('duration and step must be positive')
  const merged = mergeIntervals(busy), out: Interval[] = []
  for (let t = window.start; t + duration <= window.end; t += step) {
    const slot = { start: t, end: t + duration }
    if (!merged.some((b) => overlaps(slot, b))) out.push(slot)
  }
  return out
}

/** Free time as ranges rather than slots (for "show me when I'm free"). */
export function gaps(window: Interval, busy: Interval[]): Interval[] {
  const out: Interval[] = []; let cursor = window.start
  for (const b of mergeIntervals(busy)) {
    if (b.end <= window.start || b.start >= window.end) continue
    if (b.start > cursor) out.push({ start: cursor, end: Math.min(b.start, window.end) })
    cursor = Math.max(cursor, b.end)
  }
  if (cursor < window.end) out.push({ start: cursor, end: window.end })
  return out
}

export class Calendar {
  private booked: (Interval & { id: string })[] = []
  /** The overlap check lives here, next to the write: showing a slot as free is not a reservation. */
  book(id: string, slot: Interval): boolean {
    if (slot.end <= slot.start || this.booked.some((b) => overlaps(b, slot))) return false
    this.booked.push({ id, ...slot }); return true
  }
  cancel(id: string) { this.booked = this.booked.filter((b) => b.id !== id) }
  get busy(): Interval[] { return this.booked.map(({ start, end }) => ({ start, end })) }
}

/** Weekdays in [from, to] inclusive, UTC calendar dates, minus holidays given as 'YYYY-MM-DD'. */
export function businessDays(from: string, to: string, holidays: string[] = []): number {
  const skip = new Set(holidays); let n = 0
  for (let d = Date.parse(from + 'T00:00:00Z'); d <= Date.parse(to + 'T00:00:00Z'); d += 86_400_000) {
    const dow = new Date(d).getUTCDay(), iso = new Date(d).toISOString().slice(0, 10)
    if (dow !== 0 && dow !== 6 && !skip.has(iso)) n++
  }
  return n
}
''', "slots.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mergeIntervals, freeSlots, gaps, Calendar, overlaps, businessDays } from './slots.ts'

const H = 3_600_000, at = (h: number) => h * H, iv = (a: number, b: number) => ({ start: at(a), end: at(b) })

test('overlap is half-open: back-to-back meetings do not collide', () => {
  assert.equal(overlaps(iv(9, 10), iv(10, 11)), false); assert.equal(overlaps(iv(9, 10), iv(9.5, 10.5)), true); assert.equal(overlaps(iv(9, 12), iv(10, 11)), true)
})

test('merge: unsorted, nested, touching, empty and does not mutate input', () => {
  const input = [iv(13, 14), iv(9, 10), iv(10, 11), iv(9.5, 9.75), iv(12, 12)], copy = JSON.stringify(input)
  assert.deepEqual(mergeIntervals(input), [iv(9, 11), iv(13, 14)]); assert.equal(JSON.stringify(input), copy)
})

test('free slots avoid busy blocks and stay inside the window', () => {
  const s = freeSlots(iv(9, 17), [iv(10, 11), iv(13, 14.5)], H)
  assert.deepEqual(s.map((x) => x.start / H), [9, 11, 12, 15, 16]); assert.ok(s.every((x) => x.end <= at(17)))
})

test('step smaller than duration offers more start times; a slot that would overrun is not offered', () => {
  const s = freeSlots(iv(9, 11), [], H, H / 2); assert.deepEqual(s.map((x) => x.start / H), [9, 9.5, 10])
  assert.deepEqual(freeSlots(iv(9, 9.5), [], H), []); assert.throws(() => freeSlots(iv(9, 10), [], 0), RangeError)
})

test('gaps = free ranges', () => {
  assert.deepEqual(gaps(iv(9, 17), [iv(8, 10), iv(12, 13), iv(12.5, 14), iv(16, 20)]), [iv(10, 12), iv(14, 16)]); assert.deepEqual(gaps(iv(9, 10), []), [iv(9, 10)]); assert.deepEqual(gaps(iv(9, 10), [iv(0, 24)]), [])
})

test('booking refuses overlaps even if the slot was shown as free earlier (race)', () => {
  const cal = new Calendar(), shown = freeSlots(iv(9, 12), cal.busy, H)[0]
  assert.equal(cal.book('alice', shown), true); assert.equal(cal.book('bob', shown), false); assert.equal(cal.book('bob', iv(9.5, 10.5)), false); assert.equal(cal.book('bob', iv(10, 11)), true)
  cal.cancel('alice'); assert.equal(cal.book('carol', shown), true); assert.equal(cal.book('bad', iv(11, 11)), false)
})

test('business days skip weekends and holidays (inclusive range)', () => {
  assert.equal(businessDays('2026-03-02', '2026-03-06'), 5); assert.equal(businessDays('2026-03-02', '2026-03-08'), 5); assert.equal(businessDays('2026-03-07', '2026-03-08'), 0)
  assert.equal(businessDays('2026-12-21', '2026-12-31', ['2026-12-25', '2026-12-28']), 7); assert.equal(businessDays('2026-03-05', '2026-03-05'), 1); assert.equal(businessDays('2026-03-06', '2026-03-02'), 0)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Time zones and DST are a separate layer: convert wall-clock working hours to UTC intervals per day before calling these (see the ical/timezone recipe)"], compat={"node": ">=22.18"},
   tags=["calendar", "scheduling", "availability", "free-slots", "booking", "double-booking", "overlap", "intervals", "business-days", "holidays", "appointments", "meeting"],
   avoid="Checking availability only in the UI; closed intervals that make 9-10 and 10-11 collide; storing local times without a zone."),

RC("ts-ical-timezone-dst", "iCalendar (.ics) generation with escaping and line folding, and time-zone conversion that survives DST (Intl)", "typescript",
   "Export events to Google/Apple/Outlook calendars and show times in the viewer's zone without being off by an hour twice a year.",
   "`toICS()` with UTC `DTSTART`, `UID`, `DTSTAMP`, text escaping (comma, semicolon, backslash, newline), 75-octet line folding that never splits a UTF-8 character, CRLF endings; `zonedParts()`/`offsetMinutes()` using Intl.DateTimeFormat so DST rules come from the platform tz database, and `wallTimeToUtc()` that resolves a local wall time in a named zone (with the gap/overlap behaviour documented).",
   {"ical.ts": r'''export interface Ev { uid: string; title: string; start: number; end: number; description?: string; location?: string }   // start/end: epoch ms

const esc = (s: string) => s.replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n')
const stamp = (ms: number) => new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '')           // 20260305T140000Z

/** RFC 5545: lines are at most 75 octets; continuation lines start with a single space. Never cut inside a multi-byte character. */
export function fold(line: string): string {
  const enc = new TextEncoder(), out: string[] = []; let cur = '', bytes = 0
  for (const ch of line) {
    const n = enc.encode(ch).length, limit = out.length === 0 ? 75 : 74
    if (bytes + n > limit) { out.push(cur); cur = ''; bytes = 0 }
    cur += ch; bytes += n
  }
  out.push(cur)
  return out.join('\r\n ')
}

export function toICS(events: Ev[], now = Date.now(), prodId = '-//example//app//EN'): string {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', `PRODID:${prodId}`, 'CALSCALE:GREGORIAN']
  for (const e of events) {
    if (e.end <= e.start) throw new RangeError(`event ${e.uid}: end must be after start`)
    lines.push('BEGIN:VEVENT', `UID:${e.uid}`, `DTSTAMP:${stamp(now)}`, `DTSTART:${stamp(e.start)}`, `DTEND:${stamp(e.end)}`, `SUMMARY:${esc(e.title)}`)
    if (e.description) lines.push(`DESCRIPTION:${esc(e.description)}`)
    if (e.location) lines.push(`LOCATION:${esc(e.location)}`)
    lines.push('END:VEVENT')
  }
  lines.push('END:VCALENDAR')
  return lines.map(fold).join('\r\n') + '\r\n'
}

export function zonedParts(ms: number, tz: string) {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const p = Object.fromEntries(f.formatToParts(ms).map((x) => [x.type, x.value])) as Record<string, string>
  return { y: +p.year, mo: +p.month, d: +p.day, h: +p.hour, mi: +p.minute, s: +p.second }
}

/** Minutes east of UTC at that instant, e.g. New York: -300 in winter, -240 in summer. */
export function offsetMinutes(ms: number, tz: string): number {
  const z = zonedParts(ms, tz)
  return Math.round((Date.UTC(z.y, z.mo - 1, z.d, z.h, z.mi, z.s) - Math.floor(ms / 1000) * 1000) / 60000)
}

/** Resolve a wall-clock time in `tz` to a UTC instant. Spring-forward gap: moves forward by the gap. Fall-back overlap: picks the first (DST) occurrence. */
export function wallTimeToUtc(y: number, mo: number, d: number, h: number, mi: number, tz: string): number {
  const naive = Date.UTC(y, mo - 1, d, h, mi)
  const o1 = offsetMinutes(naive - 12 * 3600_000, tz), o2 = offsetMinutes(naive + 12 * 3600_000, tz)
  for (const off of o1 === o2 ? [o1] : [Math.max(o1, o2), Math.min(o1, o2)]) {          // try the larger (DST) offset first
    const guess = naive - off * 60_000
    if (offsetMinutes(guess, tz) === off) return guess
  }
  return naive - o1 * 60_000
}
''', "ical.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { toICS, fold, offsetMinutes, wallTimeToUtc, zonedParts } from './ical.ts'

const NOW = Date.UTC(2026, 0, 1)

test('minimal valid calendar with UTC times, CRLF endings, required fields', () => {
  const ics = toICS([{ uid: 'a@x', title: 'Standup', start: Date.UTC(2026, 2, 5, 14), end: Date.UTC(2026, 2, 5, 14, 30) }], NOW)
  assert.match(ics, /^BEGIN:VCALENDAR\r\nVERSION:2\.0\r\n/); assert.ok(ics.endsWith('END:VCALENDAR\r\n')); assert.ok(!/[^\r]\n/.test(ics))
  for (const l of ['UID:a@x', 'DTSTAMP:20260101T000000Z', 'DTSTART:20260305T140000Z', 'DTEND:20260305T143000Z', 'SUMMARY:Standup']) assert.ok(ics.includes(l), l)
})

test('text values are escaped', () => {
  const ics = toICS([{ uid: 'b', title: 'A, B; C\\D', start: 0, end: 1000, description: 'line1\nline2', location: 'Room 1, HQ' }], NOW)
  assert.ok(ics.includes('SUMMARY:A\\, B\\; C\\\\D')); assert.ok(ics.includes('DESCRIPTION:line1\\nline2')); assert.ok(ics.includes('LOCATION:Room 1\\, HQ'))
})

test('folding: lines <= 75 octets, round-trips on unfold, never splits a character', () => {
  const long = 'SUMMARY:' + 'é日本語🙂 '.repeat(30), folded = fold(long)
  for (const l of folded.split('\r\n')) assert.ok(new TextEncoder().encode(l).length <= 75, String(new TextEncoder().encode(l).length))
  assert.equal(folded.replace(/\r\n /g, ''), long); assert.equal(fold('short'), 'short')
})

test('end before start is rejected', () => {
  assert.throws(() => toICS([{ uid: 'c', title: 't', start: 10, end: 10 }]), RangeError)
})

test('offsets follow DST rules from the tz database', () => {
  const winter = Date.UTC(2026, 0, 15, 12), summer = Date.UTC(2026, 6, 15, 12)
  assert.deepEqual([offsetMinutes(winter, 'America/New_York'), offsetMinutes(summer, 'America/New_York')], [-300, -240])
  assert.deepEqual([offsetMinutes(winter, 'Europe/Berlin'), offsetMinutes(summer, 'Europe/Berlin')], [60, 120])
  assert.deepEqual([offsetMinutes(winter, 'Asia/Kolkata'), offsetMinutes(summer, 'Asia/Kolkata'), offsetMinutes(summer, 'UTC')], [330, 330, 0])
  assert.equal(offsetMinutes(summer, 'Australia/Sydney'), 600)
})

test('wall time -> UTC for ordinary days in both seasons', () => {
  assert.equal(wallTimeToUtc(2026, 1, 15, 9, 0, 'America/New_York'), Date.UTC(2026, 0, 15, 14)); assert.equal(wallTimeToUtc(2026, 7, 15, 9, 0, 'America/New_York'), Date.UTC(2026, 6, 15, 13))
  const z = zonedParts(wallTimeToUtc(2026, 7, 15, 9, 0, 'Europe/Berlin'), 'Europe/Berlin'); assert.deepEqual([z.h, z.mi], [9, 0])
})

test('DST gap and overlap are handled deterministically (US 2026: spring forward Mar 8, fall back Nov 1)', () => {
  const gap = wallTimeToUtc(2026, 3, 8, 2, 30, 'America/New_York'); assert.deepEqual([zonedParts(gap, 'America/New_York').h, zonedParts(gap, 'America/New_York').mi], [3, 30])       // 02:30 does not exist -> 03:30
  const fold1 = wallTimeToUtc(2026, 11, 1, 1, 30, 'America/New_York'); assert.equal(fold1, Date.UTC(2026, 10, 1, 5, 30))                                                                     // first 01:30 (EDT)
  assert.equal(wallTimeToUtc(2026, 3, 8, 12, 0, 'America/New_York'), Date.UTC(2026, 2, 8, 16))
})

test('a daily 09:00 meeting keeps its wall time across DST while UTC shifts', () => {
  const before = wallTimeToUtc(2026, 3, 6, 9, 0, 'America/New_York'), after = wallTimeToUtc(2026, 3, 9, 9, 0, 'America/New_York')
  assert.equal((after - before) / 3600_000, 3 * 24 - 1)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Relies on the runtime's ICU/tz database: results depend on the Node/browser version's tz data", "Recurring events: add RRULE / use the `rrule` or `ical-generator` packages; floating and TZID-based DTSTART are alternatives to UTC"], compat={"node": ">=22.18"},
   tags=["ical", "ics", "calendar", "timezone", "time-zone", "dst", "daylight-saving", "intl", "export", "scheduling", "datetime", "rfc5545"],
   avoid="Adding 24h to a Date to get 'tomorrow' across DST; hand-maintained UTC offsets; unescaped commas in SUMMARY; folding by characters instead of octets."),

RC("ts-websocket-reconnect-heartbeat", "WebSocket client: auto-reconnect with jittered backoff, heartbeat/timeout detection, send queue while offline (injectable socket)", "typescript",
   "Realtime features break on mobile networks: connections die silently, reconnect storms hammer the server, and messages sent while offline vanish.",
   "A `RealtimeClient` that takes a socket factory and timers (so it is testable without a network), runs a ping/pong heartbeat with a deadline to detect half-open connections, reconnects with exponential backoff + full jitter (reset after a healthy period), queues outbound messages (bounded) while disconnected and flushes in order on open, and exposes explicit states and an `onState` listener; a manual `close()` never reconnects.",
   {"rt.ts": r'''export interface Sock { send(data: string): void; close(): void; onopen: (() => void) | null; onmessage: ((e: { data: string }) => void) | null; onclose: (() => void) | null; onerror: (() => void) | null }
export interface Timers { set(fn: () => void, ms: number): unknown; clear(h: unknown): void; now(): number }
export type State = 'connecting' | 'open' | 'backoff' | 'closed'
export interface Opts { open: () => Sock; timers: Timers; rand?: () => number; baseMs?: number; capMs?: number; pingMs?: number; pongTimeoutMs?: number; stableMs?: number; maxQueue?: number }

export class RealtimeClient {
  state: State = 'closed'
  private sock: Sock | null = null
  private queue: string[] = []
  private fails = 0
  private openedAt = 0
  private retry: unknown = undefined
  private ping: unknown = undefined
  private dead: unknown = undefined
  private listeners: ((m: string) => void)[] = []
  private stateListeners: ((s: State) => void)[] = []
  private o: Required<Opts>

  constructor(opts: Opts) { this.o = { rand: Math.random, baseMs: 500, capMs: 30_000, pingMs: 15_000, pongTimeoutMs: 5_000, stableMs: 10_000, maxQueue: 100, ...opts } }

  onMessage(fn: (m: string) => void) { this.listeners.push(fn) }
  onState(fn: (s: State) => void) { this.stateListeners.push(fn) }
  private set(s: State) { if (this.state !== s) { this.state = s; this.stateListeners.forEach((f) => f(s)) } }

  connect() {
    if (this.state === 'connecting' || this.state === 'open') return
    this.set('connecting')
    const s = (this.sock = this.o.open())
    s.onopen = () => {
      if (this.sock !== s) return
      this.openedAt = this.o.timers.now(); this.set('open')
      for (const m of this.queue.splice(0)) s.send(m)                       // flush in order
      this.heartbeat()
    }
    s.onmessage = (e) => {
      if (this.sock !== s) return
      this.alive()                                                         // any traffic proves the link is alive, not just pongs
      if (e.data === 'pong') return
      this.listeners.forEach((f) => f(e.data))
    }
    s.onclose = s.onerror = () => { if (this.sock === s) this.lost() }
  }

  send(m: string) {
    if (this.state === 'open' && this.sock) { this.sock.send(m); return }
    this.queue.push(m); if (this.queue.length > this.o.maxQueue) this.queue.shift()
  }

  close() {
    this.stopTimers(); this.o.timers.clear(this.retry); this.retry = undefined
    const s = this.sock; this.sock = null; this.set('closed'); s?.close()
  }

  private heartbeat() {
    this.o.timers.clear(this.ping)
    this.ping = this.o.timers.set(() => {
      if (this.state !== 'open') return
      this.sock?.send('ping')
      this.dead = this.o.timers.set(() => this.lost(), this.o.pongTimeoutMs)           // no answer in time => half-open connection
      }, this.o.pingMs)
  }
  private alive() { this.o.timers.clear(this.dead); this.dead = undefined; if (this.state === 'open') this.heartbeat() }
  private stopTimers() { this.o.timers.clear(this.ping); this.o.timers.clear(this.dead); this.ping = this.dead = undefined }

  private lost() {
    this.stopTimers()
    const s = this.sock; this.sock = null; try { s?.close() } catch { /* already dead */ }
    if (this.state === 'closed') return
    if (this.openedAt && this.o.timers.now() - this.openedAt >= this.o.stableMs) this.fails = 0      // only a connection that stayed up resets the backoff
    this.openedAt = 0
    const delay = Math.min(this.o.capMs, this.o.baseMs * 2 ** this.fails++) * this.o.rand()          // full jitter: a crowd of clients must not retry in lockstep
    this.set('backoff')
    this.retry = this.o.timers.set(() => { this.retry = undefined; this.connect() }, delay)
  }
}
''', "rt.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { RealtimeClient, type Sock, type Timers } from './rt.ts'

function rig() {
  let now = 0, id = 0; const q = new Map<number, { at: number; fn: () => void }>()
  const timers: Timers = { now: () => now, set: (fn, ms) => { q.set(++id, { at: now + ms, fn }); return id }, clear: (h) => { q.delete(h as number) } }
  const advance = (ms: number) => { const end = now + ms; for (;;) { const d = [...q.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!d) break; now = d[1].at; q.delete(d[0]); d[1].fn() } now = end }
  const socks: (Sock & { sent: string[]; closed: boolean })[] = []
  const open = () => { const s: any = { sent: [], closed: false, send(d: string) { s.sent.push(d) }, close() { s.closed = true }, onopen: null, onmessage: null, onclose: null, onerror: null }; socks.push(s); return s }
  return { timers, advance, open, socks }
}
const mk = (r: ReturnType<typeof rig>, extra = {}) => new RealtimeClient({ open: r.open, timers: r.timers, rand: () => 1, baseMs: 1000, capMs: 8000, pingMs: 10_000, pongTimeoutMs: 2000, stableMs: 5000, ...extra })

test('queued messages flush in order when the socket opens', () => {
  const r = rig(), c = mk(r); c.send('a'); c.send('b'); c.connect(); assert.equal(c.state, 'connecting'); r.socks[0].onopen!(); assert.equal(c.state, 'open'); assert.deepEqual(r.socks[0].sent, ['a', 'b']); c.send('c'); assert.deepEqual(r.socks[0].sent, ['a', 'b', 'c'])
})

test('backoff grows exponentially up to the cap and resets only after a stable connection', () => {
  const r = rig(), c = mk(r), delays: number[] = []; c.connect()
  for (let i = 0; i < 5; i++) { const before = r.socks.length; r.socks.at(-1)!.onclose!(); let waited = 0; while (r.socks.length === before) { r.advance(250); waited += 250 } delays.push(waited) }
  assert.deepEqual(delays, [1000, 2000, 4000, 8000, 8000])
  r.socks.at(-1)!.onopen!(); r.advance(6000); r.socks.at(-1)!.onclose!(); let w = 0; const n = r.socks.length; while (r.socks.length === n) { r.advance(250); w += 250 }; assert.equal(w, 1000)       // stable for 6s => back to base delay
})

test('a flapping connection that dies right after opening does not reset the backoff', () => {
  const r = rig(), c = mk(r); c.connect(); r.socks[0].onclose!(); r.advance(1000); r.socks[1].onopen!(); r.advance(100); r.socks[1].onclose!()
  const n = r.socks.length; let w = 0; while (r.socks.length === n) { r.advance(250); w += 250 }; assert.equal(w, 2000)
})

test('missing pong closes the half-open socket and reconnects', () => {
  const r = rig(), c = mk(r); c.connect(); r.socks[0].onopen!(); r.advance(10_000); assert.deepEqual(r.socks[0].sent, ['ping']); assert.equal(c.state, 'open')
  r.advance(2000); assert.equal(c.state, 'backoff'); assert.equal(r.socks[0].closed, true); r.advance(1000); assert.equal(r.socks.length, 2)
})

test('any incoming message counts as a heartbeat; pongs are not delivered to the app', () => {
  const r = rig(), c = mk(r), got: string[] = []; c.onMessage((m) => got.push(m)); c.connect(); r.socks[0].onopen!(); r.advance(10_000)
  r.socks[0].onmessage!({ data: 'pong' }); r.advance(5000); assert.equal(c.state, 'open'); r.socks[0].onmessage!({ data: 'hello' }); assert.deepEqual(got, ['hello'])
})

test('close() is final: no reconnect, timers cleared, late events from the old socket ignored', () => {
  const r = rig(), c = mk(r); c.connect(); r.socks[0].onopen!(); c.close(); assert.equal(c.state, 'closed'); assert.equal(r.socks[0].closed, true)
  r.socks[0].onclose!(); r.advance(120_000); assert.equal(r.socks.length, 1); assert.equal(c.state, 'closed')
})

test('offline queue is bounded and state changes are reported once each', () => {
  const r = rig(), c = mk(r, { maxQueue: 3 }), states: string[] = []; c.onState((s) => states.push(s)); for (const m of 'abcde') c.send(m); c.connect(); r.socks[0].onopen!()
  assert.deepEqual(r.socks[0].sent, ['c', 'd', 'e']); r.socks[0].onclose!(); r.socks[0].onerror!(); assert.deepEqual(states, ['connecting', 'open', 'backoff'])
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "In a browser `open = () => new WebSocket(url)` fits the `Sock` shape after assigning handlers; auth tokens should be refreshed inside `open()` before each reconnect", "Server must answer 'ping' with 'pong' (or use protocol-level ping frames where available)"], compat={"node": ">=22.18"},
   tags=["websocket", "reconnect", "heartbeat", "ping-pong", "backoff", "realtime", "chat", "offline-queue", "connection", "socket", "presence", "live"],
   avoid="Reconnecting immediately in a loop; relying on `onclose` alone (half-open sockets never fire it); resetting the backoff on every successful open; losing messages sent while offline."),

RC("ts-pubsub-presence-sse", "In-memory pub/sub with wildcard topics, presence with TTL heartbeats, and Server-Sent-Events framing", "typescript",
   "A chat room, live dashboard or notification feed needs fan-out to subscribers, 'who is online', and the exact SSE wire format.",
   "Topic matching with `*` (one segment) and `#` (rest), subscriber isolation (one throwing handler doesn't stop the rest), bounded per-subscriber queues with drop-oldest, presence map keyed by user with multi-connection counting and TTL expiry, and an `sseFrame()` encoder that splits multi-line data, sets id/event/retry and sends comment keep-alives.",
   {"bus.ts": r'''export type Handler = (topic: string, payload: unknown) => void

export function topicMatches(pattern: string, topic: string): boolean {
  const p = pattern.split('.'), t = topic.split('.')
  for (let i = 0; i < p.length; i++) {
    if (p[i] === '#') return i === p.length - 1                    // '#' swallows the rest (must be last)
    if (i >= t.length) return false
    if (p[i] !== '*' && p[i] !== t[i]) return false
  }
  return p.length === t.length
}

export class Bus {
  private subs = new Map<number, { pattern: string; fn: Handler }>()
  private next = 1
  errors: unknown[] = []
  subscribe(pattern: string, fn: Handler): () => void { const id = this.next++; this.subs.set(id, { pattern, fn }); return () => void this.subs.delete(id) }
  publish(topic: string, payload: unknown): number {
    let n = 0
    for (const { pattern, fn } of [...this.subs.values()]) {
      if (!topicMatches(pattern, topic)) continue
      try { fn(topic, payload); n++ } catch (e) { this.errors.push(e) }          // isolate subscribers: one bad handler must not break the others
    }
    return n
  }
}

/** Bounded queue for a slow consumer (SSE/WebSocket): keeps the newest items, counts what was dropped. */
export class Outbox<T> {
  items: T[] = []
  dropped = 0
  private cap: number
  constructor(cap = 100) { this.cap = cap }
  push(x: T) { this.items.push(x); while (this.items.length > this.cap) { this.items.shift(); this.dropped++ } }
  drain(): T[] { return this.items.splice(0) }
}

export class Presence {
  private conns = new Map<string, Map<string, number>>()               // user -> connection id -> last seen
  private ttlMs: number
  constructor(ttlMs = 30_000) { this.ttlMs = ttlMs }
  beat(user: string, conn: string, now: number) { (this.conns.get(user) ?? this.conns.set(user, new Map()).get(user)!).set(conn, now) }
  leave(user: string, conn: string) { const m = this.conns.get(user); m?.delete(conn); if (m && m.size === 0) this.conns.delete(user) }
  /** Users with at least one connection seen within the TTL (a user with two tabs stays online until both are gone). */
  online(now: number): string[] {
    const out: string[] = []
    for (const [u, m] of this.conns) { for (const [c, t] of m) if (now - t >= this.ttlMs) m.delete(c); if (m.size) out.push(u); else this.conns.delete(u) }
    return out.sort()
  }
}

/** One SSE event on the wire. Multi-line data becomes multiple `data:` lines; a blank line terminates the event. */
export function sseFrame(data: string, opts: { event?: string; id?: string | number; retry?: number } = {}): string {
  const lines: string[] = []
  if (opts.retry !== undefined) lines.push(`retry: ${opts.retry}`)
  if (opts.id !== undefined) lines.push(`id: ${String(opts.id).replace(/[\r\n]/g, '')}`)
  if (opts.event) lines.push(`event: ${opts.event.replace(/[\r\n]/g, '')}`)
  for (const l of data.split(/\r\n|\r|\n/)) lines.push(`data: ${l}`)
  return lines.join('\n') + '\n\n'
}
export const sseKeepAlive = ': keep-alive\n\n'
''', "bus.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { topicMatches, Bus, Outbox, Presence, sseFrame, sseKeepAlive } from './bus.ts'

test('topic patterns: exact, * is exactly one segment, # is zero-or-more trailing segments', () => {
  const m = topicMatches
  assert.deepEqual([m('chat.room1', 'chat.room1'), m('chat.*', 'chat.room1'), m('chat.*', 'chat.room1.typing'), m('chat.#', 'chat.room1.typing'), m('#', 'a.b.c'), m('chat.#', 'chat'), m('chat.*.typing', 'chat.r1.typing'), m('chat.room1', 'chat.room2'), m('a.#.c', 'a.b.c')],
    [true, true, false, true, true, true, true, false, false])      // '#' matches zero or more trailing segments, like AMQP/MQTT
})

test('publish reaches matching subscribers only, unsubscribe works, a throwing handler does not block others', () => {
  const bus = new Bus(), got: string[] = []
  const off = bus.subscribe('news.*', (t) => got.push('A:' + t)); bus.subscribe('news.#', () => { throw new Error('boom') }); bus.subscribe('news.sport', (t, p) => got.push('B:' + t + ':' + p))
  assert.equal(bus.publish('news.sport', 1), 2); assert.deepEqual(got, ['A:news.sport', 'B:news.sport:1']); assert.equal(bus.errors.length, 1)
  off(); assert.equal(bus.publish('news.sport', 2), 1); assert.equal(bus.publish('weather.today', 3), 0)
})

test('subscribing inside a handler does not loop forever or skip', () => {
  const bus = new Bus(); let calls = 0; bus.subscribe('x', () => { calls++; bus.subscribe('x', () => { calls += 10 }) }); bus.publish('x', 0); assert.equal(calls, 1); bus.publish('x', 0); assert.equal(calls, 12)
})

test('slow consumers lose the oldest messages, not the newest, and know how many', () => {
  const o = new Outbox<number>(3); for (let i = 1; i <= 5; i++) o.push(i); assert.deepEqual(o.drain(), [3, 4, 5]); assert.equal(o.dropped, 2); assert.deepEqual(o.drain(), [])
})

test('presence: multi-tab counting and TTL expiry', () => {
  const p = new Presence(30_000); p.beat('ann', 'tab1', 0); p.beat('ann', 'tab2', 5_000); p.beat('bob', 'c1', 0)
  assert.deepEqual(p.online(10_000), ['ann', 'bob']); p.leave('ann', 'tab1'); assert.deepEqual(p.online(10_000), ['ann', 'bob'])
  assert.deepEqual(p.online(31_000), ['ann']); p.beat('bob', 'c1', 31_000); assert.deepEqual(p.online(32_000), ['ann', 'bob']); assert.deepEqual(p.online(40_000), ['bob']); assert.deepEqual(p.online(100_000), [])
})

test('SSE framing', () => {
  assert.equal(sseFrame('hello'), 'data: hello\n\n'); assert.equal(sseFrame('a\nb\r\nc'), 'data: a\ndata: b\ndata: c\n\n')
  assert.equal(sseFrame('{"x":1}', { event: 'tick', id: 7, retry: 3000 }), 'retry: 3000\nid: 7\nevent: tick\ndata: {"x":1}\n\n'); assert.equal(sseFrame('x', { event: 'a\nb' }), 'event: ab\ndata: x\n\n'); assert.equal(sseKeepAlive, ': keep-alive\n\n')
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Single-process only: across servers fan out through Redis pub/sub, NATS or Postgres LISTEN/NOTIFY", "SSE response headers: Content-Type: text/event-stream, Cache-Control: no-cache, and no proxy buffering; clients resume with Last-Event-ID"], compat={"node": ">=22.18"},
   tags=["pubsub", "publish-subscribe", "event-bus", "presence", "online", "sse", "server-sent-events", "realtime", "chat", "backpressure", "websocket", "notifications", "topics"],
   avoid="Letting one slow client's buffer grow without bound; presence by 'connected' flag (two tabs = flapping); data lines containing raw newlines in SSE."),

RC("ts-a11y-focus-roving-contrast", "Accessibility primitives: focus-trap order, roving tabindex, WCAG contrast ratio, de-duplicated live announcer", "typescript",
   "Modals, toolbars, tabs and menus need keyboard behaviour that works - and colours must pass WCAG contrast without guessing.",
   "Pure functions for the next focus index on Tab/Shift+Tab inside a trap (wrap-around, skipping disabled), roving-tabindex arrow/Home/End navigation, WCAG 2.x relative luminance and contrast ratio with AA/AAA verdicts for normal and large text, and an `Announcer` queue that de-duplicates identical consecutive messages and alternates polite/assertive regions so screen readers re-read repeated text.",
   {"a11y.ts": r'''export interface Item { disabled?: boolean }

/** Index to focus after Tab/Shift+Tab inside a focus trap; -1 if nothing is focusable. */
export function trapNext(items: Item[], current: number, shift: boolean): number {
  const ok = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0)
  if (!ok.length) return -1
  const pos = ok.indexOf(current)
  if (pos === -1) return shift ? ok[ok.length - 1] : ok[0]                           // focus escaped (click outside): pull it back in
  return ok[(pos + (shift ? -1 : 1) + ok.length) % ok.length]
}

/** Roving tabindex (toolbar, tabs, menu): returns the new active index for a key, or null if the key is not handled. */
export function rovingNext(items: Item[], current: number, key: string, orientation: 'horizontal' | 'vertical' | 'both' = 'horizontal'): number | null {
  const fwd = key === 'Home' ? 'first' : key === 'End' ? 'last'
    : (orientation !== 'vertical' && key === 'ArrowRight') || (orientation !== 'horizontal' && key === 'ArrowDown') ? 1
    : (orientation !== 'vertical' && key === 'ArrowLeft') || (orientation !== 'horizontal' && key === 'ArrowUp') ? -1 : null
  if (fwd === null) return null
  const ok = items.map((it, i) => (it.disabled ? -1 : i)).filter((i) => i >= 0)
  if (!ok.length) return null
  if (fwd === 'first') return ok[0]
  if (fwd === 'last') return ok[ok.length - 1]
  const pos = Math.max(0, ok.indexOf(current))
  return ok[(pos + fwd + ok.length) % ok.length]
}

export function hexToRgb(hex: string): [number, number, number] {
  const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim()); if (!m) throw new Error(`bad colour: ${hex}`)
  const h = m[1].length === 3 ? [...m[1]].map((c) => c + c).join('') : m[1]
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number]
}

export function luminance([r, g, b]: [number, number, number]): number {
  const f = (v: number) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}

export function contrast(fg: string, bg: string): number {
  const a = luminance(hexToRgb(fg)), b = luminance(hexToRgb(bg))
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

export function wcag(fg: string, bg: string, large = false) {
  const r = contrast(fg, bg)
  return { ratio: Math.round(r * 100) / 100, AA: r >= (large ? 3 : 4.5), AAA: r >= (large ? 4.5 : 7) }
}

/** Screen readers ignore a live region whose text did not change; alternate regions and flush duplicates so repeated messages are still read. */
export class Announcer {
  private last = ''
  private flip = false
  history: { region: 'a' | 'b'; text: string }[] = []
  announce(text: string): { region: 'a' | 'b'; text: string } | null {
    const t = text.trim(); if (!t) return null
    if (t === this.last) return null                                                // identical consecutive messages are noise
    this.last = t; this.flip = !this.flip
    const m = { region: (this.flip ? 'a' : 'b') as 'a' | 'b', text: t }; this.history.push(m); return m
  }
  reset() { this.last = '' }
}
''', "a11y.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { trapNext, rovingNext, contrast, wcag, hexToRgb, Announcer } from './a11y.ts'

const items = [{}, { disabled: true }, {}, {}]

test('focus trap wraps in both directions and skips disabled items', () => {
  assert.deepEqual([trapNext(items, 0, false), trapNext(items, 2, false), trapNext(items, 3, false), trapNext(items, 0, true), trapNext(items, 2, true)], [2, 3, 0, 3, 0])
})

test('focus that escaped the trap is pulled back in; empty traps return -1', () => {
  assert.equal(trapNext(items, -1, false), 0); assert.equal(trapNext(items, -1, true), 3); assert.equal(trapNext(items, 1, false), 0); assert.equal(trapNext([{ disabled: true }], 0, false), -1); assert.equal(trapNext([], 0, false), -1)
})

test('roving tabindex: horizontal, vertical, home/end, wrap, skip disabled, ignore other keys', () => {
  assert.deepEqual([rovingNext(items, 0, 'ArrowRight'), rovingNext(items, 3, 'ArrowRight'), rovingNext(items, 0, 'ArrowLeft'), rovingNext(items, 3, 'Home'), rovingNext(items, 0, 'End')], [2, 0, 3, 0, 3])
  assert.equal(rovingNext(items, 0, 'ArrowDown'), null); assert.equal(rovingNext(items, 0, 'ArrowDown', 'vertical'), 2); assert.equal(rovingNext(items, 0, 'ArrowRight', 'vertical'), null); assert.equal(rovingNext(items, 0, 'ArrowDown', 'both'), 2)
  assert.equal(rovingNext(items, 0, 'a'), null); assert.equal(rovingNext([{ disabled: true }], 0, 'Home'), null)
})

test('contrast ratios match published WCAG values', () => {
  assert.equal(contrast('#000', '#fff'), 21); assert.equal(contrast('#fff', '#fff'), 1)
  assert.equal(wcag('#767676', '#ffffff').ratio, 4.54); assert.equal(wcag('#777777', '#ffffff').AA, false); assert.equal(wcag('#767676', '#fff').AA, true)
})

test('large text and AAA thresholds', () => {
  assert.deepEqual(wcag('#949494', '#fff', true), { ratio: 3.03, AA: true, AAA: false }); assert.equal(wcag('#949494', '#fff').AA, false); assert.equal(wcag('#595959', '#fff').AAA, true)
})

test('colour parsing: shorthand, case, bad input', () => {
  assert.deepEqual(hexToRgb('#0af'), [0, 170, 255]); assert.deepEqual(hexToRgb('FFAA00'), [255, 170, 0]); for (const bad of ['', '#12', 'red', '#gggggg']) assert.throws(() => hexToRgb(bad), /bad colour/)
})

test('announcer drops consecutive duplicates and alternates regions', () => {
  const a = new Announcer(); assert.deepEqual(a.announce('Saved'), { region: 'a', text: 'Saved' }); assert.equal(a.announce(' Saved '), null); assert.deepEqual(a.announce('Error'), { region: 'b', text: 'Error' })
  assert.deepEqual(a.announce('Saved'), { region: 'a', text: 'Saved' }); assert.equal(a.announce('   '), null); a.reset(); assert.ok(a.announce('Saved'))
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Real accessibility still needs semantics (dialog/role, labels, focus return) and testing with a screen reader; prefer the native <dialog> and aria-live where possible", "WCAG 2.x contrast formula; APCA (WCAG 3 draft) gives different results for some pairs"], compat={"node": ">=22.18"},
   tags=["accessibility", "a11y", "focus-trap", "keyboard", "roving-tabindex", "contrast", "wcag", "aria-live", "screen-reader", "modal", "tabs", "menu", "aria"],
   avoid="Modals that let Tab escape to the page behind; arrow-key widgets where every item is a tab stop; checking contrast by eye."),
]
