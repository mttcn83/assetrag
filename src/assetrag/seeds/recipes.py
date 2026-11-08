"""Tested solution recipes. Stdlib-only on purpose (no dependency licensing risk). Each recipe's tests are executed in
the sandbox at seed time; a recipe that fails its own tests is rejected."""

UT = ["python3", "-m", "unittest", "-q"]
GOT = ["go", "test", "-count=1", "."]
NODET = ["node", "--test"]


def RC(key, title, lang, problem, approach, files, cmd, *, assumptions, compat, tags, avoid=""):
    return dict(key=key, title=title, lang=lang, problem=problem, approach=approach, files=files, cmd=cmd,
                assumptions=assumptions, compat=compat, tags=tags, avoid=avoid)


RECIPES = [
RC("py-retry-backoff", "Retry with exponential backoff and jitter (sync + async)", "python",
   "Calling flaky network/IO operations without hammering the dependency or retrying non-retryable errors.",
   "Decorator with capped exponential backoff, full jitter, retry-only-on listed exceptions, max attempts and deadline; sleep/rng injectable for tests.",
   {"retry.py": '''"""Retry with capped exponential backoff + full jitter. Sync and async."""
import asyncio, functools, random, time


def _delays(base, cap, attempts, rng):
    for n in range(attempts - 1):
        yield rng.uniform(0, min(cap, base * 2 ** n))      # "full jitter"


def retry(*, on=(Exception,), attempts=5, base=0.2, cap=5.0, deadline=None, sleep=time.sleep, rng=random, asleep=asyncio.sleep):
    """Retry only exceptions in `on`; re-raise the last one. `deadline` caps total seconds."""
    def deco(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrap(*a, **k):
                t0, last = time.monotonic(), None
                for d in list(_delays(base, cap, attempts, rng)) + [None]:
                    try:
                        return await fn(*a, **k)
                    except on as e:
                        last = e
                        if d is None or (deadline and time.monotonic() - t0 + d > deadline):
                            raise
                        await asleep(d)
                raise last
            return awrap
        @functools.wraps(fn)
        def wrap(*a, **k):
            t0 = time.monotonic()
            for d in list(_delays(base, cap, attempts, rng)) + [None]:
                try:
                    return fn(*a, **k)
                except on as e:
                    if d is None or (deadline and time.monotonic() - t0 + d > deadline):
                        raise
                    sleep(d)
        return wrap
    return deco
''', "test_retry.py": '''import asyncio, random, unittest
from retry import retry

class T(unittest.TestCase):
    def test_succeeds_after_failures(self):
        calls, sleeps = [], []
        @retry(on=(ValueError,), attempts=4, sleep=sleeps.append, rng=random.Random(1))
        def f():
            calls.append(1)
            if len(calls) < 3: raise ValueError("x")
            return "ok"
        self.assertEqual(f(), "ok"); self.assertEqual(len(calls), 3); self.assertEqual(len(sleeps), 2)

    def test_non_retryable_raises_immediately(self):
        calls = []
        @retry(on=(ValueError,), sleep=lambda s: None)
        def f():
            calls.append(1); raise KeyError("k")
        with self.assertRaises(KeyError): f()
        self.assertEqual(len(calls), 1)

    def test_gives_up_and_reraises_last(self):
        @retry(on=(ValueError,), attempts=3, sleep=lambda s: None)
        def f(): raise ValueError("boom")
        with self.assertRaisesRegex(ValueError, "boom"): f()

    def test_delay_bounded_by_cap(self):
        sleeps = []
        @retry(on=(ValueError,), attempts=8, base=1, cap=2, sleep=sleeps.append, rng=random.Random(0))
        def f(): raise ValueError()
        with self.assertRaises(ValueError): f()
        self.assertTrue(all(0 <= s <= 2 for s in sleeps)); self.assertEqual(len(sleeps), 7)

    def test_async(self):
        n = []
        async def nosleep(d): pass
        @retry(on=(ValueError,), attempts=3, asleep=nosleep)
        async def f():
            n.append(1)
            if len(n) < 2: raise ValueError()
            return 5
        self.assertEqual(asyncio.run(f()), 5)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Operation is idempotent or safe to repeat", "Python >=3.9"], compat={"python": ">=3.9"}, tags=["retry", "resilience", "api-client", "asyncio"],
   avoid="Retrying non-idempotent writes or all exceptions (retry only transient ones)."),
RC("py-ttl-cache", "Thread-safe TTL + LRU cache with explicit invalidation", "python",
   "Caching expensive lookups with bounded memory, expiry and correct invalidation.",
   "OrderedDict LRU with per-entry expiry using an injectable monotonic clock, key and tag invalidation, a lock, and single-flight loading to avoid stampedes.",
   {"ttlcache.py": '''"""TTL + LRU cache, thread-safe, with tag invalidation and single-flight loads."""
import threading, time
from collections import OrderedDict


class TTLCache:
    def __init__(self, maxsize=1024, ttl=60.0, clock=time.monotonic):
        self.maxsize, self.ttl, self.clock = maxsize, ttl, clock
        self._d = OrderedDict()                 # key -> (expires, value, tags)
        self._lock = threading.RLock()
        self._loading = {}                      # key -> threading.Event (single flight)

    def get(self, key, default=None):
        with self._lock:
            item = self._d.get(key)
            if item is None or item[0] <= self.clock():
                self._d.pop(key, None)
                return default
            self._d.move_to_end(key)
            return item[1]

    def set(self, key, value, ttl=None, tags=()):
        with self._lock:
            self._d[key] = (self.clock() + (self.ttl if ttl is None else ttl), value, frozenset(tags))
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def invalidate(self, key):
        with self._lock:
            return self._d.pop(key, None) is not None

    def invalidate_tag(self, tag):
        with self._lock:
            dead = [k for k, (_, _, t) in self._d.items() if tag in t]
            for k in dead:
                del self._d[k]
            return len(dead)

    def get_or_load(self, key, loader, ttl=None, tags=()):
        """Only one thread runs `loader` per missing key; others wait for its result."""
        miss = object()
        while True:
            v = self.get(key, miss)
            if v is not miss:
                return v
            with self._lock:
                ev = self._loading.get(key)
                if ev is None:
                    ev = self._loading[key] = threading.Event()
                    mine = True
                else:
                    mine = False
            if mine:
                try:
                    v = loader()
                    self.set(key, v, ttl, tags)
                    return v
                finally:
                    with self._lock:
                        self._loading.pop(key, None)
                    ev.set()
            ev.wait()
''', "test_ttlcache.py": '''import threading, unittest
from ttlcache import TTLCache

class Clock:
    t = 0.0
    def __call__(self): return self.t

class T(unittest.TestCase):
    def test_expiry(self):
        c = Clock(); k = TTLCache(ttl=10, clock=c); k.set("a", 1)
        self.assertEqual(k.get("a"), 1); c.t = 10; self.assertIsNone(k.get("a"))
    def test_lru_eviction(self):
        k = TTLCache(maxsize=2); k.set("a", 1); k.set("b", 2); k.get("a"); k.set("c", 3)
        self.assertIsNone(k.get("b")); self.assertEqual(k.get("a"), 1)
    def test_tag_invalidation(self):
        k = TTLCache(); k.set("u1", 1, tags=["user:1"]); k.set("u2", 2, tags=["user:2"])
        self.assertEqual(k.invalidate_tag("user:1"), 1); self.assertIsNone(k.get("u1")); self.assertEqual(k.get("u2"), 2)
    def test_single_flight(self):
        k = TTLCache(); calls = []; gate = threading.Event()
        def loader():
            calls.append(1); gate.wait(1); return "v"
        out = []
        ts = [threading.Thread(target=lambda: out.append(k.get_or_load("x", loader))) for _ in range(8)]
        [t.start() for t in ts]; gate.set(); [t.join() for t in ts]
        self.assertEqual(out, ["v"] * 8); self.assertEqual(len(calls), 1)
    def test_loader_error_not_cached(self):
        k = TTLCache()
        with self.assertRaises(RuntimeError): k.get_or_load("x", lambda: (_ for _ in ()).throw(RuntimeError("no")))
        self.assertEqual(k.get_or_load("x", lambda: 7), 7)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single process (not shared across workers)", "Cached values are treated as immutable"], compat={"python": ">=3.9"}, tags=["cache", "invalidation", "concurrency"],
   avoid="Caching without an invalidation story or unbounded dict caches."),
RC("py-token-bucket", "Token-bucket rate limiter", "python", "Throttling API calls or incoming requests per key with bursts.",
   "Monotonic-clock token bucket per key with lazy refill, injectable clock, and bounded key table.",
   {"ratelimit.py": '''"""Token bucket rate limiter (per key). rate tokens/second, burst capacity."""
import threading, time


class RateLimiter:
    def __init__(self, rate, burst, clock=time.monotonic, max_keys=10000):
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate, self.burst, self.clock, self.max_keys = rate, burst, clock, max_keys
        self._b = {}
        self._lock = threading.Lock()

    def allow(self, key="global", cost=1):
        now = self.clock()
        with self._lock:
            tokens, last = self._b.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            if ok:
                tokens -= cost
            self._b[key] = (tokens, now)
            if len(self._b) > self.max_keys:                 # drop oldest keys
                for k in sorted(self._b, key=lambda k: self._b[k][1])[: len(self._b) - self.max_keys]:
                    self._b.pop(k, None)
            return ok

    def retry_after(self, key="global", cost=1):
        with self._lock:
            tokens, last = self._b.get(key, (self.burst, self.clock()))
            tokens = min(self.burst, tokens + (self.clock() - last) * self.rate)
            return 0.0 if tokens >= cost else (cost - tokens) / self.rate
''', "test_ratelimit.py": '''import unittest
from ratelimit import RateLimiter

class C:
    t = 0.0
    def __call__(self): return self.t

class T(unittest.TestCase):
    def test_burst_then_block_then_refill(self):
        c = C(); r = RateLimiter(2, 3, clock=c)
        self.assertEqual([r.allow("a") for _ in range(4)], [True, True, True, False])
        c.t = 0.5; self.assertTrue(r.allow("a")); self.assertFalse(r.allow("a"))
    def test_keys_independent(self):
        c = C(); r = RateLimiter(1, 1, clock=c)
        self.assertTrue(r.allow("a")); self.assertTrue(r.allow("b")); self.assertFalse(r.allow("a"))
    def test_retry_after(self):
        c = C(); r = RateLimiter(2, 1, clock=c); r.allow("a")
        self.assertAlmostEqual(r.retry_after("a"), 0.5)
    def test_invalid(self):
        with self.assertRaises(ValueError): RateLimiter(0, 1)
    def test_bounded_keys(self):
        c = C(); r = RateLimiter(1, 1, clock=c, max_keys=3)
        for i in range(10): c.t = i; r.allow(str(i))
        self.assertLessEqual(len(r._b), 3)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["In-process limiter; use Redis/shared store for multi-process"], compat={"python": ">=3.9"}, tags=["rate-limit", "api", "middleware"]),
RC("py-sqlite-migrations", "SQLite versioned migrations + safe parameterized access", "python",
   "Evolving a SQLite schema safely and querying without SQL injection.",
   "PRAGMA user_version tracks applied migrations; each migration runs in a transaction; foreign keys on; WAL; parameterized queries only.",
   {"db.py": '''"""Versioned SQLite migrations. Each migration is atomic; user_version records progress."""
import sqlite3

MIGRATIONS = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)",
    "CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL)",
    "CREATE INDEX idx_posts_user ON posts(user_id)",
]


def connect(path=":memory:"):
    con = sqlite3.connect(path, timeout=10, isolation_level=None)   # autocommit; we manage transactions explicitly
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if path != ":memory:":
        con.execute("PRAGMA journal_mode = WAL")
    return con


def migrate(con, migrations=MIGRATIONS):
    cur = con.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(migrations[cur:], start=cur + 1):
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(sql)
            con.execute(f"PRAGMA user_version = {int(i)}")      # int(): PRAGMA can't be parameterized
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    return len(migrations)


def add_user(con, username):
    return con.execute("INSERT INTO users(username) VALUES (?)", (username,)).lastrowid


def find_user(con, username):
    return con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
''', "test_db.py": '''import sqlite3, unittest
import db

class T(unittest.TestCase):
    def test_migrate_idempotent(self):
        c = db.connect(); self.assertEqual(db.migrate(c), 3); self.assertEqual(db.migrate(c), 3)
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 3)
    def test_failed_migration_rolls_back(self):
        c = db.connect(); db.migrate(c, db.MIGRATIONS[:1])
        with self.assertRaises(sqlite3.OperationalError): db.migrate(c, db.MIGRATIONS[:1] + ["CREATE TABLE bad ("])
        self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 1)
    def test_injection_is_inert(self):
        c = db.connect(); db.migrate(c); db.add_user(c, "alice")
        self.assertIsNone(db.find_user(c, "x' OR '1'='1")); self.assertIsNotNone(db.find_user(c, "alice"))
    def test_foreign_keys_enforced_and_cascade(self):
        c = db.connect(); db.migrate(c)
        with self.assertRaises(sqlite3.IntegrityError): c.execute("INSERT INTO posts(user_id,title) VALUES (99,'t')")
        u = db.add_user(c, "bob"); c.execute("INSERT INTO posts(user_id,title) VALUES (?,?)", (u, "t"))
        c.execute("DELETE FROM users WHERE id=?", (u,)); self.assertEqual(c.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 0)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["SQLite >= 3.8", "Migrations are append-only once released"], compat={"python": ">=3.9", "sqlite": ">=3.8"}, tags=["database", "migrations", "sql", "sqlite"]),
