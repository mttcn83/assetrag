"""Round-1 persona recipes, part C: TypeScript building blocks for shops, bots, mobile/desktop apps, docs sites, games and
security (stdlib only, `node --test`, erasable syntax only). Providers (Stripe, FCM, Discord, Keychain, Electron) are never
called: these are the pure rules around them - the part that goes wrong - behind small injected interfaces."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

R1C = [
# ------------------------------------------------------------------ commerce
RC("ts-cart-totals-coupons", "Cart totals with coupons and tax in integer cents (TypeScript)", "typescript",
   "A shop front-end or API needs a shopping cart whose totals are exact: coupons, tax per line, rounding and no negative totals.",
   "Integer cents only; percent coupons round half-up once, fixed coupons are capped at the subtotal, minimum-spend gate, discount spread over lines proportionally (largest remainder) so per-line tax is charged on what is actually paid, immutable add/remove/setQty helpers for UI state.",
   {"cart.ts": r'''export interface Line { sku: string; unitCents: number; qty: number; taxBp: number }
export interface Coupon { code: string; percentBp?: number; fixedCents?: number; minSpendCents?: number }
export interface Totals { subtotal: number; discount: number; tax: number; total: number; taxByRate: Record<number, number> }

const halfUp = (n: number, d: number) => Math.floor((2 * n + d) / (2 * d))

export function addLine(cart: readonly Line[], line: Line): Line[] {
  const i = cart.findIndex((l) => l.sku === line.sku)
  if (i < 0) return [...cart, line]
  return cart.map((l, j) => (j === i ? { ...l, qty: l.qty + line.qty } : l))
}
export const setQty = (cart: readonly Line[], sku: string, qty: number): Line[] =>
  qty <= 0 ? cart.filter((l) => l.sku !== sku) : cart.map((l) => (l.sku === sku ? { ...l, qty } : l))

export function totals(lines: readonly Line[], coupon?: Coupon): Totals {
  for (const l of lines) if (!Number.isInteger(l.qty) || l.qty < 1 || !Number.isInteger(l.unitCents) || l.unitCents < 0) throw new RangeError(`bad line ${l.sku}`)
  const gross = lines.map((l) => l.unitCents * l.qty)
  const subtotal = gross.reduce((a, b) => a + b, 0)
  let discount = 0
  if (coupon && subtotal > 0 && subtotal >= (coupon.minSpendCents ?? 0)) {
    discount = Math.min(subtotal, halfUp(subtotal * (coupon.percentBp ?? 0), 10000) + (coupon.fixedCents ?? 0))
  }
  // largest-remainder split so the line shares sum to exactly `discount`
  const shares = gross.map((g) => (subtotal ? Math.floor((discount * g) / subtotal) : 0))
  let left = discount - shares.reduce((a, b) => a + b, 0)
  const order = gross.map((g, i) => ({ i, r: subtotal ? (discount * g) % subtotal : 0 })).sort((a, b) => b.r - a.r || a.i - b.i)
  for (const { i } of order) { if (left <= 0) break; if (shares[i] < gross[i]) { shares[i]++; left-- } }
  const taxByRate: Record<number, number> = {}
  let tax = 0
  lines.forEach((l, i) => { const t = halfUp((gross[i] - shares[i]) * l.taxBp, 10000); taxByRate[l.taxBp] = (taxByRate[l.taxBp] ?? 0) + t; tax += t })
  return { subtotal, discount, tax, total: subtotal - discount + tax, taxByRate }
}
''', "cart.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { totals, addLine, setQty, type Line } from './cart.ts'

const L = (sku: string, unitCents: number, qty = 1, taxBp = 0): Line => ({ sku, unitCents, qty, taxBp })

test('plain totals with tax', () => assert.deepEqual(totals([L('a', 1000, 2, 2000)]), { subtotal: 2000, discount: 0, tax: 400, total: 2400, taxByRate: { 2000: 400 } }))
test('percent coupon reduces the taxable base', () => { const t = totals([L('a', 1000, 1, 2000)], { code: 'TEN', percentBp: 1000 }); assert.deepEqual([t.discount, t.tax, t.total], [100, 180, 1080]) })
test('fixed coupon is capped, total never negative', () => { const t = totals([L('a', 500, 1, 2000)], { code: 'BIG', fixedCents: 9999 }); assert.deepEqual([t.discount, t.tax, t.total], [500, 0, 0]) })
test('minimum spend gate', () => {
  const c = { code: 'X', fixedCents: 100, minSpendCents: 2000 }
  assert.equal(totals([L('a', 1999)], c).discount, 0); assert.equal(totals([L('a', 2000)], c).discount, 100)
})
test('discount shares always sum exactly', () => {
  const lines = [L('a', 333, 1, 1000), L('b', 333, 1, 1000), L('c', 334, 1, 1000)]
  for (const fixed of [1, 7, 100, 999]) { const t = totals(lines, { code: 'C', fixedCents: fixed }); assert.equal(t.discount, fixed); assert.equal(t.total, t.subtotal - t.discount + t.tax) }
})
test('rounds tax half-up once per line', () => assert.equal(totals([L('a', 33, 3, 1000)]).tax, 10))
test('mixed rates', () => assert.deepEqual(totals([L('book', 1000), L('pen', 1000, 1, 2000)]).taxByRate, { 0: 0, 2000: 200 }))
test('bad input rejected', () => { assert.throws(() => totals([L('a', 100, 0)])); assert.throws(() => totals([L('a', 1.5)])) })
test('cart helpers are immutable', () => {
  const c = [L('a', 100)]; const c2 = addLine(c, L('a', 100, 2)); assert.equal(c[0].qty, 1); assert.equal(c2[0].qty, 3)
  assert.deepEqual(setQty(c2, 'a', 0), [])
})
'''}, NODE_TS, assumptions=TSA + ["Single currency with 2 minor digits; prices tax-exclusive"], compat={"node": ">=22.18"},
   tags=["ecommerce", "cart", "checkout", "coupon", "discount", "tax", "shop", "money", "react", "nextjs"],
   avoid="Floating-point prices; computing totals only in the browser (re-compute on the server); discounting after tax."),

RC("ts-checkout-address-validation", "Checkout address, email and phone validation with per-country postcodes (TypeScript)", "typescript",
   "A checkout form rejects valid addresses or accepts junk: postcodes differ by country and phones need a normal form.",
   "Per-country required fields and postcode regexes (US, CA, GB, DE, FR, NL, AU, JP), whitespace/case normalisation, a pragmatic email check (one @, dotted domain, no spaces - the real check is the confirmation mail), E.164 phone normalisation with default country, structured errors keyed by field for form display.",
   {"address.ts": r'''export interface Address { name?: string; line1?: string; line2?: string; city?: string; region?: string; postcode?: string; country?: string }
export type Errors = Partial<Record<keyof Address | 'email' | 'phone', string>>

const POSTCODE: Record<string, RegExp> = {
  US: /^\d{5}(-\d{4})?$/, CA: /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] \d[ABCEGHJ-NPRSTV-Z]\d$/, GB: /^[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}$/,
  DE: /^\d{5}$/, FR: /^\d{5}$/, NL: /^\d{4} [A-Z]{2}$/, AU: /^\d{4}$/, JP: /^\d{3}-\d{4}$/,
}
const NEEDS_REGION = new Set(['US', 'CA', 'AU'])
const DIAL: Record<string, string> = { US: '1', CA: '1', GB: '44', DE: '49', FR: '33', NL: '31', AU: '61', JP: '81' }

const clean = (s?: string) => (s ?? '').normalize('NFC').replace(/\s+/g, ' ').trim()

export function normalisePostcode(country: string, raw: string): string {
  let p = clean(raw).toUpperCase()
  if (country === 'GB' || country === 'CA') p = p.replace(/\s/g, '').replace(/^(.+?)(.{3})$/, '$1 $2')
  if (country === 'NL') p = p.replace(/\s/g, '').replace(/^(\d{4})([A-Z]{2})$/, '$1 $2')
  return p
}

export function validateAddress(a: Address): { ok: boolean; errors: Errors; value: Address } {
  const errors: Errors = {}
  const v: Address = { name: clean(a.name), line1: clean(a.line1), line2: clean(a.line2), city: clean(a.city), region: clean(a.region), country: clean(a.country).toUpperCase() }
  if (!v.name) errors.name = 'Enter the recipient name'
  if (!v.line1) errors.line1 = 'Enter the street address'
  if (!v.city) errors.city = 'Enter the city'
  if (!/^[A-Z]{2}$/.test(v.country!) || !(v.country! in POSTCODE)) errors.country = 'Choose a supported country'
  else {
    v.postcode = normalisePostcode(v.country!, a.postcode ?? '')
    if (!POSTCODE[v.country!].test(v.postcode)) errors.postcode = 'That postcode does not look right for the country'
    if (NEEDS_REGION.has(v.country!) && !v.region) errors.region = 'Enter the state or province'
  }
  return { ok: Object.keys(errors).length === 0, errors, value: v }
}

export function validateEmail(raw: string): string | null {
  const e = clean(raw)
  return /^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/.test(e) && e.length <= 254 ? e.toLowerCase() : null
}

/** -> E.164 like +4915112345678, or null. `defaultCountry` is used for national numbers (leading 0 / no +). */
export function normalisePhone(raw: string, defaultCountry: string): string | null {
  const s = clean(raw)
  if (!/^[+\d(][\d\s().-]*$/.test(s)) return null
  const digits = s.replace(/\D/g, '')
  let e164: string
  if (s.startsWith('+')) e164 = digits
  else if (s.startsWith('00')) e164 = digits.slice(2)
  else { const dial = DIAL[defaultCountry]; if (!dial) return null; e164 = dial + (dial === '1' ? digits : digits.replace(/^0+/, '')) }
  return e164.length >= 8 && e164.length <= 15 ? '+' + e164 : null
}
''', "address.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { validateAddress, validateEmail, normalisePhone, normalisePostcode } from './address.ts'

const base = { name: ' Ada  Lovelace ', line1: '1 Main St', city: 'London', country: 'gb', postcode: 'sw1a1aa' }

test('valid GB address is normalised', () => {
  const r = validateAddress(base); assert.equal(r.ok, true)
  assert.equal(r.value.postcode, 'SW1A 1AA'); assert.equal(r.value.country, 'GB'); assert.equal(r.value.name, 'Ada Lovelace')
})
test('postcode rules per country', () => {
  assert.equal(validateAddress({ ...base, country: 'US', region: 'NY', postcode: '10001-1234' }).ok, true)
  assert.equal(validateAddress({ ...base, country: 'US', region: 'NY', postcode: '1000' }).errors.postcode !== undefined, true)
  assert.equal(validateAddress({ ...base, country: 'NL', postcode: '1012ab' }).value.postcode, '1012 AB')
  assert.equal(validateAddress({ ...base, country: 'DE', postcode: '10115' }).ok, true)
  assert.equal(validateAddress({ ...base, country: 'JP', postcode: '100-0001' }).ok, true)
})
test('region required where it matters', () => assert.ok(validateAddress({ ...base, country: 'US', postcode: '10001' }).errors.region))
test('errors keyed by field, unsupported country', () => {
  const r = validateAddress({ country: 'ZZ' }); assert.equal(r.ok, false); assert.ok(r.errors.name && r.errors.line1 && r.errors.city && r.errors.country)
})
test('postcode normaliser', () => { assert.equal(normalisePostcode('CA', 'k1a0b1'), 'K1A 0B1'); assert.equal(normalisePostcode('US', ' 10001 '), '10001') })
test('email', () => {
  assert.equal(validateEmail(' Ada@Example.COM '), 'ada@example.com')
  for (const bad of ['a@b', 'a b@c.de', '@x.com', 'a@@x.com', 'a@x..com', '']) assert.equal(validateEmail(bad), null, bad)
})
test('phone to E.164', () => {
  assert.equal(normalisePhone('+49 151 1234 5678', 'US'), '+4915112345678'); assert.equal(normalisePhone('0151 12345678', 'DE'), '+4915112345678')
  assert.equal(normalisePhone('(212) 555-0100', 'US'), '+12125550100'); assert.equal(normalisePhone('0044 20 7946 0958', 'US'), '+442079460958')
  for (const bad of ['abc', '123', '+1', '']) assert.equal(normalisePhone(bad, 'US'), null, bad)
})
'''}, NODE_TS, assumptions=TSA + ["Eight countries as examples; use libphonenumber-js / an address API for global coverage", "format checks only: a real address needs verification (carrier API) and a real email needs a confirmation link"], compat={"node": ">=22.18"},
   tags=["checkout", "address", "validation", "form", "ecommerce", "postcode", "phone", "email", "shop"],
   avoid="One US-style ZIP regex for everyone; rejecting valid emails with clever regexes; storing phone numbers in free form."),

RC("ts-order-state-machine", "Order status state machine with timeline events and idempotent transitions (TypeScript)", "typescript",
   "Orders, tickets or deployments must move through states legally, show a timeline, and survive duplicate webhooks.",
   "Explicit transition table (pending -> paid -> fulfilled -> shipped -> delivered, cancel/refund branches), `apply()` returns a new order with an appended timeline event, re-applying the same event id is a no-op, illegal transitions throw with allowed next states, derived `timeline()` view for the UI.",
   {"order.ts": r'''export type Status = 'pending' | 'paid' | 'packed' | 'shipped' | 'delivered' | 'cancelled' | 'refunded'
export interface Event { id: string; to: Status; at: number; note?: string }
export interface Order { id: string; status: Status; events: readonly Event[] }

const NEXT: Record<Status, readonly Status[]> = {
  pending: ['paid', 'cancelled'], paid: ['packed', 'refunded', 'cancelled'], packed: ['shipped', 'refunded'],
  shipped: ['delivered'], delivered: ['refunded'], cancelled: [], refunded: [],
}

export class IllegalTransition extends Error {
  allowed: readonly Status[]
  constructor(from: Status, to: Status) { super(`cannot go from ${from} to ${to}`); this.allowed = NEXT[from] }
}

export const newOrder = (id: string, at: number): Order => ({ id, status: 'pending', events: [{ id: 'created', to: 'pending', at }] })
export const allowed = (s: Status) => NEXT[s]
export const isFinal = (s: Status) => NEXT[s].length === 0

/** Pure: returns the same object for a duplicate event id (safe for retried webhooks). */
export function apply(o: Order, e: Event): Order {
  if (o.events.some((x) => x.id === e.id)) return o
  if (!NEXT[o.status].includes(e.to)) throw new IllegalTransition(o.status, e.to)
  return { ...o, status: e.to, events: [...o.events, e] }
}

const LABEL: Record<Status, string> = { pending: 'Order placed', paid: 'Payment received', packed: 'Packed', shipped: 'Shipped', delivered: 'Delivered', cancelled: 'Cancelled', refunded: 'Refunded' }
/** Timeline rows for the UI, oldest first, with the current step marked. */
export function timeline(o: Order) {
  return o.events.map((e, i) => ({ label: LABEL[e.to], at: e.at, note: e.note, current: i === o.events.length - 1 }))
}
''', "order.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { newOrder, apply, timeline, allowed, isFinal, IllegalTransition } from './order.ts'

test('happy path builds a timeline', () => {
  let o = newOrder('o1', 1)
  for (const [i, s] of (['paid', 'packed', 'shipped', 'delivered'] as const).entries()) o = apply(o, { id: `e${i}`, to: s, at: 10 + i })
  assert.equal(o.status, 'delivered')
  const t = timeline(o); assert.deepEqual(t.map((r) => r.label), ['Order placed', 'Payment received', 'Packed', 'Shipped', 'Delivered']); assert.equal(t.at(-1)!.current, true); assert.equal(t.filter((r) => r.current).length, 1)
})
test('illegal transitions explain what is allowed', () => {
  const o = newOrder('o', 1)
  try { apply(o, { id: 'x', to: 'shipped', at: 2 }); assert.fail('should throw') } catch (e) { assert.ok(e instanceof IllegalTransition); assert.deepEqual((e as IllegalTransition).allowed, ['paid', 'cancelled']) }
})
test('duplicate event ids are ignored (webhook retries)', () => {
  const o = apply(newOrder('o', 1), { id: 'pay_1', to: 'paid', at: 2 }); assert.equal(apply(o, { id: 'pay_1', to: 'paid', at: 3 }), o)
})
test('immutable', () => { const o = newOrder('o', 1); apply(o, { id: 'a', to: 'paid', at: 2 }); assert.equal(o.status, 'pending'); assert.equal(o.events.length, 1) })
test('final states have no exits', () => { assert.ok(isFinal('cancelled') && isFinal('refunded')); assert.deepEqual(allowed('refunded'), []) })
test('refund after delivery allowed, cancel after shipping not', () => {
  let o = newOrder('o', 1); for (const [i, s] of (['paid', 'packed', 'shipped'] as const).entries()) o = apply(o, { id: `e${i}`, to: s, at: i })
  assert.throws(() => apply(o, { id: 'c', to: 'cancelled', at: 9 }), IllegalTransition)
  o = apply(o, { id: 'd', to: 'delivered', at: 9 }); assert.equal(apply(o, { id: 'r', to: 'refunded', at: 10 }).status, 'refunded')
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["order", "status", "timeline", "state-machine", "ecommerce", "workflow", "webhook", "tracking"],
   avoid="Free-form status strings written from many places; applying webhook events twice."),

RC("ts-inventory-stock-tracking", "Stock tracking with a movement ledger and reservations that expire (TypeScript)", "typescript",
   "A shop or ticketing app must know how many units are really available, hold stock during checkout and explain every change.",
   "Append-only ledger of movements (receive, sale, adjust, return) so on-hand is a sum, reservations with TTL subtract from available, reserve is all-or-nothing across a multi-item cart, low-stock alerts at a reorder point; the store is an interface so a database transaction can replace the in-memory one.",
   {"stock.ts": r'''export interface Movement { sku: string; delta: number; reason: 'receive' | 'sale' | 'adjust' | 'return'; at: number; ref?: string }
interface Hold { id: string; items: Record<string, number>; expires: number }

export class Stock {
  private ledger: Movement[] = []
  private holds = new Map<string, Hold>()
  private now: () => number
  constructor(now: () => number = Date.now) { this.now = now }

  record(m: Movement) { if (!Number.isInteger(m.delta) || m.delta === 0) throw new RangeError('delta must be a non-zero integer'); this.ledger.push(m) }
  onHand(sku: string) { return this.ledger.reduce((n, m) => (m.sku === sku ? n + m.delta : n), 0) }
  private held(sku: string) { let n = 0; for (const h of this.holds.values()) if (h.expires > this.now()) n += h.items[sku] ?? 0; return n }
  available(sku: string) { return this.onHand(sku) - this.held(sku) }

  /** All-or-nothing: either every line is reserved or none is. Idempotent per hold id. */
  reserve(id: string, items: Record<string, number>, ttlMs = 10 * 60_000): boolean {
    if (this.holds.has(id) && this.holds.get(id)!.expires > this.now()) return true
    for (const [sku, qty] of Object.entries(items)) if (!Number.isInteger(qty) || qty < 1 || this.available(sku) < qty) return false
    this.holds.set(id, { id, items: { ...items }, expires: this.now() + ttlMs })
    return true
  }
  /** Turns the hold into sales. Fails (false) when the hold expired - the cart must be re-validated. */
  commit(id: string, orderRef: string): boolean {
    const h = this.holds.get(id)
    if (!h || h.expires <= this.now()) { this.holds.delete(id); return false }
    for (const [sku, qty] of Object.entries(h.items)) this.record({ sku, delta: -qty, reason: 'sale', at: this.now(), ref: orderRef })
    this.holds.delete(id); return true
  }
  release(id: string) { return this.holds.delete(id) }
  lowStock(reorderPoint: Record<string, number>) { return Object.keys(reorderPoint).filter((s) => this.available(s) <= reorderPoint[s]) }
  history(sku: string) { return this.ledger.filter((m) => m.sku === sku) }
}
''', "stock.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Stock } from './stock.ts'

function mk() { let t = 0; const s = new Stock(() => t); return { s, tick: (ms: number) => { t += ms } } }

test('on hand is the sum of movements', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 10, reason: 'receive', at: 0 }); s.record({ sku: 'a', delta: -3, reason: 'sale', at: 1 }); s.record({ sku: 'a', delta: 1, reason: 'return', at: 2 })
  assert.equal(s.onHand('a'), 8); assert.equal(s.history('a').length, 3)
})
test('reservations reduce availability and cannot oversell', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 5, reason: 'receive', at: 0 })
  assert.equal(s.reserve('h1', { a: 4 }), true); assert.equal(s.reserve('h2', { a: 2 }), false); assert.equal(s.available('a'), 1)
})
test('multi-item reserve is all-or-nothing', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 5, reason: 'receive', at: 0 }); s.record({ sku: 'b', delta: 1, reason: 'receive', at: 0 })
  assert.equal(s.reserve('h', { a: 2, b: 2 }), false); assert.equal(s.available('a'), 5)
})
test('expiry returns stock; commit after expiry fails', () => {
  const { s, tick } = mk(); s.record({ sku: 'a', delta: 1, reason: 'receive', at: 0 }); s.reserve('h', { a: 1 }, 1000)
  tick(1001); assert.equal(s.available('a'), 1); assert.equal(s.commit('h', 'o1'), false)
})
test('commit writes sale movements once', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 3, reason: 'receive', at: 0 }); s.reserve('h', { a: 2 })
  assert.equal(s.commit('h', 'o1'), true); assert.equal(s.commit('h', 'o1'), false); assert.equal(s.onHand('a'), 1); assert.equal(s.history('a').at(-1)!.ref, 'o1')
})
test('reserve is idempotent per id and release frees stock', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 3, reason: 'receive', at: 0 }); s.reserve('h', { a: 2 }); s.reserve('h', { a: 2 })
  assert.equal(s.available('a'), 1); s.release('h'); assert.equal(s.available('a'), 3)
})
test('low-stock alerts and validation', () => {
  const { s } = mk(); s.record({ sku: 'a', delta: 3, reason: 'receive', at: 0 }); assert.deepEqual(s.lowStock({ a: 5, b: 0 }), ['a', 'b'])
  assert.throws(() => s.record({ sku: 'a', delta: 0, reason: 'adjust', at: 0 })); assert.equal(s.reserve('x', { a: 0 }), false)
})
'''}, NODE_TS, assumptions=TSA + ["In-memory and single process: in a database do the check-and-reserve as one conditional UPDATE or SELECT ... FOR UPDATE"], compat={"node": ">=22.18"},
   tags=["inventory", "stock", "ecommerce", "reservation", "ledger", "shop", "tickets", "warehouse"],
   avoid="Storing a mutable 'quantity' with no history; check-then-decrement across two statements."),

RC("ts-subscription-proration", "Subscription proration, trials and next-invoice preview in integer cents (TypeScript)", "typescript",
   "A SaaS needs to preview and charge the right amount when customers change plan mid-cycle, start trials or cancel.",
   "Whole-day proration with symmetric rounding, trial end handling (no charge until trial_end), upgrade charges the difference immediately while downgrade becomes a credit on the next invoice, previewInvoice() builds the line items the UI shows before confirmation.",
   {"billing.ts": r'''const DAY = 86_400_000
export interface Plan { id: string; cents: number }
export interface Period { start: number; end: number }          // epoch ms, [start, end)

const days = (a: number, b: number) => Math.round((b - a) / DAY)
const roundHalfAway = (n: number, d: number) => (n < 0 ? -Math.floor((2 * -n + d) / (2 * d)) : Math.floor((2 * n + d) / (2 * d)))

export function prorate(oldP: Plan, newP: Plan, period: Period, changeAt: number): number {
  if (!(changeAt >= period.start && changeAt < period.end)) throw new RangeError('change is outside the current period')
  const total = days(period.start, period.end), left = days(changeAt, period.end)
  return roundHalfAway(newP.cents * left, total) - roundHalfAway(oldP.cents * left, total)     // negative = credit
}

export interface InvoiceLine { desc: string; cents: number }
export function previewChange(oldP: Plan, newP: Plan, period: Period, changeAt: number) {
  const net = prorate(oldP, newP, period, changeAt)
  const lines: InvoiceLine[] = [{ desc: `Switch ${oldP.id} -> ${newP.id} (prorated)`, cents: net }]
  // upgrades are charged now; downgrades never refund to the card automatically - they become account credit
  return { dueNow: Math.max(0, net), creditToAccount: Math.max(0, -net), lines }
}

export function firstCharge(plan: Plan, start: number, trialDays: number, now: number) {
  const trialEnd = start + trialDays * DAY
  return { dueNow: now < trialEnd ? 0 : plan.cents, trialEnd, inTrial: now < trialEnd }
}
''', "billing.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { prorate, previewChange, firstCharge } from './billing.ts'

const D = 86_400_000, P = { start: 0, end: 30 * D }
const free = { id: 'basic', cents: 1000 }, pro = { id: 'pro', cents: 3000 }

test('upgrade halfway charges half the difference', () => assert.equal(prorate(free, pro, P, 15 * D), 1000))
test('downgrade gives a credit', () => assert.equal(prorate(pro, free, P, 15 * D), -1000))
test('day one is the full difference, same plan is zero', () => { assert.equal(prorate(free, pro, P, 0), 2000); assert.equal(prorate(pro, pro, P, 7 * D), 0) })
test('rounding is symmetric', () => assert.equal(prorate({ id: 'a', cents: 1000 }, { id: 'b', cents: 1001 }, P, 10 * D) + prorate({ id: 'b', cents: 1001 }, { id: 'a', cents: 1000 }, P, 10 * D), 0))
test('outside the period is rejected', () => { assert.throws(() => prorate(free, pro, P, 30 * D), RangeError); assert.throws(() => prorate(free, pro, P, -1), RangeError) })
test('preview: upgrade is due now, downgrade is account credit', () => {
  assert.deepEqual(previewChange(free, pro, P, 15 * D).dueNow, 1000); const d = previewChange(pro, free, P, 15 * D); assert.deepEqual([d.dueNow, d.creditToAccount], [0, 1000])
})
test('trial', () => { assert.equal(firstCharge(pro, 0, 14, 10 * D).dueNow, 0); assert.equal(firstCharge(pro, 0, 14, 14 * D).dueNow, 3000); assert.equal(firstCharge(pro, 0, 14, 0).inTrial, true) })
'''}, NODE_TS, assumptions=TSA + ["Calendar-day proration; Stripe prorates by the second - match your provider when you use it", "Tax is applied after proration"], compat={"node": ">=22.18"},
   tags=["subscription", "billing", "saas", "proration", "trial", "stripe", "plans", "pricing", "money"],
   avoid="Float maths for money; re-charging the whole price on plan change; refunding downgrades to the card automatically."),

RC("ts-product-filters-url", "Product listing filters: facets, price range, sort and shareable URL state (TypeScript)", "typescript",
   "A catalogue page needs category/brand facets with counts, price range, sort order, and filters that live in the URL.",
   "Pure filter function (AND across facet groups, OR within a group), facet counts computed against the other active filters, price range validation, deterministic sort with tiebreak on id, and parse/serialize of the filter state to query params that ignores unknown or malformed values.",
   {"filters.ts": r'''export interface Product { id: string; title: string; brand: string; category: string; cents: number; rating: number }
export interface State { brand: string[]; category: string[]; min?: number; max?: number; sort: 'relevance' | 'price-asc' | 'price-desc' | 'rating'; q: string }
export const empty = (): State => ({ brand: [], category: [], sort: 'relevance', q: '' })

export function apply(items: readonly Product[], s: State): Product[] {
  const q = s.q.trim().toLowerCase()
  const out = items.filter((p) =>
    (!s.brand.length || s.brand.includes(p.brand)) && (!s.category.length || s.category.includes(p.category)) &&
    (s.min == null || p.cents >= s.min) && (s.max == null || p.cents <= s.max) && (!q || p.title.toLowerCase().includes(q)))
  const by = { 'price-asc': (a: Product, b: Product) => a.cents - b.cents, 'price-desc': (a: Product, b: Product) => b.cents - a.cents, rating: (a: Product, b: Product) => b.rating - a.rating, relevance: () => 0 }[s.sort]
  return out.slice().sort((a, b) => by(a, b) || (a.id < b.id ? -1 : 1))
}

/** Counts for one facet group, computed with every OTHER filter applied (so numbers say what you'd get by ticking it). */
export function facetCounts(items: readonly Product[], s: State, group: 'brand' | 'category'): Record<string, number> {
  const rest = apply(items, { ...s, [group]: [] })
  const counts: Record<string, number> = {}
  for (const p of rest) counts[p[group]] = (counts[p[group]] ?? 0) + 1
  return counts
}

const SORTS = new Set(['relevance', 'price-asc', 'price-desc', 'rating'])
export function toQuery(s: State): string {
  const u = new URLSearchParams()
  if (s.q) u.set('q', s.q); if (s.brand.length) u.set('brand', s.brand.join(',')); if (s.category.length) u.set('category', s.category.join(','))
  if (s.min != null) u.set('min', String(s.min)); if (s.max != null) u.set('max', String(s.max)); if (s.sort !== 'relevance') u.set('sort', s.sort)
  return u.toString()
}
export function fromQuery(qs: string): State {
  const u = new URLSearchParams(qs), s = empty()
  s.q = (u.get('q') ?? '').slice(0, 100)
  s.brand = (u.get('brand') ?? '').split(',').filter(Boolean).slice(0, 20); s.category = (u.get('category') ?? '').split(',').filter(Boolean).slice(0, 20)
  const num = (k: string) => { const v = u.get(k); return v != null && /^\d{1,9}$/.test(v) ? Number(v) : undefined }
  s.min = num('min'); s.max = num('max')
  if (s.min != null && s.max != null && s.min > s.max) [s.min, s.max] = [s.max, s.min]
  const sort = u.get('sort'); if (sort && SORTS.has(sort)) s.sort = sort as State['sort']
  return s
}
''', "filters.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { apply, facetCounts, toQuery, fromQuery, empty, type Product } from './filters.ts'

const P = (id: string, brand: string, category: string, cents: number, rating = 4): Product => ({ id, title: `${brand} ${id}`, brand, category, cents, rating })
const items = [P('1', 'acme', 'shoes', 5000), P('2', 'acme', 'hats', 1500, 5), P('3', 'zed', 'shoes', 9000, 3), P('4', 'zed', 'hats', 1500, 4)]

test('AND across groups, OR within', () => {
  assert.deepEqual(apply(items, { ...empty(), brand: ['acme', 'zed'], category: ['hats'] }).map((p) => p.id), ['2', '4'])
})
test('price range and search', () => {
  assert.deepEqual(apply(items, { ...empty(), min: 1500, max: 5000 }).map((p) => p.id), ['1', '2', '4']); assert.deepEqual(apply(items, { ...empty(), q: 'ZED' }).map((p) => p.id), ['3', '4'])
})
test('sorting is deterministic with ties', () => {
  assert.deepEqual(apply(items, { ...empty(), sort: 'price-asc' }).map((p) => p.id), ['2', '4', '1', '3']); assert.deepEqual(apply(items, { ...empty(), sort: 'rating' }).map((p) => p.id), ['2', '1', '4', '3'])
})
test('facet counts ignore their own group', () => {
  const s = { ...empty(), brand: ['acme'], category: ['shoes'] }
  assert.deepEqual(facetCounts(items, s, 'brand'), { acme: 1, zed: 1 }); assert.deepEqual(facetCounts(items, s, 'category'), { shoes: 1, hats: 1 })
})
test('query string round trip', () => {
  const s = { ...empty(), brand: ['acme'], min: 100, max: 900, sort: 'price-desc' as const, q: 'red shoe' }; assert.deepEqual(fromQuery(toQuery(s)), s); assert.equal(toQuery(empty()), '')
})
test('hostile or malformed query values are ignored', () => {
  const s = fromQuery('min=abc&max=-5&sort=;drop&brand=,,a&q=' + 'x'.repeat(500)); assert.equal(s.min, undefined); assert.equal(s.max, undefined); assert.equal(s.sort, 'relevance'); assert.deepEqual(s.brand, ['a']); assert.equal(s.q.length, 100)
  const swap = fromQuery('min=900&max=100'); assert.deepEqual([swap.min, swap.max], [100, 900])
})
'''}, NODE_TS, assumptions=TSA + ["Client-side filtering; for large catalogues run the same state through your search backend"], compat={"node": ">=22.18"},
   tags=["product", "filters", "facets", "catalog", "ecommerce", "search", "url-state", "listing", "shop"],
   avoid="Facet counts that ignore the other filters (or include their own group); trusting query-string numbers."),

# ------------------------------------------------------------------ mobile / desktop / docs
RC("ts-deep-link-router", "Deep link / universal link parsing with allow-listed routes and safe redirects (TypeScript)", "typescript",
   "A mobile or web app opens `myapp://item/42` and `https://app.example/item/42?ref=x` - and must not trust the link.",
   "Normalise custom-scheme and https links to one route table, typed param extraction (ints, slugs), host/scheme allow-list, unknown routes fall back to home, `next=` redirect targets restricted to same-app paths (no `//evil.com`, no `javascript:`), and a link builder that round-trips.",
   {"links.ts": r'''export type Route = { name: 'home' } | { name: 'item'; id: number } | { name: 'profile'; handle: string } | { name: 'invite'; token: string }
const HOSTS = new Set(['app.example.com'])
const SCHEMES = new Set(['myapp:', 'https:'])

const ROUTES: [RegExp, (m: RegExpMatchArray) => Route][] = [
  [/^\/item\/(\d{1,12})$/, (m) => ({ name: 'item', id: Number(m[1]) })],
  [/^\/u\/([a-z0-9_]{3,20})$/, (m) => ({ name: 'profile', handle: m[1] })],
  [/^\/invite\/([A-Za-z0-9_-]{16,64})$/, (m) => ({ name: 'invite', token: m[1] })],
]

export function parseLink(raw: string): Route {
  let u: URL
  try { u = new URL(raw) } catch { return { name: 'home' } }
  if (!SCHEMES.has(u.protocol)) return { name: 'home' }
  let path: string
  if (u.protocol === 'https:') { if (!HOSTS.has(u.hostname)) return { name: 'home' }; path = u.pathname }
  else path = '/' + u.hostname + u.pathname                                  // myapp://item/42 -> host=item, path=/42
  path = path.replace(/\/+$/, '') || '/'
  for (const [re, make] of ROUTES) { const m = path.match(re); if (m) return make(m) }
  return { name: 'home' }
}

export function buildLink(r: Route, universal = true): string {
  const p = r.name === 'item' ? `/item/${r.id}` : r.name === 'profile' ? `/u/${r.handle}` : r.name === 'invite' ? `/invite/${r.token}` : '/'
  return universal ? `https://app.example.com${p}` : `myapp:/${p}`
}

/** Post-login `next` targets: same-app absolute paths only. */
export function safeNext(next: string | null | undefined, fallback = '/'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\') || /[\u0000-\u001f]/.test(next)) return fallback
  try { const u = new URL(next, 'https://x.invalid'); return u.origin === 'https://x.invalid' ? u.pathname + u.search + u.hash : fallback } catch { return fallback }
}
''', "links.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseLink, buildLink, safeNext } from './links.ts'

test('custom scheme and universal links resolve to the same route', () => {
  assert.deepEqual(parseLink('myapp://item/42'), { name: 'item', id: 42 }); assert.deepEqual(parseLink('https://app.example.com/item/42?ref=x'), { name: 'item', id: 42 })
  assert.deepEqual(parseLink('myapp://u/ada_l'), { name: 'profile', handle: 'ada_l' }); assert.deepEqual(parseLink('https://app.example.com/item/42/'), { name: 'item', id: 42 })
})
test('unknown hosts, schemes and routes fall back to home', () => {
  for (const bad of ['https://evil.com/item/42', 'javascript:alert(1)', 'ftp://app.example.com/item/1', 'myapp://nothing', 'not a url', 'myapp://item/abc', 'https://app.example.com.evil.com/item/1']) assert.deepEqual(parseLink(bad), { name: 'home' }, bad)
})
test('invite tokens are length and charset checked', () => {
  assert.equal(parseLink('myapp://invite/short').name, 'home'); assert.equal(parseLink('myapp://invite/' + 'a'.repeat(20)).name, 'invite')
})
test('build then parse round trips', () => {
  for (const r of [{ name: 'item', id: 7 }, { name: 'profile', handle: 'abc' }, { name: 'invite', token: 'x'.repeat(24) }] as const) for (const uni of [true, false]) assert.deepEqual(parseLink(buildLink(r, uni)), r)
})
test('safeNext blocks open redirects', () => {
  assert.equal(safeNext('/settings?tab=2#x'), '/settings?tab=2#x')
  for (const bad of ['//evil.com', 'https://evil.com', '/\\evil.com', 'javascript:alert(1)', '', null, undefined, '/ok\n//evil.com']) assert.equal(safeNext(bad as any), '/', String(bad))
})
'''}, NODE_TS, assumptions=TSA + ["Native side (apple-app-site-association / assetlinks.json / intent filters) is configured separately"], compat={"node": ">=22.18"},
   tags=["deep-link", "universal-link", "mobile", "react-native", "expo", "routing", "open-redirect", "security", "android", "ios"],
   avoid="Passing link params straight to navigation or SQL; redirecting to any `next=` URL."),

RC("ts-token-store-refresh", "Auth token storage with single-flight refresh and expiry skew (TypeScript)", "typescript",
   "A mobile/desktop client keeps tokens in secure storage and must refresh once even when ten requests hit an expired token.",
   "Storage is an injected interface (Keychain/Keystore/safeStorage/keytar), access tokens refreshed `skew` seconds early, concurrent callers share one in-flight refresh promise, a refresh failure clears the stored tokens and signals logout, refresh-token rotation persisted before use.",
   {"tokens.ts": r'''export interface SecureStore { get(key: string): Promise<string | null>; set(key: string, value: string): Promise<void>; delete(key: string): Promise<void> }
export interface Tokens { access: string; refresh: string; expiresAt: number }          // expiresAt in epoch ms
export type Refresher = (refresh: string) => Promise<Tokens>
const KEY = 'auth.tokens'

export class AuthExpired extends Error {}

export class TokenManager {
  private inflight: Promise<Tokens> | null = null
  private store: SecureStore
  private refresher: Refresher
  private now: () => number
  private skewMs: number
  constructor(store: SecureStore, refresher: Refresher, now: () => number = Date.now, skewMs = 30_000) { this.store = store; this.refresher = refresher; this.now = now; this.skewMs = skewMs }

  async save(t: Tokens) { await this.store.set(KEY, JSON.stringify(t)) }
  async clear() { await this.store.delete(KEY) }
  private async load(): Promise<Tokens | null> {
    const raw = await this.store.get(KEY); if (!raw) return null
    try { const t = JSON.parse(raw); return typeof t.access === 'string' && typeof t.refresh === 'string' && Number.isFinite(t.expiresAt) ? t : null } catch { return null }
  }

  /** Returns a valid access token, refreshing at most once for any number of concurrent callers. */
  async accessToken(): Promise<string> {
    const t = await this.load()
    if (!t) throw new AuthExpired('not signed in')
    if (t.expiresAt - this.skewMs > this.now()) return t.access
    this.inflight ??= this.refreshNow(t).finally(() => { this.inflight = null })
    return (await this.inflight).access
  }

  private async refreshNow(old: Tokens): Promise<Tokens> {
    try {
      const fresh = await this.refresher(old.refresh)
      await this.save(fresh)                         // persist the ROTATED refresh token before anyone uses the new access token
      return fresh
    } catch (e) {
      await this.clear()
      throw new AuthExpired('refresh failed: ' + (e instanceof Error ? e.message : String(e)))
    }
  }
}
''', "tokens.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { TokenManager, AuthExpired, type SecureStore } from './tokens.ts'

const mem = (): SecureStore & { m: Map<string, string> } => { const m = new Map<string, string>(); return { m, get: async (k) => m.get(k) ?? null, set: async (k, v) => void m.set(k, v), delete: async (k) => void m.delete(k) } }

test('valid token is returned without refreshing', async () => {
  const s = mem(); let calls = 0; const tm = new TokenManager(s, async () => { calls++; throw new Error('no') }, () => 1000)
  await tm.save({ access: 'A', refresh: 'R', expiresAt: 1_000_000 }); assert.equal(await tm.accessToken(), 'A'); assert.equal(calls, 0)
})
test('ten concurrent callers share one refresh (single flight)', async () => {
  const s = mem(); let calls = 0
  const tm = new TokenManager(s, async (r) => { calls++; await new Promise((r) => setTimeout(r, 10)); return { access: 'A2', refresh: 'R2', expiresAt: 9e12 } }, () => 1000)
  await tm.save({ access: 'A', refresh: 'R', expiresAt: 500 })
  const got = await Promise.all(Array.from({ length: 10 }, () => tm.accessToken()))
  assert.equal(calls, 1); assert.ok(got.every((g) => g === 'A2'))
  assert.equal(JSON.parse(s.m.get('auth.tokens')!).refresh, 'R2')               // rotated refresh token persisted
})
test('refreshes early using the skew', async () => {
  const s = mem(); let calls = 0; const tm = new TokenManager(s, async () => { calls++; return { access: 'B', refresh: 'R', expiresAt: 9e12 } }, () => 1000, 30_000)
  await tm.save({ access: 'A', refresh: 'R', expiresAt: 1000 + 20_000 }); assert.equal(await tm.accessToken(), 'B'); assert.equal(calls, 1)
})
test('failed refresh clears storage and signals logout', async () => {
  const s = mem(); const tm = new TokenManager(s, async () => { throw new Error('invalid_grant') }, () => 1000)
  await tm.save({ access: 'A', refresh: 'R', expiresAt: 0 })
  await assert.rejects(tm.accessToken(), AuthExpired); assert.equal(s.m.size, 0); await assert.rejects(tm.accessToken(), /not signed in/)
})
test('corrupt storage is treated as signed out; a later refresh can run again', async () => {
  const s = mem(); s.m.set('auth.tokens', '{nope'); const tm = new TokenManager(s, async () => ({ access: 'C', refresh: 'R', expiresAt: 9e12 }), () => 1)
  await assert.rejects(tm.accessToken(), AuthExpired)
  await tm.save({ access: 'x', refresh: 'R', expiresAt: 0 }); assert.equal(await tm.accessToken(), 'C'); assert.equal(await tm.accessToken(), 'C')
})
'''}, NODE_TS, assumptions=TSA + ["Real secure storage (expo-secure-store, react-native-keychain, Electron safeStorage, keytar) implements SecureStore", "Never use AsyncStorage/localStorage for refresh tokens on mobile/desktop"], compat={"node": ">=22.18"},
   tags=["auth", "token", "secure-storage", "keychain", "refresh", "mobile", "react-native", "expo", "electron", "oauth", "security"],
   avoid="Each request refreshing independently (rotation then invalidates all but one); tokens in plain AsyncStorage/localStorage."),

RC("ts-push-payload-builder", "Push notification payloads: size limits, collapse keys, quiet hours (TypeScript)", "typescript",
   "Sending push through FCM/APNs/Web Push: payloads over the byte limit are rejected, duplicates annoy users, and 3am buzzes get uninstalls.",
   "Build a provider-neutral message then map to FCM / APNs / WebPush shapes; truncate the body by UTF-8 bytes (never mid-codepoint) to fit the limit, collapse key per conversation, quiet-hours check in the user's time zone via Intl, and per-user rate cap; no network calls.",
   {"push.ts": r'''export interface Msg { title: string; body: string; collapseKey?: string; data?: Record<string, string>; urgent?: boolean }
const enc = new TextEncoder()
export const bytes = (s: string) => enc.encode(s).length

export function truncateBytes(s: string, max: number): string {
  if (bytes(s) <= max) return s
  const ell = '…'; let out = ''
  for (const ch of s) { if (bytes(out + ch + ell) > max) break; out += ch }          // iterates code points, never splits a surrogate pair
  return out + ell
}

export const LIMITS = { fcm: 4096, apns: 4096, webpush: 3993 } as const

export function toFcm(m: Msg) {
  const body = truncateBytes(m.body, 1000)
  return { notification: { title: truncateBytes(m.title, 100), body }, data: m.data ?? {}, android: { priority: m.urgent ? 'high' : 'normal', collapse_key: m.collapseKey } }
}
export function toApns(m: Msg) {
  const payload = { aps: { alert: { title: truncateBytes(m.title, 100), body: truncateBytes(m.body, 1000) }, sound: 'default' }, ...(m.data ?? {}) }
  if (bytes(JSON.stringify(payload)) > LIMITS.apns) throw new RangeError('APNs payload exceeds 4096 bytes: send an id and fetch the content in the app')
  return { headers: { 'apns-priority': m.urgent ? '10' : '5', 'apns-collapse-id': m.collapseKey ?? '' }, payload }
}

/** true when `nowMs` falls inside quiet hours (e.g. 22:00-07:00, wraps midnight) in the user's IANA time zone. */
export function inQuietHours(nowMs: number, tz: string, startHour = 22, endHour = 7): boolean {
  const h = Number(new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hourCycle: 'h23', timeZone: tz }).format(nowMs))
  return startHour > endHour ? h >= startHour || h < endHour : h >= startHour && h < endHour
}

export class RateCap {
  private sent = new Map<string, number[]>()
  private max: number
  private windowMs: number
  constructor(max = 5, windowMs = 3_600_000) { this.max = max; this.windowMs = windowMs }
  allow(user: string, now: number): boolean {
    const hits = (this.sent.get(user) ?? []).filter((t) => t > now - this.windowMs)
    if (hits.length >= this.max) { this.sent.set(user, hits); return false }
    hits.push(now); this.sent.set(user, hits); return true
  }
}
''', "push.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { truncateBytes, bytes, toApns, toFcm, inQuietHours, RateCap } from './push.ts'

test('truncation respects UTF-8 bytes and never splits a character', () => {
  const t = truncateBytes('héllo 😀😀😀😀😀', 20); assert.ok(bytes(t) <= 20); assert.ok(t.endsWith('…')); assert.ok(!t.includes('�'))
  assert.equal(truncateBytes('short', 20), 'short')
})
test('apns refuses payloads that are too big, fcm truncates', () => {
  assert.throws(() => toApns({ title: 't', body: 'b', data: { blob: 'x'.repeat(5000) } }), RangeError)
  assert.ok(bytes(toFcm({ title: 't', body: 'é'.repeat(2000) }).notification.body) <= 1000)
})
test('collapse key and priority mapping', () => {
  const a = toApns({ title: 't', body: 'b', collapseKey: 'chat-1', urgent: true }); assert.equal(a.headers['apns-collapse-id'], 'chat-1'); assert.equal(a.headers['apns-priority'], '10')
  assert.equal(toFcm({ title: 't', body: 'b' }).android.priority, 'normal')
})
test('quiet hours wrap midnight and honour the time zone', () => {
  const t = Date.UTC(2026, 0, 15, 23, 30)                       // 23:30 UTC
  assert.equal(inQuietHours(t, 'UTC'), true); assert.equal(inQuietHours(t, 'Asia/Tokyo'), false)       // 08:30 in Tokyo
  assert.equal(inQuietHours(Date.UTC(2026, 0, 15, 12, 0), 'UTC'), false); assert.equal(inQuietHours(Date.UTC(2026, 0, 15, 6, 59), 'UTC'), true); assert.equal(inQuietHours(Date.UTC(2026, 0, 15, 7, 0), 'UTC'), false)
})
test('rate cap is per user and sliding', () => {
  const c = new RateCap(2, 1000); assert.ok(c.allow('a', 0) && c.allow('a', 10)); assert.equal(c.allow('a', 20), false); assert.ok(c.allow('b', 20)); assert.ok(c.allow('a', 1011))
})
'''}, NODE_TS, assumptions=TSA + ["Provider limits as of writing (FCM/APNs 4 KB); sending uses firebase-admin / node-apn / web-push"], compat={"node": ">=22.18"},
   tags=["push", "notifications", "fcm", "apns", "mobile", "web-push", "quiet-hours", "expo", "react-native"],
   avoid="Slicing strings by characters for byte limits; no collapse key (notification spam); pushing during the user's night."),

RC("ts-global-shortcuts", "Keyboard shortcut parser: accelerators, platform mapping, conflicts, typing guard (TypeScript)", "typescript",
   "An Electron/Tauri/web app needs configurable shortcuts like `CmdOrCtrl+Shift+K` that work on every OS, never fire while typing, and warn on conflicts.",
   "Parse accelerator strings into a canonical form (sorted modifiers, normalised key names, aliases), map CmdOrCtrl by platform, match KeyboardEvent-like objects, ignore events from inputs/textareas/contenteditable unless the combo uses a modifier, and detect duplicate bindings before registering with the OS.",
   {"shortcuts.ts": r'''export interface Combo { ctrl: boolean; alt: boolean; shift: boolean; meta: boolean; key: string }
const ALIAS: Record<string, string> = { esc: 'escape', return: 'enter', del: 'delete', space: ' ', plus: '+', up: 'arrowup', down: 'arrowdown', left: 'arrowleft', right: 'arrowright' }

export function parse(acc: string, platform: 'mac' | 'other' = 'other'): Combo {
  const c: Combo = { ctrl: false, alt: false, shift: false, meta: false, key: '' }
  const parts = acc.split(/\+(?!$)/).map((p) => p.trim()).filter(Boolean)
  for (const p of parts) {
    const k = p.toLowerCase()
    if (k === 'cmdorctrl' || k === 'commandorcontrol') platform === 'mac' ? (c.meta = true) : (c.ctrl = true)
    else if (k === 'ctrl' || k === 'control') c.ctrl = true
    else if (k === 'cmd' || k === 'command' || k === 'meta' || k === 'super') c.meta = true
    else if (k === 'alt' || k === 'option') c.alt = true
    else if (k === 'shift') c.shift = true
    else if (c.key) throw new Error(`two keys in "${acc}"`)
    else c.key = ALIAS[k] ?? k
  }
  if (!c.key) throw new Error(`no key in "${acc}"`)
  return c
}
export const canonical = (c: Combo) => [c.ctrl && 'Ctrl', c.alt && 'Alt', c.shift && 'Shift', c.meta && 'Meta', c.key.length === 1 ? c.key.toUpperCase() : c.key].filter(Boolean).join('+')

export interface Ev { key: string; ctrlKey: boolean; altKey: boolean; shiftKey: boolean; metaKey: boolean; target?: { tagName?: string; isContentEditable?: boolean } }
export function matches(c: Combo, e: Ev): boolean {
  return c.ctrl === e.ctrlKey && c.alt === e.altKey && c.shift === e.shiftKey && c.meta === e.metaKey && c.key === e.key.toLowerCase()
}
export function isTyping(e: Ev): boolean {
  const t = e.target?.tagName?.toUpperCase()
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT' || !!e.target?.isContentEditable
}

export class Registry {
  private map = new Map<string, { combo: Combo; run: () => void }>()
  private platform: 'mac' | 'other'
  constructor(platform: 'mac' | 'other' = 'other') { this.platform = platform }
  register(acc: string, run: () => void) {
    const combo = parse(acc, this.platform), id = canonical(combo)
    if (this.map.has(id)) throw new Error(`shortcut ${id} is already bound`)
    this.map.set(id, { combo, run })
  }
  dispatch(e: Ev): boolean {
    for (const { combo, run } of this.map.values()) {
      if (!matches(combo, e)) continue
      if (isTyping(e) && !(combo.ctrl || combo.meta || combo.alt)) return false          // plain keys never hijack typing
      run(); return true
    }
    return false
  }
}
''', "shortcuts.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, canonical, matches, Registry, type Ev } from './shortcuts.ts'

const ev = (key: string, o: Partial<Ev> = {}): Ev => ({ key, ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, ...o })

test('CmdOrCtrl maps by platform', () => {
  assert.equal(canonical(parse('CmdOrCtrl+Shift+K', 'mac')), 'Shift+Meta+K'); assert.equal(canonical(parse('CmdOrCtrl+Shift+K', 'other')), 'Ctrl+Shift+K')
})
test('aliases and canonical form ignore order and case', () => {
  assert.equal(canonical(parse('shift+ctrl+esc')), canonical(parse('Control+Shift+Escape'))); assert.equal(parse('Ctrl+Space').key, ' '); assert.equal(parse('Ctrl++').key, '+')
})
test('invalid accelerators throw', () => { assert.throws(() => parse('Ctrl+Shift')); assert.throws(() => parse('Ctrl+A+B')); assert.throws(() => parse('')) })
test('matching is exact on modifiers', () => {
  const c = parse('Ctrl+K'); assert.ok(matches(c, ev('k', { ctrlKey: true }))); assert.ok(matches(c, ev('K', { ctrlKey: true }))); assert.ok(!matches(c, ev('k', { ctrlKey: true, shiftKey: true }))); assert.ok(!matches(c, ev('k')))
})
test('duplicates are rejected before registering with the OS', () => {
  const r = new Registry(); r.register('Ctrl+K', () => {}); assert.throws(() => r.register('control+k', () => {}), /already bound/)
})
test('plain-key shortcuts never fire while typing; modified ones do', () => {
  const r = new Registry(); const hits: string[] = []; r.register('/', () => hits.push('search')); r.register('Ctrl+Enter', () => hits.push('send'))
  const input = { tagName: 'INPUT' }
  assert.equal(r.dispatch(ev('/', { target: input })), false); assert.equal(r.dispatch(ev('/', { target: { tagName: 'DIV' } })), true)
  assert.equal(r.dispatch(ev('Enter', { ctrlKey: true, target: input })), true); assert.deepEqual(hits, ['search', 'send'])
  assert.equal(r.dispatch(ev('x', { target: { isContentEditable: true } })), false)
})
'''}, NODE_TS, assumptions=TSA + ["Registering OS-wide hotkeys uses Electron globalShortcut / Tauri global-shortcut; this is the parsing/matching layer"], compat={"node": ">=22.18"},
   tags=["shortcuts", "hotkeys", "keyboard", "electron", "tauri", "desktop", "accelerator", "accessibility", "command-palette"],
   avoid="Plain-letter shortcuts firing inside text fields; platform-specific strings stored in config."),

RC("ts-seo-meta-og", "SEO head tags: title, canonical, Open Graph, Twitter cards and JSON-LD (TypeScript)", "typescript",
   "A docs/blog/marketing site needs correct `<head>` tags on every page without XSS or duplicate/missing values.",
   "Build the tag list from page data: trimmed title with site suffix and length guard, description truncation on word boundary, absolute canonical (strip tracking params), OG + Twitter fields with sensible fallbacks, JSON-LD Article with `<` escaped so it cannot close the script tag, and HTML attribute escaping for every value.",
   {"seo.ts": r'''export interface Page { title: string; description?: string; path: string; image?: string; type?: 'website' | 'article'; published?: string; author?: string }
export interface Site { name: string; origin: string; defaultImage: string; twitter?: string }

export const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const TRACKING = /^(utm_|fbclid$|gclid$|mc_)/

export function truncateWords(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim(); if (t.length <= max) return t
  const cut = t.slice(0, max - 1); const i = cut.lastIndexOf(' ')
  return (i > max * 0.6 ? cut.slice(0, i) : cut).replace(/[\s,;:.-]+$/, '') + '…'
}
export function canonicalUrl(site: Site, path: string): string {
  const u = new URL(path, site.origin)
  for (const k of [...u.searchParams.keys()]) if (TRACKING.test(k)) u.searchParams.delete(k)
  u.hash = ''; return u.origin === new URL(site.origin).origin ? u.toString() : site.origin + '/'
}

export function jsonLd(site: Site, p: Page): string {
  const data = { '@context': 'https://schema.org', '@type': p.type === 'article' ? 'Article' : 'WebPage', headline: p.title, description: p.description, url: canonicalUrl(site, p.path), ...(p.published ? { datePublished: p.published } : {}), ...(p.author ? { author: { '@type': 'Person', name: p.author } } : {}), publisher: { '@type': 'Organization', name: site.name } }
  return JSON.stringify(data).replace(/</g, '\\u003c')                        // "</script>" inside a value cannot end the block
}

export function headTags(site: Site, p: Page): string[] {
  const title = p.path === '/' ? site.name : `${truncateWords(p.title, 60)} | ${site.name}`
  const desc = truncateWords(p.description ?? '', 160), url = canonicalUrl(site, p.path), img = new URL(p.image ?? site.defaultImage, site.origin).toString()
  const meta = (k: string, v: string, attr = 'name') => `<meta ${attr}="${k}" content="${esc(v)}">`
  const tags = [`<title>${esc(title)}</title>`, `<link rel="canonical" href="${esc(url)}">`]
  if (desc) tags.push(meta('description', desc))
  tags.push(meta('og:title', title, 'property'), meta('og:type', p.type ?? 'website', 'property'), meta('og:url', url, 'property'), meta('og:image', img, 'property'), meta('og:site_name', site.name, 'property'))
  if (desc) tags.push(meta('og:description', desc, 'property'))
  tags.push(meta('twitter:card', 'summary_large_image'), meta('twitter:title', title), meta('twitter:image', img))
  if (site.twitter) tags.push(meta('twitter:site', site.twitter))
  tags.push(`<script type="application/ld+json">${jsonLd(site, { ...p, description: desc || undefined })}</script>`)
  return tags
}
''', "seo.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { headTags, canonicalUrl, truncateWords, jsonLd, esc } from './seo.ts'

const site = { name: 'Acme Docs', origin: 'https://docs.acme.dev', defaultImage: '/og.png', twitter: '@acme' }

test('canonical drops tracking params and fragments, keeps real ones', () => {
  assert.equal(canonicalUrl(site, '/a?utm_source=x&page=2&fbclid=1#top'), 'https://docs.acme.dev/a?page=2')
  assert.equal(canonicalUrl(site, 'https://evil.com/x'), 'https://docs.acme.dev/')
})
test('description truncates on a word boundary with an ellipsis', () => {
  const t = truncateWords('word '.repeat(100), 50); assert.ok(t.length <= 50 && t.endsWith('…') && !t.includes('wor…')); assert.equal(truncateWords('short text', 50), 'short text')
})
test('all the expected tags exist with absolute urls', () => {
  const html = headTags(site, { title: 'Install guide', description: 'How to install.', path: '/install', type: 'article', published: '2026-01-02' }).join('\n')
  for (const needle of ['<title>Install guide | Acme Docs</title>', 'rel="canonical" href="https://docs.acme.dev/install"', 'property="og:image" content="https://docs.acme.dev/og.png"', 'name="twitter:card"', 'name="twitter:site" content="@acme"', 'application/ld+json', '"@type":"Article"', 'datePublished']) assert.ok(html.includes(needle), needle)
})
test('home page uses the bare site name', () => assert.ok(headTags(site, { title: 'Home', path: '/' })[0].includes('<title>Acme Docs</title>')))
test('values are escaped everywhere, JSON-LD cannot break out of its script tag', () => {
  const p = { title: '"><script>alert(1)</script>', description: 'x" onload="y', path: '/a' }
  const html = headTags(site, p).join('\n'); assert.ok(!html.includes('<script>alert')); assert.ok(!html.includes('onload="y'))
  assert.ok(!jsonLd(site, { title: '</script><b>', path: '/' }).includes('</script>')); assert.equal(esc('<&">'), '&lt;&amp;&quot;&gt;')
})
test('no description means no description tags', () => assert.ok(!headTags(site, { title: 'T', path: '/t' }).join('').includes('name="description"')))
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["seo", "meta", "open-graph", "twitter-card", "json-ld", "canonical", "docs", "blog", "head", "astro", "nextjs"],
   avoid="Interpolating titles into HTML unescaped; relative og:image URLs; duplicate/missing canonical."),

RC("ts-contact-form-handler", "Contact form handler: validation, honeypot, rate limit, header-injection safe email payload (TypeScript)", "typescript",
   "A static site's contact form posts to a function that emails the owner: it must not become a spam relay or leak via header injection.",
   "Validate and length-limit fields, honeypot and minimum-fill-time checks that quietly succeed for bots, per-IP rate limit, strip CR/LF from anything that reaches an email header, reply-to only if the address validates, link-count spam heuristic, and return a typed result for the HTTP layer; sending is injected.",
   {"contact.ts": r'''export interface Submission { name?: string; email?: string; message?: string; website?: string; startedAt?: number }
export interface Mail { to: string; replyTo?: string; subject: string; text: string }
export type Result = { status: 200; mail?: Mail } | { status: 400; errors: Record<string, string> } | { status: 429 }

const noCrlf = (s: string) => s.replace(/[\r\n]+/g, ' ').trim()
const EMAIL = /^[^\s@,;<>]+@[^\s@,;<>.]+(\.[^\s@,;<>.]+)+$/

export class IpLimiter {
  private hits = new Map<string, number[]>()
  private max: number
  private windowMs: number
  constructor(max = 3, windowMs = 600_000) { this.max = max; this.windowMs = windowMs }
  take(ip: string, now: number) {
    const h = (this.hits.get(ip) ?? []).filter((t) => t > now - this.windowMs)
    if (h.length >= this.max) { this.hits.set(ip, h); return false }
    h.push(now); this.hits.set(ip, h); return true
  }
}

export function handle(s: Submission, ip: string, now: number, limiter: IpLimiter, owner: string): Result {
  if (!limiter.take(ip, now)) return { status: 429 }
  if (s.website) return { status: 200 }                                         // honeypot filled: pretend success, send nothing
  if (s.startedAt != null && now - s.startedAt < 2000) return { status: 200 }   // submitted faster than a human can type
  const errors: Record<string, string> = {}
  const name = noCrlf(s.name ?? ''), email = noCrlf(s.email ?? ''), message = (s.message ?? '').trim()
  if (name.length < 1 || name.length > 100) errors.name = 'Please enter your name (max 100 characters)'
  if (email && !EMAIL.test(email)) errors.email = 'That email address does not look right'
  if (message.length < 10 || message.length > 5000) errors.message = 'Message must be between 10 and 5000 characters'
  if (Object.keys(errors).length) return { status: 400, errors }
  if ((message.match(/https?:\/\//g) ?? []).length > 2) return { status: 200 }   // link-stuffed: drop silently
  return { status: 200, mail: { to: owner, replyTo: email || undefined, subject: `Contact form: ${noCrlf(name).slice(0, 60)}`, text: `From: ${name} <${email || 'no email'}>\n\n${message}` } }
}
''', "contact.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { handle, IpLimiter } from './contact.ts'

const ok = { name: 'Ada', email: 'ada@example.com', message: 'Hello, I would like to talk about a project.' }
const run = (s: any, ip = '1.1.1.1', now = 100_000, l = new IpLimiter()) => handle(s, ip, now, l, 'me@site.dev')

test('valid submission produces a mail', () => { const r: any = run(ok); assert.equal(r.status, 200); assert.equal(r.mail.replyTo, 'ada@example.com'); assert.equal(r.mail.to, 'me@site.dev') })
test('validation errors are per field', () => { const r: any = run({ name: '', email: 'bad', message: 'short' }); assert.equal(r.status, 400); assert.deepEqual(Object.keys(r.errors).sort(), ['email', 'message', 'name']) })
test('header injection attempts are flattened', () => {
  const r: any = run({ ...ok, name: 'Ada\r\nBcc: victim@x.com', email: 'a@b.co\r\nBcc: v@x.com' })
  assert.equal(r.status === 200 ? r.mail?.subject.includes('\n') : false, false)
  if (r.status === 400) assert.ok(r.errors.email); else assert.ok(!r.mail.subject.includes('\n'))
})
test('honeypot and too-fast submissions succeed silently without sending', () => {
  assert.deepEqual(run({ ...ok, website: 'http://spam' }), { status: 200 }); assert.deepEqual(run({ ...ok, startedAt: 99_500 }), { status: 200 })
})
test('rate limit per ip', () => { const l = new IpLimiter(2, 1000); assert.equal((run(ok, 'a', 0, l) as any).status, 200); run(ok, 'a', 1, l); assert.equal(run(ok, 'a', 2, l).status, 429); assert.equal(run(ok, 'b', 2, l).status, 200); assert.equal(run(ok, 'a', 5000, l).status, 200) })
test('link-stuffed messages are dropped', () => assert.deepEqual(run({ ...ok, message: 'see http://a.com http://b.com http://c.com for more details' }), { status: 200 }))
test('email is optional but validated when given', () => { assert.equal((run({ ...ok, email: '' }) as any).mail.replyTo, undefined); assert.equal(run({ ...ok, email: 'x' }).status, 400) })
'''}, NODE_TS, assumptions=TSA + ["Add a real CAPTCHA/Turnstile verification if spam persists (server-side token check)", "In-memory limiter: use KV/Redis on serverless"], compat={"node": ">=22.18"},
   tags=["contact-form", "email", "spam", "honeypot", "rate-limit", "serverless", "static-site", "security", "form"],
   avoid="Putting form fields into email headers unsanitised; telling bots they were detected."),

# ------------------------------------------------------------------ bots / games / security
RC("ts-reminder-scheduler", "Reminders: parse 'in 10m' / 'tomorrow 9am', due queue, snooze (TypeScript)", "typescript",
   "A chat bot or app lets users say `remind me in 2h to call mom` and must fire exactly once, survive restarts and parse sanely.",
   "Parse relative durations (s/m/h/d/w, words) and `today|tomorrow HH[:MM][am|pm]` in the user's UTC offset, reject past/absurd times, store reminders sorted by due time, `due(now)` pops each exactly once, snooze re-queues, serialize/restore for persistence.",
   {"remind.ts": r'''export interface Reminder { id: number; user: string; text: string; at: number }

const UNIT: Record<string, number> = { s: 1e3, sec: 1e3, m: 6e4, min: 6e4, h: 36e5, hr: 36e5, hour: 36e5, d: 864e5, day: 864e5, w: 6048e5, week: 6048e5 }
const MAX = 365 * 864e5

/** -> epoch ms or null. `offsetMin` is the user's UTC offset in minutes (e.g. +120). */
export function parseWhen(input: string, now: number, offsetMin = 0): number | null {
  const s = input.trim().toLowerCase()
  const rel = s.match(/^(?:in\s+)?(\d{1,6})\s*(s|sec|m|min|h|hr|hours?|d|days?|w|weeks?)s?$/)
  if (rel) { const key = rel[2].replace(/s$/, ''); const unit = UNIT[key] ?? UNIT[key.replace(/s$/, '')]; if (!unit) return null; const at = now + Number(rel[1]) * unit; return at - now <= MAX ? at : null }
  const abs = s.match(/^(today|tomorrow)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$/)
  if (abs) {
    let h = Number(abs[2]); const m = Number(abs[3] ?? 0)
    if (abs[4]) { if (h < 1 || h > 12) return null; h = (h % 12) + (abs[4] === 'pm' ? 12 : 0) }
    if (h > 23 || m > 59) return null
    const local = new Date(now + offsetMin * 6e4)
    const day = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + (abs[1] === 'tomorrow' ? 1 : 0))
    const at = day + (h * 60 + m) * 6e4 - offsetMin * 6e4
    return at > now ? at : null
  }
  return null
}

export class Reminders {
  private list: Reminder[] = []
  private seq = 0
  add(user: string, text: string, at: number): Reminder { const r = { id: ++this.seq, user, text: text.slice(0, 500), at }; this.list.push(r); this.list.sort((a, b) => a.at - b.at || a.id - b.id); return r }
  due(now: number): Reminder[] { const k = this.list.findIndex((r) => r.at > now); const out = this.list.splice(0, k < 0 ? this.list.length : k); return out }
  snooze(r: Reminder, ms: number, now: number) { return this.add(r.user, r.text, now + ms) }
  cancel(id: number, user: string) { const i = this.list.findIndex((r) => r.id === id && r.user === user); return i >= 0 && !!this.list.splice(i, 1) }
  next() { return this.list[0]?.at ?? null }
  dump() { return JSON.stringify({ seq: this.seq, list: this.list }) }
  static restore(s: string) { const r = new Reminders(); const d = JSON.parse(s); r.seq = d.seq; r.list = d.list; return r }
}
''', "remind.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseWhen, Reminders } from './remind.ts'

const NOW = Date.UTC(2026, 0, 15, 10, 0, 0)

test('relative durations', () => {
  assert.equal(parseWhen('in 10m', NOW), NOW + 600_000); assert.equal(parseWhen('2 hours', NOW), NOW + 7_200_000); assert.equal(parseWhen('1d', NOW), NOW + 86_400_000); assert.equal(parseWhen('3 weeks', NOW), NOW + 3 * 604_800_000)
})
test('absolute times honour the user offset', () => {
  assert.equal(parseWhen('tomorrow 9am', NOW, 0), Date.UTC(2026, 0, 16, 9)); assert.equal(parseWhen('tomorrow 9am', NOW, 120), Date.UTC(2026, 0, 16, 7))
  assert.equal(parseWhen('today at 5:30pm', NOW, 0), Date.UTC(2026, 0, 15, 17, 30)); assert.equal(parseWhen('tomorrow 12am', NOW, 0), Date.UTC(2026, 0, 16, 0)); assert.equal(parseWhen('tomorrow 12pm', NOW, 0), Date.UTC(2026, 0, 16, 12))
})
test('past, absurd or malformed input is rejected', () => {
  for (const bad of ['today 9am', 'in 0x', 'soon', 'tomorrow 25:00', 'tomorrow 13pm', 'in 99999d', '']) assert.equal(parseWhen(bad, NOW), null, bad)
})
test('due pops each reminder exactly once, in order', () => {
  const r = new Reminders(); r.add('u', 'b', 200); r.add('u', 'a', 100); r.add('u', 'c', 300)
  assert.deepEqual(r.due(250).map((x) => x.text), ['a', 'b']); assert.deepEqual(r.due(250), []); assert.equal(r.next(), 300)
})
test('snooze, cancel (owner only) and persistence', () => {
  const r = new Reminders(); const a = r.add('u1', 'x', 100); assert.equal(r.cancel(a.id, 'u2'), false)
  const [fired] = r.due(100); const s = r.snooze(fired, 600, 150); assert.equal(s.at, 750)
  const r2 = Reminders.restore(r.dump()); assert.deepEqual(r2.due(1000).map((x) => x.text), ['x']); assert.equal(r2.add('u', 'y', 5).id, s.id + 1)
  assert.equal(r.cancel(s.id, 'u1'), true)
})
'''}, NODE_TS, assumptions=TSA + ["Natural-language dates beyond these forms need chrono-node", "Persisting `dump()` is the caller's job; a timer wakes at `next()`"], compat={"node": ">=22.18"},
   tags=["reminder", "scheduler", "bot", "discord", "telegram", "slack", "time", "parser", "schedule"],
   avoid="setTimeout per reminder with no persistence; ignoring the user's time zone."),

RC("ts-chat-webhook-relay", "Webhook receiver that verifies, formats and relays events to chat safely (TypeScript)", "typescript",
   "A bot receives GitHub/Stripe/CI webhooks and posts them to Discord/Slack: it must verify signatures and never let event text ping @everyone or break markdown.",
   "Verify `sha256=` HMAC over the raw body with timingSafeEqual, dedupe by delivery id, route by event type to short message formatters, neutralise mass mentions and mention syntax, escape markdown specials in untrusted fields, and cap message length.",
   {"relay.ts": r'''import { createHmac, timingSafeEqual } from 'node:crypto'

export function verify(secret: string, rawBody: string, header: string | undefined): boolean {
  if (!header?.startsWith('sha256=')) return false
  const want = createHmac('sha256', secret).update(rawBody).digest()
  let got: Buffer; try { got = Buffer.from(header.slice(7), 'hex') } catch { return false }
  return got.length === want.length && timingSafeEqual(got, want)
}

/** Untrusted text -> safe for Discord/Slack: no mass pings, no user/role mention syntax, markdown neutralised. */
export function sanitize(s: string, max = 200): string {
  const t = s.replace(/@(everyone|here|channel)/gi, '@​$1').replace(/<@[!&]?\d+>/g, '[mention]').replace(/<!(?:channel|here|everyone)[^>]*>/gi, '[mention]')
    .replace(/([*_`~|>\\\[\]])/g, '\\$1').replace(/\s+/g, ' ').trim()
  return t.length > max ? t.slice(0, max - 1) + '…' : t
}

export interface Delivery { id: string; event: string; payload: any }
export function format(d: Delivery): string | null {
  const p = d.payload ?? {}
  switch (d.event) {
    case 'push': { const n = p.commits?.length ?? 0; return `**${sanitize(p.pusher?.name ?? 'someone', 40)}** pushed ${n} commit${n === 1 ? '' : 's'} to \`${sanitize(String(p.ref ?? '').replace('refs/heads/', ''), 60)}\`` }
    case 'issues': return p.action === 'opened' ? `New issue #${Number(p.issue?.number)}: ${sanitize(p.issue?.title ?? '')}` : null
    case 'workflow_run': return p.workflow_run?.conclusion === 'failure' ? `CI failed: ${sanitize(p.workflow_run?.name ?? '')} on \`${sanitize(p.workflow_run?.head_branch ?? '', 60)}\`` : null
    case 'ping': return null
    default: return null                                 // unknown events are acknowledged but not posted
  }
}

export class Relay {
  private seen = new Set<string>()
  private secret: string
  constructor(secret: string) { this.secret = secret }
  /** -> {status, message?}; the HTTP layer returns status and posts message if present */
  handle(raw: string, sig: string | undefined, id: string | undefined, event: string | undefined): { status: number; message?: string } {
    if (!verify(this.secret, raw, sig)) return { status: 401 }
    if (!id || !event) return { status: 400 }
    if (this.seen.has(id)) return { status: 200 }                             // redelivery
    this.seen.add(id); if (this.seen.size > 5000) this.seen.delete(this.seen.values().next().value!)
    let payload: unknown; try { payload = JSON.parse(raw) } catch { return { status: 400 } }
    const message = format({ id, event, payload })
    return message ? { status: 200, message } : { status: 200 }
  }
}
''', "relay.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import { verify, sanitize, format, Relay } from './relay.ts'

const sign = (secret: string, body: string) => 'sha256=' + createHmac('sha256', secret).update(body).digest('hex')

test('signature verification', () => {
  const body = '{"a":1}'; assert.ok(verify('s', body, sign('s', body)))
  for (const bad of [sign('other', body), sign('s', body + ' '), undefined, 'sha256=', 'sha256=zz', 'sha1=abc']) assert.equal(verify('s', body, bad as any), false)
})
test('sanitize neutralises mass mentions, mention syntax and markdown', () => {
  const s = sanitize('hi @everyone <@123456> <@&99> <!channel> *bold* `code` [x](http://e)'); assert.ok(!/@everyone/.test(s)); assert.ok(!s.includes('<@')); assert.ok(!s.includes('<!channel')); assert.ok(s.includes('\\*bold\\*') && s.includes('\\`code\\`'))
  assert.equal(sanitize('x'.repeat(500), 50).length, 50)
})
test('formatters', () => {
  assert.equal(format({ id: '1', event: 'push', payload: { pusher: { name: 'ada' }, ref: 'refs/heads/main', commits: [{}, {}] } }), '**ada** pushed 2 commits to `main`')
  assert.equal(format({ id: '1', event: 'issues', payload: { action: 'opened', issue: { number: 7, title: 'Bug @everyone' } } })?.includes('@everyone'), false)
  assert.equal(format({ id: '1', event: 'issues', payload: { action: 'closed' } }), null); assert.equal(format({ id: '1', event: 'workflow_run', payload: { workflow_run: { conclusion: 'success' } } }), null)
  assert.equal(format({ id: '1', event: 'weird', payload: {} }), null)
})
test('relay: 401 for bad signature, dedupes redeliveries, 400 for bad json', () => {
  const r = new Relay('s'); const body = JSON.stringify({ action: 'opened', issue: { number: 1, title: 'T' } })
  assert.equal(r.handle(body, 'sha256=00', 'd1', 'issues').status, 401)
  const first = r.handle(body, sign('s', body), 'd1', 'issues'); assert.ok(first.message)
  assert.deepEqual(r.handle(body, sign('s', body), 'd1', 'issues'), { status: 200 })
  assert.equal(r.handle('{nope', sign('s', '{nope'), 'd2', 'issues').status, 400); assert.equal(r.handle(body, sign('s', body), undefined, 'issues').status, 400)
})
'''}, NODE_TS, assumptions=TSA + ["GitHub-style header; Slack signs `v0:ts:body`, Stripe `ts.body` (see the webhook-verification recipe)", "Posting to the chat API is out of scope"], compat={"node": ">=22.18"},
   tags=["webhook", "bot", "discord", "slack", "github", "relay", "hmac", "security", "ci", "notifications"],
   avoid="Echoing event text unsanitised into chat; verifying the signature against re-serialised JSON."),

RC("ts-game-input-mapper", "Game input mapper: keyboard, gamepad and touch to actions with deadzones and rebinding (TypeScript)", "typescript",
   "A browser/desktop game must support keyboard, gamepad and touch with one set of actions, edge detection and user rebinding.",
   "Raw device state is polled into an `InputState`; bindings map device inputs to named actions; analog sticks get a radial deadzone with rescaling; `pressed`/`released` are true for exactly one frame; rebinding replaces a binding and reports conflicts; everything is deterministic and testable without a browser.",
   {"input.ts": r'''export type Source = { kind: 'key'; code: string } | { kind: 'pad'; button: number } | { kind: 'axis'; axis: number; dir: 1 | -1 } | { kind: 'touch'; zone: string }
export interface Raw { keys: ReadonlySet<string>; padButtons: readonly boolean[]; padAxes: readonly number[]; touchZones: ReadonlySet<string> }

/** Radial deadzone with rescaling so the stick still reaches 1.0 and has no jump at the edge. */
export function deadzone(x: number, y: number, dz = 0.2): [number, number] {
  const mag = Math.hypot(x, y); if (mag < dz) return [0, 0]
  const scaled = Math.min(1, (mag - dz) / (1 - dz)); return [(x / mag) * scaled, (y / mag) * scaled]
}

export class Input {
  private bindings = new Map<string, Source[]>()
  private cur = new Map<string, number>()
  private prev = new Map<string, number>()
  bind(action: string, ...src: Source[]) { this.bindings.set(action, src) }

  /** Call once per frame (in the fixed update, not in event handlers). */
  poll(raw: Raw, axisDeadzone = 0.2) {
    this.prev = this.cur; this.cur = new Map()
    const [lx, ly] = deadzone(raw.padAxes[0] ?? 0, raw.padAxes[1] ?? 0, axisDeadzone); const stick = [lx, ly]
    for (const [action, sources] of this.bindings) {
      let v = 0
      for (const s of sources) {
        if (s.kind === 'key' && raw.keys.has(s.code)) v = Math.max(v, 1)
        else if (s.kind === 'pad' && raw.padButtons[s.button]) v = Math.max(v, 1)
        else if (s.kind === 'axis') v = Math.max(v, Math.max(0, (stick[s.axis] ?? 0) * s.dir))
        else if (s.kind === 'touch' && raw.touchZones.has(s.zone)) v = Math.max(v, 1)
      }
      this.cur.set(action, v)
    }
  }
  value(a: string) { return this.cur.get(a) ?? 0 }
  down(a: string) { return this.value(a) > 0 }
  pressed(a: string) { return this.down(a) && !((this.prev.get(a) ?? 0) > 0) }
  released(a: string) { return !this.down(a) && (this.prev.get(a) ?? 0) > 0 }

  /** Returns the actions that already use this source so the UI can ask to swap. */
  conflicts(src: Source, except?: string) {
    const key = JSON.stringify(src); const out: string[] = []
    for (const [a, list] of this.bindings) if (a !== except && list.some((s) => JSON.stringify(s) === key)) out.push(a)
    return out
  }
  rebind(action: string, index: number, src: Source) { const l = [...(this.bindings.get(action) ?? [])]; l[index] = src; this.bindings.set(action, l) }
}
''', "input.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Input, deadzone, type Raw } from './input.ts'

const raw = (o: Partial<Raw> = {}): Raw => ({ keys: new Set(), padButtons: [], padAxes: [], touchZones: new Set(), ...o })
const make = () => { const i = new Input(); i.bind('jump', { kind: 'key', code: 'Space' }, { kind: 'pad', button: 0 }, { kind: 'touch', zone: 'right' }); i.bind('right', { kind: 'key', code: 'KeyD' }, { kind: 'axis', axis: 0, dir: 1 }); return i }

test('any device triggers the same action', () => {
  for (const r of [raw({ keys: new Set(['Space']) }), raw({ padButtons: [true] }), raw({ touchZones: new Set(['right']) })]) { const i = make(); i.poll(r); assert.ok(i.down('jump')) }
})
test('pressed and released fire for exactly one frame', () => {
  const i = make(); i.poll(raw()); assert.ok(!i.pressed('jump'))
  i.poll(raw({ keys: new Set(['Space']) })); assert.ok(i.pressed('jump')); i.poll(raw({ keys: new Set(['Space']) })); assert.ok(!i.pressed('jump') && i.down('jump'))
  i.poll(raw()); assert.ok(i.released('jump')); i.poll(raw()); assert.ok(!i.released('jump'))
})
test('deadzone ignores drift and rescales to full range', () => {
  assert.deepEqual(deadzone(0.1, 0.1, 0.2), [0, 0]); const [x] = deadzone(1, 0, 0.2); assert.equal(x, 1); const [h] = deadzone(0.6, 0, 0.2); assert.ok(Math.abs(h - 0.5) < 1e-9)
})
test('analog axis gives a proportional value and respects direction', () => {
  const i = make(); i.poll(raw({ padAxes: [0.6, 0] })); assert.ok(Math.abs(i.value('right') - 0.5) < 1e-9); i.poll(raw({ padAxes: [-1, 0] })); assert.equal(i.value('right'), 0); i.poll(raw({ padAxes: [0.1, 0.1] })); assert.equal(i.value('right'), 0)
})
test('rebinding and conflicts', () => {
  const i = make(); assert.deepEqual(i.conflicts({ kind: 'key', code: 'Space' }), ['jump']); assert.deepEqual(i.conflicts({ kind: 'key', code: 'Space' }, 'jump'), [])
  i.rebind('jump', 0, { kind: 'key', code: 'KeyW' }); i.poll(raw({ keys: new Set(['Space']) })); assert.ok(!i.down('jump')); i.poll(raw({ keys: new Set(['KeyW']) })); assert.ok(i.down('jump'))
})
'''}, NODE_TS, assumptions=TSA + ["Raw state comes from KeyboardEvent.code sets, navigator.getGamepads() and touch hit-zones"], compat={"node": ">=22.18"},
   tags=["game", "input", "gamepad", "keyboard", "touch", "controller", "deadzone", "rebinding", "mobile"],
   avoid="Handling keydown directly in game logic (varying repeat rates); a square deadzone; hard-coded keys."),

RC("ts-ssrf-guard", "SSRF guard for server-side URL fetching (private ranges, odd IP encodings, redirects) (TypeScript)", "typescript",
   "A feature fetches user-supplied URLs (link previews, webhooks, image import) and must not reach localhost, the cloud metadata service or the internal network.",
   "Allow only http(s) on normal ports, resolve the hostname yourself and check EVERY returned address against private/loopback/link-local/CGNAT/metadata ranges (IPv4, IPv6, IPv4-mapped IPv6, decimal/octal/hex IP forms), connect to the validated address, and re-validate on each redirect hop with a hop limit.",
   {"ssrf.ts": r'''import { isIP } from 'node:net'

function v4ToInt(ip: string): number { return ip.split('.').reduce((n, o) => n * 256 + Number(o), 0) }
const V4_BLOCKS: [string, number][] = [['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16], ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15], ['224.0.0.0', 4], ['240.0.0.0', 4]]

export function isPrivateV4(ip: string): boolean {
  const n = v4ToInt(ip)
  return V4_BLOCKS.some(([base, bits]) => { const mask = bits === 0 ? 0 : (~0 << (32 - bits)) >>> 0; return ((n & mask) >>> 0) === ((v4ToInt(base) & mask) >>> 0) })
}
export function isPrivateIp(ip: string): boolean {
  const fam = isIP(ip)
  if (fam === 4) return isPrivateV4(ip)
  if (fam !== 6) return true                                                        // not an IP at all: fail closed
  const l = ip.toLowerCase()
  const mapped = l.match(/^(?:0{0,4}:){0,5}:?(?:ffff:)(\d+\.\d+\.\d+\.\d+)$/) ?? l.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/)
  if (mapped) return isPrivateV4(mapped[1])
  const hexMapped = l.match(/^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/)
  if (hexMapped) { const a = parseInt(hexMapped[1], 16), b = parseInt(hexMapped[2], 16); return isPrivateV4(`${a >> 8}.${a & 255}.${b >> 8}.${b & 255}`) }
  return l === '::' || l === '::1' || /^f[cd]/.test(l) || /^fe[89ab]/.test(l) || /^ff/.test(l)          // unspecified, loopback, ULA, link-local, multicast
}

export type Resolver = (host: string) => Promise<string[]>
export class Blocked extends Error {}

/** -> a vetted URL and the address to connect to (pin it so DNS cannot change between check and use). */
export async function vet(raw: string, resolve: Resolver): Promise<{ url: URL; address: string }> {
  let url: URL; try { url = new URL(raw) } catch { throw new Blocked('invalid URL') }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Blocked('only http(s) is allowed')
  if (url.username || url.password) throw new Blocked('credentials in URL are not allowed')
  if (url.port && !['80', '443', '8080', '8443'].includes(url.port)) throw new Blocked('port not allowed')
  const host = url.hostname.replace(/^\[|\]$/g, '')                          // WHATWG URL already normalised 2130706433 / 0x7f.1 / 0177.0.0.1 to 127.0.0.1
  const addrs = isIP(host) ? [host] : await resolve(host)
  if (!addrs.length) throw new Blocked('host does not resolve')
  for (const a of addrs) if (isPrivateIp(a)) throw new Blocked(`address ${a} is not public`)       // ALL records, not just the first
  return { url, address: addrs[0] }
}

export async function fetchSafe(raw: string, resolve: Resolver, doFetch: (url: URL, address: string) => Promise<{ status: number; location?: string; body?: string }>, maxHops = 3) {
  let cur = raw
  for (let hop = 0; hop <= maxHops; hop++) {
    const { url, address } = await vet(cur, resolve)
    const res = await doFetch(url, address)
    if (res.status >= 300 && res.status < 400 && res.location) { cur = new URL(res.location, url).toString(); continue }   // re-vet every redirect
    return res
  }
  throw new Blocked('too many redirects')
}
''', "ssrf.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { isPrivateIp, vet, fetchSafe, Blocked } from './ssrf.ts'

const dns = (map: Record<string, string[]>) => async (h: string) => map[h] ?? []

test('private, loopback, link-local and metadata addresses are blocked', () => {
  for (const ip of ['127.0.0.1', '10.1.2.3', '172.16.0.1', '172.31.255.255', '192.168.1.1', '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1', '::1', '::', 'fd00::1', 'fe80::1', '::ffff:127.0.0.1', '::ffff:7f00:1', '::ffff:a9fe:a9fe']) assert.ok(isPrivateIp(ip), ip)
  for (const ip of ['8.8.8.8', '1.1.1.1', '172.32.0.1', '172.15.0.1', '2606:4700:4700::1111']) assert.ok(!isPrivateIp(ip), ip)
  assert.ok(isPrivateIp('not-an-ip'))
})
test('weird IPv4 spellings are normalised by URL parsing and then blocked', async () => {
  for (const u of ['http://2130706433/', 'http://0x7f.1/', 'http://0177.0.0.1/', 'http://127.1/', 'http://[::1]/', 'http://[::ffff:127.0.0.1]/']) await assert.rejects(vet(u, dns({})), Blocked, u)
})
test('scheme, credentials and port rules', async () => {
  for (const u of ['file:///etc/passwd', 'gopher://x', 'http://user:pw@example.com/', 'http://example.com:22/', 'nonsense']) await assert.rejects(vet(u, dns({ 'example.com': ['93.184.216.34'] })), Blocked, u)
})
test('every DNS answer is checked (rebinding / multi-record tricks)', async () => {
  await assert.rejects(vet('http://evil.test/', dns({ 'evil.test': ['93.184.216.34', '127.0.0.1'] })), /not public/)
  await assert.rejects(vet('http://nx.test/', dns({})), /does not resolve/)
  const ok = await vet('https://good.test/a', dns({ 'good.test': ['93.184.216.34'] })); assert.equal(ok.address, '93.184.216.34')
})
test('redirects are re-validated and capped', async () => {
  const r = dns({ 'a.test': ['93.184.216.34'], 'b.test': ['10.0.0.5'] })
  await assert.rejects(fetchSafe('http://a.test/', r, async () => ({ status: 302, location: 'http://b.test/x' })), /not public/)
  await assert.rejects(fetchSafe('http://a.test/', r, async () => ({ status: 302, location: '/loop' })), /too many redirects/)
  assert.equal((await fetchSafe('http://a.test/', r, async (u) => (u.pathname === '/' ? { status: 301, location: '/ok' } : { status: 200, body: 'hi' }))).body, 'hi')
})
'''}, NODE_TS, assumptions=TSA + ["Resolver and fetch are injected: use dns.promises.lookup({all:true}) and an undici dispatcher that connects to the pinned address", "Defence in depth: also block egress to internal ranges at the network layer"], compat={"node": ">=22.18"},
   tags=["ssrf", "security", "url", "fetch", "link-preview", "webhook", "cloud-metadata", "dns-rebinding", "owasp"],
   avoid="Regex-checking the hostname string; validating once and fetching by name again (DNS rebinding); following redirects blindly."),
]
