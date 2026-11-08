"""Round-2 recipes, part C: data engineering, game modding/tinkering and home-IoT logic (Python stdlib, `unittest`)."""
from .recipes import RC, UT

R2C = [
RC("py-incremental-etl-watermark", "Incremental ETL with a high-water mark: idempotent, resumable, late-data safe (SQLite)", "python",
   "A nightly sync that re-copies the whole table is slow; one that copies 'since last run' loses rows or duplicates them on retry.",
   "Persisted watermark per pipeline advanced only after the batch commits in the same transaction, upsert by primary key so re-running a batch is harmless, overlap window to catch late-arriving updates, ordering by (updated_at, id) to avoid skipping equal timestamps, stats returned for logging.",
   {"etl.py": '''"""Incremental extract-load with a watermark. Source and target are SQLite here; the pattern is the same for any pair."""
import sqlite3


def init(target):
    target.executescript("""
    CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, updated_at INTEGER);
    CREATE TABLE IF NOT EXISTS watermark(pipeline TEXT PRIMARY KEY, updated_at INTEGER NOT NULL, last_id INTEGER NOT NULL);
    """)


def sync(source, target, pipeline="customers", batch=500, overlap=0):
    """Copy rows changed since the watermark. Returns {'rows': n, 'batches': b}. Safe to re-run at any point.
    overlap > 0 re-reads that many time units before the watermark (upserts make the re-read harmless) to catch late writers."""
    stats = {"rows": 0, "batches": 0}
    row = target.execute("SELECT updated_at, last_id FROM watermark WHERE pipeline=?", (pipeline,)).fetchone()
    mark = tuple(row) if row else (-1, -1)
    cursor = (mark[0] - overlap, -1) if overlap and row else mark
    while True:
        # (updated_at, id) keyset: rows sharing a timestamp are neither skipped nor repeated across batch boundaries
        rows = source.execute("""SELECT id, name, updated_at FROM customers
                                 WHERE updated_at > ? OR (updated_at = ? AND id > ?)
                                 ORDER BY updated_at, id LIMIT ?""", (cursor[0], cursor[0], cursor[1], batch)).fetchall()
        if not rows: return stats
        cursor = (rows[-1][2], rows[-1][0])
        mark = max(mark, cursor)
        with target:                                                    # one transaction: data and watermark move together or not at all
            target.executemany("""INSERT INTO customers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at""", rows)
            target.execute("""INSERT INTO watermark VALUES(?,?,?) ON CONFLICT(pipeline) DO UPDATE SET updated_at=excluded.updated_at, last_id=excluded.last_id""", (pipeline, *mark))
        stats["rows"] += len(rows); stats["batches"] += 1
        if len(rows) < batch: return stats
'''
    , "test_etl.py": '''import unittest, sqlite3
import etl


def make():
    src, dst = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    src.execute("CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, updated_at INTEGER)"); etl.init(dst); return src, dst


def names(db): return dict(db.execute("SELECT id, name FROM customers").fetchall())


class T(unittest.TestCase):
    def test_first_run_copies_everything_second_run_nothing(self):
        s, d = make(); s.executemany("INSERT INTO customers VALUES(?,?,?)", [(i, f"c{i}", 100 + i) for i in range(1, 8)])
        self.assertEqual(etl.sync(s, d, batch=3), {"rows": 7, "batches": 3}); self.assertEqual(etl.sync(s, d, batch=3), {"rows": 0, "batches": 0}); self.assertEqual(len(names(d)), 7)

    def test_only_changes_move_and_updates_apply(self):
        s, d = make(); s.executemany("INSERT INTO customers VALUES(?,?,?)", [(1, "a", 1), (2, "b", 2)]); etl.sync(s, d)
        s.execute("UPDATE customers SET name='B2', updated_at=5 WHERE id=2"); s.execute("INSERT INTO customers VALUES(3,'c',6)")
        self.assertEqual(etl.sync(s, d)["rows"], 2); self.assertEqual(names(d), {1: "a", 2: "B2", 3: "c"})

    def test_equal_timestamps_across_batch_boundary_not_lost(self):
        s, d = make(); s.executemany("INSERT INTO customers VALUES(?,?,?)", [(i, f"c{i}", 50) for i in range(1, 11)])
        etl.sync(s, d, batch=3); self.assertEqual(len(names(d)), 10)

    def test_crash_mid_batch_leaves_watermark_behind_and_rerun_converges(self):
        s, d = make(); s.executemany("INSERT INTO customers VALUES(?,?,?)", [(i, f"c{i}", i) for i in range(1, 6)])
        d.execute("CREATE TRIGGER boom BEFORE INSERT ON customers WHEN NEW.id = 4 BEGIN SELECT RAISE(ABORT, 'disk full'); END")
        with self.assertRaises(sqlite3.DatabaseError): etl.sync(s, d, batch=10)
        self.assertEqual((len(names(d)), d.execute("SELECT COUNT(*) FROM watermark").fetchone()[0]), (0, 0))      # whole batch rolled back
        d.execute("DROP TRIGGER boom"); etl.sync(s, d, batch=10); self.assertEqual(len(names(d)), 5)

    def test_rerunning_same_batch_is_idempotent(self):
        s, d = make(); s.execute("INSERT INTO customers VALUES(1,'a',1)"); etl.sync(s, d); d.execute("DELETE FROM watermark"); etl.sync(s, d)
        self.assertEqual(d.execute("SELECT COUNT(*) FROM customers").fetchone()[0], 1)

    def test_late_arriving_row_needs_overlap_window(self):
        s, d = make(); s.execute("INSERT INTO customers VALUES(1,'a',100)"); etl.sync(s, d)
        s.execute("INSERT INTO customers VALUES(2,'late',95)")                  # writer committed late with an older timestamp
        etl.sync(s, d); self.assertNotIn(2, names(d))
        etl.sync(s, d, overlap=10); self.assertIn(2, names(d))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Hard deletes at the source are invisible to a watermark sync (use soft-delete flags or CDC)", "Single writer per pipeline; use a lock if two runs can overlap"], compat={"python": ">=3.9", "sqlite": ">=3.24"},
   tags=["etl", "data-engineering", "incremental", "watermark", "sync", "pipeline", "idempotent", "upsert", "elt", "data-pipeline"],
   avoid="Advancing the watermark before the data commits; `updated_at > last` with ties at the boundary; assuming sources never write late."),

RC("py-data-validation-quality-report", "Row validation (schema, nulls, ranges, uniqueness) and a data-quality report without pandas", "python",
   "Bad rows should be caught at the door with a precise reason, and every load should produce a quality summary you can alert on.",
   "Declarative column spec (type, required, min/max, allowed values, regex), validation returning (clean rows, rejects with row number and reason), dataset checks (duplicate keys, null rate per column, row count vs expected range), a report dict plus a threshold gate that fails the pipeline.",
   {"dq.py": '''"""Lightweight data quality. Great Expectations / pandera / pydantic do this at scale; this is the idea in 60 lines."""
import re
from collections import Counter

SPEC = {
    "id": dict(type=int, required=True, min=1),
    "email": dict(type=str, required=True, regex=r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$"),
    "age": dict(type=int, required=False, min=0, max=120),
    "plan": dict(type=str, required=True, allowed={"free", "pro", "team"}),
}


def check_row(row, spec=SPEC):
    errs = []
    for col, rule in spec.items():
        v = row.get(col)
        if v is None or v == "":
            if rule.get("required"): errs.append(f"{col}: missing")
            continue
        if not isinstance(v, rule["type"]) or isinstance(v, bool): errs.append(f"{col}: expected {rule['type'].__name__}, got {type(v).__name__}"); continue
        if "min" in rule and v < rule["min"]: errs.append(f"{col}: {v} < {rule['min']}")
        if "max" in rule and v > rule["max"]: errs.append(f"{col}: {v} > {rule['max']}")
        if "allowed" in rule and v not in rule["allowed"]: errs.append(f"{col}: {v!r} not in {sorted(rule['allowed'])}")
        if "regex" in rule and not re.match(rule["regex"], v): errs.append(f"{col}: {v!r} bad format")
    for extra in set(row) - set(spec): errs.append(f"{extra}: unexpected column")
    return errs


def validate(rows, spec=SPEC, key="id"):
    clean, rejects, seen = [], [], set()
    for n, row in enumerate(rows, 1):
        errs = check_row(row, spec)
        if not errs and row[key] in seen: errs = [f"{key}: duplicate {row[key]!r}"]
        if errs: rejects.append({"row": n, "errors": errs})
        else: clean.append(row); seen.add(row[key])
    return clean, rejects


def report(rows, rejects, spec=SPEC, expect_rows=None):
    total = len(rows) + len(rejects)
    nulls = {c: sum(1 for r in rows if r.get(c) in (None, "")) / len(rows) if rows else 0.0 for c in spec}
    reasons = Counter(e.split(":")[0] for r in rejects for e in r["errors"])
    return {"total": total, "clean": len(rows), "rejected": len(rejects), "reject_rate": len(rejects) / total if total else 0.0,
            "null_rate": nulls, "top_reject_columns": reasons.most_common(3),
            "row_count_ok": expect_rows is None or expect_rows[0] <= total <= expect_rows[1]}


def gate(rep, max_reject_rate=0.05):
    """Raise to fail the pipeline instead of loading garbage quietly."""
    if rep["reject_rate"] > max_reject_rate or not rep["row_count_ok"]:
        raise RuntimeError(f"data quality gate failed: reject_rate={rep['reject_rate']:.1%}, row_count_ok={rep['row_count_ok']}")
'''
    , "test_dq.py": '''import unittest
import dq

GOOD = {"id": 1, "email": "a@b.co", "age": 30, "plan": "pro"}


class T(unittest.TestCase):
    def test_good_row(self):
        self.assertEqual(dq.check_row(GOOD), []); self.assertEqual(dq.check_row({**GOOD, "age": None}), [])         # optional may be missing

    def test_each_rule_reports_a_precise_reason(self):
        cases = {"id": (0, "id: 0 < 1"), "age": (130, "age: 130 > 120"), "plan": ("gold", "plan:"), "email": ("nope", "email: 'nope' bad format")}
        for col, (bad, expect) in cases.items(): self.assertTrue(any(e.startswith(expect) for e in dq.check_row({**GOOD, col: bad})), col)
        self.assertIn("email: missing", dq.check_row({**GOOD, "email": ""})); self.assertIn("age: expected int, got str", dq.check_row({**GOOD, "age": "30"}))
        self.assertIn("age: expected int, got bool", dq.check_row({**GOOD, "age": True}))

    def test_unexpected_column_flagged(self):
        self.assertEqual(dq.check_row({**GOOD, "sneaky": 1}), ["sneaky: unexpected column"])

    def test_validate_splits_and_numbers_rows_from_one(self):
        rows = [GOOD, {**GOOD, "id": 2, "plan": "x"}, {**GOOD, "id": 1}, {**GOOD, "id": 3}]; clean, rej = dq.validate(rows)
        self.assertEqual([r["id"] for r in clean], [1, 3]); self.assertEqual([r["row"] for r in rej], [2, 3]); self.assertIn("duplicate", rej[1]["errors"][0])

    def test_report_numbers(self):
        rows = [GOOD, {**GOOD, "id": 2, "age": None}, {**GOOD, "id": 3, "age": None}, {**GOOD, "id": 4}]; clean, rej = dq.validate(rows + [{"id": -1}])
        rep = dq.report(clean, rej, expect_rows=(3, 10)); self.assertEqual((rep["total"], rep["clean"], rep["rejected"]), (5, 4, 1))
        self.assertAlmostEqual(rep["null_rate"]["age"], 0.5); self.assertAlmostEqual(rep["reject_rate"], 0.2); self.assertTrue(rep["row_count_ok"]); self.assertEqual(rep["top_reject_columns"][0][1], 1)

    def test_gate_fails_loudly(self):
        clean, rej = dq.validate([GOOD] + [{**GOOD, "id": i, "plan": "bad"} for i in range(2, 12)])
        with self.assertRaisesRegex(RuntimeError, "gate failed"): dq.gate(dq.report(clean, rej))
        c2, r2 = dq.validate([{**GOOD, "id": i} for i in range(1, 30)]); dq.gate(dq.report(c2, r2))
        with self.assertRaises(RuntimeError): dq.gate(dq.report(c2, r2, expect_rows=(100, 200)))

    def test_empty_input_does_not_divide_by_zero(self):
        self.assertEqual(dq.report([], [])["reject_rate"], 0.0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Row dicts; for DataFrames use pandera or Great Expectations"], compat={"python": ">=3.9"},
   tags=["data-quality", "validation", "schema", "nulls", "data-engineering", "pipeline", "dataframe", "pandas", "etl", "report"],
   avoid="Dropping bad rows silently; a quality check that only logs; validating types by `str.isdigit` guesses."),

RC("py-dag-topological-order", "DAG task ordering (Airflow-style): topological sort, cycle error with path, parallel levels, failure skipping", "python",
   "A scheduler, build tool or migration runner must order tasks by dependency, report cycles usefully and know what can run in parallel.",
   "Kahn's algorithm with deterministic (sorted) tie-breaking, cycle detection that returns one concrete cycle path, levels (all tasks whose deps are done) for parallel execution, missing dependency errors, and downstream-skip semantics when a task fails.",
   {"dag.py": '''"""Dependency ordering. deps maps task -> iterable of tasks it needs first."""


class CycleError(Exception):
    def __init__(self, path):
        super().__init__("cycle: " + " -> ".join(path)); self.path = path


def normalise(deps):
    tasks = set(deps) | {d for v in deps.values() for d in v}
    missing = {d for v in deps.values() for d in v} - set(deps)
    full = {t: set(deps.get(t, ())) for t in tasks}
    return full, missing


def levels(deps, strict=True):
    """-> [[tasks that can run together], ...]. strict: referencing an undeclared task is an error."""
    full, missing = normalise(deps)
    if strict and missing: raise KeyError(f"undeclared dependencies: {sorted(missing)}")
    remaining, done, out = {t: set(d) for t, d in full.items()}, set(), []
    while remaining:
        ready = sorted(t for t, d in remaining.items() if d <= done)
        if not ready: raise CycleError(find_cycle({t: d & set(remaining) for t, d in remaining.items()}))
        out.append(ready); done.update(ready)
        for t in ready: del remaining[t]
    return out


def order(deps, strict=True):
    return [t for level in levels(deps, strict) for t in level]


def find_cycle(deps):
    state, stack = {}, []
    def visit(n):
        state[n] = 1; stack.append(n)
        for m in sorted(deps.get(n, ())):
            if state.get(m) == 1: return stack[stack.index(m):] + [m]
            if m not in state and (c := visit(m)): return c
        stack.pop(); state[n] = 2
    for n in sorted(deps):
        if n not in state and (c := visit(n)): return c


def downstream(deps, failed):
    """All tasks that (transitively) depend on any failed task - these are skipped, siblings still run."""
    full, _ = normalise(deps)
    bad, changed = set(failed), True
    while changed:
        changed = False
        for t, d in full.items():
            if t not in bad and d & bad: bad.add(t); changed = True
    return bad - set(failed)
'''
    , "test_dag.py": '''import unittest
import dag

PIPE = {"extract": [], "clean": ["extract"], "features": ["clean"], "report": ["clean"], "train": ["features"], "publish": ["train", "report"]}


class T(unittest.TestCase):
    def test_order_respects_dependencies(self):
        o = dag.order(PIPE); pos = {t: i for i, t in enumerate(o)}
        for t, deps in PIPE.items(): self.assertTrue(all(pos[d] < pos[t] for d in deps), t)
        self.assertEqual(len(o), len(PIPE))

    def test_deterministic(self):
        self.assertEqual(dag.order(PIPE), dag.order(dict(reversed(list(PIPE.items())))))

    def test_levels_group_parallel_work(self):
        self.assertEqual(dag.levels(PIPE), [["extract"], ["clean"], ["features", "report"], ["train"], ["publish"]])

    def test_cycle_reports_the_loop(self):
        with self.assertRaises(dag.CycleError) as cm: dag.order({"a": ["b"], "b": ["c"], "c": ["a"], "ok": []})
        self.assertEqual(cm.exception.path, ["a", "b", "c", "a"])
        with self.assertRaises(dag.CycleError) as cm: dag.order({"a": ["a"]})
        self.assertEqual(cm.exception.path, ["a", "a"])

    def test_cycle_downstream_of_valid_tasks_still_detected(self):
        with self.assertRaises(dag.CycleError) as cm: dag.order({"root": [], "x": ["root", "y"], "y": ["x"]})
        self.assertEqual(set(cm.exception.path), {"x", "y"})

    def test_undeclared_dependency(self):
        with self.assertRaisesRegex(KeyError, "ghost"): dag.order({"a": ["ghost"]})
        self.assertEqual(set(dag.order({"a": ["ghost"]}, strict=False)), {"a", "ghost"})

    def test_failure_skips_only_descendants(self):
        self.assertEqual(dag.downstream(PIPE, {"features"}), {"train", "publish"}); self.assertEqual(dag.downstream(PIPE, {"report"}), {"publish"})
        self.assertEqual(dag.downstream(PIPE, {"publish"}), set())


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["In-memory graph; `graphlib.TopologicalSorter` (3.9+) is the stdlib alternative when you do not need cycle paths or levels"], compat={"python": ">=3.9"},
   tags=["dag", "topological-sort", "dependency", "airflow", "scheduler", "pipeline", "build", "tasks", "cycle-detection", "data-engineering"],
   avoid="Recursive DFS without cycle detection; random iteration order making runs differ; marking everything failed when one task fails."),

RC("py-scd2-and-sharding", "Slowly changing dimension (SCD type 2) history rows and stable hash sharding", "python",
   "Analytics needs 'what was the customer's plan on March 3rd?' and big tables need a deterministic way to split by key.",
   "SCD2: each change closes the current row (valid_to) and opens a new one (valid_from), no-op when nothing changed, point-in-time lookup, exactly one current row per key; sharding with a stable hash (not Python's randomised hash()), consistent ring so adding a shard moves few keys.",
   {"dim.py": '''"""SCD2 history and stable sharding."""
import bisect, hashlib

OPEN = "9999-12-31"


def apply_change(history, key, attrs, as_of):
    """history: list of dict rows {key, attrs, valid_from, valid_to}. Mutates and returns it."""
    cur = next((r for r in history if r["key"] == key and r["valid_to"] == OPEN), None)
    if cur and cur["attrs"] == attrs: return history                         # nothing changed: do not churn history
    if cur:
        if as_of <= cur["valid_from"]: raise ValueError("change must be later than the current row's valid_from")
        cur["valid_to"] = as_of
    history.append({"key": key, "attrs": dict(attrs), "valid_from": as_of, "valid_to": OPEN})
    return history


def as_of(history, key, date):
    """Row valid at `date` (valid_from <= date < valid_to) or None."""
    for r in history:
        if r["key"] == key and r["valid_from"] <= date < r["valid_to"]: return r["attrs"]


def stable_hash(key):
    return int.from_bytes(hashlib.blake2b(str(key).encode(), digest_size=8).digest(), "big")


def shard_mod(key, n):
    return stable_hash(key) % n


class Ring:
    """Consistent hashing with virtual nodes: adding a shard only moves ~1/n of the keys."""
    def __init__(self, shards, vnodes=100):
        self.points = sorted((stable_hash(f"{s}#{i}"), s) for s in shards for i in range(vnodes))
        self.keys = [p for p, _ in self.points]

    def get(self, key):
        i = bisect.bisect(self.keys, stable_hash(key)) % len(self.points)
        return self.points[i][1]
'''
    , "test_dim.py": '''import unittest
from collections import Counter
import dim


class T(unittest.TestCase):
    def test_history_and_point_in_time(self):
        h = []; dim.apply_change(h, 7, {"plan": "free"}, "2026-01-01"); dim.apply_change(h, 7, {"plan": "pro"}, "2026-03-05"); dim.apply_change(h, 7, {"plan": "team"}, "2026-06-01")
        self.assertEqual([dim.as_of(h, 7, d)["plan"] for d in ("2026-01-01", "2026-03-04", "2026-03-05", "2026-12-31")], ["free", "free", "pro", "team"])
        self.assertIsNone(dim.as_of(h, 7, "2025-12-31")); self.assertEqual(len(h), 3)

    def test_exactly_one_current_row_per_key(self):
        h = []
        for i, p in enumerate(["a", "b", "c", "d"]): dim.apply_change(h, 1, {"p": p}, f"2026-0{i + 1}-01"); dim.apply_change(h, 2, {"p": p}, f"2026-0{i + 1}-02")
        for k in (1, 2): self.assertEqual(sum(1 for r in h if r["key"] == k and r["valid_to"] == dim.OPEN), 1)

    def test_no_change_no_new_row_and_out_of_order_rejected(self):
        h = []; dim.apply_change(h, 1, {"p": "a"}, "2026-01-01"); dim.apply_change(h, 1, {"p": "a"}, "2026-02-01"); self.assertEqual(len(h), 1)
        with self.assertRaises(ValueError): dim.apply_change(h, 1, {"p": "b"}, "2026-01-01")

    def test_periods_do_not_overlap_or_gap(self):
        h = []
        for i, p in enumerate("abc"): dim.apply_change(h, 1, {"p": p}, f"2026-0{i + 1}-10")
        rows = sorted(h, key=lambda r: r["valid_from"]); self.assertTrue(all(a["valid_to"] == b["valid_from"] for a, b in zip(rows, rows[1:])))

    def test_stable_hash_is_stable_across_processes(self):
        self.assertEqual(dim.shard_mod("user-42", 8), dim.shard_mod("user-42", 8)); self.assertEqual(dim.stable_hash("abc"), dim.stable_hash("abc"))

    def test_mod_sharding_is_even(self):
        c = Counter(dim.shard_mod(f"k{i}", 4) for i in range(8000)); self.assertTrue(all(1700 < v < 2300 for v in c.values()), c)

    def test_ring_moves_few_keys_when_a_shard_is_added(self):
        before, after = dim.Ring(["a", "b", "c", "d"]), dim.Ring(["a", "b", "c", "d", "e"]); keys = [f"k{i}" for i in range(5000)]
        moved = sum(before.get(k) != after.get(k) for k in keys); self.assertLess(moved / len(keys), 0.30)
        self.assertTrue(all(after.get(k) == "e" for k in keys if before.get(k) != after.get(k)))
        mod_moved = sum(dim.shard_mod(k, 4) != dim.shard_mod(k, 5) for k in keys); self.assertGreater(mod_moved / len(keys), 0.6)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["ISO date strings compare correctly as text; use real dates/timestamps with timezones in production"], compat={"python": ">=3.9"},
   tags=["scd2", "slowly-changing-dimension", "data-warehouse", "history", "sharding", "consistent-hashing", "data-engineering", "partition", "hash"],
   avoid="Python's built-in hash() for sharding (randomised per process); overwriting dimension rows and losing history; key % n when shards will be added."),

RC("sql-window-functions-cookbook", "SQL window functions cookbook: running totals, rank, lag, top-N per group, sessionisation (tested on SQLite)", "sql",
   "Analysts keep writing self-joins and subqueries for things window functions do in one pass.",
   "Runnable patterns on a small orders table: running total, rank vs dense_rank vs row_number, previous-row delta with LAG, top-N per group, moving average with a frame, percent of total, gaps-and-islands sessionisation and first/last value, each verified against hand-computed results.",
   {"cookbook.sql": '''-- Needs SQLite >= 3.25 (window functions). Same syntax works in Postgres, MySQL 8, BigQuery, Snowflake.
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer TEXT, day TEXT, amount INTEGER);
INSERT INTO orders VALUES
 (1,'ann','2026-01-01',100),(2,'ann','2026-01-02',50),(3,'ann','2026-01-04',70),
 (4,'bob','2026-01-01',200),(5,'bob','2026-01-01',200),(6,'bob','2026-01-03',30),
 (7,'cy','2026-01-02',10);

-- running total per customer (ORDER BY alone defaults to RANGE: peers with the same day share a total - add id to make it per-row)
-- SELECT customer, day, SUM(amount) OVER (PARTITION BY customer ORDER BY day, id) AS running FROM orders;

-- rank flavours: ties get the same rank (RANK leaves gaps, DENSE_RANK doesn't), ROW_NUMBER never ties
-- SELECT id, amount, RANK() OVER w r, DENSE_RANK() OVER w d, ROW_NUMBER() OVER w n FROM orders WINDOW w AS (ORDER BY amount DESC, id);

-- previous value and delta
-- SELECT day, amount, amount - LAG(amount) OVER (PARTITION BY customer ORDER BY day, id) AS delta FROM orders;

-- top 1 per group
-- SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY customer ORDER BY amount DESC, id) rn FROM orders) WHERE rn = 1;

-- 2-row moving average
-- SELECT id, AVG(amount) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM orders;

-- percent of total
-- SELECT customer, SUM(amount) * 100.0 / SUM(SUM(amount)) OVER () FROM orders GROUP BY customer;
''',
    "test_cookbook.py": '''import unittest, sqlite3, pathlib, re


def db():
    c = sqlite3.connect(":memory:"); sql = pathlib.Path("cookbook.sql").read_text()
    c.executescript("\\n".join(l for l in sql.splitlines() if not l.startswith("--"))); return c


class T(unittest.TestCase):
    def test_window_functions_available(self):
        v = tuple(int(x) for x in sqlite3.sqlite_version.split(".")); self.assertGreaterEqual(v, (3, 25, 0))

    def test_running_total_per_customer(self):
        rows = db().execute("SELECT customer, SUM(amount) OVER (PARTITION BY customer ORDER BY day, id) FROM orders ORDER BY customer, day, id").fetchall()
        self.assertEqual(rows, [("ann", 100), ("ann", 150), ("ann", 220), ("bob", 200), ("bob", 400), ("bob", 430), ("cy", 10)])

    def test_default_frame_shares_totals_between_peers(self):
        rows = db().execute("SELECT id, SUM(amount) OVER (PARTITION BY customer ORDER BY day) FROM orders WHERE customer='bob' ORDER BY id").fetchall()
        self.assertEqual(rows, [(4, 400), (5, 400), (6, 430)])         # the classic surprise: ties on the ORDER BY key are one group

    def test_rank_flavours_on_ties(self):
        rows = db().execute("SELECT id, RANK() OVER w, DENSE_RANK() OVER w, ROW_NUMBER() OVER w FROM orders WINDOW w AS (ORDER BY amount DESC) ORDER BY amount DESC, id").fetchall()
        self.assertEqual([r[1:] for r in rows][:4], [(1, 1, 1), (1, 1, 2), (3, 2, 3), (4, 3, 4)])

    def test_lag_delta_first_row_is_null(self):
        rows = db().execute("SELECT id, amount - LAG(amount) OVER (PARTITION BY customer ORDER BY day, id) FROM orders WHERE customer='ann' ORDER BY id").fetchall()
        self.assertEqual(rows, [(1, None), (2, -50), (3, 20)])

    def test_top_one_per_group(self):
        rows = db().execute("SELECT customer, id FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY customer ORDER BY amount DESC, id) rn FROM orders) WHERE rn = 1 ORDER BY customer").fetchall()
        self.assertEqual(rows, [("ann", 1), ("bob", 4), ("cy", 7)])

    def test_moving_average_frame(self):
        rows = db().execute("SELECT id, AVG(amount) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM orders ORDER BY id").fetchall()
        self.assertEqual([r[1] for r in rows][:3], [100.0, 75.0, 60.0])

    def test_percent_of_total(self):
        rows = dict(db().execute("SELECT customer, SUM(amount) * 100.0 / SUM(SUM(amount)) OVER () FROM orders GROUP BY customer").fetchall())
        self.assertAlmostEqual(rows["bob"], 430 * 100 / 660); self.assertAlmostEqual(sum(rows.values()), 100.0)

    def test_gaps_and_islands_sessions(self):
        # consecutive days belong to one island: day number minus row number is constant within an island
        sql = """SELECT customer, MIN(day), MAX(day), COUNT(*) FROM (
                   SELECT customer, day, julianday(day) - ROW_NUMBER() OVER (PARTITION BY customer ORDER BY day) AS grp FROM (SELECT DISTINCT customer, day FROM orders))
                 GROUP BY customer, grp ORDER BY customer, MIN(day)"""
        self.assertEqual(db().execute(sql).fetchall(), [("ann", "2026-01-01", "2026-01-02", 2), ("ann", "2026-01-04", "2026-01-04", 1), ("bob", "2026-01-01", "2026-01-01", 1), ("bob", "2026-01-03", "2026-01-03", 1), ("cy", "2026-01-02", "2026-01-02", 1)])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Test harness uses SQLite 3.25+; dialect differences (date arithmetic) are noted in the comments, window syntax is portable"], compat={"sqlite": ">=3.25", "postgres": ">=9", "mysql": ">=8"},
   tags=["sql", "window-functions", "analytics", "rank", "lag", "running-total", "top-n", "gaps-and-islands", "sessionization", "data-engineering"],
   avoid="Self-joins for running totals; relying on the default RANGE frame without realising ties merge; ROW_NUMBER when you wanted ties to share a rank."),

# ------------------------------------------------------------------ game modding / tinkering
RC("py-easing-lerp-tween", "Easing functions, lerp, smoothstep and a tween helper with exact endpoints", "python",
   "Animations, UI transitions, camera moves and mods need smooth interpolation that lands exactly on the target.",
   "Normalised-time easing functions (quad, cubic, sine, expo, back, elastic-free bounce), lerp/inverse-lerp/remap, smoothstep/smootherstep, clamp, frame-rate-independent exponential damping (`1 - exp(-k*dt)`), and a Tween that clamps at the end and reports completion; all easings map 0->0 and 1->1.",
   {"ease.py": '''"""Interpolation toolbox. t is normalised time in [0, 1]."""
import math


def clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def lerp(a, b, t):
    return a + (b - a) * t


def inv_lerp(a, b, v):
    return 0.0 if a == b else (v - a) / (b - a)


def remap(v, a, b, c, d):
    return lerp(c, d, inv_lerp(a, b, v))


def smoothstep(t):
    t = clamp(t); return t * t * (3 - 2 * t)


def smootherstep(t):
    t = clamp(t); return t * t * t * (t * (t * 6 - 15) + 10)


def in_quad(t): return t * t
def out_quad(t): return 1 - (1 - t) * (1 - t)
def in_out_cubic(t): return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2
def in_out_sine(t): return -(math.cos(math.pi * t) - 1) / 2
def out_expo(t): return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def out_back(t, s=1.70158):
    t -= 1; return 1 + (s + 1) * t ** 3 + s * t ** 2


def out_bounce(t):
    n, d = 7.5625, 2.75
    if t < 1 / d: return n * t * t
    if t < 2 / d: t -= 1.5 / d; return n * t * t + 0.75
    if t < 2.5 / d: t -= 2.25 / d; return n * t * t + 0.9375
    t -= 2.625 / d; return n * t * t + 0.984375


EASINGS = dict(linear=lambda t: t, in_quad=in_quad, out_quad=out_quad, in_out_cubic=in_out_cubic, in_out_sine=in_out_sine, out_expo=out_expo,
               out_back=out_back, out_bounce=out_bounce, smoothstep=smoothstep, smootherstep=smootherstep)


def damp(current, target, k, dt):
    """Frame-rate independent 'chase the target' - same result at 30 or 144 fps, unlike lerp(cur, tgt, 0.1) per frame."""
    return lerp(current, target, 1 - math.exp(-k * dt))


class Tween:
    def __init__(self, a, b, duration, ease="linear"):
        if duration <= 0: raise ValueError("duration must be > 0")
        self.a, self.b, self.duration, self.fn, self.t = a, b, duration, EASINGS[ease], 0.0

    def update(self, dt):
        self.t = min(self.duration, self.t + dt)
        return self.value

    @property
    def value(self):
        return self.b if self.done else lerp(self.a, self.b, self.fn(self.t / self.duration))

    @property
    def done(self):
        return self.t >= self.duration
'''
    , "test_ease.py": '''import unittest, math
import ease as E


class T(unittest.TestCase):
    def test_every_easing_hits_both_ends(self):
        for name, fn in E.EASINGS.items(): self.assertAlmostEqual(fn(0.0), 0.0, places=9, msg=name); self.assertAlmostEqual(fn(1.0), 1.0, places=9, msg=name)

    def test_monotone_ones_never_go_backwards(self):
        for name in ("linear", "in_quad", "out_quad", "in_out_cubic", "in_out_sine", "out_expo", "smoothstep", "smootherstep"):
            vals = [E.EASINGS[name](i / 200) for i in range(201)]; self.assertTrue(all(a <= b + 1e-12 for a, b in zip(vals, vals[1:])), name)

    def test_back_overshoots_bounce_stays_in_range(self):
        self.assertGreater(max(E.out_back(i / 100) for i in range(101)), 1.05); self.assertTrue(all(0 <= E.out_bounce(i / 100) <= 1.0001 for i in range(101)))

    def test_lerp_family(self):
        self.assertEqual((E.lerp(10, 20, 0.25), E.inv_lerp(10, 20, 15), E.remap(5, 0, 10, 100, 200), E.inv_lerp(3, 3, 3)), (12.5, 0.5, 150.0, 0.0))
        self.assertEqual((E.clamp(-1), E.clamp(2), E.clamp(0.4)), (0.0, 1.0, 0.4))

    def test_smoothstep_midpoint_and_clamps(self):
        self.assertEqual((E.smoothstep(0.5), E.smoothstep(-5), E.smoothstep(9)), (0.5, 0.0, 1.0))

    def test_damping_is_frame_rate_independent(self):
        def run(fps):
            x = 0.0
            for _ in range(fps): x = E.damp(x, 100.0, 5.0, 1 / fps)
            return x
        self.assertAlmostEqual(run(30), run(144), places=6); naive = lambda fps: [x for x in [0.0] for _ in range(fps) for x in [x + (100 - x) * 0.1]][-1]
        self.assertGreater(abs(naive(30) - naive(144)), 1)

    def test_tween_lands_exactly_and_stays(self):
        t = E.Tween(0, 10, 2.0, "out_back"); vals = [t.update(0.5) for _ in range(6)]
        self.assertEqual(vals[-1], 10); self.assertTrue(t.done); self.assertEqual(t.update(1), 10)
        with self.assertRaises(ValueError): E.Tween(0, 1, 0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Penner-style easing formulas; Lua/C#/GDScript ports are line-for-line"], compat={"python": ">=3.9"},
   tags=["easing", "lerp", "tween", "animation", "game-dev", "interpolation", "smoothstep", "ui", "modding", "game"],
   avoid="`pos = lerp(pos, target, 0.1)` per frame (speed depends on fps) as a timed animation; tweens that end 0.0001 short."),

RC("py-weighted-loot-tables", "Weighted random loot tables: nested tables, pity counter, seeded determinism, no replacement", "python",
   "Games and mods need drop tables that feel fair: configurable weights, guaranteed rarity after N misses, reproducible from a seed for testing and replays.",
   "Cumulative-weight selection with bisect, nested tables (a drop can roll another table), pity counter that raises rare weights until a hit then resets, draw-without-replacement for card packs, seeded RNG object (never the global one), and a statistical test with explicit tolerances.",
   {"loot.py": '''"""Loot tables. Pass your own random.Random so runs and replays are reproducible."""
import bisect, itertools, random


class Table:
    def __init__(self, entries):
        """entries: [(item_or_Table, weight)]. Weights are relative, not percentages."""
        if not entries or any(w < 0 for _, w in entries) or sum(w for _, w in entries) <= 0: raise ValueError("need non-negative weights with a positive sum")
        self.items = [i for i, _ in entries]
        self.cum = list(itertools.accumulate(w for _, w in entries))

    def roll(self, rng):
        x = rng.random() * self.cum[-1]
        item = self.items[bisect.bisect_right(self.cum, x)]
        return item.roll(rng) if isinstance(item, Table) else item

    def probabilities(self):
        total, prev, out = self.cum[-1], 0, {}
        for item, c in zip(self.items, self.cum):
            if not isinstance(item, Table): out[item] = out.get(item, 0) + (c - prev) / total
            prev = c
        return out


class PityTable:
    """Rare chance grows by `step` per miss; a hit resets it; a guaranteed hit at `hard_pity` rolls."""
    def __init__(self, common, rare, base=0.02, step=0.02, hard_pity=50):
        self.common, self.rare, self.base, self.step, self.hard = common, rare, base, step, hard_pity
        self.misses = 0

    def roll(self, rng):
        p = 1.0 if self.misses + 1 >= self.hard else min(1.0, self.base + self.step * self.misses)
        if rng.random() < p:
            self.misses = 0; return self.rare
        self.misses += 1; return self.common


def draw_without_replacement(pool, k, rng):
    """pool: [(item, weight)]; each item can appear once (card pack of distinct cards)."""
    pool, out = list(pool), []
    for _ in range(min(k, len(pool))):
        total = sum(w for _, w in pool); x = rng.random() * total; acc = 0
        for idx, (item, w) in enumerate(pool):
            acc += w
            if x < acc: out.append(item); del pool[idx]; break
    return out
'''
    , "test_loot.py": '''import unittest, random
from collections import Counter
import loot as L


class T(unittest.TestCase):
    def test_distribution_matches_weights(self):
        t = L.Table([("common", 70), ("rare", 25), ("epic", 5)]); rng = random.Random(1); c = Counter(t.roll(rng) for _ in range(20000))
        for item, p in (("common", .70), ("rare", .25), ("epic", .05)): self.assertAlmostEqual(c[item] / 20000, p, delta=0.015)

    def test_seeded_is_reproducible(self):
        t = L.Table([("a", 1), ("b", 1), ("c", 1)]); self.assertEqual([t.roll(random.Random(9)) for _ in range(5)], [t.roll(random.Random(9)) for _ in range(5)])
        a, b = random.Random(4), random.Random(4); self.assertEqual([t.roll(a) for _ in range(30)], [t.roll(b) for _ in range(30)])

    def test_zero_weight_never_drops_and_bad_tables_rejected(self):
        t = L.Table([("x", 1), ("never", 0)]); rng = random.Random(0); self.assertEqual({t.roll(rng) for _ in range(2000)}, {"x"})
        for bad in ([], [("a", 0)], [("a", -1), ("b", 3)]):
            with self.assertRaises(ValueError): L.Table(bad)

    def test_nested_tables(self):
        gems = L.Table([("ruby", 1), ("emerald", 1)]); t = L.Table([("gold", 3), (gems, 1)]); rng = random.Random(2); c = Counter(t.roll(rng) for _ in range(20000))
        self.assertAlmostEqual(c["gold"] / 20000, 0.75, delta=0.015); self.assertAlmostEqual(c["ruby"] / 20000, 0.125, delta=0.012); self.assertEqual(t.probabilities(), {"gold": 0.75})

    def test_pity_guarantees_a_hit(self):
        for seed in range(200):
            p = L.PityTable("junk", "legend", base=0.0, step=0.0, hard_pity=10); rng = random.Random(seed); rolls = [p.roll(rng) for _ in range(10)]
            self.assertEqual(rolls[-1], "legend"); self.assertEqual(rolls.count("legend"), 1)

    def test_pity_raises_average_hit_rate_and_resets(self):
        p = L.PityTable("c", "r", base=0.01, step=0.03, hard_pity=40); rng = random.Random(3); seen = [p.roll(rng) for _ in range(5000)]
        gaps, run = [], 0
        for s in seen:
            run += 1
            if s == "r": gaps.append(run); run = 0
        self.assertLessEqual(max(gaps), 40); self.assertGreater(len(gaps), 5000 / 40)

    def test_pack_has_no_duplicates(self):
        pool = [(f"card{i}", i + 1) for i in range(10)]
        for seed in range(50): pack = L.draw_without_replacement(pool, 5, random.Random(seed)); self.assertEqual(len(set(pack)), 5)
        self.assertEqual(len(L.draw_without_replacement(pool, 99, random.Random(0))), 10)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Floating-point weights; for provably exact odds use integer weights and rng.randrange(total)"], compat={"python": ">=3.9"},
   tags=["loot", "random", "weighted", "game-dev", "drop-table", "gacha", "pity", "probability", "rng", "modding", "game"],
   avoid="Global random without a seed; copy-pasted if/elif ladders of percentages; rarity with no pity in a paid-loot game (also a regulatory topic in some regions)."),

RC("py-procedural-dungeon-bsp", "Procedural dungeon generation (BSP rooms + corridors) guaranteed connected and reproducible", "python",
   "Roguelikes and level generators need rooms that never overlap, are always reachable from each other, and can be regenerated from a seed.",
   "Recursive binary space partition into leaves, a room inside each leaf with margins, sibling rooms joined with L-shaped corridors so connectivity holds by construction, flood-fill verification, seeded RNG, ASCII rendering for debugging.",
   {"dungeon.py": '''"""BSP dungeon generator. Grid of '#' (wall) and '.' (floor)."""
import random


class Leaf:
    def __init__(self, x, y, w, h): self.x, self.y, self.w, self.h, self.a, self.b, self.room = x, y, w, h, None, None, None

    def split(self, rng, min_size):
        if self.a: return False
        horiz = rng.random() < 0.5
        if self.w > self.h * 1.25: horiz = False
        elif self.h > self.w * 1.25: horiz = True
        mx = (self.h if horiz else self.w) - min_size
        if mx < min_size: return False
        cut = rng.randint(min_size, mx)
        if horiz: self.a, self.b = Leaf(self.x, self.y, self.w, cut), Leaf(self.x, self.y + cut, self.w, self.h - cut)
        else: self.a, self.b = Leaf(self.x, self.y, cut, self.h), Leaf(self.x + cut, self.y, self.w - cut, self.h)
        return True

    def leaves(self):
        return [self] if not self.a else self.a.leaves() + self.b.leaves()

    def any_room(self, rng):
        if not self.a: return self.room
        return rng.choice([r for r in (self.a.any_room(rng), self.b.any_room(rng)) if r])


def center(r):
    return r[0] + r[2] // 2, r[1] + r[3] // 2


def generate(w=60, h=30, min_leaf=8, seed=0):
    rng = random.Random(seed)
    root = Leaf(0, 0, w, h); todo = [root]
    while todo:
        leaf = todo.pop()
        if (leaf.w > 24 or leaf.h > 24 or rng.random() < 0.75) and leaf.split(rng, min_leaf): todo += [leaf.a, leaf.b]
    grid = [["#"] * w for _ in range(h)]
    for leaf in root.leaves():
        rw, rh = rng.randint(3, leaf.w - 2), rng.randint(3, leaf.h - 2)
        rx, ry = leaf.x + rng.randint(1, leaf.w - rw - 1), leaf.y + rng.randint(1, leaf.h - rh - 1)
        leaf.room = (rx, ry, rw, rh)
        for y in range(ry, ry + rh):
            for x in range(rx, rx + rw): grid[y][x] = "."
    def join(node):
        if not node.a: return
        join(node.a); join(node.b)
        (x1, y1), (x2, y2) = center(node.a.any_room(rng)), center(node.b.any_room(rng))
        for x in range(min(x1, x2), max(x1, x2) + 1): grid[y1][x] = "."
        for y in range(min(y1, y2), max(y1, y2) + 1): grid[y][x2] = "."
    join(root)
    return ["".join(r) for r in grid], [l.room for l in root.leaves()]


def connected(grid):
    h, w = len(grid), len(grid[0])
    floors = {(x, y) for y in range(h) for x in range(w) if grid[y][x] == "."}
    if not floors: return False
    seen, stack = set(), [next(iter(floors))]
    while stack:
        x, y = stack.pop()
        if (x, y) in seen: continue
        seen.add((x, y)); stack += [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)) if (x + dx, y + dy) in floors]
    return seen == floors
'''
    , "test_dungeon.py": '''import unittest
import dungeon as D


class T(unittest.TestCase):
    def test_always_connected_across_many_seeds(self):
        for seed in range(150): g, rooms = D.generate(seed=seed); self.assertTrue(D.connected(g), seed); self.assertGreaterEqual(len(rooms), 2, seed)

    def test_rooms_are_inside_the_map_and_disjoint(self):
        for seed in range(60):
            g, rooms = D.generate(seed=seed)
            for (x, y, w, h) in rooms: self.assertTrue(x >= 1 and y >= 1 and x + w <= len(g[0]) - 0 and y + h <= len(g) - 0)
            for i, a in enumerate(rooms):
                for b in rooms[i + 1:]: self.assertFalse(a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3], (seed, a, b))

    def test_border_stays_wall_and_reproducible(self):
        g, _ = D.generate(seed=5); self.assertTrue(set(g[0]) == {"#"} and set(g[-1]) == {"#"} and all(r[0] == "#" and r[-1] == "#" for r in g))
        self.assertEqual(D.generate(seed=5), D.generate(seed=5)); self.assertNotEqual(D.generate(seed=5)[0], D.generate(seed=6)[0])

    def test_dimensions_and_density_sane(self):
        g, _ = D.generate(40, 20, seed=1); self.assertEqual((len(g), len(g[0])), (20, 40)); floor = sum(r.count(".") for r in g) / (40 * 20); self.assertTrue(0.1 < floor < 0.7, floor)

    def test_connected_detects_a_split_map(self):
        self.assertFalse(D.connected(["#.#.#"])); self.assertTrue(D.connected(["#...#"])); self.assertFalse(D.connected(["###"]))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Grid-based 2D; cellular automata or wave-function-collapse give more organic caves"], compat={"python": ">=3.9"},
   tags=["procedural", "dungeon", "roguelike", "bsp", "level-generation", "game-dev", "map", "random", "modding", "game"],
   avoid="Random rooms with retry-until-no-overlap and no connectivity check; unseeded generation you cannot reproduce for a bug report."),

# ------------------------------------------------------------------ home / IoT
RC("py-wifi-reconnect-backoff-state", "Connection supervisor for flaky devices: reconnect with capped jittered backoff, offline queue, state machine", "python",
   "A sensor node on bad Wi-Fi/MQTT must keep measuring while offline, reconnect politely and flush what it buffered - without blocking or hammering the router.",
   "Explicit state machine (CONNECTED/BACKOFF/CONNECTING), exponential backoff with cap and full jitter, reset after a stable period, bounded offline queue that drops oldest, flush order preserved, injectable clock and transport so it is testable without hardware (portable to MicroPython).",
   {"link.py": '''"""Reconnect supervisor. `connect()` and `send(msg)` are injected; time comes from `now()` so tests can fake it. MicroPython-friendly (no typing, no deque)."""
import random


class Link:
    def __init__(self, connect, send, now, base=1.0, cap=60.0, stable_after=30.0, max_queue=100, rng=random.Random(0)):
        self.connect, self.send, self.now = connect, send, now
        self.base, self.cap, self.stable_after, self.max_queue, self.rng = base, cap, stable_after, max_queue, rng
        self.state, self.fails, self.retry_at, self.up_since, self.queue, self.dropped = "BACKOFF", 0, 0.0, None, [], 0

    def publish(self, msg):
        """Never blocks: sends now if connected, else buffers (oldest dropped past max_queue)."""
        if self.state == "CONNECTED":
            try: self.send(msg); return True
            except OSError: self._down()
        self.queue.append(msg)
        if len(self.queue) > self.max_queue: self.queue.pop(0); self.dropped += 1
        return False

    def tick(self):
        """Call from the main loop every iteration."""
        t = self.now()
        if self.state == "CONNECTED":
            if self.up_since is not None and t - self.up_since >= self.stable_after: self.fails = 0       # only a *stable* link resets the backoff
            return
        if t < self.retry_at: return
        try:
            self.connect(); self.state, self.up_since = "CONNECTED", t
            while self.queue:
                self.send(self.queue[0]); self.queue.pop(0)           # remove only after a successful send: a failure mid-flush loses nothing
        except OSError:
            self._down()

    def _down(self):
        self.state, self.up_since = "BACKOFF", None
        self.fails += 1
        delay = min(self.cap, self.base * 2 ** (self.fails - 1))
        self.retry_at = self.now() + self.rng.uniform(0, delay)       # full jitter: a room of devices must not retry in lockstep
'''
    , "test_link.py": '''import unittest, random
import link


class Net:
    def __init__(self): self.up, self.sent, self.t, self.connects = False, [], 0.0, 0
    def connect(self):
        self.connects += 1
        if not self.up: raise OSError("no wifi")
    def send(self, m):
        if not self.up: raise OSError("dead")
        self.sent.append(m)
    def now(self): return self.t


def make(**kw):
    n = Net(); return n, link.Link(n.connect, n.send, n.now, rng=random.Random(1), **kw)


class T(unittest.TestCase):
    def test_buffers_offline_flushes_in_order_on_reconnect(self):
        n, l = make(); [l.publish(i) for i in range(3)]; self.assertEqual(n.sent, [])
        n.up = True; n.t = 100; l.tick(); self.assertEqual((l.state, n.sent), ("CONNECTED", [0, 1, 2])); l.publish(3); self.assertEqual(n.sent, [0, 1, 2, 3])

    def test_backoff_grows_is_capped_and_jittered(self):
        n, l = make(base=1, cap=8); delays = []
        for _ in range(12):
            n.t = l.retry_at + 0.001; before = n.t; l.tick(); delays.append(l.retry_at - before)
        self.assertTrue(all(0 <= d <= 8 for d in delays)); self.assertGreater(max(delays), 1); self.assertGreater(len(set(round(d, 3) for d in delays)), 5)

    def test_does_not_hammer_before_retry_time(self):
        n, l = make(); n.t = 1000; l.tick(); c = n.connects
        for _ in range(50): l.tick()                       # same instant
        self.assertEqual(n.connects, c)

    def test_queue_is_bounded_drops_oldest(self):
        n, l = make(max_queue=3); [l.publish(i) for i in range(6)]; self.assertEqual((l.queue, l.dropped), ([3, 4, 5], 3))

    def test_failure_during_flush_loses_nothing(self):
        n, l = make(); [l.publish(i) for i in range(4)]; n.up = True
        real = n.send
        def flaky(m):
            if m == 2: n.up = False
            return real(m)
        l.send = flaky; n.t = 100; l.tick(); self.assertEqual(n.sent, [0, 1]); self.assertEqual(l.queue, [2, 3]); self.assertEqual(l.state, "BACKOFF")
        l.send = real; n.up = True; n.t = 1000; l.tick(); self.assertEqual(n.sent, [0, 1, 2, 3])

    def test_send_failure_when_connected_switches_to_backoff_and_buffers(self):
        n, l = make(); n.up = True; n.t = 10; l.tick(); n.up = False; self.assertFalse(l.publish("x")); self.assertEqual((l.state, l.queue), ("BACKOFF", ["x"]))

    def test_backoff_resets_only_after_stable_period(self):
        n, l = make(stable_after=30); n.t = 0; l.tick(); n.t = l.retry_at + 1; l.tick(); n.t = l.retry_at + 1; l.tick(); self.assertGreaterEqual(l.fails, 2)
        n.up = True; n.t = l.retry_at + 1; l.tick(); self.assertEqual(l.state, "CONNECTED"); self.assertGreaterEqual(l.fails, 2)
        n.t += 31; l.tick(); self.assertEqual(l.fails, 0)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["On MicroPython replace `random.Random` with `urandom`-based jitter and `time.ticks_ms` for now()", "Pairs with MQTT/HTTP clients: connect() and send() wrap their calls"], compat={"python": ">=3.9", "micropython": "ok (no stdlib extras)"},
   tags=["iot", "wifi", "reconnect", "backoff", "mqtt", "esp32", "micropython", "offline", "queue", "state-machine", "sensor", "home-automation"],
   avoid="Blocking `while not wifi.isconnected(): sleep(1)` loops; fixed-interval retries from every device at once; unbounded offline buffers that exhaust RAM."),

RC("py-sensor-readings-smoothing-dashboard", "Sensor readings: outlier rejection, smoothing, rolling min/max/avg and a tiny JSON + HTML dashboard", "python",
   "A Raspberry Pi / ESP bridge collects temperature or humidity and needs clean numbers and a no-framework page to show them.",
   "Ring buffer of timestamped readings, median-of-N and exponential smoothing, spike rejection using the median absolute deviation, rolling stats over a time window, calibration offset, stdlib `http.server` serving /api/readings (JSON) and a single HTML page that polls it; bind to localhost by default.",
   {"sensors.py": '''"""Sensor data handling plus a minimal dashboard server (stdlib only, localhost by default)."""
import json, statistics, threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Series:
    def __init__(self, maxlen=1000, offset=0.0, alpha=0.3, mad_k=5.0, confirm=5):
        self.buf, self.offset, self.alpha, self.mad_k, self.smooth, self.rejected = deque(maxlen=maxlen), offset, alpha, mad_k, None, 0
        self.confirm, self.streak = confirm, 0
        self.lock = threading.Lock()

    def add(self, ts, value):
        """Returns True if accepted, False if rejected as a spike (a sustained jump is accepted after `confirm` readings)."""
        v = value + self.offset
        with self.lock:
            recent = [x for _, x in list(self.buf)[-15:]]
            if len(recent) >= 5:
                med = statistics.median(recent); mad = statistics.median(abs(x - med) for x in recent) or 0.1
                if abs(v - med) > self.mad_k * 1.4826 * mad:
                    self.streak += 1
                    if self.streak < self.confirm:
                        self.rejected += 1; return False
                    self.buf.clear(); self.smooth = None            # `confirm` outliers in a row is a real step change, not a spike: adopt the new level
            self.streak = 0
            self.smooth = v if self.smooth is None else self.alpha * v + (1 - self.alpha) * self.smooth
            self.buf.append((ts, v)); return True

    def window(self, now, seconds):
        with self.lock:
            xs = [v for t, v in self.buf if now - seconds <= t <= now]
        return dict(n=len(xs), min=min(xs), max=max(xs), avg=sum(xs) / len(xs)) if xs else dict(n=0, min=None, max=None, avg=None)

    def snapshot(self, last=100):
        with self.lock:
            return dict(smooth=self.smooth, rejected=self.rejected, points=list(self.buf)[-last:])


PAGE = b"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width"><title>Sensors</title>
<h1 id=v>--</h1><canvas id=c width=600 height=160></canvas>
<script>async function t(){const d=await (await fetch('/api/readings')).json();document.getElementById('v').textContent=d.smooth==null?'--':d.smooth.toFixed(1);
const c=document.getElementById('c').getContext('2d');c.clearRect(0,0,600,160);const p=d.points;if(p.length>1){const ys=p.map(x=>x[1]),lo=Math.min(...ys),hi=Math.max(...ys)||1;
c.beginPath();p.forEach((q,i)=>{const x=i/(p.length-1)*600,y=150-(q[1]-lo)/((hi-lo)||1)*140;i?c.lineTo(x,y):c.moveTo(x,y)});c.stroke()}}t();setInterval(t,2000)</script>"""


def make_server(series, host="127.0.0.1", port=0):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/readings": body, ctype = json.dumps(series.snapshot()).encode(), "application/json"
            elif self.path == "/": body, ctype = PAGE, "text/html; charset=utf-8"
            else: self.send_error(404); return
            self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    return ThreadingHTTPServer((host, port), H)
'''
    , "test_sensors.py": '''import unittest, threading, json, urllib.request
import sensors as S


class T(unittest.TestCase):
    def test_spike_rejected_normal_accepted(self):
        s = S.Series()
        for i, v in enumerate([20.0, 20.1, 19.9, 20.2, 20.0, 20.1]): self.assertTrue(s.add(i, v))
        self.assertFalse(s.add(10, 85.0)); self.assertTrue(s.add(11, 20.4)); self.assertEqual(s.rejected, 1); self.assertEqual(len(s.snapshot()["points"]), 7)

    def test_genuine_step_change_eventually_accepted_not_stuck(self):
        s = S.Series(); [s.add(i, 20.0 + (i % 3) * 0.1) for i in range(10)]
        results = [s.add(100 + i, 25.0 + (i % 3) * 0.1) for i in range(12)]; self.assertTrue(results[-1]); self.assertEqual(results.count(False), 4); self.assertAlmostEqual(s.snapshot()['smooth'], 25.1, delta=0.2)

    def test_smoothing_converges_without_lag_explosion(self):
        s = S.Series(alpha=0.5); s.add(0, 10.0); s.add(1, 10.0); s.add(2, 10.0); s.add(3, 10.0); s.add(4, 10.0); s.add(5, 10.5)
        self.assertAlmostEqual(s.smooth, 10.25)

    def test_calibration_offset_applied(self):
        s = S.Series(offset=-1.5); s.add(0, 22.0); self.assertEqual(s.snapshot()["points"][0][1], 20.5)

    def test_rolling_window_stats(self):
        s = S.Series(); [s.add(t, 20 + t * 0.01) for t in range(0, 100, 10)]; w = s.window(now=95, seconds=30)
        self.assertEqual(w["n"], 3); self.assertAlmostEqual(w["avg"], 20.8); self.assertEqual(s.window(now=1000, seconds=5), dict(n=0, min=None, max=None, avg=None))

    def test_buffer_is_bounded(self):
        s = S.Series(maxlen=50); [s.add(i, 20.0 + (i % 2) * 0.05) for i in range(500)]; self.assertEqual(len(s.snapshot(1000)["points"]), 50)

    def test_http_endpoints(self):
        s = S.Series(); s.add(1, 21.5); srv = S.make_server(s); threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"; d = json.load(urllib.request.urlopen(base + "/api/readings"))
            self.assertEqual(d["smooth"], 21.5); self.assertIn(b"<canvas", urllib.request.urlopen(base + "/").read())
            with self.assertRaises(urllib.error.HTTPError) as cm: urllib.request.urlopen(base + "/nope")
            self.assertEqual(cm.exception.code, 404); self.assertEqual(srv.server_address[0], "127.0.0.1")
        finally: srv.shutdown(); srv.server_close()


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["No auth: keep it on localhost or behind a reverse proxy/VPN; binding 0.0.0.0 exposes readings to the LAN", "Test uses loopback sockets inside the sandbox"], compat={"python": ">=3.9"},
   tags=["iot", "sensor", "raspberry-pi", "dashboard", "temperature", "smoothing", "outlier", "http-server", "home-automation", "esp32", "mqtt", "telemetry"],
   avoid="Averaging raw spikes into the display; binding dashboards to 0.0.0.0 by default; unbounded in-memory history on a Pi."),
]
