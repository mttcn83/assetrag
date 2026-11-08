"""Round-2 recipes, part E: SaaS building blocks and small-site UI logic (TypeScript, node --test, erasable syntax only).
Pure logic with injected clocks/randomness; nothing here talks to a database, mail provider or browser."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

LOGIC = "logic only: persistence, email delivery and the DOM are out of scope and stubbed"

R2E = [
RC("ts-magic-link-signed-tokens", "Passwordless magic-link tokens: HMAC-signed, expiring, single-use, constant-time verify (node:crypto)", "typescript",
   "Email-login links must not be forgeable, replayable or valid forever.",
   "Token = base64url(payload JSON) + '.' + HMAC-SHA256 signature; payload has email, expiry and a random jti; verification checks signature with timingSafeEqual before parsing the payload, then expiry, then marks the jti used (single use) in a store you inject; secret rotation by accepting a list of keys.",
   {"magic.ts": r'''import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'

export interface UsedStore { has(jti: string): boolean; add(jti: string, expiresAt: number): void }
export interface Payload { email: string; exp: number; jti: string }
const b64 = (b: Buffer | string) => Buffer.from(b).toString('base64url')
const mac = (secret: string, data: string) => createHmac('sha256', secret).update(data).digest()

export function issue(email: string, secrets: string[], now: number, ttlMs = 15 * 60_000): string {
  const payload: Payload = { email: email.trim().toLowerCase(), exp: now + ttlMs, jti: randomBytes(16).toString('base64url') }
  const body = b64(JSON.stringify(payload))
  return `${body}.${b64(mac(secrets[0], body))}`                      // always sign with the newest key
}

export type Result = { ok: true; email: string } | { ok: false; reason: 'malformed' | 'bad-signature' | 'expired' | 'used' }

export function verify(token: string, secrets: string[], now: number, used: UsedStore): Result {
  const parts = token.split('.')
  if (parts.length !== 2) return { ok: false, reason: 'malformed' }
  const [body, sig] = parts, given = Buffer.from(sig, 'base64url')
  // accept any current key so rotating secrets does not log everybody out; compare in constant time
  const valid = secrets.some((s) => { const want = mac(s, body); return want.length === given.length && timingSafeEqual(want, given) })
  if (!valid) return { ok: false, reason: 'bad-signature' }
  let p: Payload
  try { p = JSON.parse(Buffer.from(body, 'base64url').toString('utf8')) } catch { return { ok: false, reason: 'malformed' } }
  if (typeof p.email !== 'string' || typeof p.exp !== 'number' || typeof p.jti !== 'string') return { ok: false, reason: 'malformed' }
  if (now >= p.exp) return { ok: false, reason: 'expired' }
  if (used.has(p.jti)) return { ok: false, reason: 'used' }
  used.add(p.jti, p.exp)                                               // consume atomically in your DB (UNIQUE jti) when you have more than one server
  return { ok: true, email: p.email }
}

export class MemoryUsed implements UsedStore {
  private m = new Map<string, number>()
  has(j: string) { return this.m.has(j) }
  add(j: string, exp: number) { this.m.set(j, exp) }
  sweep(now: number) { for (const [k, e] of this.m) if (e <= now) this.m.delete(k) }
  get size() { return this.m.size }
}
''', "magic.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { issue, verify, MemoryUsed } from './magic.ts'

const S = ['secret-new', 'secret-old'], T0 = 1_000_000

test('valid token logs the (normalised) email in once', () => {
  const used = new MemoryUsed(), t = issue('  Ann@Example.COM ', S, T0)
  assert.deepEqual(verify(t, S, T0 + 1000, used), { ok: true, email: 'ann@example.com' })
})

test('single use: the second click fails', () => {
  const used = new MemoryUsed(), t = issue('a@b.co', S, T0)
  assert.equal(verify(t, S, T0, used).ok, true); assert.deepEqual(verify(t, S, T0, used), { ok: false, reason: 'used' })
})

test('expires exactly at exp', () => {
  const used = new MemoryUsed(), t = issue('a@b.co', S, T0, 60_000)
  assert.deepEqual(verify(t, S, T0 + 60_000, used), { ok: false, reason: 'expired' }); assert.equal(verify(t, S, T0 + 59_999, new MemoryUsed()).ok, true)
})

test('tampering with payload or signature is rejected before anything is parsed', () => {
  const [body, sig] = issue('a@b.co', S, T0).split('.'), evil = Buffer.from(JSON.stringify({ email: 'root@b.co', exp: T0 + 1e9, jti: 'x' })).toString('base64url')
  for (const t of [`${evil}.${sig}`, `${body}.${sig.slice(0, -2)}AA`, `${body}.`, `${body}.${'A'.repeat(sig.length)}`]) assert.deepEqual(verify(t, S, T0, new MemoryUsed()), { ok: false, reason: 'bad-signature' })
})

test('malformed tokens never throw', () => {
  for (const t of ['', 'abc', 'a.b.c', '.', '...']) assert.equal(verify(t, S, T0, new MemoryUsed()).ok, false)
})

test('key rotation: tokens signed with an older key still verify, unknown keys do not', () => {
  const old = issue('a@b.co', ['secret-old'], T0)
  assert.equal(verify(old, ['secret-new', 'secret-old'], T0, new MemoryUsed()).ok, true); assert.equal(verify(old, ['secret-new'], T0, new MemoryUsed()).ok, false)
})

test('every token is unique even for the same user and time', () => {
  assert.notEqual(issue('a@b.co', S, T0), issue('a@b.co', S, T0))
})

test('used-store can be swept so it does not grow forever', () => {
  const used = new MemoryUsed(); verify(issue('a@b.co', S, T0, 1000), S, T0, used); assert.equal(used.size, 1); used.sweep(T0 + 2000); assert.equal(used.size, 0)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Send the link only to the address typed in, rate-limit requests per email and IP, and make the landing page POST (mail scanners prefetch GET links and burn single-use tokens)"], compat={"node": ">=22.18"},
   tags=["magic-link", "passwordless", "auth", "login", "hmac", "token", "saas", "email-login", "security", "signed-token"],
   avoid="Comparing signatures with ===; tokens that never expire or can be reused; trusting the payload before verifying the signature; GET links that consume the token."),

RC("ts-usage-metering-plan-limits", "Usage metering and plan limits: monthly periods, soft/hard limits, idempotent events, overage", "typescript",
   "A SaaS needs 'you've used 80% of your API calls' warnings, hard stops on the free plan and correct monthly resets - without double counting retried events.",
   "Plans as data (limits per metric, hard vs overage-allowed), UTC calendar-month period keys, `record()` that dedupes by event id, returns {allowed, used, limit, remaining, status: ok|warn|blocked|overage}, check-before-record for expensive actions, and a period rollover that starts from zero without deleting history.",
   {"meter.ts": r'''export interface Limit { included: number; overage?: boolean }
export type Plans = Record<string, Record<string, Limit>>
export interface Usage { allowed: boolean; used: number; limit: number; remaining: number; status: 'ok' | 'warn' | 'blocked' | 'overage'; duplicate?: boolean }

export const PLANS: Plans = {
  free: { apiCalls: { included: 1000 }, seats: { included: 3 } },
  pro: { apiCalls: { included: 100_000, overage: true }, seats: { included: 10 } },
}

export const periodKey = (ms: number) => new Date(ms).toISOString().slice(0, 7)          // UTC calendar month: 2026-03

export class Meter {
  private counts = new Map<string, number>()
  private seen = new Set<string>()
  private plans: Plans
  private warnAt: number
  constructor(plans: Plans = PLANS, warnAt = 0.8) { this.plans = plans; this.warnAt = warnAt }

  private key(tenant: string, metric: string, now: number) { return `${tenant}|${metric}|${periodKey(now)}` }
  used(tenant: string, metric: string, now: number) { return this.counts.get(this.key(tenant, metric, now)) ?? 0 }

  private view(plan: string, metric: string, used: number): Usage {
    const lim = this.plans[plan]?.[metric]
    if (!lim) throw new Error(`plan ${plan} has no metric ${metric}`)
    const status = used > lim.included ? (lim.overage ? 'overage' : 'blocked') : used >= lim.included * this.warnAt ? 'warn' : 'ok'
    return { allowed: status !== 'blocked', used, limit: lim.included, remaining: Math.max(0, lim.included - used), status }
  }

  /** Would `n` more units be allowed? Does not change anything (use before expensive work). */
  check(tenant: string, plan: string, metric: string, now: number, n = 1): Usage {
    const lim = this.plans[plan]?.[metric]; const after = this.used(tenant, metric, now) + n
    const u = this.view(plan, metric, after)
    return { ...u, used: this.used(tenant, metric, now), allowed: u.allowed && !!lim }
  }

  /** Record usage. A repeated eventId (client retry, webhook redelivery) is ignored. Hard-limited plans refuse instead of counting. */
  record(tenant: string, plan: string, metric: string, now: number, n = 1, eventId?: string): Usage {
    const k = this.key(tenant, metric, now)
    if (eventId) {
      const e = `${k}|${eventId}`
      if (this.seen.has(e)) return { ...this.view(plan, metric, this.counts.get(k) ?? 0), duplicate: true }
    }
    const lim = this.plans[plan]?.[metric]
    const next = (this.counts.get(k) ?? 0) + n
    if (lim && !lim.overage && next > lim.included) return { ...this.view(plan, metric, next), allowed: false, used: this.counts.get(k) ?? 0 }
    this.counts.set(k, next)
    if (eventId) this.seen.add(`${k}|${eventId}`)
    return this.view(plan, metric, next)
  }
}
''', "meter.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Meter, periodKey } from './meter.ts'

const MAR = Date.UTC(2026, 2, 15), APR = Date.UTC(2026, 3, 1)

test('status moves ok -> warn at 80% -> blocked past the limit (free plan is hard-limited)', () => {
  const m = new Meter()
  assert.equal(m.record('t1', 'free', 'apiCalls', MAR, 700).status, 'ok')
  assert.equal(m.record('t1', 'free', 'apiCalls', MAR, 100).status, 'warn')
  const full = m.record('t1', 'free', 'apiCalls', MAR, 200); assert.deepEqual([full.status, full.remaining, full.allowed], ['warn', 0, true])
  const over = m.record('t1', 'free', 'apiCalls', MAR, 1); assert.deepEqual([over.allowed, over.used], [false, 1000])           // refused, not counted
  assert.equal(m.used('t1', 'apiCalls', MAR), 1000)
})

test('pro plan keeps going and reports overage', () => {
  const m = new Meter(); m.record('t', 'pro', 'apiCalls', MAR, 100_000); const r = m.record('t', 'pro', 'apiCalls', MAR, 50)
  assert.deepEqual([r.allowed, r.status, r.used], [true, 'overage', 100_050])
})

test('retried events are counted once', () => {
  const m = new Meter(); m.record('t', 'free', 'apiCalls', MAR, 5, 'evt-1'); const again = m.record('t', 'free', 'apiCalls', MAR, 5, 'evt-1')
  assert.equal(again.duplicate, true); assert.equal(m.used('t', 'apiCalls', MAR), 5); m.record('t', 'free', 'apiCalls', MAR, 5, 'evt-2'); assert.equal(m.used('t', 'apiCalls', MAR), 10)
})

test('new calendar month starts from zero; tenants and metrics are separate', () => {
  const m = new Meter(); m.record('a', 'free', 'apiCalls', MAR, 999); assert.equal(m.used('a', 'apiCalls', APR), 0); assert.equal(m.used('b', 'apiCalls', MAR), 0); assert.equal(m.used('a', 'seats', MAR), 0)
  assert.equal(m.record('a', 'free', 'apiCalls', APR, 1).allowed, true)
  assert.deepEqual([periodKey(Date.UTC(2026, 0, 31, 23, 59, 59)), periodKey(Date.UTC(2026, 1, 1))], ['2026-01', '2026-02'])
})

test('check() previews without consuming', () => {
  const m = new Meter(); m.record('t', 'free', 'apiCalls', MAR, 990)
  assert.equal(m.check('t', 'free', 'apiCalls', MAR, 5).allowed, true); assert.equal(m.check('t', 'free', 'apiCalls', MAR, 11).allowed, false); assert.equal(m.used('t', 'apiCalls', MAR), 990)
})

test('unknown plan or metric is a loud error, not unlimited usage', () => {
  const m = new Meter(); assert.throws(() => m.record('t', 'gold', 'apiCalls', MAR), /no metric/); assert.throws(() => m.record('t', 'free', 'gpu', MAR), /no metric/)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "In production the counter is an atomic `INCR`/`UPDATE ... RETURNING` (Redis/Postgres), and event ids a UNIQUE column"], compat={"node": ">=22.18"},
   tags=["saas", "usage", "metering", "billing", "plan-limits", "quota", "entitlements", "pricing", "idempotent", "api-limits", "subscription"],
   avoid="Counting in the request handler without idempotency; local-time month boundaries; unknown plan => unlimited; read-then-write counters that race."),

RC("ts-team-roles-invites", "Team roles, permissions and invites: hierarchy, can(), expiring invites, last-owner protection", "typescript",
   "Multi-user SaaS: who can invite whom, who can remove the last owner (nobody), and invites that expire and can't be reused.",
   "Role ranks (owner > admin > member > viewer), permission table per action, `can(role, action)`, rule that you can only grant roles below your own (owners can grant owner), invites with expiry and single acceptance, role changes and removals that refuse to leave a team with zero owners.",
   {"team.ts": r'''export type Role = 'owner' | 'admin' | 'member' | 'viewer'
export const RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }
export type Action = 'read' | 'write' | 'invite' | 'remove' | 'billing' | 'delete-team'

const MIN_ROLE: Record<Action, Role> = { read: 'viewer', write: 'member', invite: 'admin', remove: 'admin', billing: 'owner', 'delete-team': 'owner' }
export const can = (role: Role, action: Action) => RANK[role] >= RANK[MIN_ROLE[action]]

/** You may grant roles up to your own rank; only owners create owners. */
export const canGrant = (actor: Role, target: Role) => can(actor, 'invite') && RANK[target] <= RANK[actor] && (target !== 'owner' || actor === 'owner')

export interface Invite { token: string; email: string; role: Role; exp: number; accepted?: boolean }

export class Team {
  members = new Map<string, Role>()
  invites = new Map<string, Invite>()
  constructor(owner: string) { this.members.set(owner, 'owner') }

  private owners() { return [...this.members.values()].filter((r) => r === 'owner').length }
  private role(user: string): Role { const r = this.members.get(user); if (!r) throw new Error(`${user} is not on this team`); return r }

  invite(actor: string, email: string, role: Role, now: number, token: string, ttlMs = 7 * 86_400_000): Invite {
    if (!canGrant(this.role(actor), role)) throw new Error('not allowed to grant that role')
    const mail = email.trim().toLowerCase()
    for (const i of this.invites.values()) if (i.email === mail && !i.accepted && i.exp > now) throw new Error('already invited')
    const inv = { token, email: mail, role, exp: now + ttlMs }; this.invites.set(token, inv); return inv
  }

  accept(token: string, user: string, email: string, now: number) {
    const inv = this.invites.get(token)
    if (!inv || inv.accepted) throw new Error('invite is invalid or already used')
    if (now >= inv.exp) throw new Error('invite has expired')
    if (inv.email !== email.trim().toLowerCase()) throw new Error('invite was sent to a different email')
    inv.accepted = true; this.members.set(user, inv.role)
  }

  setRole(actor: string, user: string, role: Role) {
    const a = this.role(actor), cur = this.role(user)
    if (!canGrant(a, role) || RANK[cur] > RANK[a]) throw new Error('not allowed to change that member')
    if (cur === 'owner' && role !== 'owner' && this.owners() === 1) throw new Error('a team needs at least one owner')
    this.members.set(user, role)
  }

  remove(actor: string, user: string) {
    const a = this.role(actor), cur = this.role(user)
    if (actor !== user && (!can(a, 'remove') || RANK[cur] >= RANK[a] && a !== 'owner')) throw new Error('not allowed to remove that member')
    if (cur === 'owner' && this.owners() === 1) throw new Error('a team needs at least one owner')
    this.members.delete(user)
  }
}
''', "team.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { can, canGrant, Team } from './team.ts'

const T0 = 1_000

test('permission table', () => {
  assert.deepEqual((['viewer', 'member', 'admin', 'owner'] as const).map((r) => [can(r, 'read'), can(r, 'write'), can(r, 'invite'), can(r, 'billing')]),
    [[true, false, false, false], [true, true, false, false], [true, true, true, false], [true, true, true, true]])
})

test('you can only grant up to your own rank; only owners make owners', () => {
  assert.equal(canGrant('admin', 'member'), true); assert.equal(canGrant('admin', 'admin'), true); assert.equal(canGrant('admin', 'owner'), false)
  assert.equal(canGrant('owner', 'owner'), true); assert.equal(canGrant('member', 'viewer'), false)
})

test('invite -> accept happy path, case-insensitive email', () => {
  const t = new Team('ann'); t.invite('ann', 'Bob@X.com', 'member', T0, 'tok1'); t.accept('tok1', 'bob', 'bob@x.COM', T0 + 1); assert.equal(t.members.get('bob'), 'member')
})

test('invites expire, are single use, and are bound to the invited email', () => {
  const t = new Team('ann'); t.invite('ann', 'b@x.com', 'viewer', T0, 'tok', 1000)
  assert.throws(() => t.accept('tok', 'eve', 'eve@x.com', T0), /different email/); assert.throws(() => t.accept('tok', 'bob', 'b@x.com', T0 + 1000), /expired/)
  t.accept('tok', 'bob', 'b@x.com', T0 + 999); assert.throws(() => t.accept('tok', 'bob2', 'b@x.com', T0 + 999), /invalid or already used/); assert.throws(() => t.accept('nope', 'x', 'x', T0), /invalid/)
})

test('members cannot invite; admins cannot mint owners; duplicate pending invites refused', () => {
  const t = new Team('ann'); t.invite('ann', 'a@x.com', 'admin', T0, 't1'); t.accept('t1', 'adam', 'a@x.com', T0); t.invite('ann', 'm@x.com', 'member', T0, 't2'); t.accept('t2', 'mo', 'm@x.com', T0)
  assert.throws(() => t.invite('mo', 'z@x.com', 'viewer', T0, 't3'), /not allowed/); assert.throws(() => t.invite('adam', 'z@x.com', 'owner', T0, 't4'), /not allowed/)
  t.invite('adam', 'z@x.com', 'viewer', T0, 't5'); assert.throws(() => t.invite('adam', 'Z@x.com', 'viewer', T0, 't6'), /already invited/)
})

test('the last owner can be neither demoted nor removed (nor can they leave)', () => {
  const t = new Team('ann'); assert.throws(() => t.setRole('ann', 'ann', 'admin'), /at least one owner/); assert.throws(() => t.remove('ann', 'ann'), /at least one owner/)
  t.invite('ann', 'o@x.com', 'owner', T0, 'tk'); t.accept('tk', 'olga', 'o@x.com', T0); t.setRole('ann', 'ann', 'admin'); assert.equal(t.members.get('ann'), 'admin')
  assert.throws(() => t.remove('ann', 'olga'), /not allowed/)
})

test('admins cannot touch owners or peers above them', () => {
  const t = new Team('ann'); t.invite('ann', 'a@x.com', 'admin', T0, 'k'); t.accept('k', 'adam', 'a@x.com', T0)
  assert.throws(() => t.setRole('adam', 'ann', 'viewer'), /not allowed/); assert.throws(() => t.remove('adam', 'ann'), /not allowed/); assert.throws(() => t.remove('ghost', 'ann'), /not on this team/)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "Fine-grained, per-resource permissions need a policy engine (Casbin, Cedar, OpenFGA)"], compat={"node": ">=22.18"},
   tags=["roles", "permissions", "rbac", "invites", "team", "saas", "multi-user", "authorization", "access-control", "organization"],
   avoid="Role checks scattered as `if (user.isAdmin)`; invites with no expiry or email binding; letting the last owner leave."),

RC("ts-tenant-isolated-repository", "Multi-tenant data isolation: tenant-scoped repository that cannot leak across tenants", "typescript",
   "In a shared-database SaaS, one forgotten `WHERE tenant_id = ?` leaks customer data; isolation must be structural, not remembered.",
   "A `scoped(tenantId)` factory is the only way to get a repository; every operation applies the tenant filter, ids from other tenants look like 'not found' (no existence oracle), updates can't move a row to another tenant, tests try cross-tenant read/update/delete/list/count; same shape applies to SQL (always-on tenant predicate or Postgres row-level security).",
   {"tenant.ts": r'''export interface Row { id: string; tenantId: string }

export class Store<T extends Row> {
  private rows = new Map<string, T>()
  /** Only reachable through scoped(): application code never sees unscoped data. */
  scoped(tenantId: string): Repo<T> {
    if (!tenantId || typeof tenantId !== 'string') throw new Error('tenantId is required')
    return new Repo<T>(this.rows, tenantId)
  }
  /** Break-glass for migrations/tests only; a real app puts this behind a separate import. */
  get unsafeAll(): T[] { return [...this.rows.values()] }
}

export class Repo<T extends Row> {
  private rows: Map<string, T>
  readonly tenantId: string
  constructor(rows: Map<string, T>, tenantId: string) { this.rows = rows; this.tenantId = tenantId }
  private mine(id: string): T | undefined { const r = this.rows.get(id); return r && r.tenantId === this.tenantId ? r : undefined }

  get(id: string): T | undefined { const r = this.mine(id); return r ? { ...r } : undefined }                   // other tenant's id == not found
  list(pred: (r: T) => boolean = () => true): T[] { return [...this.rows.values()].filter((r) => r.tenantId === this.tenantId && pred(r)).map((r) => ({ ...r })) }
  count(): number { return this.list().length }
  insert(row: Omit<T, 'tenantId'>): T {
    if (this.rows.has(row.id)) throw new Error('id already exists')                                                  // ids are globally unique; do not reveal whose it is
    const full = { ...row, tenantId: this.tenantId } as T; this.rows.set(row.id, full); return { ...full }
  }
  update(id: string, patch: Partial<Omit<T, 'id' | 'tenantId'>>): T {
    const r = this.mine(id); if (!r) throw new Error('not found')
    const { id: _id, tenantId: _tenant, ...safe } = patch as Record<string, unknown>                                 // types exclude these keys, but a cast-around must not work at runtime either
    Object.assign(r, safe); return { ...r }
  }
  delete(id: string): boolean { return this.mine(id) ? this.rows.delete(id) : false }
}
''', "tenant.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Store, type Row } from './tenant.ts'

interface Doc extends Row { title: string }
function seeded() {
  const s = new Store<Doc>(), a = s.scoped('acme'), b = s.scoped('beta')
  a.insert({ id: 'd1', title: 'acme secret' }); a.insert({ id: 'd2', title: 'acme roadmap' }); b.insert({ id: 'd3', title: 'beta plan' })
  return { s, a, b }
}

test('each tenant sees only its own rows', () => {
  const { a, b } = seeded(); assert.deepEqual(a.list().map((r) => r.id), ['d1', 'd2']); assert.deepEqual(b.list().map((r) => r.id), ['d3']); assert.equal(a.count(), 2)
})

test('guessing another tenant\'s id looks exactly like not found', () => {
  const { a, b } = seeded(); assert.equal(a.get('d3'), undefined); assert.equal(a.get('does-not-exist'), undefined)
  assert.throws(() => a.update('d3', { title: 'pwned' }), /not found/); assert.equal(b.get('d3')!.title, 'beta plan')
  assert.equal(a.delete('d3'), false); assert.equal(b.count(), 1)
})

test('predicates cannot widen the scope', () => {
  const { a } = seeded(); assert.deepEqual(a.list(() => true).map((r) => r.id), ['d1', 'd2']); assert.deepEqual(a.list((r) => r.title.includes('beta')), [])
})

test('inserting an existing id fails without leaking who owns it; tenantId cannot be chosen by the caller', () => {
  const { a, s } = seeded(); assert.throws(() => a.insert({ id: 'd3', title: 'x' }), /already exists/)
  const forged = a.insert({ id: 'd9', title: 't', tenantId: 'beta' } as never); assert.equal(forged.tenantId, 'acme'); assert.equal(s.unsafeAll.find((r) => r.id === 'd9')!.tenantId, 'acme')
})

test('update cannot move a row to another tenant, even with a forged patch', () => {
  const { a, b } = seeded(); a.update('d1', { tenantId: 'beta' } as never)
  a.update('d1', { id: 'hijack' } as never)
  assert.equal(a.get('d1')?.tenantId, 'acme'); assert.equal(b.get('d1'), undefined); assert.equal(a.get('hijack'), undefined)
})

test('returned rows are copies: mutating them does not edit the store', () => {
  const { a } = seeded(); const r = a.get('d1')!; r.title = 'changed'; assert.equal(a.get('d1')!.title, 'acme secret')
})

test('empty tenant id is refused instead of matching everything', () => {
  const s = new Store<Doc>(); for (const bad of ['', undefined as never, null as never]) assert.throws(() => s.scoped(bad), /tenantId is required/)
})
'''}, NODE_TS, assumptions=TSA + [LOGIC, "In SQL, never put tenant_id in UPDATE ... SET, include `AND tenant_id = $n` in every statement, and add Postgres row-level security as a second lock"], compat={"node": ">=22.18"},
   tags=["multi-tenant", "tenant", "isolation", "saas", "security", "row-level-security", "data-leak", "authorization", "repository", "idor"],
   avoid="Remembering to add tenant_id in each query; returning 403 for another tenant's id (reveals it exists); trusting a client-supplied tenant id."),
]
