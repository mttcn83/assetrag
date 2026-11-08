"""Round-1 persona recipes, part A: commerce and backend building blocks (Python stdlib only, `unittest`).
Money is always integer minor units or Decimal - never float. Providers (Stripe etc.) are not called; the recipes are
the logic around them (totals, idempotency, signature checks) which is what goes wrong in practice."""
from .recipes import RC, UT

R1A = [
RC("py-cart-totals-coupons", "Shopping cart totals with coupons and tax in integer cents", "python",
   "A shop or checkout needs correct totals: line items, a coupon, tax, rounding, and never a negative amount.",
   "Integer cents everywhere; percent coupons round half-up once on the subtotal, fixed coupons are capped at the subtotal, a minimum-spend rule gates coupons, tax is computed on the discounted subtotal per rate, and the total is the exact sum of its parts.",
   {"cart.py": '''"""Cart maths in integer cents. No floats."""
from dataclasses import dataclass, field


def half_up(numerator, denominator):
    """Integer division rounding half up (for non-negative values)."""
    return (2 * numerator + denominator) // (2 * denominator)


@dataclass(frozen=True)
class Line:
    sku: str
    unit_cents: int
    qty: int
    tax_bp: int = 0              # tax rate in basis points (2000 = 20%)


@dataclass(frozen=True)
class Coupon:
    code: str
    percent_bp: int = 0          # 1000 = 10%
    fixed_cents: int = 0
    min_spend_cents: int = 0


@dataclass
class Totals:
    subtotal: int
    discount: int
    tax: int
    total: int
    tax_by_rate: dict = field(default_factory=dict)


def totals(lines, coupon=None):
    for l in lines:
        if l.qty < 1 or l.unit_cents < 0:
            raise ValueError(f"bad line {l.sku}")
    subtotal = sum(l.unit_cents * l.qty for l in lines)
    discount = 0
    if coupon and subtotal >= coupon.min_spend_cents:
        discount = half_up(subtotal * coupon.percent_bp, 10000) + coupon.fixed_cents
        discount = min(discount, subtotal)
    # spread the discount over lines proportionally so tax is charged on what is actually paid
    tax_by_rate, tax, left = {}, 0, discount
    for i, l in enumerate(lines):
        gross = l.unit_cents * l.qty
        share = left if i == len(lines) - 1 else (half_up(discount * gross, subtotal) if subtotal else 0)
        share = min(share, gross, left)
        left -= share
        t = half_up((gross - share) * l.tax_bp, 10000)
        tax_by_rate[l.tax_bp] = tax_by_rate.get(l.tax_bp, 0) + t
        tax += t
    return Totals(subtotal, discount, tax, subtotal - discount + tax, tax_by_rate)
'''
    , "test_cart.py": '''import unittest
from cart import Line, Coupon, totals


class T(unittest.TestCase):
    def test_plain(self):
        t = totals([Line("a", 1000, 2, 2000)])
        self.assertEqual((t.subtotal, t.discount, t.tax, t.total), (2000, 0, 400, 2400))

    def test_percent_coupon_and_tax_on_discounted(self):
        t = totals([Line("a", 1000, 1, 2000)], Coupon("TEN", percent_bp=1000))
        self.assertEqual((t.discount, t.tax, t.total), (100, 180, 1080))

    def test_fixed_coupon_capped_never_negative(self):
        t = totals([Line("a", 500, 1, 2000)], Coupon("BIG", fixed_cents=9999))
        self.assertEqual((t.discount, t.tax, t.total), (500, 0, 0))

    def test_min_spend_gate(self):
        c = Coupon("X", fixed_cents=100, min_spend_cents=2000)
        self.assertEqual(totals([Line("a", 1999, 1)], c).discount, 0)
        self.assertEqual(totals([Line("a", 2000, 1)], c).discount, 100)

    def test_rounding_half_up_once(self):
        # 3 x 33 cents at 10%: 99 * 0.1 = 9.9 -> 10 (not 3 x round(3.3)=9)
        t = totals([Line("a", 33, 3, 1000)])
        self.assertEqual(t.tax, 10)

    def test_mixed_rates_split(self):
        t = totals([Line("book", 1000, 1, 0), Line("pen", 1000, 1, 2000)])
        self.assertEqual(t.tax_by_rate, {0: 0, 2000: 200})

    def test_discount_shares_sum_exactly(self):
        t = totals([Line("a", 333, 1, 1000), Line("b", 333, 1, 1000), Line("c", 334, 1, 1000)], Coupon("C", fixed_cents=100))
        self.assertEqual(t.discount, 100)
        self.assertEqual(t.total, t.subtotal - t.discount + t.tax)

    def test_rejects_bad_lines(self):
        with self.assertRaises(ValueError): totals([Line("a", 100, 0)])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single currency with 2 minor digits", "Prices are tax-exclusive (add tax on top)"], compat={"python": ">=3.9"},
   tags=["ecommerce", "cart", "checkout", "coupon", "discount", "tax", "money", "invoice"],
   avoid="float arithmetic for money; rounding each line separately and the total again; applying tax before the discount."),

RC("py-invoice-builder", "Invoice with gapless numbering per year and a tax summary", "python",
   "A small business/SaaS needs invoices with legally sequential numbers, line totals and a VAT summary that adds up.",
   "SQLite counter table incremented inside the same transaction that inserts the invoice (gapless even on rollback), number format INV-2026-0001, integer cents, tax summary grouped by rate, credit notes as negative documents rather than edits.",
   {"invoicing.py": '''"""Gapless invoice numbers + totals. The counter and the invoice are written in ONE transaction."""
import sqlite3


def connect(path=":memory:"):
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS counters(year INTEGER PRIMARY KEY, last INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS invoices(number TEXT PRIMARY KEY, customer TEXT NOT NULL, total_cents INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'invoice');
    CREATE TABLE IF NOT EXISTS lines(number TEXT NOT NULL, desc TEXT, qty INTEGER, unit_cents INTEGER, tax_bp INTEGER);
    """)
    return db


def next_number(db, year):
    db.execute("INSERT INTO counters(year,last) VALUES(?,0) ON CONFLICT(year) DO NOTHING", (year,))
    db.execute("UPDATE counters SET last = last + 1 WHERE year = ?", (year,))
    n = db.execute("SELECT last FROM counters WHERE year = ?", (year,)).fetchone()[0]
    return f"INV-{year}-{n:04d}"


def summary(lines):
    by = {}
    for desc, qty, unit, bp in lines:
        net = qty * unit
        tax = (2 * net * bp + 10000) // 20000
        n, t = by.get(bp, (0, 0))
        by[bp] = (n + net, t + tax)
    net = sum(n for n, _ in by.values()); tax = sum(t for _, t in by.values())
    return {"by_rate": by, "net": net, "tax": tax, "total": net + tax}


def issue(db, year, customer, lines):
    s = summary(lines)
    with db:                                   # one transaction: a failure rolls the counter back too
        number = next_number(db, year)
        db.execute("INSERT INTO invoices(number,customer,total_cents) VALUES(?,?,?)", (number, customer, s["total"]))
        db.executemany("INSERT INTO lines VALUES(?,?,?,?,?)", [(number, *l) for l in lines])
    return number, s


def credit_note(db, year, original, customer, total_cents):
    """Never edit an issued invoice: issue a negative document that references it."""
    with db:
        number = next_number(db, year).replace("INV-", "CN-")
        db.execute("INSERT INTO invoices(number,customer,total_cents,kind) VALUES(?,?,?,'credit')", (number, customer, -abs(total_cents)))
    return number
'''
    , "test_invoicing.py": '''import unittest
from invoicing import connect, issue, summary, credit_note


class T(unittest.TestCase):
    def test_sequential_numbers(self):
        db = connect()
        a, _ = issue(db, 2026, "acme", [("work", 1, 1000, 2000)])
        b, _ = issue(db, 2026, "acme", [("work", 1, 1000, 2000)])
        self.assertEqual((a, b), ("INV-2026-0001", "INV-2026-0002"))

    def test_counter_per_year(self):
        db = connect()
        issue(db, 2026, "a", [("x", 1, 1, 0)])
        n, _ = issue(db, 2027, "a", [("x", 1, 1, 0)])
        self.assertEqual(n, "INV-2027-0001")

    def test_failed_insert_does_not_burn_a_number(self):
        db = connect()
        issue(db, 2026, "a", [("x", 1, 1, 0)])
        with self.assertRaises(Exception):
            issue(db, 2026, None, [("x", 1, 1, 0)])          # NOT NULL violation after the counter bump
        n, _ = issue(db, 2026, "a", [("x", 1, 1, 0)])
        self.assertEqual(n, "INV-2026-0002")

    def test_tax_summary_by_rate(self):
        s = summary([("a", 2, 1000, 2000), ("b", 1, 500, 0)])
        self.assertEqual(s["by_rate"], {2000: (2000, 400), 0: (500, 0)})
        self.assertEqual((s["net"], s["tax"], s["total"]), (2500, 400, 2900))

    def test_credit_note_is_negative_and_numbered(self):
        db = connect()
        issue(db, 2026, "a", [("x", 1, 1000, 0)])
        cn = credit_note(db, 2026, "INV-2026-0001", "a", 1000)
        self.assertTrue(cn.startswith("CN-2026-"))
        self.assertEqual(db.execute("SELECT total_cents FROM invoices WHERE number=?", (cn,)).fetchone()[0], -1000)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["SQLite single-writer (use SELECT ... FOR UPDATE on a counter row in Postgres)", "Tax regime details (reverse charge, rounding rules) are jurisdiction-specific"], compat={"python": ">=3.9", "sqlite": ">=3.24"},
   tags=["invoice", "billing", "ecommerce", "sqlite", "tax", "vat", "saas"],
   avoid="Using MAX(number)+1 or an autoincrement id (gaps and races); editing issued invoices."),

RC("py-inventory-reservation", "Stock reservation that never oversells (reserve, commit, release, expire)", "python",
   "A shop or ticketing system must hold stock during checkout without overselling under concurrency.",
   "SQLite with a single conditional UPDATE (`available >= qty`) as the atomic reserve; reservations expire by timestamp and are released by a sweep; commit turns a hold into a sale idempotently.",
   {"stock.py": '''"""Reservation-based inventory. The atomic step is one conditional UPDATE, never read-then-write."""
import sqlite3, time


def connect(path=":memory:"):
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS stock(sku TEXT PRIMARY KEY, on_hand INTEGER NOT NULL CHECK(on_hand >= 0), held INTEGER NOT NULL DEFAULT 0 CHECK(held >= 0), CHECK(held <= on_hand));
    CREATE TABLE IF NOT EXISTS holds(id TEXT PRIMARY KEY, sku TEXT, qty INTEGER, expires REAL, state TEXT DEFAULT 'held');
    """)
    return db


def add(db, sku, qty):
    db.execute("INSERT INTO stock(sku,on_hand) VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET on_hand = on_hand + excluded.on_hand", (sku, qty))


def reserve(db, hold_id, sku, qty, ttl=600, now=time.time):
    if qty < 1:
        raise ValueError("qty")
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM holds WHERE id=?", (hold_id,)).fetchone():
            db.execute("COMMIT"); return True                          # idempotent retry
        cur = db.execute("UPDATE stock SET held = held + ? WHERE sku = ? AND on_hand - held >= ?", (qty, sku, qty))
        if cur.rowcount != 1:
            db.execute("ROLLBACK"); return False
        db.execute("INSERT INTO holds(id,sku,qty,expires) VALUES(?,?,?,?)", (hold_id, sku, qty, now() + ttl))
        db.execute("COMMIT"); return True
    except Exception:
        db.execute("ROLLBACK") if db.in_transaction else None
        raise


def commit(db, hold_id):
    """Hold -> sale: stock leaves the shelf. Safe to call twice."""
    db.execute("BEGIN IMMEDIATE")
    h = db.execute("SELECT sku, qty, state FROM holds WHERE id=?", (hold_id,)).fetchone()
    if not h or h[2] == "committed":
        db.execute("COMMIT"); return bool(h)
    if h[2] != "held":
        db.execute("ROLLBACK"); return False
    db.execute("UPDATE stock SET on_hand = on_hand - ?, held = held - ? WHERE sku = ?", (h[1], h[1], h[0]))
    db.execute("UPDATE holds SET state='committed' WHERE id=?", (hold_id,))
    db.execute("COMMIT"); return True


def release_expired(db, now=time.time):
    db.execute("BEGIN IMMEDIATE")
    rows = db.execute("SELECT id, sku, qty FROM holds WHERE state='held' AND expires <= ?", (now(),)).fetchall()
    for id_, sku, qty in rows:
        db.execute("UPDATE stock SET held = held - ? WHERE sku = ?", (qty, sku))
        db.execute("UPDATE holds SET state='expired' WHERE id=?", (id_,))
    db.execute("COMMIT"); return len(rows)


def available(db, sku):
    r = db.execute("SELECT on_hand - held FROM stock WHERE sku=?", (sku,)).fetchone()
    return r[0] if r else 0
'''
    , "test_stock.py": '''import unittest, sqlite3, threading, os, tempfile
import stock


class T(unittest.TestCase):
    def test_reserve_until_empty(self):
        db = stock.connect(); stock.add(db, "a", 3)
        self.assertTrue(stock.reserve(db, "h1", "a", 2))
        self.assertFalse(stock.reserve(db, "h2", "a", 2))
        self.assertEqual(stock.available(db, "a"), 1)

    def test_reserve_is_idempotent(self):
        db = stock.connect(); stock.add(db, "a", 3)
        stock.reserve(db, "h1", "a", 2); stock.reserve(db, "h1", "a", 2)
        self.assertEqual(stock.available(db, "a"), 1)

    def test_commit_reduces_on_hand_once(self):
        db = stock.connect(); stock.add(db, "a", 3)
        stock.reserve(db, "h1", "a", 2)
        self.assertTrue(stock.commit(db, "h1")); self.assertTrue(stock.commit(db, "h1"))
        self.assertEqual(stock.available(db, "a"), 1)
        self.assertEqual(db.execute("SELECT on_hand FROM stock").fetchone()[0], 1)

    def test_expired_holds_return_to_pool(self):
        db = stock.connect(); stock.add(db, "a", 1)
        stock.reserve(db, "h1", "a", 1, ttl=10, now=lambda: 100)
        self.assertEqual(stock.release_expired(db, now=lambda: 109), 0)
        self.assertEqual(stock.release_expired(db, now=lambda: 111), 1)
        self.assertEqual(stock.available(db, "a"), 1)
        self.assertFalse(stock.commit(db, "h1"))

    def test_concurrent_buyers_never_oversell(self):
        path = os.path.join(tempfile.mkdtemp(), "s.db")
        db = stock.connect(path); stock.add(db, "a", 5); db.close()
        wins = []
        def buy(i):
            c = stock.connect(path); c.execute("PRAGMA busy_timeout=5000")
            if stock.reserve(c, f"h{i}", "a", 1): wins.append(i)
        ts = [threading.Thread(target=buy, args=(i,)) for i in range(20)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(wins), 5)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["SQLite (use `UPDATE ... WHERE available >= qty` + row locks in Postgres)", "A periodic sweep calls release_expired"], compat={"python": ">=3.9", "sqlite": ">=3.24"},
   tags=["ecommerce", "inventory", "stock", "reservation", "concurrency", "checkout", "tickets"],
   avoid="SELECT stock; if enough: UPDATE (race = oversell); holding stock forever when a checkout is abandoned."),

RC("py-subscription-proration", "Subscription plan-change proration and trial handling in integer cents", "python",
   "A SaaS must charge the right amount when a customer upgrades/downgrades mid-cycle or starts on a trial.",
   "Daily proration: credit for unused days of the old plan, charge for remaining days of the new plan, net amount can be negative (credit), trials charge nothing until trial_end, cycle length from the actual period.",
   {"billing.py": '''"""Proration maths for subscriptions. Integer cents, whole-day granularity, explicit rounding."""
from datetime import date


def days(a, b):
    return (b - a).days


def half_up(n, d):
    return (2 * n + d) // (2 * d) if n >= 0 else -((2 * -n + d) // (2 * d))


def proration(old_cents, new_cents, period_start, period_end, change_on):
    """Net amount to bill now when switching plans on `change_on` inside [period_start, period_end)."""
    if not (period_start <= change_on < period_end):
        raise ValueError("change date outside the current period")
    total = days(period_start, period_end)
    left = days(change_on, period_end)
    credit = half_up(old_cents * left, total)
    charge = half_up(new_cents * left, total)
    return charge - credit                       # negative = credit on next invoice


def first_charge(price_cents, start, trial_days, today):
    """Amount due today for a new subscription: nothing during trial, full price afterwards."""
    from datetime import timedelta
    trial_end = start + timedelta(days=trial_days)
    return (0, trial_end) if today < trial_end else (price_cents, trial_end)
'''
    , "test_billing.py": '''import unittest
from datetime import date
from billing import proration, first_charge


class T(unittest.TestCase):
    P = (date(2026, 1, 1), date(2026, 1, 31))           # 30 days

    def test_upgrade_mid_cycle(self):
        # halfway: new 3000 vs old 1000 -> (3000-1000)/2 = 1000
        self.assertEqual(proration(1000, 3000, *self.P, date(2026, 1, 16)), 1000)

    def test_downgrade_gives_credit(self):
        self.assertEqual(proration(3000, 1000, *self.P, date(2026, 1, 16)), -1000)

    def test_change_on_first_day_is_full_difference(self):
        self.assertEqual(proration(1000, 3000, *self.P, date(2026, 1, 1)), 2000)

    def test_same_plan_zero(self):
        self.assertEqual(proration(1500, 1500, *self.P, date(2026, 1, 10)), 0)

    def test_outside_period_rejected(self):
        with self.assertRaises(ValueError): proration(1, 2, *self.P, date(2026, 2, 1))

    def test_rounding_is_symmetric(self):
        up = proration(1000, 1001, *self.P, date(2026, 1, 11)); down = proration(1001, 1000, *self.P, date(2026, 1, 11))
        self.assertEqual(up, -down)

    def test_trial_then_charge(self):
        self.assertEqual(first_charge(2900, date(2026, 1, 1), 14, date(2026, 1, 10))[0], 0)
        self.assertEqual(first_charge(2900, date(2026, 1, 1), 14, date(2026, 1, 15))[0], 2900)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Calendar-day proration (some providers use seconds)", "Tax is applied after proration"], compat={"python": ">=3.9"},
   tags=["subscription", "billing", "saas", "proration", "stripe", "trial", "money"],
   avoid="Float maths for proration; re-charging the full price on every plan change; using 30 as the cycle length for every month."),

RC("py-idempotency-keys", "Idempotency-Key handling for POST endpoints (store, replay, conflict)", "python",
   "A payment/order endpoint is retried by clients and must not create duplicates.",
   "Persist (key, request fingerprint, status, response) in SQLite; same key + same body replays the stored response, same key + different body is a 422-style conflict, in-flight duplicates are rejected, failures before completion release the key.",
   {"idempotency.py": '''"""Idempotency-Key store. `run(key, body, handler)` executes handler at most once per key."""
import hashlib, json, sqlite3


class Conflict(Exception): pass
class InFlight(Exception): pass


def connect(path=":memory:"):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("CREATE TABLE IF NOT EXISTS idem(key TEXT PRIMARY KEY, fp TEXT NOT NULL, state TEXT NOT NULL, response TEXT)")
    return db


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run(db, key, body, handler):
    fp = fingerprint(body)
    try:
        db.execute("INSERT INTO idem(key,fp,state) VALUES(?,?,'running')", (key, fp))
    except sqlite3.IntegrityError:
        stored_fp, state, resp = db.execute("SELECT fp,state,response FROM idem WHERE key=?", (key,)).fetchone()
        if stored_fp != fp:
            raise Conflict("Idempotency-Key reused with a different request body")
        if state == "running":
            raise InFlight("request with this key is still being processed")
        return json.loads(resp), True                       # replay
    try:
        result = handler(body)
    except Exception:
        db.execute("DELETE FROM idem WHERE key=?", (key,))   # let the client retry a failed attempt
        raise
    db.execute("UPDATE idem SET state='done', response=? WHERE key=?", (json.dumps(result), key))
    return result, False
'''
    , "test_idempotency.py": '''import unittest
from idempotency import connect, run, Conflict, InFlight


class T(unittest.TestCase):
    def test_handler_runs_once_and_replays(self):
        db, calls = connect(), []
        h = lambda b: calls.append(1) or {"id": len(calls)}
        a = run(db, "k1", {"amt": 5}, h); b = run(db, "k1", {"amt": 5}, h)
        self.assertEqual((a, b), (({"id": 1}, False), ({"id": 1}, True)))
        self.assertEqual(len(calls), 1)

    def test_same_key_different_body_conflicts(self):
        db = connect(); run(db, "k", {"amt": 5}, lambda b: {})
        with self.assertRaises(Conflict): run(db, "k", {"amt": 6}, lambda b: {})

    def test_key_order_in_body_does_not_matter(self):
        db = connect(); run(db, "k", {"a": 1, "b": 2}, lambda b: {"x": 1})
        self.assertTrue(run(db, "k", {"b": 2, "a": 1}, lambda b: {"x": 2})[1])

    def test_failure_releases_key(self):
        db = connect()
        def boom(b): raise RuntimeError("gateway down")
        with self.assertRaises(RuntimeError): run(db, "k", {}, boom)
        self.assertEqual(run(db, "k", {}, lambda b: {"ok": 1}), ({"ok": 1}, False))

    def test_in_flight_duplicate_rejected(self):
        db = connect()
        def reenter(b):
            with self.assertRaises(InFlight): run(db, "k", b, lambda x: {})
            return {"ok": 1}
        run(db, "k", {}, reenter)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["One database shared by all workers", "Keys expire via a cleanup job (24h is typical)"], compat={"python": ">=3.9", "sqlite": ">=3.24"},
   tags=["idempotency", "api", "payments", "backend", "retry", "stripe", "webhook"],
   avoid="Deduplicating by checking a cache first and writing later (race); replaying a stored response for a different payload."),

RC("py-webhook-signature-verify", "Verify signed webhooks (HMAC-SHA256, timestamp tolerance, replay guard)", "python",
   "Receiving Stripe/GitHub/Slack-style webhooks safely: reject forged, tampered, stale and replayed requests.",
   "Sign `timestamp.payload` with HMAC-SHA256, compare with hmac.compare_digest, reject timestamps outside a tolerance, support secret rotation (multiple secrets), and remember seen event ids to reject replays.",
   {"webhook.py": '''"""Webhook signature verification. Verify against the RAW body bytes, before any JSON parsing."""
import hashlib, hmac, time


class BadSignature(Exception): pass


def sign(secret: bytes, payload: bytes, ts: int) -> str:
    mac = hmac.new(secret, f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secrets, payload: bytes, header: str, tolerance=300, now=time.time, seen=None, event_id=None):
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, sig = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        raise BadSignature("malformed signature header")
    if abs(now() - ts) > tolerance:
        raise BadSignature("timestamp outside tolerance")
    expected = [hmac.new(s, f"{ts}.".encode() + payload, hashlib.sha256).hexdigest() for s in secrets]
    if not any(hmac.compare_digest(e, sig) for e in expected):   # constant time, any active secret
        raise BadSignature("signature mismatch")
    if seen is not None and event_id is not None:
        if event_id in seen:
            raise BadSignature("replayed event")
        seen.add(event_id)
    return True
'''
    , "test_webhook.py": '''import unittest
from webhook import sign, verify, BadSignature

S, BODY = b"whsec_test", b'{"id":"evt_1","amount":100}'


class T(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(verify([S], BODY, sign(S, BODY, 1000), now=lambda: 1010))

    def test_tampered_body(self):
        with self.assertRaises(BadSignature): verify([S], BODY + b" ", sign(S, BODY, 1000), now=lambda: 1000)

    def test_wrong_secret(self):
        with self.assertRaises(BadSignature): verify([b"other"], BODY, sign(S, BODY, 1000), now=lambda: 1000)

    def test_stale_timestamp(self):
        with self.assertRaises(BadSignature): verify([S], BODY, sign(S, BODY, 1000), now=lambda: 1400)

    def test_future_timestamp(self):
        with self.assertRaises(BadSignature): verify([S], BODY, sign(S, BODY, 2000), now=lambda: 1000)

    def test_secret_rotation(self):
        self.assertTrue(verify([b"new", S], BODY, sign(S, BODY, 1000), now=lambda: 1000))

    def test_replay_rejected(self):
        seen = set(); h = sign(S, BODY, 1000)
        verify([S], BODY, h, now=lambda: 1000, seen=seen, event_id="evt_1")
        with self.assertRaises(BadSignature): verify([S], BODY, h, now=lambda: 1001, seen=seen, event_id="evt_1")

    def test_malformed_header(self):
        for h in ("", "garbage", "t=abc,v1=x", "t=1"):
            with self.assertRaises(BadSignature): verify([S], BODY, h, now=lambda: 1)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["The provider signs `timestamp.payload` (Stripe format); adapt the signed string for GitHub (`sha256=` over the body) or Slack (`v0:ts:body`)", "Raw request bytes are available"], compat={"python": ">=3.9"},
   tags=["webhook", "hmac", "signature", "stripe", "github", "security", "api", "replay"],
   avoid="Comparing signatures with ==; verifying after json.loads/re-serialising; no timestamp window."),

RC("py-rbac-policy", "Role-based access control with deny rules and resource ownership", "python",
   "An API needs a small permission model: roles, per-resource ownership, explicit deny, and a default of deny.",
   "Roles map to permission sets with inheritance; policy check is `can(user, action, resource)`: explicit deny wins, owners get owner-scoped permissions, anything not granted is denied; roles are data so they can be audited.",
   {"rbac.py": '''"""Tiny RBAC: default deny, explicit deny wins, ownership-scoped permissions ("own" suffix)."""
ROLES = {
    "viewer": {"allow": {"post:read"}},
    "editor": {"inherits": ["viewer"], "allow": {"post:create", "post:update:own", "post:delete:own"}},
    "moderator": {"inherits": ["editor"], "allow": {"post:update", "post:delete"}, "deny": {"user:ban"}},
    "admin": {"inherits": ["moderator"], "allow": {"user:ban", "user:read"}},
}


def expand(role, roles=ROLES, _seen=None):
    _seen = _seen or set()
    if role in _seen:
        raise ValueError(f"role cycle at {role}")
    _seen.add(role)
    r = roles[role]
    allow, deny = set(r.get("allow", ())), set(r.get("deny", ()))
    for parent in r.get("inherits", ()):
        a, d = expand(parent, roles, _seen.copy())
        allow |= a; deny |= d
    return allow, deny


def can(user, action, resource=None, roles=ROLES):
    """user = {"id":..., "roles":[...]}; resource may carry "owner_id"."""
    allow, deny = set(), set()
    for role in user.get("roles", ()):
        if role not in roles:
            continue                                    # unknown role grants nothing
        a, d = expand(role, roles)
        allow |= a; deny |= d
    if action in deny:
        return False                                    # explicit deny beats any allow
    if action in allow:
        return True
    if action + ":own" in allow and resource is not None:
        return resource.get("owner_id") == user.get("id")
    return False
'''
    , "test_rbac.py": '''import unittest
from rbac import can, expand, ROLES


class T(unittest.TestCase):
    def test_default_deny(self):
        self.assertFalse(can({"id": 1, "roles": []}, "post:read"))
        self.assertFalse(can({"id": 1, "roles": ["ghost"]}, "post:read"))

    def test_inheritance(self):
        self.assertTrue(can({"id": 1, "roles": ["editor"]}, "post:read"))

    def test_owner_scoped(self):
        u = {"id": 1, "roles": ["editor"]}
        self.assertTrue(can(u, "post:update", {"owner_id": 1}))
        self.assertFalse(can(u, "post:update", {"owner_id": 2}))
        self.assertFalse(can(u, "post:update"))                 # no resource -> no ownership proof

    def test_moderator_can_update_any(self):
        self.assertTrue(can({"id": 9, "roles": ["moderator"]}, "post:update", {"owner_id": 1}))

    def test_explicit_deny_beats_allow(self):
        # admin inherits moderator (deny user:ban) and allows it: deny still wins
        self.assertFalse(can({"id": 1, "roles": ["admin"]}, "user:ban"))

    def test_cycle_detected(self):
        bad = {"a": {"inherits": ["b"]}, "b": {"inherits": ["a"]}}
        with self.assertRaises(ValueError): expand("a", bad)

    def test_multiple_roles_union(self):
        self.assertTrue(can({"id": 1, "roles": ["viewer", "admin"]}, "user:read"))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Roles are static config; per-tenant roles need a store"], compat={"python": ">=3.9"},
   tags=["rbac", "authorization", "permissions", "roles", "security", "backend", "api", "admin"],
   avoid="Checking role names in handlers (if user.role == 'admin'); allow-by-default; trusting a client-supplied owner id."),

RC("py-totp-2fa", "TOTP two-factor codes (RFC 6238) with drift window and replay protection", "python",
   "Adding authenticator-app 2FA: generate the secret/URI, verify 6-digit codes, tolerate clock drift, refuse code reuse.",
   "HOTP per RFC 4226 over 30 s counters with HMAC-SHA1, base32 secrets, +-1 step window, constant-time compare, remember the last accepted counter per user so a code works once.",
   {"totp.py": '''"""RFC 6238 TOTP. stdlib only."""
import base64, hashlib, hmac, secrets, struct, time
from urllib.parse import quote


def new_secret(nbytes=20):
    return base64.b32encode(secrets.token_bytes(nbytes)).decode().rstrip("=")


def hotp(secret_b32, counter, digits=6, algo=hashlib.sha1):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), algo).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(code).zfill(digits)


def totp(secret_b32, at=None, step=30, **kw):
    return hotp(secret_b32, int((time.time() if at is None else at) // step), **kw)


def verify(secret_b32, code, last_counter=-1, at=None, step=30, window=1):
    """Return the accepted counter (store it!) or None. Codes at or before last_counter are refused (replay)."""
    now = int((time.time() if at is None else at) // step)
    for c in range(now - window, now + window + 1):
        if c > last_counter and hmac.compare_digest(hotp(secret_b32, c), str(code).zfill(6)):
            return c
    return None


def provisioning_uri(secret_b32, account, issuer):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret_b32}&issuer={quote(issuer)}&digits=6&period=30"
'''
    , "test_totp.py": '''import unittest, base64, hashlib
import totp as t

# RFC 6238 Appendix B (SHA-1 secret "12345678901234567890"), 8 digits
SECRET = base64.b32encode(b"12345678901234567890").decode()


class T(unittest.TestCase):
    def test_rfc_vectors(self):
        for at, want in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (20000000000, "65353130")]:
            self.assertEqual(t.totp(SECRET, at=at, digits=8), want)

    def test_verify_window(self):
        code = t.totp(SECRET, at=1000)
        self.assertIsNotNone(t.verify(SECRET, code, at=1000))
        self.assertIsNotNone(t.verify(SECRET, code, at=1000 + 30))      # one step late is OK
        self.assertIsNone(t.verify(SECRET, code, at=1000 + 120))        # far outside the window

    def test_replay_refused(self):
        code = t.totp(SECRET, at=1000)
        c = t.verify(SECRET, code, at=1000)
        self.assertIsNone(t.verify(SECRET, code, last_counter=c, at=1000))

    def test_wrong_code(self):
        self.assertIsNone(t.verify(SECRET, "000000", at=1000))

    def test_new_secret_roundtrip(self):
        s = t.new_secret(); self.assertEqual(len(t.totp(s)), 6)

    def test_uri(self):
        u = t.provisioning_uri("ABC", "me@x.io", "My App")
        self.assertTrue(u.startswith("otpauth://totp/My%20App:me%40x.io?secret=ABC"))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Server clock is NTP-synced", "Last accepted counter is stored per user"], compat={"python": ">=3.9"},
   tags=["totp", "2fa", "mfa", "authentication", "security", "login", "otp"],
   avoid="Accepting a code more than once; comparing with ==; huge drift windows; storing the secret unencrypted when you can avoid it."),

RC("py-pkce-oauth", "OAuth 2.0 PKCE (S256) verifier/challenge and state check", "python",
   "A CLI, SPA or mobile app doing the authorization-code flow needs PKCE and CSRF-safe state.",
   "Random 43-128 char verifier from the unreserved set, challenge = base64url(SHA256(verifier)) without padding, a random state compared in constant time on return, and the verifier kept only in memory/session.",
   {"pkce.py": '''"""PKCE (RFC 7636) + state helpers."""
import base64, hashlib, hmac, secrets, string

UNRESERVED = string.ascii_letters + string.digits + "-._~"


def verifier(n=64):
    if not 43 <= n <= 128:
        raise ValueError("verifier length must be 43..128")
    return "".join(secrets.choice(UNRESERVED) for _ in range(n))


def challenge(v):
    return base64.urlsafe_b64encode(hashlib.sha256(v.encode("ascii")).digest()).rstrip(b"=").decode()


def new_state():
    return secrets.token_urlsafe(24)


def state_ok(expected, got):
    return bool(expected) and hmac.compare_digest(expected.encode(), (got or "").encode())


def authorize_url(base, client_id, redirect, scope, v, state):
    from urllib.parse import urlencode
    q = dict(response_type="code", client_id=client_id, redirect_uri=redirect, scope=scope, state=state,
             code_challenge=challenge(v), code_challenge_method="S256")
    return base + "?" + urlencode(q)
'''
    , "test_pkce.py": '''import unittest
import pkce


class T(unittest.TestCase):
    def test_rfc7636_vector(self):
        self.assertEqual(pkce.challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_verifier_charset_and_length(self):
        v = pkce.verifier(80)
        self.assertEqual(len(v), 80); self.assertTrue(set(v) <= set(pkce.UNRESERVED))
        for n in (42, 129):
            with self.assertRaises(ValueError): pkce.verifier(n)

    def test_challenge_has_no_padding(self):
        self.assertNotIn("=", pkce.challenge(pkce.verifier()))

    def test_state(self):
        s = pkce.new_state()
        self.assertTrue(pkce.state_ok(s, s)); self.assertFalse(pkce.state_ok(s, s + "x"))
        self.assertFalse(pkce.state_ok("", "")); self.assertFalse(pkce.state_ok(s, None))

    def test_url(self):
        u = pkce.authorize_url("https://id.example/auth", "cid", "http://127.0.0.1:8123/cb", "openid", "v" * 50, "st")
        self.assertIn("code_challenge_method=S256", u); self.assertIn("redirect_uri=http%3A%2F%2F127.0.0.1%3A8123%2Fcb", u)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Provider supports PKCE (all modern ones do)", "Loopback redirect for CLIs (http://127.0.0.1:<port>)"], compat={"python": ">=3.9"},
   tags=["oauth", "pkce", "login", "authentication", "cli", "spa", "mobile", "security"],
   avoid="Implicit flow; plain challenge method; reusing one verifier; skipping the state check."),

RC("py-cursor-pagination", "Keyset (cursor) pagination with opaque, tamper-evident cursors", "python",
   "A list API must page through large or changing tables without OFFSET slowness or skipped/duplicated rows.",
   "Order by (created, id) and fetch `limit+1` rows after the cursor tuple; cursor is base64url JSON signed with HMAC so clients cannot forge it; next_cursor is None on the last page.",
   {"paging.py": '''"""Keyset pagination over SQLite (same SQL works on Postgres with row values)."""
import base64, hashlib, hmac, json

KEY = b"change-me"


def encode(created, id_, key=KEY):
    body = base64.urlsafe_b64encode(json.dumps([created, id_]).encode()).rstrip(b"=")
    sig = hmac.new(key, body, hashlib.sha256).hexdigest()[:16].encode()
    return (body + b"." + sig).decode()


def decode(cursor, key=KEY):
    try:
        body, sig = cursor.encode().rsplit(b".", 1)
        good = hmac.new(key, body, hashlib.sha256).hexdigest()[:16].encode()
        if not hmac.compare_digest(sig, good):
            raise ValueError
        created, id_ = json.loads(base64.urlsafe_b64decode(body + b"=" * (-len(body) % 4)))
        return created, id_
    except Exception:
        raise ValueError("invalid cursor") from None


def page(db, limit=20, cursor=None, key=KEY):
    limit = max(1, min(int(limit), 100))
    if cursor:
        c, i = decode(cursor, key)
        rows = db.execute("SELECT id, created, title FROM items WHERE (created, id) < (?, ?) ORDER BY created DESC, id DESC LIMIT ?", (c, i, limit + 1)).fetchall()
    else:
        rows = db.execute("SELECT id, created, title FROM items ORDER BY created DESC, id DESC LIMIT ?", (limit + 1,)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = encode(rows[-1][1], rows[-1][0], key) if more else None
    return rows, nxt
'''
    , "test_paging.py": '''import unittest, sqlite3
import paging


def db_with(n):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, created INTEGER, title TEXT)")
    db.executemany("INSERT INTO items(created,title) VALUES(?,?)", [(i // 3, f"t{i}") for i in range(n)])   # many equal timestamps
    return db


class T(unittest.TestCase):
    def walk(self, db, limit):
        seen, cur = [], None
        while True:
            rows, cur = paging.page(db, limit, cur)
            seen += [r[0] for r in rows]
            if not cur: return seen

    def test_every_row_exactly_once_with_ties(self):
        db = db_with(50)
        seen = self.walk(db, 7)
        self.assertEqual(len(seen), 50); self.assertEqual(len(set(seen)), 50)

    def test_stable_when_new_rows_arrive(self):
        db = db_with(20)
        rows, cur = paging.page(db, 5)
        db.execute("INSERT INTO items(created,title) VALUES(999,'new')")        # newer than everything: must not shift page 2
        rows2, _ = paging.page(db, 5, cur)
        self.assertTrue(set(r[0] for r in rows).isdisjoint(r[0] for r in rows2))

    def test_last_page_has_no_cursor(self):
        _, cur = paging.page(db_with(3), 10); self.assertIsNone(cur)

    def test_tampered_cursor_rejected(self):
        c = paging.encode(5, 5)
        for bad in (c[:-2] + "zz", "garbage", c.replace(".", "")):
            with self.assertRaises(ValueError): paging.decode(bad)

    def test_limit_is_clamped(self):
        rows, _ = paging.page(db_with(300), 10_000); self.assertEqual(len(rows), 100)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["SQLite >= 3.15 row values (Postgres/MySQL support them too)", "Index on (created, id)"], compat={"python": ">=3.9", "sqlite": ">=3.15"},
   tags=["pagination", "api", "cursor", "sql", "backend", "infinite-scroll", "rest"],
   avoid="OFFSET for deep pages; ordering by a non-unique column alone; exposing raw ids as cursors without validation."),

RC("py-sqlite-job-queue", "Durable job queue on SQLite with retries, visibility timeout and dead letters", "python",
   "A small service needs background jobs without Redis: survive restarts, retry with backoff, never run a job twice at once.",
   "One table; claim is a single UPDATE ... RETURNING guarded by state/visible_at; a crashed worker's job reappears after the visibility timeout; attempts counted, exponential backoff, dead-letter after max attempts.",
   {"jobqueue.py": '''"""SQLite-backed job queue (needs SQLite >= 3.35 for RETURNING)."""
import json, sqlite3, time


def connect(path=":memory:"):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute("""CREATE TABLE IF NOT EXISTS jobs(
        id INTEGER PRIMARY KEY, kind TEXT, payload TEXT, state TEXT DEFAULT 'ready',
        attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 5, visible_at REAL DEFAULT 0, last_error TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, visible_at)")
    return db


def enqueue(db, kind, payload, delay=0, max_attempts=5, now=time.time):
    return db.execute("INSERT INTO jobs(kind,payload,visible_at,max_attempts) VALUES(?,?,?,?)", (kind, json.dumps(payload), now() + delay, max_attempts)).lastrowid


def claim(db, visibility=30, now=time.time):
    """Atomically take one due job; if the worker dies, it becomes visible again after `visibility` seconds."""
    t = now()
    row = db.execute("""UPDATE jobs SET state='running', attempts=attempts+1, visible_at=?
                        WHERE id = (SELECT id FROM jobs WHERE state IN ('ready','running') AND visible_at <= ? AND attempts < max_attempts ORDER BY visible_at, id LIMIT 1)
                        RETURNING id, kind, payload, attempts""", (t + visibility, t)).fetchone()
    return None if not row else dict(id=row[0], kind=row[1], payload=json.loads(row[2]), attempts=row[3])


def done(db, job_id):
    db.execute("UPDATE jobs SET state='done' WHERE id=?", (job_id,))


def fail(db, job, err, base=2.0, now=time.time):
    delay = base * 2 ** (job["attempts"] - 1)
    db.execute("UPDATE jobs SET state='ready', visible_at=?, last_error=? WHERE id=?", (now() + delay, str(err), job["id"]))


def dead(db):
    """Jobs that used all attempts and never finished."""
    return db.execute("SELECT id, kind, last_error FROM jobs WHERE state != 'done' AND attempts >= max_attempts").fetchall()
'''
    , "test_jobqueue.py": '''import unittest
import jobqueue as jq


class T(unittest.TestCase):
    def test_fifo_and_done(self):
        db = jq.connect(); jq.enqueue(db, "a", {"n": 1}, now=lambda: 0); jq.enqueue(db, "a", {"n": 2}, now=lambda: 0)
        j = jq.claim(db, now=lambda: 1); self.assertEqual(j["payload"], {"n": 1}); jq.done(db, j["id"])
        self.assertEqual(jq.claim(db, now=lambda: 1)["payload"], {"n": 2})

    def test_claimed_job_not_given_twice_until_visibility_expires(self):
        db = jq.connect(); jq.enqueue(db, "a", {}, now=lambda: 0)
        self.assertIsNotNone(jq.claim(db, visibility=30, now=lambda: 1))
        self.assertIsNone(jq.claim(db, visibility=30, now=lambda: 20))
        again = jq.claim(db, visibility=30, now=lambda: 40)           # worker presumably crashed
        self.assertEqual(again["attempts"], 2)

    def test_delay(self):
        db = jq.connect(); jq.enqueue(db, "a", {}, delay=60, now=lambda: 0)
        self.assertIsNone(jq.claim(db, now=lambda: 59)); self.assertIsNotNone(jq.claim(db, now=lambda: 61))

    def test_backoff_and_dead_letter(self):
        db = jq.connect(); jq.enqueue(db, "a", {}, max_attempts=3, now=lambda: 0)
        t = 0
        for expected_delay in (2, 4):
            j = jq.claim(db, now=lambda: t); jq.fail(db, j, "boom", now=lambda: t)
            self.assertIsNone(jq.claim(db, now=lambda: t + expected_delay - 0.1))
            t += expected_delay
        j = jq.claim(db, now=lambda: t); jq.fail(db, j, "boom", now=lambda: t)
        self.assertIsNone(jq.claim(db, now=lambda: t + 1000))              # attempts exhausted
        self.assertEqual([d[0] for d in jq.dead(db)], [j["id"]])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["SQLite >= 3.35", "Handlers are idempotent (at-least-once delivery)"], compat={"python": ">=3.9", "sqlite": ">=3.35"},
   tags=["queue", "jobs", "background", "worker", "retry", "sqlite", "backend", "cron"],
   avoid="Polling with SELECT then UPDATE (two workers take the same job); retrying forever; treating delivery as exactly-once."),

RC("py-cron-next-run", "Cron expression parser with next-run calculation (5 fields)", "python",
   "A scheduler, backup tool or bot needs 'run at 02:30 on weekdays' without pulling in a dependency.",
   "Parse `m h dom mon dow` with *, lists, ranges and steps into sets; walk forward minute by minute with field-skipping; use the POSIX rule that dom and dow are OR-ed when both are restricted; validate ranges with precise errors.",
   {"cron.py": '''"""5-field cron: minute hour day-of-month month day-of-week (0=Sunday). Naive datetimes (apply the zone yourself)."""
from datetime import datetime, timedelta

RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
         "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _num(tok, lo, hi):
    v = NAMES.get(tok.lower()) if not tok.isdigit() else int(tok)
    if v is None or not lo <= v <= hi and not (hi == 6 and v == 7):
        raise ValueError(f"bad value {tok!r} (allowed {lo}-{hi})")
    return 0 if (hi == 6 and v == 7) else v


def _field(text, lo, hi):
    out = set()
    for part in text.split(","):
        rng, _, step = part.partition("/")
        step = int(step) if step else 1
        if step < 1:
            raise ValueError("step must be >= 1")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, y = rng.split("-"); a, b = _num(x, lo, hi), _num(y, lo, hi)
            if a > b: raise ValueError(f"descending range {rng!r}")
        else:
            a = _num(rng, lo, hi); b = hi if step > 1 else a
        out |= set(range(a, b + 1, step))
    return out


def parse(expr):
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    sets = [_field(p, *RANGES[i]) for i, p in enumerate(parts)]
    return sets, parts[2] != "*", parts[4] != "*"


def matches(spec, dt):
    (mi, ho, dom, mon, dow), dom_r, dow_r = spec
    if dt.minute not in mi or dt.hour not in ho or dt.month not in mon:
        return False
    d_ok, w_ok = dt.day in dom, (dt.isoweekday() % 7) in dow
    return (d_ok or w_ok) if (dom_r and dow_r) else (d_ok and w_ok)       # POSIX: OR when both restricted


def next_run(expr, after, limit_years=5):
    spec = parse(expr)
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = t + timedelta(days=366 * limit_years)
    while t < end:
        if t.month not in spec[0][3]:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1); continue
        if not _day_ok(spec, t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1); continue
        if t.hour not in spec[0][1]:
            t = t.replace(minute=0) + timedelta(hours=1); continue
        if t.minute not in spec[0][0]:
            t += timedelta(minutes=1); continue
        return t
    raise ValueError("no run within the search horizon (impossible date like Feb 31?)")


def _day_ok(spec, t):
    (_, _, dom, _, dow), dom_r, dow_r = spec
    d_ok, w_ok = t.day in dom, (t.isoweekday() % 7) in dow
    return (d_ok or w_ok) if (dom_r and dow_r) else (d_ok and w_ok)
'''
    , "test_cron.py": '''import unittest
from datetime import datetime as D
from cron import next_run, parse


class T(unittest.TestCase):
    def test_every_minute(self):
        self.assertEqual(next_run("* * * * *", D(2026, 1, 1, 10, 0, 30)), D(2026, 1, 1, 10, 1))

    def test_daily_0230(self):
        self.assertEqual(next_run("30 2 * * *", D(2026, 1, 1, 3, 0)), D(2026, 1, 2, 2, 30))
        self.assertEqual(next_run("30 2 * * *", D(2026, 1, 1, 1, 0)), D(2026, 1, 1, 2, 30))

    def test_weekdays_only(self):
        # 2026-01-02 is a Friday; next weekday 09:00 after Fri 10:00 is Mon Jan 5
        self.assertEqual(next_run("0 9 * * 1-5", D(2026, 1, 2, 10, 0)), D(2026, 1, 5, 9, 0))

    def test_steps_and_lists(self):
        self.assertEqual(next_run("*/15 * * * *", D(2026, 1, 1, 10, 7)), D(2026, 1, 1, 10, 15))
        self.assertEqual(next_run("0 8,20 * * *", D(2026, 1, 1, 9, 0)), D(2026, 1, 1, 20, 0))

    def test_month_names_and_dom(self):
        self.assertEqual(next_run("0 0 1 jan *", D(2026, 6, 1)), D(2027, 1, 1))

    def test_dom_or_dow_posix_rule(self):
        # "0 0 13 * 5": the 13th OR any Friday -> from Jan 1 2026 (Thu) next is Fri Jan 2
        self.assertEqual(next_run("0 0 13 * 5", D(2026, 1, 1, 12, 0)), D(2026, 1, 2))

    def test_leap_day(self):
        self.assertEqual(next_run("0 0 29 2 *", D(2026, 3, 1)), D(2028, 2, 29))

    def test_impossible_date_errors(self):
        with self.assertRaises(ValueError): next_run("0 0 31 2 *", D(2026, 1, 1))

    def test_bad_expressions(self):
        for bad in ("* * * *", "61 * * * *", "* 24 * * *", "*/0 * * * *", "5-1 * * * *", "x * * * *"):
            with self.assertRaises(ValueError): parse(bad)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["No named macros (@daily) or seconds field", "Timezone/DST handling is the caller's job"], compat={"python": ">=3.9"},
   tags=["cron", "scheduler", "backup", "automation", "schedule", "bot", "systemd-timer"],
   avoid="Looping minute-by-minute for years without skipping; OR/AND confusion between day-of-month and day-of-week."),

RC("py-audit-log-hashchain", "Tamper-evident audit log (hash chain) with verification", "python",
   "Admin/finance/security actions must be logged so that edits or deletions of past entries are detectable.",
   "Append-only JSONL where each record stores the SHA-256 of the previous record plus its own canonical body; verify walks the file and reports the first broken index; optional HMAC key so an attacker cannot recompute the chain.",
   {"auditlog.py": '''"""Append-only, hash-chained audit log. Detects edits/deletes/reordering of earlier records."""
import hashlib, hmac, json, os, time

GENESIS = "0" * 64


def _digest(prev, body, key):
    data = (prev + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()
    return hmac.new(key, data, hashlib.sha256).hexdigest() if key else hashlib.sha256(data).hexdigest()


def append(path, actor, action, target, key=None, now=time.time):
    prev = GENESIS
    if os.path.exists(path):
        with open(path, "rb") as f:
            last = None
            for last in f: pass
            if last: prev = json.loads(last)["hash"]
    body = {"ts": now(), "actor": actor, "action": action, "target": target}
    rec = {**body, "prev": prev, "hash": _digest(prev, body, key)}
    with open(path, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\\n"); f.flush(); os.fsync(f.fileno())
    return rec


def verify(path, key=None):
    """-> (ok, index_of_first_bad_record or None)"""
    prev = GENESIS
    with open(path) as f:
        for i, line in enumerate(f):
            try:
                rec = json.loads(line)
                body = {k: rec[k] for k in ("ts", "actor", "action", "target")}
            except (ValueError, KeyError):
                return False, i
            if rec.get("prev") != prev or not hmac.compare_digest(rec.get("hash", ""), _digest(prev, body, key)):
                return False, i
            prev = rec["hash"]
    return True, None
'''
    , "test_auditlog.py": '''import unittest, tempfile, os, json
import auditlog as a


class T(unittest.TestCase):
    def setUp(self):
        self.p = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
        for i in range(5): a.append(self.p, "alice", "delete", f"doc{i}")

    def lines(self): return open(self.p).read().splitlines()
    def write(self, ls): open(self.p, "w").write("\\n".join(ls) + "\\n")

    def test_clean_log_verifies(self):
        self.assertEqual(a.verify(self.p), (True, None))

    def test_edit_detected(self):
        ls = self.lines(); r = json.loads(ls[2]); r["actor"] = "mallory"; ls[2] = json.dumps(r, sort_keys=True); self.write(ls)
        self.assertEqual(a.verify(self.p), (False, 2))

    def test_deleted_record_detected(self):
        ls = self.lines(); del ls[1]; self.write(ls)
        self.assertEqual(a.verify(self.p), (False, 1))

    def test_reorder_detected(self):
        ls = self.lines(); ls[1], ls[2] = ls[2], ls[1]; self.write(ls)
        self.assertFalse(a.verify(self.p)[0])

    def test_garbage_line(self):
        self.write(self.lines() + ["not json"]); self.assertEqual(a.verify(self.p), (False, 5))

    def test_keyed_chain_cannot_be_recomputed_without_key(self):
        p = os.path.join(tempfile.mkdtemp(), "k.jsonl")
        a.append(p, "x", "y", "z", key=b"secret"); self.assertTrue(a.verify(p, key=b"secret")[0])
        self.assertFalse(a.verify(p, key=b"wrong")[0]); self.assertFalse(a.verify(p)[0])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Truncating the *tail* is only detectable if the head hash is anchored elsewhere (print it, ship it to another host)", "Single writer or a lock around append"], compat={"python": ">=3.9"},
   tags=["audit", "logging", "security", "compliance", "hash-chain", "tamper", "admin"],
   avoid="Plain text logs for security events; trusting a chain with no anchored head."),

RC("py-feature-flag-rollout", "Feature flags with deterministic percentage rollout and overrides", "python",
   "Ship a feature to 10% of users, keep each user's experience stable, force it on for staff, and kill it instantly.",
   "Hash (flag, user id) with SHA-256 to a stable bucket 0..9999; flag has enabled/kill switch, allow/deny lists, percentage; changing the flag name reshuffles buckets, raising the percentage only adds users.",
   {"flags.py": '''"""Deterministic feature flags. Same user + same flag -> same answer, forever, with no storage."""
import hashlib


def bucket(flag, user_id):
    h = hashlib.sha256(f"{flag}:{user_id}".encode()).digest()
    return int.from_bytes(h[:4], "big") % 10000          # 0..9999 -> 0.01% granularity


def enabled(flag, cfg, user_id):
    c = cfg.get(flag)
    if not c or c.get("kill"):
        return False                                    # unknown flag or kill switch: off
    if user_id in c.get("deny", ()):
        return False
    if user_id in c.get("allow", ()):
        return True
    return bucket(flag, user_id) < round(c.get("percent", 0) * 100)
'''
    , "test_flags.py": '''import unittest
from flags import enabled, bucket


class T(unittest.TestCase):
    def test_stable(self):
        cfg = {"f": {"percent": 50}}
        self.assertEqual([enabled("f", cfg, u) for u in range(200)], [enabled("f", cfg, u) for u in range(200)])

    def test_percent_roughly_right(self):
        cfg = {"f": {"percent": 10}}
        n = sum(enabled("f", cfg, f"user{i}") for i in range(20000))
        self.assertTrue(1700 < n < 2300, n)

    def test_raising_percent_only_adds_users(self):
        lo, hi = {"f": {"percent": 10}}, {"f": {"percent": 30}}
        for i in range(2000):
            if enabled("f", lo, i): self.assertTrue(enabled("f", hi, i))

    def test_zero_and_hundred(self):
        self.assertFalse(any(enabled("f", {"f": {"percent": 0}}, i) for i in range(500)))
        self.assertTrue(all(enabled("f", {"f": {"percent": 100}}, i) for i in range(500)))

    def test_allow_deny_kill(self):
        cfg = {"f": {"percent": 0, "allow": ["staff"], "deny": ["bad"]}}
        self.assertTrue(enabled("f", cfg, "staff")); self.assertFalse(enabled("f", cfg, "bad"))
        cfg["f"]["kill"] = True; self.assertFalse(enabled("f", cfg, "staff"))

    def test_flags_are_independent(self):
        self.assertNotEqual([bucket("a", i) for i in range(20)], [bucket("b", i) for i in range(20)])

    def test_unknown_flag_off(self):
        self.assertFalse(enabled("nope", {}, 1))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Stable user ids (anonymous users need a persistent cookie id)"], compat={"python": ">=3.9"},
   tags=["feature-flag", "rollout", "ab-test", "backend", "experiment", "deploy"],
   avoid="random() per request (flapping experience); reusing one hash for every flag (correlated cohorts)."),
]
