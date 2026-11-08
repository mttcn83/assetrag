"""Round-2 recipes, part B: fintech, ML engineering and scientific computing (Python stdlib only, `unittest`). Money uses Decimal or
integer cents; randomness is always seeded; numeric results are compared with tolerances stated in the tests."""
from .recipes import RC, UT

R2B = [
RC("py-double-entry-ledger", "Double-entry ledger: balanced postings, trial balance, reversals (SQLite)", "python",
   "Anything that tracks money (wallets, bookkeeping, marketplace payouts) needs a ledger where it is impossible to lose cents.",
   "Append-only entries made of postings (account, signed integer cents) that must sum to zero or the transaction is rejected, balances derived from postings (never stored as the source of truth), reversal = new transaction with negated postings, trial balance must always sum to zero, idempotency key per transaction.",
   {"ledger.py": '''"""Double-entry ledger on SQLite. Amounts are integer cents; debits positive, credits negative."""
import sqlite3


class Unbalanced(Exception): pass


def connect(path=":memory:"):
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS tx(id INTEGER PRIMARY KEY, key TEXT UNIQUE, memo TEXT, reverses INTEGER REFERENCES tx(id));
    CREATE TABLE IF NOT EXISTS posting(tx INTEGER NOT NULL REFERENCES tx(id), account TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents != 0));
    CREATE INDEX IF NOT EXISTS posting_account ON posting(account);
    -- postings are immutable
    CREATE TRIGGER IF NOT EXISTS posting_no_update BEFORE UPDATE ON posting BEGIN SELECT RAISE(ABORT, 'postings are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS posting_no_delete BEFORE DELETE ON posting BEGIN SELECT RAISE(ABORT, 'postings are immutable'); END;
    """)
    return db


def post(db, key, memo, postings, reverses=None):
    """postings: [(account, cents)]. Returns the tx id; the same key returns the original tx (idempotent)."""
    if sum(c for _, c in postings) != 0 or len(postings) < 2:
        raise Unbalanced(f"postings sum to {sum(c for _, c in postings)}, need 0 over at least 2 lines")
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT id FROM tx WHERE key=?", (key,)).fetchone()
        if row:
            db.execute("COMMIT"); return row[0]
        tid = db.execute("INSERT INTO tx(key,memo,reverses) VALUES(?,?,?)", (key, memo, reverses)).lastrowid
        db.executemany("INSERT INTO posting VALUES(?,?,?)", [(tid, a, c) for a, c in postings])
        db.execute("COMMIT"); return tid
    except Exception:
        db.execute("ROLLBACK"); raise


def balance(db, account):
    return db.execute("SELECT COALESCE(SUM(cents),0) FROM posting WHERE account=?", (account,)).fetchone()[0]


def trial_balance(db):
    rows = dict(db.execute("SELECT account, SUM(cents) FROM posting GROUP BY account").fetchall())
    assert sum(rows.values()) == 0, "ledger out of balance"       # holds by construction; a violation means corruption
    return rows


def reverse(db, tx_id, key):
    ps = db.execute("SELECT account, cents FROM posting WHERE tx=?", (tx_id,)).fetchall()
    if db.execute("SELECT 1 FROM tx WHERE reverses=?", (tx_id,)).fetchone():
        raise ValueError("transaction already reversed")
    return post(db, key, f"reversal of {tx_id}", [(a, -c) for a, c in ps], reverses=tx_id)
'''
    , "test_ledger.py": '''import unittest, sqlite3
import ledger as L


class T(unittest.TestCase):
    def setUp(self): self.db = L.connect()

    def test_payment_moves_money_and_balances(self):
        L.post(self.db, "p1", "customer pays", [("cash", 1000), ("revenue", -1000)])
        self.assertEqual((L.balance(self.db, "cash"), L.balance(self.db, "revenue")), (1000, -1000)); self.assertEqual(sum(L.trial_balance(self.db).values()), 0)

    def test_unbalanced_rejected_and_nothing_written(self):
        with self.assertRaises(L.Unbalanced): L.post(self.db, "bad", "x", [("cash", 100), ("revenue", -99)])
        with self.assertRaises(L.Unbalanced): L.post(self.db, "one", "x", [("cash", 0)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tx").fetchone()[0], 0)

    def test_idempotent_key(self):
        a = L.post(self.db, "k", "m", [("a", 5), ("b", -5)]); b = L.post(self.db, "k", "m", [("a", 5), ("b", -5)])
        self.assertEqual(a, b); self.assertEqual(L.balance(self.db, "a"), 5)

    def test_multi_leg_split(self):
        L.post(self.db, "s", "sale with fee", [("cash", 1000), ("revenue", -900), ("tax_payable", -100)]); self.assertEqual(L.trial_balance(self.db)["tax_payable"], -100)

    def test_reversal_nets_to_zero_once(self):
        t = L.post(self.db, "p", "m", [("cash", 700), ("revenue", -700)]); L.reverse(self.db, t, "r")
        self.assertEqual((L.balance(self.db, "cash"), L.balance(self.db, "revenue")), (0, 0))
        with self.assertRaises(ValueError): L.reverse(self.db, t, "r2")

    def test_history_cannot_be_edited(self):
        L.post(self.db, "p", "m", [("a", 5), ("b", -5)])
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute("UPDATE posting SET cents = 9")
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute("DELETE FROM posting")


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single currency per ledger (add a currency column and per-currency balance checks for multi-currency)", "SQLite triggers stand in for database permissions; in Postgres also revoke UPDATE/DELETE"], compat={"python": ">=3.9", "sqlite": ">=3.24"},
   tags=["ledger", "accounting", "double-entry", "fintech", "money", "bookkeeping", "wallet", "payments", "sqlite"],
   avoid="A mutable `balance` column updated in place; floats for money; editing old transactions instead of reversing them."),

RC("py-loan-amortization-interest", "Loan amortization schedule and compound interest with Decimal (final-payment adjusted)", "python",
   "A calculator or fintech feature must produce a payment schedule whose last payment closes the loan to the cent, and compound-interest projections that match the bank.",
   "Decimal with explicit ROUND_HALF_UP to cents each period, payment = P*r/(1-(1+r)^-n), interest on the remaining balance, last payment adjusted for rounding drift, schedule sums reconcile (principal paid = loan), zero-rate loans handled, compound interest with periods per year and contributions.",
   {"finance.py": '''"""Loan and interest maths in Decimal. Amounts in currency units, rounded to cents each period."""
from decimal import Decimal as D, ROUND_HALF_UP, getcontext

getcontext().prec = 40
C = D("0.01")


def cents(x):
    return D(x).quantize(C, rounding=ROUND_HALF_UP)


def payment(principal, annual_rate, months):
    p, r = D(principal), D(annual_rate) / 12
    if months <= 0 or p < 0 or r < 0: raise ValueError("bad loan terms")
    if r == 0: return cents(p / months)
    return cents(p * r / (1 - (1 + r) ** -months))


def schedule(principal, annual_rate, months):
    """-> list of dicts: n, payment, interest, principal, balance. The last row closes the loan exactly."""
    bal, pay, r, rows = cents(principal), payment(principal, annual_rate, months), D(annual_rate) / 12, []
    for n in range(1, months + 1):
        interest = cents(bal * r)
        prin = bal if n == months else pay - interest
        this_pay = prin + interest
        bal = cents(bal - prin)
        rows.append(dict(n=n, payment=this_pay, interest=interest, principal=prin, balance=bal))
    return rows


def compound(principal, annual_rate, years, periods_per_year=12, contribution=0):
    """Future value with a contribution at the end of every period."""
    p, r, n = D(principal), D(annual_rate) / periods_per_year, int(years * periods_per_year)
    bal = p
    for _ in range(n):
        bal = cents(bal * (1 + r) + D(contribution))
    return bal


def apy(annual_rate, periods_per_year):
    return (1 + D(annual_rate) / periods_per_year) ** periods_per_year - 1
'''
    , "test_finance.py": '''import unittest
from decimal import Decimal as D
import finance as F


class T(unittest.TestCase):
    def test_known_payment(self):
        # 200,000 at 6% for 30 years -> 1199.10 (standard mortgage table value)
        self.assertEqual(F.payment("200000", "0.06", 360), D("1199.10"))

    def test_schedule_closes_to_zero_and_principal_adds_up(self):
        rows = F.schedule("10000", "0.0725", 24)
        self.assertEqual(rows[-1]["balance"], D("0.00")); self.assertEqual(sum(r["principal"] for r in rows), D("10000.00"))
        self.assertEqual(sum(r["payment"] for r in rows), sum(r["principal"] + r["interest"] for r in rows))

    def test_last_payment_absorbs_rounding_drift(self):
        rows = F.schedule("1000", "0.09", 7); pay = rows[0]["payment"]
        self.assertTrue(all(r["payment"] == pay for r in rows[:-1])); self.assertLess(abs(rows[-1]["payment"] - pay), D("0.10"))

    def test_zero_interest(self):
        rows = F.schedule("1200", "0", 12); self.assertTrue(all(r["interest"] == 0 and r["payment"] == D("100.00") for r in rows))

    def test_interest_declines_principal_grows(self):
        rows = F.schedule("5000", "0.05", 12); self.assertTrue(all(rows[i]["interest"] >= rows[i + 1]["interest"] for i in range(11)))
        self.assertTrue(all(rows[i]["principal"] <= rows[i + 1]["principal"] + D("0.02") for i in range(10)))

    def test_compound_matches_closed_form(self):
        self.assertAlmostEqual(float(F.compound("1000", "0.05", 10, 1)), 1000 * 1.05 ** 10, delta=0.1)
        self.assertGreater(F.compound("0", "0.05", 1, 12, "100"), D("1200"))

    def test_apy(self):
        self.assertAlmostEqual(float(F.apy("0.12", 12)), 0.126825, places=6)

    def test_bad_terms(self):
        for args in (("100", "0.05", 0), ("-1", "0.05", 12), ("100", "-0.1", 12)):
            with self.assertRaises(ValueError): F.payment(*args)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Monthly compounding/payments, rate nominal annual; day-count conventions (actual/360) differ by product", "Not financial advice - illustrative maths; verify against your lender's disclosure"], compat={"python": ">=3.9"},
   tags=["loan", "amortization", "mortgage", "interest", "compound", "finance", "fintech", "calculator", "decimal"],
   avoid="Float arithmetic; rounding only at the end (schedule drifts from the bank's); leaving a few cents on the last payment."),

RC("py-luhn-iban-validators", "Validators: Luhn (cards), IBAN mod-97, ISBN-13, with format normalisation", "python",
   "A form must catch typos in card, IBAN or ISBN numbers before sending them to an API - without ever storing or logging the real values.",
   "Strip spaces/dashes, check charset and length, Luhn checksum for card PANs with network detection by prefix, IBAN: move the first four chars, letters to numbers, mod 97 == 1 plus per-country length table, ISBN-13 weighted checksum; all return normalised values and reasons, and mask output for logs.",
   {"validators.py": '''"""Checksum validators. They detect typos, not fraud: a valid number can still be fake or closed."""
import re

IBAN_LEN = {"DE": 22, "GB": 22, "FR": 27, "ES": 24, "IT": 27, "NL": 18, "BE": 16, "CH": 21, "AT": 20, "PL": 28, "PT": 25, "IE": 22, "SE": 24}


def digits(s):
    return re.sub(r"[ \\-]", "", s)


def luhn_ok(number):
    d = digits(number)
    if not d.isdigit() or not 12 <= len(d) <= 19: return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2: n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def card_network(number):
    d = digits(number)
    if re.match(r"4\\d{12}(\\d{3}(\\d{3})?)?$", d): return "visa"
    if re.match(r"(5[1-5]\\d{4}|2(2[2-9]\\d|[3-6]\\d\\d|7[01]\\d|720)\\d{2})\\d{10}$", d): return "mastercard"
    if re.match(r"3[47]\\d{13}$", d): return "amex"
    return None


def mask(number):
    d = digits(number)
    return "*" * (len(d) - 4) + d[-4:] if len(d) > 4 else "*" * len(d)


def iban_ok(iban):
    s = re.sub(r"\\s", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\\d{2}[A-Z0-9]{10,30}", s): return False
    if s[:2] in IBAN_LEN and len(s) != IBAN_LEN[s[:2]]: return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def isbn13_ok(isbn):
    d = digits(isbn)
    return d.isdigit() and len(d) == 13 and sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d)) % 10 == 0
'''
    , "test_validators.py": '''import unittest
from validators import luhn_ok, card_network, mask, iban_ok, isbn13_ok


class T(unittest.TestCase):
    def test_luhn_test_numbers(self):
        for good in ("4242 4242 4242 4242", "4111-1111-1111-1111", "5555555555554444", "378282246310005", "2223003122003222"): self.assertTrue(luhn_ok(good), good)
        for bad in ("4242 4242 4242 4241", "1234567890123456", "abcd", "42", "4" * 25): self.assertFalse(luhn_ok(bad), bad)

    def test_single_digit_typos_and_swaps_are_caught(self):
        base = "4242424242424242"
        for i in range(len(base)):
            for d in "0123456789":
                if d != base[i]: self.assertFalse(luhn_ok(base[:i] + d + base[i + 1:]), (i, d))

    def test_network_detection(self):
        self.assertEqual([card_network(n) for n in ("4242424242424242", "5555555555554444", "2223003122003222", "378282246310005", "6011111111111117")], ["visa", "mastercard", "mastercard", "amex", None])

    def test_masking_for_logs(self):
        self.assertEqual(mask("4242 4242 4242 4242"), "************4242")

    def test_iban(self):
        for good in ("DE89 3704 0044 0532 0130 00", "GB82WEST12345698765432", "FR1420041010050500013M02606", "NL91ABNA0417164300"): self.assertTrue(iban_ok(good), good)
        for bad in ("DE89 3704 0044 0532 0130 01", "GB82WEST1234569876543", "XX00", "DE89370400440532013", "de893704004405320130000"): self.assertFalse(iban_ok(bad), bad)

    def test_isbn13(self):
        self.assertTrue(isbn13_ok("978-0-306-40615-7")); self.assertFalse(isbn13_ok("978-0-306-40615-8")); self.assertFalse(isbn13_ok("12345"))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Length table covers common IBAN countries only (others are checked by checksum + generic length)", "Test card numbers are public processor test values, not real cards"], compat={"python": ">=3.9"},
   tags=["luhn", "iban", "validation", "credit-card", "isbn", "checksum", "fintech", "forms", "payments"],
   avoid="Logging full card numbers; treating checksum validity as proof the card/account exists; regex-only IBAN checks."),

RC("py-tax-brackets-decimal", "Progressive tax brackets: marginal vs effective rate, thresholds, deductions (Decimal)", "python",
   "Salary, payroll or tax-estimate features computing tax by applying one rate to everything (wrong) instead of per band.",
   "Bracket table of (upper_limit, rate) applied to the slice of income inside each band, deductions before brackets and credits after, breakdown per band for display, marginal rate at an income, effective rate, boundary incomes handled exactly, validation of ascending thresholds. Example numbers are fictional.",
   {"tax.py": '''"""Progressive tax. Bracket data is configuration - the numbers below are FICTIONAL, use your jurisdiction's current tables."""
from decimal import Decimal as D, ROUND_HALF_UP

BRACKETS = [(D(10000), D("0")), (D(40000), D("0.20")), (D(100000), D("0.35")), (None, D("0.45"))]    # (upper limit or None, rate)


def validate(brackets):
    last = D(-1)
    for i, (upper, rate) in enumerate(brackets):
        if not D(0) <= rate < 1: raise ValueError("rate must be in [0, 1)")
        if upper is None:
            if i != len(brackets) - 1: raise ValueError("only the last bracket may be unbounded")
        elif upper <= last: raise ValueError("bracket limits must be strictly ascending")
        else: last = upper
    if brackets[-1][0] is not None: raise ValueError("last bracket must be unbounded")


def breakdown(income, brackets=BRACKETS, deduction=D(0), credit=D(0)):
    validate(brackets)
    taxable = max(D(0), D(income) - D(deduction))
    rows, lower = [], D(0)
    for upper, rate in brackets:
        top = taxable if upper is None else min(taxable, upper)
        amount = max(D(0), top - lower)
        rows.append(dict(lower=lower, upper=upper, rate=rate, amount=amount, tax=(amount * rate)))
        if upper is None or taxable <= upper: break
        lower = upper
    total = max(D(0), sum(r["tax"] for r in rows) - D(credit)).quantize(D("0.01"), ROUND_HALF_UP)
    return dict(taxable=taxable, rows=rows, tax=total)


def effective_rate(income, **kw):
    inc = D(income)
    return D(0) if inc <= 0 else (breakdown(inc, **kw)["tax"] / inc)


def marginal_rate(income, brackets=BRACKETS, deduction=D(0)):
    taxable = max(D(0), D(income) - D(deduction))
    for upper, rate in brackets:
        if upper is None or taxable < upper: return rate
    return brackets[-1][1]
'''
    , "test_tax.py": '''import unittest
from decimal import Decimal as D
import tax as X


class T(unittest.TestCase):
    def test_per_band_not_flat(self):
        # 50,000 -> 0 on first 10k, 20% of 30k = 6000, 35% of 10k = 3500
        r = X.breakdown(50000); self.assertEqual(r["tax"], D("9500.00")); self.assertEqual([x["amount"] for x in r["rows"]], [D(10000), D(30000), D(10000)])

    def test_boundaries_are_exact(self):
        self.assertEqual(X.breakdown(10000)["tax"], D("0.00")); self.assertEqual(X.breakdown(40000)["tax"], D("6000.00"))
        self.assertEqual(X.breakdown(D("40000.01"))["tax"], D("6000.00"))      # 0.01 * 35% = 0.0035 rounds to 0.00

    def test_top_bracket(self):
        self.assertEqual(X.breakdown(200000)["tax"], D("6000") + D("21000") + D("45000"))

    def test_deduction_before_credit_after(self):
        base = X.breakdown(50000)["tax"]; self.assertLess(X.breakdown(50000, deduction=D(5000))["tax"], base)
        self.assertEqual(X.breakdown(50000, credit=D(1000))["tax"], base - D(1000)); self.assertEqual(X.breakdown(50000, credit=D(99999))["tax"], D("0.00"))

    def test_marginal_and_effective(self):
        self.assertEqual(X.marginal_rate(5000), D("0")); self.assertEqual(X.marginal_rate(40000), D("0.35")); self.assertEqual(X.marginal_rate(500000), D("0.45"))
        self.assertAlmostEqual(float(X.effective_rate(50000)), 0.19, places=4); self.assertEqual(X.effective_rate(0), 0)

    def test_more_income_never_means_less_take_home(self):
        prev = D(0)
        for inc in range(0, 300000, 997):
            net = inc - X.breakdown(inc)["tax"]; self.assertGreaterEqual(net, prev); prev = net

    def test_invalid_tables_rejected(self):
        for bad in ([(D(10), D("0.1")), (D(5), D("0.2")), (None, D("0.3"))], [(D(10), D("0.1"))], [(None, D("0.1")), (D(5), D("0.2"))], [(None, D("1.5"))]):
            with self.assertRaises(ValueError): X.validate(bad)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Bracket numbers are fictional: tax law, allowances and phase-outs are jurisdiction-specific", "Not tax advice"], compat={"python": ">=3.9"},
   tags=["tax", "brackets", "payroll", "salary", "calculator", "fintech", "decimal", "progressive"],
   avoid="Applying the top marginal rate to the whole income; float money; hard-coding tax tables in code instead of data."),

RC("py-expense-categorizer-budget", "Expense categorisation rules engine and monthly budget tracker", "python",
   "A budgeting app or script imports bank transactions and must label them and show spend vs budget per category.",
   "Ordered rules (merchant regex or keyword, amount range, first match wins, explicit 'uncategorised'), normalisation of messy merchant strings, per-month aggregation in integer cents, budget status (ok/warn at 80%/over) and remaining, rollover optional, rule hit report to improve rules.",
   {"budget.py": '''"""Categorise transactions and compare to a monthly budget. Amounts are integer cents (negative = spend)."""
import re
from collections import defaultdict


def normalise(desc):
    d = re.sub(r"\\b(pos|card|purchase|debit|visa|\\d{4,})\\b", " ", desc.lower())
    return re.sub(r"[^a-z0-9& ]+", " ", d).split() and " ".join(re.sub(r"[^a-z0-9& ]+", " ", d).split())


class Rule:
    def __init__(self, category, pattern=None, min_cents=None, max_cents=None):
        self.category, self.re = category, re.compile(pattern) if pattern else None
        self.min, self.max = min_cents, max_cents

    def match(self, desc, cents):
        if self.re and not self.re.search(desc): return False
        a = abs(cents)
        return (self.min is None or a >= self.min) and (self.max is None or a <= self.max)


def categorise(txns, rules):
    """txns: [(date 'YYYY-MM-DD', description, cents)] -> list of (date, desc, cents, category). First matching rule wins."""
    out = []
    for date, desc, cents in txns:
        n = normalise(desc) or ""
        cat = next((r.category for r in rules if r.match(n, cents)), "uncategorised")
        out.append((date, desc, cents, cat))
    return out


def monthly_spend(rows):
    m = defaultdict(lambda: defaultdict(int))
    for date, _, cents, cat in rows:
        if cents < 0: m[date[:7]][cat] += -cents
    return {k: dict(v) for k, v in m.items()}


def status(spent, budget, warn=0.8):
    if budget <= 0: return "over" if spent > 0 else "ok"
    return "over" if spent > budget else "warn" if spent >= budget * warn else "ok"


def report(rows, budgets, month):
    spent = monthly_spend(rows).get(month, {})
    return {c: dict(spent=spent.get(c, 0), budget=b, remaining=b - spent.get(c, 0), status=status(spent.get(c, 0), b)) for c, b in budgets.items()} | \\
           ({"uncategorised": dict(spent=spent["uncategorised"], budget=0, remaining=-spent["uncategorised"], status="over")} if spent.get("uncategorised") else {})
'''
    , "test_budget.py": '''import unittest
from budget import Rule, categorise, monthly_spend, status, report, normalise

RULES = [Rule("groceries", r"tesco|lidl|aldi|whole foods"), Rule("transport", r"uber|shell|bp "), Rule("rent", r"landlord", min_cents=50000), Rule("subscriptions", r"netflix|spotify")]
TX = [("2026-03-01", "POS 4829 LIDL BERLIN", -4520), ("2026-03-02", "UBER *TRIP 8812", -1830), ("2026-03-03", "NETFLIX.COM", -1299),
      ("2026-03-04", "LANDLORD GMBH", -85000), ("2026-03-05", "LANDLORD GMBH small refund", 2000), ("2026-03-06", "Mystery Shop", -999), ("2026-04-01", "ALDI", -3000)]


class T(unittest.TestCase):
    def test_normalise_strips_noise(self):
        self.assertEqual(normalise("POS 4829 LIDL  BERLIN*"), "lidl berlin")

    def test_first_match_and_default(self):
        rows = categorise(TX, RULES); self.assertEqual([r[3] for r in rows], ["groceries", "transport", "subscriptions", "rent", "uncategorised", "uncategorised", "groceries"])

    def test_amount_range_rule(self):
        self.assertEqual(categorise([("2026-03-01", "landlord gmbh", -100)], RULES)[0][3], "uncategorised")

    def test_income_is_not_spend(self):
        self.assertEqual(monthly_spend(categorise(TX, RULES))["2026-03"].get("rent"), 85000)
        self.assertNotIn("2026-03-05", [r for r in monthly_spend(categorise(TX, RULES))])

    def test_status_thresholds(self):
        self.assertEqual([status(s, 10000) for s in (7999, 8000, 10000, 10001)], ["ok", "warn", "warn", "over"]); self.assertEqual(status(1, 0), "over")

    def test_report_with_remaining_and_uncategorised(self):
        r = report(categorise(TX, RULES), {"groceries": 5000, "transport": 1000, "rent": 90000}, "2026-03")
        self.assertEqual((r["groceries"]["status"], r["transport"]["status"], r["rent"]["status"]), ("warn", "over", "warn")); self.assertEqual(r["transport"]["remaining"], -830)
        self.assertEqual(r["uncategorised"]["spent"], 999)

    def test_months_separate(self):
        self.assertEqual(monthly_spend(categorise(TX, RULES))["2026-04"], {"groceries": 3000})


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single currency; transfers between own accounts need an explicit rule to avoid double counting"], compat={"python": ">=3.9"},
   tags=["budget", "expenses", "categorize", "rules", "finance", "fintech", "bank", "transactions", "personal-finance"],
   avoid="Categorising by exact merchant string; floats for amounts; counting income or internal transfers as spend."),

# ------------------------------------------------------------------ ML engineering
RC("py-ml-split-metrics", "Stratified train/val/test split and classification metrics (confusion matrix, precision, recall, F1)", "python",
   "Evaluating a classifier honestly: splits that keep class balance and no leakage, metrics you can verify by hand.",
   "Seeded stratified split (each class split with the same ratios, deterministic order), group-aware split option so one user's rows stay together, confusion matrix for any labels, per-class precision/recall/F1 with zero-division handling, macro/micro/weighted averages, accuracy vs balanced accuracy.",
   {"evalkit.py": '''"""Splits and metrics without sklearn (use sklearn in production; this shows exactly what the numbers mean)."""
import random
from collections import Counter, defaultdict


def stratified_split(labels, ratios=(0.7, 0.15, 0.15), seed=0):
    """-> three index lists. Each class contributes proportionally to every split."""
    if abs(sum(ratios) - 1) > 1e-9: raise ValueError("ratios must sum to 1")
    rng, by = random.Random(seed), defaultdict(list)
    for i, y in enumerate(labels): by[y].append(i)
    parts = [[] for _ in ratios]
    for y in sorted(by, key=str):
        idx = by[y][:]; rng.shuffle(idx)
        cuts, acc = [], 0
        for r in ratios[:-1]:
            acc += r; cuts.append(round(acc * len(idx)))
        start = 0
        for p, end in zip(parts, cuts + [len(idx)]): p.extend(idx[start:end]); start = end
    return [sorted(p) for p in parts]


def group_split(groups, test_frac=0.2, seed=0):
    """Whole groups go to one side so the same user/patient/document never appears in both train and test."""
    uniq = sorted(set(groups), key=str); random.Random(seed).shuffle(uniq)
    test_groups = set(uniq[:max(1, round(len(uniq) * test_frac))])
    return [i for i, g in enumerate(groups) if g not in test_groups], [i for i, g in enumerate(groups) if g in test_groups]


def confusion(y_true, y_pred, labels=None):
    labels = labels or sorted(set(y_true) | set(y_pred), key=str)
    c = Counter(zip(y_true, y_pred))
    return labels, [[c[(t, p)] for p in labels] for t in labels]        # rows = truth, cols = prediction


def prf(y_true, y_pred, average="macro"):
    labels, m = confusion(y_true, y_pred)
    out = {}
    for i, lab in enumerate(labels):
        tp, fp, fn = m[i][i], sum(m[r][i] for r in range(len(labels))) - m[i][i], sum(m[i]) - m[i][i]
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        out[lab] = dict(precision=p, recall=r, f1=(2 * p * r / (p + r) if p + r else 0.0), support=sum(m[i]))
    n = len(labels)
    if average == "macro": return {k: sum(v[k] for v in out.values()) / n for k in ("precision", "recall", "f1")}
    if average == "weighted":
        tot = sum(v["support"] for v in out.values()); return {k: sum(v[k] * v["support"] for v in out.values()) / tot for k in ("precision", "recall", "f1")}
    return out


def accuracy(y_true, y_pred):
    return sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true)


def balanced_accuracy(y_true, y_pred):
    r = prf(y_true, y_pred, average=None)
    return sum(v["recall"] for v in r.values()) / len(r)
'''
    , "test_evalkit.py": '''import unittest
from collections import Counter
import evalkit as E


class T(unittest.TestCase):
    def test_split_is_disjoint_complete_and_stratified(self):
        y = ["a"] * 80 + ["b"] * 20; tr, va, te = E.stratified_split(y, seed=1)
        self.assertEqual(sorted(tr + va + te), list(range(100))); self.assertFalse(set(tr) & set(te) or set(tr) & set(va) or set(va) & set(te))
        self.assertEqual(Counter(y[i] for i in te), {"a": 12, "b": 3}); self.assertEqual(Counter(y[i] for i in tr), {"a": 56, "b": 14})

    def test_split_deterministic_and_seed_sensitive(self):
        y = list("aabbccdd" * 10); self.assertEqual(E.stratified_split(y, seed=3), E.stratified_split(y, seed=3)); self.assertNotEqual(E.stratified_split(y, seed=3)[2], E.stratified_split(y, seed=4)[2])

    def test_group_split_has_no_leakage(self):
        groups = [i // 4 for i in range(100)]; tr, te = E.group_split(groups, 0.2, seed=2)
        self.assertFalse({groups[i] for i in tr} & {groups[i] for i in te}); self.assertEqual(len(tr) + len(te), 100)

    def test_confusion_matrix_rows_are_truth(self):
        labels, m = E.confusion(["cat", "cat", "dog", "dog", "dog"], ["cat", "dog", "dog", "dog", "cat"])
        self.assertEqual((labels, m), (["cat", "dog"], [[1, 1], [1, 2]]))

    def test_precision_recall_by_hand(self):
        yt, yp = [1, 1, 1, 0, 0, 0, 0, 0], [1, 1, 0, 1, 0, 0, 0, 0]
        per = E.prf(yt, yp, average=None)[1]; self.assertAlmostEqual(per["precision"], 2 / 3); self.assertAlmostEqual(per["recall"], 2 / 3); self.assertAlmostEqual(per["f1"], 2 / 3)
        self.assertEqual(per["support"], 3)

    def test_never_predicted_class_is_zero_not_error(self):
        r = E.prf([0, 0, 1, 1], [0, 0, 0, 0], average=None); self.assertEqual((r[1]["precision"], r[1]["recall"], r[1]["f1"]), (0.0, 0.0, 0.0))

    def test_accuracy_hides_imbalance_balanced_does_not(self):
        yt, yp = [0] * 95 + [1] * 5, [0] * 100; self.assertAlmostEqual(E.accuracy(yt, yp), 0.95); self.assertAlmostEqual(E.balanced_accuracy(yt, yp), 0.5)

    def test_macro_vs_weighted(self):
        yt, yp = [0] * 9 + [1], [0] * 9 + [0]
        self.assertLess(E.prf(yt, yp)["f1"], E.prf(yt, yp, average="weighted")["f1"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single-label classification; use scikit-learn metrics for multilabel/ranking/regression"], compat={"python": ">=3.9"},
   tags=["machine-learning", "ml", "metrics", "precision", "recall", "f1", "confusion-matrix", "train-test-split", "stratified", "evaluation"],
   avoid="Random splits that put one user's rows in train and test; reporting accuracy on imbalanced data; shuffling without a seed."),

RC("py-training-loop-utils", "Training loop utilities: early stopping, LR schedules (warmup + cosine), shuffled batch iterator", "python",
   "A custom training loop needs the unglamorous parts right: stop when validation stalls, warm the learning rate up, batch without dropping or repeating samples.",
   "EarlyStopping with patience/min_delta/mode and best-epoch tracking, schedules as pure functions of step (linear warmup, cosine decay to a floor, step decay), epoch-seeded shuffled batches covering every sample exactly once with optional drop_last, deterministic across runs.",
   {"trainutil.py": '''"""Framework-agnostic training helpers (PyTorch/JAX/numpy loops can all call these)."""
import math, random


class EarlyStopping:
    def __init__(self, patience=5, min_delta=0.0, mode="min"):
        if mode not in ("min", "max"): raise ValueError("mode must be min or max")
        self.patience, self.min_delta, self.mode = patience, min_delta, mode
        self.best, self.best_epoch, self.bad, self.epoch = None, -1, 0, -1

    def improved(self, v):
        if self.best is None: return True
        return v < self.best - self.min_delta if self.mode == "min" else v > self.best + self.min_delta

    def step(self, value):
        """Feed the validation metric once per epoch -> True when training should stop."""
        self.epoch += 1
        if self.improved(value): self.best, self.best_epoch, self.bad = value, self.epoch, 0
        else: self.bad += 1
        return self.bad >= self.patience


def warmup_cosine(step, total, base_lr, warmup=0, min_lr=0.0):
    if step < warmup: return base_lr * (step + 1) / warmup
    if step >= total: return min_lr
    t = (step - warmup) / max(1, total - warmup)
    return min_lr + 0.5 * (base_lr - min_lr) * (1 + math.cos(math.pi * t))


def step_decay(step, base_lr, drop=0.5, every=10):
    return base_lr * drop ** (step // every)


def batches(n, batch_size, epoch=0, seed=0, shuffle=True, drop_last=False):
    """Yield index lists. Same (seed, epoch) -> same order; different epochs -> different orders; each index once per epoch."""
    idx = list(range(n))
    if shuffle: random.Random(seed * 1_000_003 + epoch).shuffle(idx)
    for i in range(0, n, batch_size):
        b = idx[i:i + batch_size]
        if len(b) < batch_size and drop_last: return
        yield b
'''
    , "test_trainutil.py": '''import unittest, math
import trainutil as T_


class T(unittest.TestCase):
    def test_early_stopping_min_mode(self):
        es, stops = T_.EarlyStopping(patience=3), []
        for v in [1.0, 0.8, 0.7, 0.71, 0.72, 0.73, 0.5]: stops.append(es.step(v))
        self.assertEqual(stops, [False, False, False, False, False, True, False]); self.assertEqual((es.best, es.best_epoch), (0.5, 6))   # stopping is a signal; a later improvement resets the counter

    def test_min_delta_ignores_noise(self):
        es = T_.EarlyStopping(patience=2, min_delta=0.01)
        self.assertFalse(es.step(1.0)); self.assertFalse(es.step(0.995)); self.assertTrue(es.step(0.996))

    def test_max_mode(self):
        es = T_.EarlyStopping(patience=2, mode="max"); [es.step(v) for v in (0.5, 0.6, 0.55)]; self.assertEqual(es.best_epoch, 1); self.assertTrue(es.step(0.58))
        with self.assertRaises(ValueError): T_.EarlyStopping(mode="up")

    def test_warmup_then_cosine_to_floor(self):
        lr = lambda s: T_.warmup_cosine(s, 100, 1.0, warmup=10, min_lr=0.1)
        self.assertAlmostEqual(lr(0), 0.1); self.assertAlmostEqual(lr(9), 1.0); self.assertAlmostEqual(lr(10), 1.0)
        self.assertAlmostEqual(lr(55), 0.55, places=2); self.assertAlmostEqual(lr(100), 0.1); self.assertAlmostEqual(lr(1000), 0.1)
        vals = [lr(s) for s in range(10, 100)]; self.assertTrue(all(a >= b - 1e-12 for a, b in zip(vals, vals[1:])))

    def test_step_decay(self):
        self.assertEqual([T_.step_decay(s, 1.0, 0.5, 10) for s in (0, 9, 10, 25)], [1.0, 1.0, 0.5, 0.25])

    def test_every_sample_exactly_once_per_epoch(self):
        seen = [i for b in T_.batches(103, 10, epoch=2) for i in b]; self.assertEqual(sorted(seen), list(range(103)))

    def test_deterministic_but_epoch_dependent(self):
        a, b, c = (list(T_.batches(50, 7, epoch=e, seed=1)) for e in (0, 0, 1)); self.assertEqual(a, b); self.assertNotEqual(a, c)

    def test_drop_last_and_no_shuffle(self):
        self.assertEqual([len(b) for b in T_.batches(25, 10, drop_last=True)], [10, 10]); self.assertEqual(list(T_.batches(5, 2, shuffle=False)), [[0, 1], [2, 3], [4]])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Plain Python; frameworks (PyTorch DataLoader/LR schedulers) provide equivalents — use them when available"], compat={"python": ">=3.9"},
   tags=["machine-learning", "ml", "training", "early-stopping", "learning-rate", "scheduler", "cosine", "warmup", "batching", "deep-learning"],
   avoid="Reshuffling with the same seed every epoch; stopping on training loss; dropping the last partial batch silently."),

RC("py-kmeans-scaler-cosine", "k-means (k-means++), standard scaler and cosine-similarity search, pure Python", "python",
   "Clustering rows, normalising features and finding the nearest embeddings in small projects or prototypes without installing numpy.",
   "k-means++ initialisation with a seed, Lloyd iterations until assignments stop changing, empty clusters re-seeded; StandardScaler fit on train only with zero-variance guard and inverse transform; cosine top-k with L2-normalised vectors and tie-stable ordering.",
   {"mlbasics.py": '''"""Small, readable ML building blocks (numpy/sklearn/faiss for anything big)."""
import math, random


def dist2(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b))


def kmeans(points, k, seed=0, max_iter=100):
    if not 1 <= k <= len(points): raise ValueError("k must be between 1 and the number of points")
    rng = random.Random(seed)
    centers = [list(rng.choice(points))]
    while len(centers) < k:                                           # k-means++: pick far points with probability ~ D^2
        d = [min(dist2(p, c) for c in centers) for p in points]
        if sum(d) == 0: centers.append(list(rng.choice(points))); continue
        r, acc = rng.random() * sum(d), 0
        for p, w in zip(points, d):
            acc += w
            if acc >= r: centers.append(list(p)); break
    labels = [None] * len(points)
    for _ in range(max_iter):
        new = [min(range(k), key=lambda j: dist2(p, centers[j])) for p in points]
        if new == labels: break
        labels = new
        for j in range(k):
            members = [p for p, l in zip(points, labels) if l == j]
            centers[j] = [sum(c) / len(members) for c in zip(*members)] if members else list(rng.choice(points))
    inertia = sum(dist2(p, centers[l]) for p, l in zip(points, labels))
    return labels, centers, inertia


class StandardScaler:
    def fit(self, rows):
        n, cols = len(rows), list(zip(*rows))
        self.mean = [sum(c) / n for c in cols]
        self.std = [math.sqrt(sum((x - m) ** 2 for x in c) / n) or 1.0 for c, m in zip(cols, self.mean)]      # constant column -> std 1, not a division by zero
        return self

    def transform(self, rows):
        return [[(x - m) / s for x, m, s in zip(r, self.mean, self.std)] for r in rows]

    def inverse(self, rows):
        return [[x * s + m for x, m, s in zip(r, self.mean, self.std)] for r in rows]


def normalise(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else list(v)


def cosine_topk(query, vectors, k=3):
    q = normalise(query)
    scored = [(sum(a * b for a, b in zip(q, normalise(v))), i) for i, v in enumerate(vectors)]
    return sorted(scored, key=lambda t: (-t[0], t[1]))[:k]
'''
    , "test_mlbasics.py": '''import unittest, random
import mlbasics as M


def blobs(seed=0):
    r = random.Random(seed); pts = []
    for cx, cy in ((0, 0), (10, 10), (0, 10)):
        pts += [[cx + r.gauss(0, 0.5), cy + r.gauss(0, 0.5)] for _ in range(30)]
    return pts


class T(unittest.TestCase):
    def test_finds_three_blobs(self):
        labels, centers, inertia = M.kmeans(blobs(), 3, seed=1)
        groups = [set(labels[i * 30:(i + 1) * 30]) for i in range(3)]
        self.assertTrue(all(len(g) == 1 for g in groups)); self.assertEqual(len({next(iter(g)) for g in groups}), 3); self.assertLess(inertia, 90 * 1.0)

    def test_deterministic_and_bad_k(self):
        self.assertEqual(M.kmeans(blobs(), 3, seed=5)[0], M.kmeans(blobs(), 3, seed=5)[0])
        for k in (0, 1000):
            with self.assertRaises(ValueError): M.kmeans(blobs(), k)

    def test_identical_points_do_not_crash(self):
        labels, centers, inertia = M.kmeans([[1, 1]] * 6, 3); self.assertEqual(inertia, 0)

    def test_scaler_zero_mean_unit_variance_and_inverse(self):
        rows = [[1, 100, 5], [2, 200, 5], [3, 300, 5]]; sc = M.StandardScaler().fit(rows); z = sc.transform(rows)
        for col in list(zip(*z))[:2]: self.assertAlmostEqual(sum(col) / 3, 0); self.assertAlmostEqual(sum(x * x for x in col) / 3, 1)
        self.assertEqual([r[2] for r in z], [0.0, 0.0, 0.0])
        for a, b in zip(sc.inverse(z), rows): self.assertTrue(all(abs(x - y) < 1e-9 for x, y in zip(a, b)))

    def test_scaler_uses_train_statistics_only(self):
        sc = M.StandardScaler().fit([[0], [10]]); self.assertEqual(sc.transform([[5]]), [[0.0]]); self.assertEqual(sc.transform([[20]]), [[3.0]])

    def test_cosine_topk(self):
        vecs = [[1, 0], [0.9, 0.1], [0, 1], [-1, 0]]; top = M.cosine_topk([1, 0], vecs, 3)
        self.assertEqual([i for _, i in top], [0, 1, 2]); self.assertAlmostEqual(top[0][0], 1.0)
        self.assertEqual([i for _, i in M.cosine_topk([5, 0], vecs, 1)], [0])                        # scale invariant

    def test_zero_vector_is_safe_and_ties_stable(self):
        self.assertEqual(M.cosine_topk([0, 0], [[1, 0], [0, 1]], 2), [(0.0, 0), (0.0, 1)])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Pure Python is O(n*k*iters); use numpy/scikit-learn/faiss beyond a few thousand points"], compat={"python": ">=3.9"},
   tags=["machine-learning", "ml", "kmeans", "clustering", "scaler", "normalize", "standardize", "cosine-similarity", "embeddings", "vector-search"],
   avoid="Fitting the scaler on test data; random init without k-means++ or a seed; comparing unnormalised vectors with a dot product."),

RC("py-experiment-config", "Experiment config: dataclass + YAML/JSON file + dotted CLI overrides + validation + config hash", "python",
   "Runs must be reproducible and comparable: one config object, overridable from the command line, validated, and fingerprinted into the run id.",
   "Nested dataclasses loaded from YAML/JSON, `a.b.c=value` overrides with type coercion from the field annotations, unknown keys are errors (typos don't silently do nothing), validation hooks, canonical JSON dump and short SHA-256 hash for run directories.",
   {"expconfig.py": '''"""Typed experiment configs. Unknown keys are errors; overrides are coerced to the declared type."""
import dataclasses, hashlib, json
from dataclasses import dataclass, field, fields, is_dataclass


@dataclass
class Optim:
    lr: float = 1e-3
    weight_decay: float = 0.0
    schedule: str = "cosine"


@dataclass
class Config:
    name: str = "run"
    seed: int = 0
    epochs: int = 10
    batch_size: int = 32
    use_amp: bool = False
    optim: Optim = field(default_factory=Optim)
    tags: list = field(default_factory=list)

    def validate(self):
        if self.epochs < 1 or self.batch_size < 1: raise ValueError("epochs and batch_size must be >= 1")
        if self.optim.lr <= 0: raise ValueError("optim.lr must be > 0")
        if self.optim.schedule not in ("cosine", "step", "constant"): raise ValueError(f"unknown schedule {self.optim.schedule!r}")


def _coerce(value, typ):
    if isinstance(value, typ if isinstance(typ, type) else object) and typ is not float: return value
    if typ is bool:
        if str(value).lower() in ("1", "true", "yes", "on"): return True
        if str(value).lower() in ("0", "false", "no", "off"): return False
        raise ValueError(f"not a boolean: {value!r}")
    if typ in (int, float, str): return typ(value)
    if typ is list: return value if isinstance(value, list) else str(value).split(",")
    return value


def build(cls, data, path=""):
    known = {f.name: f for f in fields(cls)}
    extra = set(data) - set(known)
    if extra: raise KeyError(f"unknown config key(s): {', '.join(path + k for k in sorted(extra))}")
    kwargs = {}
    for k, v in data.items():
        t = known[k].type
        kwargs[k] = build(t, v, path + k + ".") if is_dataclass(t) and isinstance(v, dict) else _coerce(v, t)
    return cls(**kwargs)


def apply_overrides(data, overrides):
    """overrides: ['optim.lr=0.01', 'epochs=5'] applied onto a nested dict."""
    data = json.loads(json.dumps(data))
    for o in overrides:
        key, sep, val = o.partition("=")
        if not sep: raise ValueError(f"override {o!r} must look like key=value")
        node = data
        parts = key.split(".")
        for p in parts[:-1]: node = node.setdefault(p, {})
        node[parts[-1]] = val
    return data


def load(base: dict, overrides=()):
    cfg = build(Config, apply_overrides(base, overrides)); cfg.validate(); return cfg


def fingerprint(cfg, n=8):
    canon = json.dumps(dataclasses.asdict(cfg), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()[:n]
'''
    , "test_expconfig.py": '''import unittest, yaml
import expconfig as X

YAML = """
name: baseline
seed: 7
optim: {lr: 0.003, schedule: step}
tags: [a, b]
"""


class T(unittest.TestCase):
    def test_load_yaml_with_defaults(self):
        c = X.load(yaml.safe_load(YAML)); self.assertEqual((c.name, c.seed, c.epochs, c.optim.lr, c.optim.schedule, c.tags), ("baseline", 7, 10, 0.003, "step", ["a", "b"]))

    def test_dotted_overrides_are_typed(self):
        c = X.load({}, ["optim.lr=0.5", "epochs=3", "use_amp=true", "tags=x,y", "name=foo"])
        self.assertEqual((c.optim.lr, c.epochs, c.use_amp, c.tags, c.name), (0.5, 3, True, ["x", "y"], "foo")); self.assertIsInstance(c.epochs, int)

    def test_typos_are_errors_not_silence(self):
        with self.assertRaisesRegex(KeyError, "optim.learning_rate"): X.load({"optim": {"learning_rate": 1}})
        with self.assertRaisesRegex(KeyError, "epoch"): X.load({}, ["epoch=3"])

    def test_bad_values_rejected(self):
        for ov in (["epochs=abc"], ["use_amp=maybe"], ["epochs=0"], ["optim.lr=-1"], ["optim.schedule=zigzag"], ["noequals"]):
            with self.assertRaises((ValueError, KeyError)): X.load({}, ov)

    def test_base_is_not_mutated(self):
        base = {"optim": {"lr": 0.1}}; X.load(base, ["optim.lr=0.2"]); self.assertEqual(base["optim"]["lr"], 0.1)

    def test_fingerprint_changes_with_content_only(self):
        a, b, c = X.load({}), X.load({}), X.load({}, ["seed=1"]); self.assertEqual(X.fingerprint(a), X.fingerprint(b)); self.assertNotEqual(X.fingerprint(a), X.fingerprint(c)); self.assertEqual(len(X.fingerprint(a)), 8)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["PyYAML is only used by the test to read a YAML file; the module itself takes plain dicts", "Hydra/OmegaConf/pydantic-settings are the full-featured alternatives"], compat={"python": ">=3.9"},
   tags=["machine-learning", "ml", "config", "experiment", "reproducibility", "dataclass", "yaml", "hydra", "overrides", "hash"],
   avoid="argparse flags duplicated by hand for every hyperparameter; silently ignoring unknown config keys; run directories named by timestamp only."),

# ------------------------------------------------------------------ science
RC("py-stats-bootstrap-regression", "Descriptive stats, least-squares line fit and bootstrap confidence intervals (seeded)", "python",
   "Quick, auditable analysis without numpy: mean/variance/median/mode, a regression with R^2, and a bootstrap CI that is reproducible.",
   "Welford one-pass mean/variance (numerically stable), median/mode with ties handled, ordinary least squares via closed form with R^2, percentile bootstrap CI using a seeded RNG, and sanity tests against hand-computed values and known properties.",
   {"sci.py": '''"""Small, auditable statistics. For real work use numpy/scipy/statsmodels."""
import math, random
from collections import Counter


def mean_var(xs, ddof=1):
    """Welford's algorithm: stable even when the mean is huge relative to the spread."""
    n, m, s = 0, 0.0, 0.0
    for x in xs:
        n += 1; d = x - m; m += d / n; s += d * (x - m)
    if n - ddof <= 0: raise ValueError("not enough data")
    return m, s / (n - ddof)


def median(xs):
    s = sorted(xs); n = len(s)
    if not n: raise ValueError("empty")
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def modes(xs):
    c = Counter(xs); top = max(c.values()); return sorted(k for k, v in c.items() if v == top)


def linfit(xs, ys):
    """-> (slope, intercept, r2) by ordinary least squares."""
    n = len(xs)
    if n != len(ys) or n < 2: raise ValueError("need >= 2 paired points")
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0: raise ValueError("x has no variance")
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)); slope = sxy / sxx; icpt = my - slope * mx
    ss_tot = sum((y - my) ** 2 for y in ys); ss_res = sum((y - (icpt + slope * x)) ** 2 for x, y in zip(xs, ys))
    return slope, icpt, (1 - ss_res / ss_tot) if ss_tot else 1.0


def bootstrap_ci(xs, stat=lambda v: sum(v) / len(v), n_boot=2000, alpha=0.05, seed=0):
    rng = random.Random(seed)
    stats = sorted(stat([rng.choice(xs) for _ in xs]) for _ in range(n_boot))
    return stats[int(n_boot * alpha / 2)], stats[min(n_boot - 1, int(n_boot * (1 - alpha / 2)))]
'''
    , "test_sci.py": '''import unittest, random, math
import sci


class T(unittest.TestCase):
    def test_mean_var_hand_values(self):
        m, v = sci.mean_var([2, 4, 4, 4, 5, 5, 7, 9], ddof=0); self.assertEqual((m, v), (5.0, 4.0))
        self.assertAlmostEqual(sci.mean_var([1, 2, 3, 4])[1], 5 / 3)

    def test_stable_with_huge_offset(self):
        xs = [1e9 + d for d in (4, 7, 13, 16)]; self.assertAlmostEqual(sci.mean_var(xs)[1], 30.0, places=6)      # naive sum-of-squares loses all precision here

    def test_median_and_modes(self):
        self.assertEqual((sci.median([3, 1, 2]), sci.median([1, 2, 3, 4])), (2, 2.5)); self.assertEqual(sci.modes([1, 2, 2, 3, 3]), [2, 3])
        with self.assertRaises(ValueError): sci.median([])

    def test_linfit_exact_and_noisy(self):
        s, i, r2 = sci.linfit([0, 1, 2, 3], [1, 3, 5, 7]); self.assertEqual((s, i, r2), (2.0, 1.0, 1.0))
        r = random.Random(1); xs = list(range(100)); ys = [3 * x + 2 + r.gauss(0, 5) for x in xs]; s, i, r2 = sci.linfit(xs, ys)
        self.assertAlmostEqual(s, 3, delta=0.1); self.assertAlmostEqual(i, 2, delta=4); self.assertGreater(r2, 0.99)

    def test_linfit_bad_input(self):
        for args in (([1], [1]), ([1, 1, 1], [1, 2, 3]), ([1, 2], [1])):
            with self.assertRaises(ValueError): sci.linfit(*args)

    def test_bootstrap_reproducible_and_covers_truth(self):
        r = random.Random(3); data = [r.gauss(10, 2) for _ in range(200)]
        a, b = sci.bootstrap_ci(data, seed=1), sci.bootstrap_ci(data, seed=1); self.assertEqual(a, b)
        self.assertTrue(a[0] < sum(data) / len(data) < a[1]); self.assertLess(a[1] - a[0], 1.0)
        lo, hi = sci.bootstrap_ci(data, stat=sci.median, seed=2); self.assertTrue(lo < sci.median(data) < hi)

    def test_ci_narrows_with_more_data(self):
        r = random.Random(4); small = [r.gauss(0, 1) for _ in range(20)]; big = [r.gauss(0, 1) for _ in range(500)]
        w = lambda d: (lambda ci: ci[1] - ci[0])(sci.bootstrap_ci(d, seed=0)); self.assertGreater(w(small), w(big))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Percentile bootstrap assumes i.i.d. observations; time series need block bootstrap"], compat={"python": ">=3.9"},
   tags=["statistics", "bootstrap", "regression", "least-squares", "confidence-interval", "science", "data-analysis", "variance", "reproducible"],
   avoid="Naive sum-of-squares variance; unseeded resampling; reporting a fit without R^2 and a residual look."),

RC("py-numerics-roots-integration-interp", "Numerical basics: bisection/Newton root finding, Simpson integration, interpolation with gap filling", "python",
   "Simulations and lab scripts need a root, an integral or missing samples filled — with convergence guarantees and clear failures.",
   "Bisection (guaranteed bracket), Newton with derivative and fallback to bisection when it diverges, composite Simpson with even-n check and error estimate by halving, linear interpolation that fills None gaps (not extrapolating at the ends unless asked), tolerances explicit.",
   {"numerics.py": '''"""Numerical methods with explicit tolerances and failure modes."""


def bisect(f, a, b, tol=1e-12, max_iter=200):
    fa, fb = f(a), f(b)
    if fa == 0: return a
    if fb == 0: return b
    if fa * fb > 0: raise ValueError("f(a) and f(b) must have opposite signs")
    for _ in range(max_iter):
        m = (a + b) / 2; fm = f(m)
        if fm == 0 or (b - a) / 2 < tol: return m
        if fa * fm < 0: b, fb = m, fm
        else: a, fa = m, fm
    raise RuntimeError("bisection did not converge")


def newton(f, df, x0, bracket=None, tol=1e-12, max_iter=50):
    """Newton's method; if a step leaves `bracket` or the derivative vanishes, fall back to bisection inside it."""
    x = x0
    for _ in range(max_iter):
        d = df(x)
        if d == 0 or d != d:
            break
        nx = x - f(x) / d
        if bracket and not (bracket[0] <= nx <= bracket[1]): break
        if abs(nx - x) < tol: return nx
        x = nx
    if bracket: return bisect(f, *bracket, tol=tol)
    raise RuntimeError("newton failed and no bracket given")


def simpson(f, a, b, n=100):
    if n % 2: raise ValueError("n must be even")
    h = (b - a) / n
    s = f(a) + f(b) + sum((4 if i % 2 else 2) * f(a + i * h) for i in range(1, n))
    return s * h / 3


def simpson_adaptive(f, a, b, tol=1e-9, n=8, max_n=1 << 20):
    prev = simpson(f, a, b, n)
    while n < max_n:
        n *= 2; cur = simpson(f, a, b, n)
        if abs(cur - prev) < tol * 15: return cur, abs(cur - prev) / 15       # (value, error estimate)
        prev = cur
    raise RuntimeError("did not reach tolerance")


def fill_gaps(xs, ys, extrapolate=False):
    """Linear interpolation of None values using the known neighbours. xs must be increasing."""
    if any(b <= a for a, b in zip(xs, xs[1:])): raise ValueError("xs must be strictly increasing")
    known = [(x, y) for x, y in zip(xs, ys) if y is not None]
    if len(known) < 2: raise ValueError("need at least two known points")
    out = []
    for x, y in zip(xs, ys):
        if y is not None: out.append(y); continue
        lo = max((k for k in known if k[0] < x), default=None, key=lambda k: k[0]); hi = min((k for k in known if k[0] > x), default=None, key=lambda k: k[0])
        if lo and hi: out.append(lo[1] + (hi[1] - lo[1]) * (x - lo[0]) / (hi[0] - lo[0]))
        elif extrapolate:
            a, b = (known[-2], known[-1]) if hi is None else (known[0], known[1])
            out.append(a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))
        else: out.append(None)
    return out
'''
    , "test_numerics.py": '''import unittest, math
import numerics as N


class T(unittest.TestCase):
    def test_bisect_sqrt2_and_bad_bracket(self):
        self.assertAlmostEqual(N.bisect(lambda x: x * x - 2, 0, 2), math.sqrt(2), places=10)
        with self.assertRaises(ValueError): N.bisect(lambda x: x * x + 1, -1, 1)
        self.assertEqual(N.bisect(lambda x: x, 0, 5), 0)

    def test_newton_fast_and_falls_back(self):
        calls = [0]
        def f(x): calls[0] += 1; return x * x - 2
        self.assertAlmostEqual(N.newton(f, lambda x: 2 * x, 1.0), math.sqrt(2), places=12); self.assertLess(calls[0], 15)
        # derivative zero at the start point: plain Newton dies, bracketed version recovers via bisection
        self.assertAlmostEqual(N.newton(lambda x: x ** 3 - 1, lambda x: 3 * x * x, 0.0, bracket=(0, 2)), 1.0, places=9)
        with self.assertRaises(RuntimeError): N.newton(lambda x: x ** 3 - 1, lambda x: 3 * x * x, 0.0)

    def test_simpson_exact_for_cubics_and_needs_even_n(self):
        self.assertAlmostEqual(N.simpson(lambda x: x ** 3, 0, 2, 2), 4.0, places=12)
        with self.assertRaises(ValueError): N.simpson(math.sin, 0, 1, 3)

    def test_simpson_adaptive_sin(self):
        v, err = N.simpson_adaptive(math.sin, 0, math.pi); self.assertAlmostEqual(v, 2.0, places=8); self.assertLess(err, 1e-8)

    def test_fill_gaps_interior_and_ends(self):
        self.assertEqual(N.fill_gaps([0, 1, 2, 3, 4], [0, None, 4, None, None]), [0, 2.0, 4, None, None])
        self.assertEqual(N.fill_gaps([0, 1, 2, 3, 4], [0, None, 4, None, None], extrapolate=True), [0, 2.0, 4, 6.0, 8.0])

    def test_uneven_spacing_uses_x_not_index(self):
        self.assertEqual(N.fill_gaps([0, 1, 10], [0, None, 10]), [0, 1.0, 10])

    def test_fill_gaps_validation(self):
        for xs, ys in (([0, 0, 1], [1, None, 2]), ([0, 1], [None, None])):
            with self.assertRaises(ValueError): N.fill_gaps(xs, ys)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Scalar functions only; scipy.optimize / scipy.integrate for multivariate or stiff problems"], compat={"python": ">=3.9"},
   tags=["numerical", "root-finding", "newton", "bisection", "integration", "simpson", "interpolation", "missing-values", "science", "simulation"],
   avoid="Newton without a safeguard; trapezoid with a handful of points and no error estimate; interpolating by index when x is unevenly spaced."),

RC("py-reproducible-experiment-record", "Reproducible experiment runs: seeding, config hash, environment capture, results JSON with metadata", "python",
   "Months later nobody can reproduce figure 3: which seed, which parameters, which library versions, which code?",
   "A `Run` helper that derives per-component RNG streams from one master seed (so adding a component doesn't reshuffle others), records config hash, Python and platform info, package versions (importlib.metadata), start/end time and duration, writes results atomically to `runs/<id>/result.json`, and refuses to overwrite an existing run.",
   {"runrec.py": '''"""Experiment recording. One master seed -> independent named RNG streams; everything needed to repeat the run is written next to the result."""
import hashlib, json, os, platform, random, sys, time
from importlib import metadata


def stream(master_seed, name):
    """Independent, stable RNG per component name: adding a new component never changes existing ones."""
    h = hashlib.sha256(f"{master_seed}:{name}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def env_info(packages=()):
    vers = {}
    for p in packages:
        try: vers[p] = metadata.version(p)
        except metadata.PackageNotFoundError: vers[p] = None
    return {"python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(), "packages": vers}


def config_hash(cfg):
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:12]


class Run:
    def __init__(self, root, cfg, seed=0, packages=(), clock=time.time):
        self.cfg, self.seed, self.clock = cfg, seed, clock
        self.id = f"{config_hash(cfg)}-s{seed}"
        self.dir = os.path.join(root, self.id)
        os.makedirs(root, exist_ok=True)
        try: os.mkdir(self.dir)
        except FileExistsError: raise FileExistsError(f"run {self.id} already exists; change config/seed or delete it deliberately")
        self.meta = {"id": self.id, "config": cfg, "seed": seed, "env": env_info(packages), "started": clock()}

    def rng(self, name):
        return stream(self.seed, name)

    def finish(self, results):
        end = self.clock()
        self.meta.update(finished=end, seconds=round(end - self.meta["started"], 3), results=results)
        tmp = os.path.join(self.dir, ".result.json.tmp"); final = os.path.join(self.dir, "result.json")
        with open(tmp, "w") as f: json.dump(self.meta, f, indent=2, sort_keys=True, default=str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, final)                                          # atomic: result.json is either complete or absent
        return final
'''
    , "test_runrec.py": '''import unittest, tempfile, os, json
import runrec as R


class T(unittest.TestCase):
    def test_streams_are_stable_and_independent(self):
        a = [R.stream(1, "data").random() for _ in range(3)]; b = [R.stream(1, "data").random() for _ in range(3)]
        self.assertEqual(a, b); self.assertNotEqual(a, [R.stream(1, "init").random() for _ in range(3)]); self.assertNotEqual(a, [R.stream(2, "data").random() for _ in range(3)])

    def test_adding_a_component_does_not_change_others(self):
        before = R.stream(7, "model").random(); R.stream(7, "new_augmentation").random(); self.assertEqual(before, R.stream(7, "model").random())

    def test_config_hash_ignores_key_order_but_not_values(self):
        self.assertEqual(R.config_hash({"a": 1, "b": 2}), R.config_hash({"b": 2, "a": 1})); self.assertNotEqual(R.config_hash({"a": 1}), R.config_hash({"a": 2}))

    def test_run_writes_complete_metadata(self):
        root = tempfile.mkdtemp(); t = iter([100.0, 103.5]); run = R.Run(root, {"lr": 0.1}, seed=3, packages=["pip", "definitely-not-installed"], clock=lambda: next(t))
        path = run.finish({"acc": 0.9}); d = json.load(open(path))
        self.assertEqual((d["seed"], d["config"], d["results"], d["seconds"]), (3, {"lr": 0.1}, {"acc": 0.9}, 3.5)); self.assertIn("python", d["env"]); self.assertIsNone(d["env"]["packages"]["definitely-not-installed"])
        self.assertEqual(os.listdir(run.dir), ["result.json"])

    def test_refuses_to_overwrite_a_run(self):
        root = tempfile.mkdtemp(); R.Run(root, {"x": 1}, seed=1)
        with self.assertRaises(FileExistsError): R.Run(root, {"x": 1}, seed=1)
        R.Run(root, {"x": 1}, seed=2)           # different seed = different run


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Does not capture git commit or GPU/driver details (add `git rev-parse HEAD` and nvidia-smi output if relevant)", "Framework seeds (torch.manual_seed, numpy default_rng) should be derived from `run.rng(name)`"], compat={"python": ">=3.9"},
   tags=["reproducibility", "experiment", "seed", "science", "ml", "metadata", "results", "research", "tracking"],
   avoid="One global random.seed(); results saved without the config that produced them; overwriting previous run directories."),
]