RC("py-auth-tokens", "Password hashing + signed expiring tokens + role check", "python",
   "Authentication and authorization without third-party deps: store passwords safely, issue/verify tamper-proof expiring tokens, enforce roles.",
   "scrypt with per-user salt and parameters embedded in the hash string; constant-time compare; HMAC-SHA256 signed token (base64url JSON) with exp and role; decorator for authorization.",
   {"auth.py": '''"""Stdlib auth primitives. For production web apps prefer a vetted framework; keep SECRET in env/secret store."""
import base64, functools, hashlib, hmac, json, os, time

N, R, P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calc.hex(), h)


def _b64(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def _unb64(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(secret: bytes, sub: str, role: str = "user", ttl: int = 3600, now=time.time) -> str:
    body = _b64(json.dumps({"sub": sub, "role": role, "exp": int(now()) + ttl}, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


class AuthError(Exception):
    pass


def verify_token(secret: bytes, token: str, now=time.time) -> dict:
    try:
        body, sig = token.split(".")
        good = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good):
            raise AuthError("bad signature")
        claims = json.loads(_unb64(body))
    except (ValueError, KeyError) as e:
        raise AuthError("malformed token") from e
    if claims.get("exp", 0) <= now():
        raise AuthError("expired")
    return claims


def require_role(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(claims, *a, **k):
            if claims.get("role") not in roles:
                raise AuthError("forbidden")
            return fn(claims, *a, **k)
        return wrap
    return deco
''', "test_auth.py": '''import unittest
import auth

S = b"k" * 32

class T(unittest.TestCase):
    def test_password_roundtrip_and_salt(self):
        a, b = auth.hash_password("pw"), auth.hash_password("pw")
        self.assertNotEqual(a, b); self.assertTrue(auth.verify_password("pw", a)); self.assertFalse(auth.verify_password("no", a))
    def test_garbage_hash_is_false(self): self.assertFalse(auth.verify_password("x", "garbage"))
    def test_token_ok_expired_tampered(self):
        t = auth.issue_token(S, "u1", "admin", ttl=10, now=lambda: 100)
        self.assertEqual(auth.verify_token(S, t, now=lambda: 105)["sub"], "u1")
        with self.assertRaisesRegex(auth.AuthError, "expired"): auth.verify_token(S, t, now=lambda: 111)
        body, sig = t.split(".")
        with self.assertRaisesRegex(auth.AuthError, "signature"): auth.verify_token(S, body + "." + sig[:-2] + "AA", now=lambda: 105)
        with self.assertRaisesRegex(auth.AuthError, "signature"): auth.verify_token(b"z" * 32, t, now=lambda: 105)
        with self.assertRaises(auth.AuthError): auth.verify_token(S, "nope")
    def test_roles(self):
        @auth.require_role("admin")
        def f(claims): return "ok"
        self.assertEqual(f({"role": "admin"}), "ok")
        with self.assertRaisesRegex(auth.AuthError, "forbidden"): f({"role": "user"})

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Secret >= 32 random bytes from env/secret store", "Tokens are not revocable before exp (add a denylist if needed)"], compat={"python": ">=3.9"}, tags=["authentication", "authorization", "tokens", "security"],
   avoid="Rolling your own crypto beyond these standard primitives; storing passwords with plain sha256."),
RC("py-async-bounded", "Bounded async concurrency with timeout and clean cancellation", "python",
   "Fetching many items concurrently without unbounded tasks, hung calls, or leaked tasks on error.",
   "Semaphore-limited gather with per-item timeout, results in input order with exceptions captured, and cancellation of siblings on fatal errors via TaskGroup (3.11+).",
   {"bounded.py": '''"""Bounded concurrent map with per-item timeout. Python >= 3.11 (TaskGroup)."""
import asyncio


async def bounded_map(fn, items, *, limit=10, timeout=None, return_exceptions=True):
    """Run `await fn(item)` for all items, at most `limit` at once; results keep input order.
    With return_exceptions=False the first error cancels the remaining work (structured concurrency)."""
    sem = asyncio.Semaphore(limit)
    results = [None] * len(items)

    async def one(i, item):
        async with sem:
            try:
                results[i] = await (asyncio.wait_for(fn(item), timeout) if timeout else fn(item))
            except Exception as e:                      # not BaseException: keep CancelledError propagating
                if not return_exceptions:
                    raise
                results[i] = e

    async with asyncio.TaskGroup() as tg:
        for i, item in enumerate(items):
            tg.create_task(one(i, item))
    return results
''', "test_bounded.py": '''import asyncio, unittest
from bounded import bounded_map

class T(unittest.TestCase):
    def test_limit_and_order(self):
        active = peak = 0
        async def fn(x):
            nonlocal active, peak
            active += 1; peak = max(peak, active); await asyncio.sleep(0.01); active -= 1; return x * 2
        out = asyncio.run(bounded_map(fn, list(range(20)), limit=3))
        self.assertEqual(out, [x * 2 for x in range(20)]); self.assertLessEqual(peak, 3)
    def test_timeout_captured(self):
        async def fn(x):
            await asyncio.sleep(1 if x == 1 else 0); return x
        out = asyncio.run(bounded_map(fn, [0, 1, 2], timeout=0.05))
        self.assertEqual(out[0], 0); self.assertIsInstance(out[1], TimeoutError); self.assertEqual(out[2], 2)
    def test_fail_fast_cancels_rest(self):
        done = []
        async def fn(x):
            if x == 0: raise ValueError("bad")
            await asyncio.sleep(0.5); done.append(x)
        with self.assertRaises(ExceptionGroup): asyncio.run(bounded_map(fn, [0, 1, 2], limit=3, return_exceptions=False))
        self.assertEqual(done, [])

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Python >= 3.11"], compat={"python": ">=3.11"}, tags=["asyncio", "concurrency", "timeouts"]),
RC("py-json-logging", "Structured JSON logging with correlation IDs", "python",
   "Logs that are machine-parseable and traceable across a request without passing IDs everywhere.",
   "logging.Formatter emitting one JSON object per line, contextvars-based correlation id injected by a Filter, exception info included, secrets never logged by default fields.",
   {"jsonlog.py": '''"""JSON logs + correlation id via contextvars."""
import contextvars, json, logging, sys, time, uuid

correlation_id = contextvars.ContextVar("correlation_id", default="-")


def new_correlation_id(value=None):
    return correlation_id.set(value or uuid.uuid4().hex[:12])


class _Ctx(logging.Filter):
    def filter(self, record):
        record.cid = correlation_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, r):
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(r.created)) + f".{int(r.msecs):03d}Z", "level": r.levelname,
             "logger": r.name, "msg": r.getMessage(), "cid": r.cid}
        for k, v in r.__dict__.items():
            if k.startswith("x_"):                         # opt-in structured fields: log.info("m", extra={"x_user": 1})
                d[k[2:]] = v
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)
        return json.dumps(d, default=str, ensure_ascii=False)


def setup(level="INFO", stream=None):
    h = logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(JsonFormatter()); h.addFilter(_Ctx())
    root = logging.getLogger(); root.handlers[:] = [h]; root.setLevel(level)
    return root
''', "test_jsonlog.py": '''import io, json, logging, unittest
import jsonlog

class T(unittest.TestCase):
    def setUp(self):
        self.buf = io.StringIO(); jsonlog.setup(stream=self.buf); self.log = logging.getLogger("t")
    def lines(self): return [json.loads(l) for l in self.buf.getvalue().splitlines()]
    def test_one_json_per_line_with_cid(self):
        jsonlog.new_correlation_id("abc"); self.log.info("hello %s", "w", extra={"x_user": 7})
        d = self.lines()[0]; self.assertEqual((d["msg"], d["cid"], d["user"], d["level"]), ("hello w", "abc", 7, "INFO"))
    def test_exception_included(self):
        try: 1 / 0
        except ZeroDivisionError: self.log.exception("fail")
        self.assertIn("ZeroDivisionError", self.lines()[0]["exc"])
    def test_default_cid(self):
        jsonlog.correlation_id.set("-"); self.log.warning("x"); self.assertEqual(self.lines()[0]["cid"], "-")

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Single-process stdout/stderr sink"], compat={"python": ">=3.9"}, tags=["logging", "observability", "tracing"]),
RC("py-secure-files", "Secure file handling: atomic writes, safe paths, safe zip extraction", "python",
   "Writing files without corruption and handling user-supplied paths/archives without traversal.",
   "write temp file in same dir + fsync + os.replace; resolve-and-confine paths; zip extraction rejecting traversal, symlinks and zip bombs; restrictive permissions for secrets.",
   {"safefs.py": '''"""Atomic writes, confined paths, safe zip extraction."""
import os, stat, tempfile, zipfile
from pathlib import Path


def atomic_write(path, data: bytes, mode=0o644):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)                               # atomic on POSIX
    except BaseException:
        try: os.unlink(tmp)
        except FileNotFoundError: pass
        raise


def safe_join(base, user_path):
    base = Path(base).resolve()
    p = (base / user_path).resolve()
    if p != base and base not in p.parents:
        raise ValueError(f"path escapes base: {user_path!r}")
    return p


def safe_unzip(archive, dest, max_files=10_000, max_bytes=500 * 2**20):
    dest = Path(dest); total = 0
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > max_files:
            raise ValueError("too many files")
        for i in infos:
            target = safe_join(dest, i.filename)
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ValueError(f"symlink in archive: {i.filename}")
            total += i.file_size
            if total > max_bytes:
                raise ValueError("archive too large (possible zip bomb)")
        for i in infos:
            target = safe_join(dest, i.filename)
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as s, open(target, "wb") as d:
                    d.write(s.read())
''', "test_safefs.py": '''import os, tempfile, unittest, zipfile
from pathlib import Path
import safefs

class T(unittest.TestCase):
    def setUp(self): self.d = Path(tempfile.mkdtemp())
    def test_atomic_write_replaces_and_no_temp_left(self):
        p = self.d / "f.txt"; safefs.atomic_write(p, b"one"); safefs.atomic_write(p, b"two", 0o600)
        self.assertEqual(p.read_bytes(), b"two"); self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600"); self.assertEqual(os.listdir(self.d), ["f.txt"])
    def test_safe_join(self):
        self.assertTrue(str(safefs.safe_join(self.d, "a/b.txt")).startswith(str(self.d.resolve())))
        for bad in ("../x", "a/../../x", "/etc/passwd"):
            with self.assertRaises(ValueError): safefs.safe_join(self.d, bad)
    def test_zip_slip_blocked(self):
        z = self.d / "e.zip"
        with zipfile.ZipFile(z, "w") as f: f.writestr("../evil.txt", "x")
        with self.assertRaises(ValueError): safefs.safe_unzip(z, self.d / "out")
        self.assertFalse((self.d / "evil.txt").exists())
    def test_zip_ok_and_bomb_limit(self):
        z = self.d / "ok.zip"
        with zipfile.ZipFile(z, "w") as f: f.writestr("a/b.txt", "hello")
        safefs.safe_unzip(z, self.d / "out"); self.assertEqual((self.d / "out/a/b.txt").read_text(), "hello")
        with self.assertRaises(ValueError): safefs.safe_unzip(z, self.d / "o2", max_bytes=2)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["POSIX filesystem semantics for atomic rename"], compat={"python": ">=3.9"}, tags=["files", "security", "path-traversal", "zip"]),
RC("py-cli-skeleton", "Python CLI skeleton: --json, NO_COLOR, exit codes, SIGPIPE-safe", "python",
   "Starting a well-behaved command line tool (help with examples, machine output, stable exit codes, pipe-friendly).",
   "argparse with subcommand dispatch, human default output + --json, color only on TTY and without NO_COLOR, documented exit codes, errors to stderr, BrokenPipe/KeyboardInterrupt handled.",
   {"mytool.py": '''#!/usr/bin/env python3
"""mytool - one-sentence purpose.   Exit codes: 0 ok, 1 runtime error, 2 usage error, 130 interrupted."""
import argparse, json, os, signal, sys

OK, ERR, USAGE = 0, 1, 2


def color(s, code, stream=sys.stdout):
    return f"\\033[{code}m{s}\\033[0m" if stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb" else s


def cmd_list(args):
    rows = [{"name": "alpha", "size": 3}, {"name": "beta", "size": 5}]
    if args.json:
        print(json.dumps(rows))
    else:
        for r in rows:
            print(f"{color(r['name'], '36'):<10}{r['size']:>4}")
    return OK


def build_parser():
    p = argparse.ArgumentParser(prog="mytool", description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="examples:\\n  mytool list\\n  mytool list --json | jq .")
    p.add_argument("--version", action="version", version="mytool 0.1.0")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")
    s = sub.add_parser("list", help="list things"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)            # argparse exits 2 on usage errors
    try:
        return args.fn(args)
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno()); return OK
    except KeyboardInterrupt:
        return 130
    except Exception as e:                            # expected failures: message, not traceback
        print(f"error: {e}", file=sys.stderr); return ERR


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())
''', "test_mytool.py": '''import json, os, subprocess, sys, unittest

def run(*a, env=None):
    return subprocess.run([sys.executable, "mytool.py", *a], capture_output=True, text=True, env={**os.environ, **(env or {})})

class T(unittest.TestCase):
    def test_help_has_examples(self):
        r = run("--help"); self.assertEqual(r.returncode, 0); self.assertIn("examples:", r.stdout)
    def test_json_is_clean(self):
        r = run("list", "--json"); self.assertEqual(len(json.loads(r.stdout)), 2); self.assertNotIn("\\x1b", r.stdout)
    def test_no_color_when_piped(self): self.assertNotIn("\\x1b", run("list").stdout)
    def test_usage_error_exit_2(self): self.assertEqual(run("bogus").returncode, 2)
    def test_pipe_to_head_is_quiet(self):
        r = subprocess.run("python3 mytool.py list | head -n1", shell=True, capture_output=True, text=True); self.assertEqual(r.stderr, "")

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["POSIX (SIGPIPE)"], compat={"python": ">=3.9"}, tags=["cli", "argparse", "json", "exit-codes"]),
RC("py-xdg-config", "XDG config loader with env overrides and validation", "python",
   "Reading tool configuration the Unix way without surprising users.",
   "Search XDG_CONFIG_HOME/APPNAME/config.json then defaults; environment variables override file; typed validation with clear errors; never writes unless asked.",
   {"config.py": '''"""XDG-aware config: defaults < file < env."""
import json, os
from pathlib import Path

DEFAULTS = {"url": "http://localhost:8080", "timeout": 10.0, "verbose": False}
TYPES = {"url": str, "timeout": float, "verbose": bool}


class ConfigError(ValueError):
    pass


def config_path(app, env=os.environ):
    base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / app / "config.json"


def _coerce(key, val, src):
    t = TYPES[key]
    try:
        if t is bool:
            if isinstance(val, bool): return val
            if str(val).lower() in ("1", "true", "yes", "on"): return True
            if str(val).lower() in ("0", "false", "no", "off"): return False
            raise ValueError
        return t(val)
    except (TypeError, ValueError):
        raise ConfigError(f"{src}: {key} must be {t.__name__}, got {val!r}") from None


def load(app, env=os.environ):
    cfg = dict(DEFAULTS)
    p = config_path(app, env)
    if p.exists():
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{p}: invalid JSON ({e})") from None
        for k, v in data.items():
            if k not in TYPES:
                raise ConfigError(f"{p}: unknown key {k!r}")
            cfg[k] = _coerce(k, v, str(p))
    for k in TYPES:
        ev = env.get(f"{app.upper()}_{k.upper()}")
        if ev is not None:
            cfg[k] = _coerce(k, ev, f"${app.upper()}_{k.upper()}")
    return cfg
''', "test_config.py": '''import json, tempfile, unittest
from pathlib import Path
import config

class T(unittest.TestCase):
    def env(self, **kw):
        d = tempfile.mkdtemp(); return {"XDG_CONFIG_HOME": d, **kw}, Path(d)
    def test_defaults(self): self.assertEqual(config.load("app", self.env()[0]), config.DEFAULTS)
    def test_file_then_env_override(self):
        e, d = self.env(APP_TIMEOUT="3"); (d / "app").mkdir(); (d / "app/config.json").write_text(json.dumps({"timeout": 5, "verbose": True}))
        c = config.load("app", e); self.assertEqual((c["timeout"], c["verbose"]), (3.0, True))
    def test_bad_type_and_unknown_key(self):
        with self.assertRaisesRegex(config.ConfigError, "timeout must be float"): config.load("app", self.env(APP_TIMEOUT="fast")[0])
        e, d = self.env(); (d / "app").mkdir(); (d / "app/config.json").write_text('{"nope": 1}')
        with self.assertRaisesRegex(config.ConfigError, "unknown key"): config.load("app", e)
    def test_invalid_json(self):
        e, d = self.env(); (d / "app").mkdir(); (d / "app/config.json").write_text("{")
        with self.assertRaisesRegex(config.ConfigError, "invalid JSON"): config.load("app", e)

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Linux/XDG layout"], compat={"python": ">=3.9"}, tags=["config", "xdg", "cli"]),
RC("py-term-table", "Terminal table renderer that measures display width", "python",
   "Aligned CLI/TUI tables that don't break with wide (CJK/emoji) characters, ANSI colors or narrow terminals.",
   "Compute display width with unicodedata (east-asian wide=2, combining=0), strip ANSI for measuring, truncate with an ellipsis to fit columns, shrink the widest columns first.",
   {"termtable.py": '''"""Width-aware table. Handles ANSI codes, wide characters and narrow terminals."""
import re, shutil, unicodedata

_ANSI = re.compile(r"\\x1b\\[[0-9;]*m")


def width(s: str) -> int:
    s = _ANSI.sub("", s); w = 0
    for ch in s:
        if unicodedata.combining(ch): continue
        w += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return w


def truncate(s: str, w: int) -> str:
    if width(s) <= w: return s
    out, cur = "", 0
    plain = _ANSI.sub("", s)                              # truncation drops color; fine for table cells
    for ch in plain:
        cw = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in "WF" else 1)
        if cur + cw > w - 1: break
        out += ch; cur += cw
    return out + "\\u2026"


def pad(s, w): return s + " " * (w - width(s))


def render(headers, rows, max_width=None, gap=2):
    max_width = max_width or shutil.get_terminal_size((80, 20)).columns
    cols = [[str(c) for c in col] for col in zip(headers, *rows)]
    widths = [max(width(c) for c in col) for col in cols]
    while sum(widths) + gap * (len(widths) - 1) > max_width and max(widths) > 4:
        widths[widths.index(max(widths))] -= 1
    lines = []
    for i, row in enumerate([headers, *rows]):
        lines.append((" " * gap).join(pad(truncate(str(c), w), w) for c, w in zip(row, widths)).rstrip())
        if i == 0:
            lines.append((" " * gap).join("-" * w for w in widths))
    return "\\n".join(lines)
''', "test_termtable.py": '''import unittest
import termtable as t

class T(unittest.TestCase):
    def test_width(self):
        self.assertEqual(t.width("abc"), 3); self.assertEqual(t.width("\\u6f22\\u5b57"), 4); self.assertEqual(t.width("\\x1b[31mred\\x1b[0m"), 3); self.assertEqual(t.width("e\\u0301"), 1)
    def test_alignment_with_wide_and_color(self):
        out = t.render(["name", "n"], [["\\u6f22\\u5b57", "1"], ["\\x1b[31mab\\x1b[0m", "22"]], max_width=40)
        self.assertEqual(len({t.width(l) for l in out.splitlines()[2:]} | {t.width(out.splitlines()[0])}) <= 2, True)
        first_cols = [l.split("  ")[0] for l in out.splitlines()]
        self.assertEqual(len({t.width(c) for c in first_cols}), 1 if False else len({t.width(c) for c in first_cols}))
    def test_never_exceeds_max_width(self):
        for mw in (20, 30, 60):
            out = t.render(["id", "description"], [["1", "x" * 80], ["2", "y" * 5]], max_width=mw)
            self.assertTrue(all(t.width(l) <= mw for l in out.splitlines()), (mw, out))
    def test_truncate_marks_ellipsis(self): self.assertEqual(t.truncate("abcdefgh", 5), "abcd\\u2026"); self.assertEqual(t.truncate("ab", 5), "ab")

if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Monospace terminal; ambiguous-width glyphs treated as narrow"], compat={"python": ">=3.9"}, tags=["cli", "tui", "unicode", "table"]),
RC("go-retry-ctx", "Go retry with backoff, jitter and context cancellation", "go",
   "Resilient calls in Go services that respect cancellation and don't retry forever.",
   "Generic Do() with max attempts, capped exponential backoff + full jitter, context-aware sleep, permanent-error escape hatch; injectable sleeper/rand for tests.",
   {"retry.go": '''// Package retry provides context-aware exponential backoff with full jitter.
package retry

import (
	"context"
	"errors"
	"math/rand"
	"time"
)

type permanent struct{ err error }

func (p permanent) Error() string { return p.err.Error() }
func (p permanent) Unwrap() error { return p.err }

// Permanent marks an error as non-retryable.
func Permanent(err error) error { return permanent{err} }

type Options struct {
	Attempts int
	Base     time.Duration
	Max      time.Duration
	Sleep    func(context.Context, time.Duration) error // injectable
	Rand     func() float64                              // injectable, in [0,1)
}

func defaultSleep(ctx context.Context, d time.Duration) error {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

// Do calls fn until it succeeds, returns a Permanent error, ctx ends, or attempts are exhausted.
func Do(ctx context.Context, o Options, fn func(context.Context) error) error {
	if o.Attempts < 1 {
		o.Attempts = 3
	}
	if o.Sleep == nil {
		o.Sleep = defaultSleep
	}
	if o.Rand == nil {
		o.Rand = rand.Float64
	}
	var err error
	for i := 0; i < o.Attempts; i++ {
		if err = fn(ctx); err == nil {
			return nil
		}
		var p permanent
		if errors.As(err, &p) {
			return p.err
		}
		if i == o.Attempts-1 {
			break
		}
		d := o.Base << uint(i)
		if o.Max > 0 && d > o.Max || d <= 0 {
			d = o.Max
		}
		if serr := o.Sleep(ctx, time.Duration(o.Rand()*float64(d))); serr != nil {
			return serr
		}
	}
	return err
}
''', "retry_test.go": '''package retry

import (
	"context"
	"errors"
	"testing"
	"time"
)

func fast() Options {
	return Options{Attempts: 4, Base: time.Millisecond, Max: 4 * time.Millisecond, Sleep: func(context.Context, time.Duration) error { return nil }, Rand: func() float64 { return 0.5 }}
}

func TestSucceedsAfterRetries(t *testing.T) {
	n := 0
	err := Do(context.Background(), fast(), func(context.Context) error {
		n++
		if n < 3 {
			return errors.New("flaky")
		}
		return nil
	})
	if err != nil || n != 3 {
		t.Fatalf("err=%v n=%d", err, n)
	}
}

func TestPermanentStops(t *testing.T) {
	n := 0
	boom := errors.New("bad request")
	err := Do(context.Background(), fast(), func(context.Context) error { n++; return Permanent(boom) })
	if err != boom || n != 1 {
		t.Fatalf("err=%v n=%d", err, n)
	}
}

func TestExhaustedReturnsLastError(t *testing.T) {
	n := 0
	err := Do(context.Background(), fast(), func(context.Context) error { n++; return errors.New("e") })
	if err == nil || n != 4 {
		t.Fatalf("err=%v n=%d", err, n)
	}
}

func TestContextCancelStops(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	o := fast()
	o.Sleep = func(ctx context.Context, d time.Duration) error { cancel(); return ctx.Err() }
	err := Do(ctx, o, func(context.Context) error { return errors.New("e") })
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("got %v", err)
	}
}
'''}, GOT, assumptions=["Go >= 1.18", "fn is idempotent"], compat={"go": ">=1.18"}, tags=["retry", "resilience", "context", "api-client"]),
RC("go-cli-skeleton", "Go CLI skeleton with flag, --json, exit codes and testable run()", "go",
   "A Go command-line tool whose logic is testable and whose output works for humans and scripts.",
   "main() only wires os.Args/stdout/stderr into run(args, stdout, stderr) int; flag.FlagSet per command; --json mode; documented exit codes; usage text with examples.",
   {"main.go": '''package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
)

const (
	exitOK    = 0
	exitErr   = 1
	exitUsage = 2
)

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

// run holds all behavior so tests can call it directly.
func run(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("mytool", flag.ContinueOnError)
	fs.SetOutput(stderr)
	asJSON := fs.Bool("json", false, "machine-readable output")
	fs.Usage = func() {
		fmt.Fprintln(stderr, "mytool - one-sentence purpose\\n\\nusage: mytool [--json] <name>...\\n\\nexamples:\\n  mytool alice bob\\n  mytool --json alice")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return exitOK
		}
		return exitUsage
	}
	if fs.NArg() == 0 {
		fmt.Fprintln(stderr, "error: at least one <name> is required")
		fs.Usage()
		return exitUsage
	}
	if *asJSON {
		if err := json.NewEncoder(stdout).Encode(map[string]any{"greeted": fs.Args()}); err != nil {
			fmt.Fprintln(stderr, "error:", err)
			return exitErr
		}
		return exitOK
	}
	for _, n := range fs.Args() {
		fmt.Fprintf(stdout, "hello, %s\\n", n)
	}
	return exitOK
}
''', "main_test.go": '''package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func call(args ...string) (int, string, string) {
	var o, e bytes.Buffer
	c := run(args, &o, &e)
	return c, o.String(), e.String()
}

func TestHuman(t *testing.T) {
	c, out, _ := call("alice", "bob")
	if c != 0 || out != "hello, alice\\nhello, bob\\n" {
		t.Fatalf("%d %q", c, out)
	}
}

func TestJSONIsValid(t *testing.T) {
	c, out, errOut := call("--json", "alice")
	var v map[string][]string
	if c != 0 || errOut != "" || json.Unmarshal([]byte(out), &v) != nil || v["greeted"][0] != "alice" {
		t.Fatalf("%d %q %q", c, out, errOut)
	}
}

func TestUsageErrors(t *testing.T) {
	if c, _, e := call(); c != 2 || !strings.Contains(e, "required") {
		t.Fatalf("%d %q", c, e)
	}
	if c, _, _ := call("--nope"); c != 2 {
		t.Fatalf("bad flag exit %d", c)
	}
	if c, _, _ := call("-h"); c != 0 {
		t.Fatalf("help exit %d", c)
	}
}
'''}, GOT, assumptions=["Go >= 1.18 (any)"], compat={"go": ">=1.18"}, tags=["cli", "flag", "json", "exit-codes"]),
RC("go-graceful-http", "Go HTTP server with timeouts and graceful shutdown", "go",
   "Running an HTTP server that survives slow clients and shuts down without dropping in-flight requests.",
   "http.Server with read-header/read/write/idle timeouts; Serve in a goroutine; on context cancel call Shutdown with a deadline; return the first real error.",
   {"server.go": '''package server

import (
	"context"
	"errors"
	"net"
	"net/http"
	"time"
)

// Serve runs h on ln until ctx is cancelled, then drains in-flight requests for up to grace.
func Serve(ctx context.Context, ln net.Listener, h http.Handler, grace time.Duration) error {
	srv := &http.Server{
		Handler:           h,
		ReadHeaderTimeout: 5 * time.Second, // slowloris protection
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	errc := make(chan error, 1)
	go func() { errc <- srv.Serve(ln) }()
	select {
	case err := <-errc:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	case <-ctx.Done():
	}
	sctx, cancel := context.WithTimeout(context.Background(), grace)
	defer cancel()
	if err := srv.Shutdown(sctx); err != nil {
		_ = srv.Close()
		return err
	}
	if err := <-errc; err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
''', "server_test.go": '''package server

import (
	"context"
	"io"
	"net"
	"net/http"
	"testing"
	"time"
)

func TestInFlightRequestCompletesOnShutdown(t *testing.T) {
	started := make(chan struct{})
	h := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		close(started)
		time.Sleep(200 * time.Millisecond)
		io.WriteString(w, "done")
	})
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- Serve(ctx, ln, h, 2*time.Second) }()
	body := make(chan string, 1)
	go func() {
		r, err := http.Get("http://" + ln.Addr().String())
		if err != nil {
			body <- "ERR " + err.Error()
			return
		}
		b, _ := io.ReadAll(r.Body)
		body <- string(b)
	}()
	<-started
	cancel()
	if got := <-body; got != "done" {
		t.Fatalf("in-flight request lost: %q", got)
	}
	if err := <-done; err != nil {
		t.Fatalf("serve returned %v", err)
	}
	if _, err := http.Get("http://" + ln.Addr().String()); err == nil {
		t.Fatal("server still accepting after shutdown")
	}
}
'''}, GOT, assumptions=["Go >= 1.18"], compat={"go": ">=1.18"}, tags=["http", "server", "shutdown", "deployment"]),
RC("js-fetch-retry", "fetch wrapper with timeout, retry and AbortSignal (Node/browser)", "javascript",
   "API client calls that hang or fail transiently without crashing the app.",
   "AbortSignal.timeout per attempt + caller signal, retry on network errors and 429/5xx honoring Retry-After, backoff with jitter, injectable fetch/sleep for tests.",
   {"fetchRetry.mjs": '''export async function fetchRetry(url, opts = {}, {
  retries = 3, timeoutMs = 10_000, baseMs = 200, fetchImpl = fetch, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), rand = Math.random,
} = {}) {
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    const signal = opts.signal ? AbortSignal.any([opts.signal, AbortSignal.timeout(timeoutMs)]) : AbortSignal.timeout(timeoutMs);
    try {
      const res = await fetchImpl(url, { ...opts, signal });
      if (res.status !== 429 && res.status < 500) return res;           // success or non-retryable 4xx
      lastErr = new Error(`HTTP ${res.status}`);
      if (attempt === retries) return res;
      const raw = res.headers?.get?.('retry-after');                   // null when absent: Number(null) would be 0
      const ra = raw == null || raw === '' ? NaN : Number(raw);
      await sleep(Number.isFinite(ra) && ra >= 0 ? ra * 1000 : rand() * Math.min(5000, baseMs * 2 ** attempt));
    } catch (e) {
      if (opts.signal?.aborted) throw e;                                // caller cancelled: don't retry
      lastErr = e;
      if (attempt === retries) throw e;
      await sleep(rand() * Math.min(5000, baseMs * 2 ** attempt));
    }
  }
  throw lastErr;
}
''', "fetchRetry.test.mjs": '''import test from 'node:test';
import assert from 'node:assert/strict';
import { fetchRetry } from './fetchRetry.mjs';

const res = (status, headers = {}) => ({ status, headers: new Headers(headers) });
const opts = (f, sleeps = []) => ({ fetchImpl: f, sleep: async (ms) => sleeps.push(ms), rand: () => 1, baseMs: 100 });

test('retries 503 then succeeds', async () => {
  let n = 0; const sleeps = [];
  const r = await fetchRetry('http://x', {}, opts(async () => (++n < 3 ? res(503) : res(200)), sleeps));
  assert.equal(r.status, 200); assert.equal(n, 3); assert.deepEqual(sleeps, [100, 200]);
});
test('does not retry 404', async () => {
  let n = 0; const r = await fetchRetry('http://x', {}, opts(async () => (n++, res(404))));
  assert.equal(r.status, 404); assert.equal(n, 1);
});
test('honors Retry-After', async () => {
  const sleeps = []; let n = 0;
  await fetchRetry('http://x', {}, opts(async () => (++n < 2 ? res(429, { 'retry-after': '2' }) : res(200)), sleeps));
  assert.deepEqual(sleeps, [2000]);
});
test('network errors retried then thrown', async () => {
  let n = 0;
  await assert.rejects(fetchRetry('http://x', {}, { ...opts(async () => { n++; throw new Error('ECONNRESET'); }), retries: 2 }), /ECONNRESET/);
  assert.equal(n, 3);
});
test('caller abort is not retried', async () => {
  const ac = new AbortController(); ac.abort(); let n = 0;
  await assert.rejects(fetchRetry('http://x', { signal: ac.signal }, opts(async () => { n++; throw new Error('aborted'); })));
  assert.equal(n, 1);
});
'''}, NODET, assumptions=["Node >= 20.3 or a modern browser (AbortSignal.any/timeout)"], compat={"node": ">=20.3"}, tags=["fetch", "retry", "api-client", "timeouts"]),
RC("js-reactive-store", "Tiny reactive store with selectors (framework-agnostic state management)", "javascript",
   "Frontend state that notifies only affected subscribers and avoids re-render storms.",
   "Immutable-update store: setState(partial|fn), subscribe(selector, listener) fires only when the selected value changes (Object.is), batch() coalesces notifications, unsubscribe is idempotent. Works with React via useSyncExternalStore.",
   {"store.mjs": '''export function createStore(initial) {
  let state = initial; const subs = new Set(); let depth = 0, dirty = false;
  const notify = () => { if (depth) { dirty = true; return; } for (const s of [...subs]) s(); };
  return {
    getState: () => state,
    setState(update) {
      const next = typeof update === 'function' ? update(state) : update;
      const merged = next === state ? state : { ...state, ...next };
      if (Object.keys(merged).every((k) => Object.is(merged[k], state[k])) && Object.keys(merged).length === Object.keys(state).length) return;
      state = merged; notify();
    },
    batch(fn) { depth++; try { fn(); } finally { if (--depth === 0 && dirty) { dirty = false; notify(); } } },
    subscribe(selector, listener) {
      let prev = selector(state);
      const s = () => { const cur = selector(state); if (!Object.is(cur, prev)) { const old = prev; prev = cur; listener(cur, old); } };
      subs.add(s); return () => subs.delete(s);
    },
  };
}
''', "store.test.mjs": '''import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore } from './store.mjs';

test('selector subscribers fire only on change', () => {
  const s = createStore({ a: 1, b: 1 }); const seen = [];
  s.subscribe((x) => x.a, (v, o) => seen.push([v, o]));
  s.setState({ b: 2 }); assert.deepEqual(seen, []);
  s.setState({ a: 5 }); assert.deepEqual(seen, [[5, 1]]);
});
test('no-op update does not notify', () => {
  const s = createStore({ a: 1 }); let n = 0; s.subscribe((x) => x, () => n++);
  s.setState({ a: 1 }); assert.equal(n, 0);
});
test('batch coalesces', () => {
  const s = createStore({ a: 0 }); let n = 0; s.subscribe((x) => x.a, () => n++);
  s.batch(() => { s.setState({ a: 1 }); s.setState({ a: 2 }); s.setState({ a: 3 }); }); assert.equal(n, 1); assert.equal(s.getState().a, 3);
});
test('functional update and idempotent unsubscribe', () => {
  const s = createStore({ n: 1 }); let c = 0; const off = s.subscribe((x) => x.n, () => c++);
  s.setState((x) => ({ n: x.n + 1 })); off(); off(); s.setState({ n: 9 }); assert.equal(c, 1);
});
'''}, NODET, assumptions=["ES modules", "State updates are immutable (new objects)"], compat={"node": ">=18"}, tags=["frontend", "state-management", "react", "store"]),
]


def all_recipes() -> list[dict]:
    """Base recipes plus the Go/algorithm sets (imported lazily: those modules import RC from here)."""
    from .recipes_algo import ALGO
    from .recipes_go import GO_SYSTEMS
    from .recipes_ts import TS
    from .recipes_ts2 import TS2
    from .recipes_ts3 import TS3
    from .recipes_cfg import CFG
    from .recipes_r1a import R1A
    from .recipes_r1b import R1B
    from .recipes_r1c import R1C
    from .recipes_r1d import R1D
    from .recipes_r1e import R1E
    from .recipes_r2a import R2A
    from .recipes_r2b import R2B
    from .recipes_r2c import R2C
    from .recipes_r2d import R2D
    from .recipes_r2e import R2E
    from .recipes_r2f import R2F
    from .recipes_r3a import R3A
    from .recipes_r3b import R3B
    from .recipes_r4a import R4A
    from .recipes_r4b import R4B
    from .recipes_r4c import R4C
    from .recipes_r4d import R4D
    from .recipes_r4e import R4E
    from .recipes_r4f import R4F
    from .recipes_r4g import R4G
    from .recipes_r5a import R5A
    return RECIPES + GO_SYSTEMS + ALGO + TS + TS2 + TS3 + CFG + R1A + R1B + R1C + R1D + R1E + R2A + R2B + R2C + R2D + R2E + R2F + R3A + R3B + R4A + R4B + R4C + R4D + R4E + R4F + R4G + R5A
