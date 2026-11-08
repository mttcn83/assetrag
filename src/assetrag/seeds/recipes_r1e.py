"""Round-1 persona recipes, part E: Rust (std only, compiled and tested with `rustc --test` inside the sandbox; the toolchain is
mounted read-only and no crates are downloaded). Where a mature crate exists (clap, thiserror, anyhow, lru) the recipe says so:
these are the patterns, small enough to read, not replacements for those crates."""
from .recipes import RC

RS = ["sh", "-c", "rustc --edition 2021 --test lib.rs -o /tmp/t 2>&1 && /tmp/t"]
RSA = ["Rust 2021, std only (no crates): in a real project prefer the crate named in the recipe", "compiled with plain rustc in the sandbox; clippy and cargo features are not exercised"]

R1E = [
RC("rs-error-handling-cli", "Rust application errors: typed enum, From conversions, `?`, source chain and exit codes", "rust",
   "A Rust CLI prints `Error: Os { code: 2 }` or panics with unwrap(); users need a clear message and scripts need distinct exit codes.",
   "One error enum implementing Display + std::error::Error with `source()`, From impls so `?` converts io/parse errors, context added at the call site, a `report()` that prints the chain (`caused by:`), and a stable mapping from variant to process exit code. This is what thiserror/anyhow generate for you.",
   {"lib.rs": '''use std::error::Error as StdError;
use std::fmt;
use std::fs;
use std::io;
use std::num::ParseIntError;
use std::path::{Path, PathBuf};

#[derive(Debug)]
pub enum AppError {
    Io { path: PathBuf, source: io::Error },
    Parse { line: usize, source: ParseIntError },
    Usage(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::Io { path, .. } => write!(f, "cannot read {}", path.display()),
            AppError::Parse { line, .. } => write!(f, "line {line} is not a number"),
            AppError::Usage(m) => write!(f, "usage error: {m}"),
        }
    }
}

impl StdError for AppError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        match self {
            AppError::Io { source, .. } => Some(source),
            AppError::Parse { source, .. } => Some(source),
            AppError::Usage(_) => None,
        }
    }
}

impl AppError {
    /// Stable exit codes scripts can rely on (2 is conventionally "usage").
    pub fn exit_code(&self) -> i32 {
        match self {
            AppError::Usage(_) => 2,
            AppError::Io { .. } => 3,
            AppError::Parse { .. } => 4,
        }
    }
}

/// Attach the path to an io::Error without losing the original.
pub trait IoContext<T> {
    fn with_path(self, p: &Path) -> Result<T, AppError>;
}
impl<T> IoContext<T> for Result<T, io::Error> {
    fn with_path(self, p: &Path) -> Result<T, AppError> {
        self.map_err(|source| AppError::Io { path: p.to_path_buf(), source })
    }
}

pub fn sum_file(path: &Path) -> Result<i64, AppError> {
    let text = fs::read_to_string(path).with_path(path)?;
    let mut total = 0;
    for (i, line) in text.lines().enumerate().filter(|(_, l)| !l.trim().is_empty()) {
        total += line.trim().parse::<i64>().map_err(|source| AppError::Parse { line: i + 1, source })?;
    }
    Ok(total)
}

/// "Error: ...\\n  caused by: ..." - the whole chain, not just the top message.
pub fn report(e: &dyn StdError) -> String {
    let mut out = format!("Error: {e}");
    let mut cur = e.source();
    while let Some(c) = cur {
        out.push_str(&format!("\\n  caused by: {c}"));
        cur = c.source();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn tmp(name: &str, body: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("rs_err_{}_{}", std::process::id(), name));
        fs::File::create(&p).unwrap().write_all(body.as_bytes()).unwrap();
        p
    }

    #[test]
    fn happy_path_skips_blank_lines() {
        assert_eq!(sum_file(&tmp("ok", "1\\n\\n 2 \\n3\\n")).unwrap(), 6);
    }

    #[test]
    fn missing_file_names_the_path_and_keeps_the_cause() {
        let e = sum_file(Path::new("/definitely/not/here.txt")).unwrap_err();
        assert!(e.to_string().contains("/definitely/not/here.txt"));
        assert_eq!(e.exit_code(), 3);
        let r = report(&e);
        assert!(r.starts_with("Error: cannot read"), "{r}");
        assert!(r.contains("caused by:"), "{r}");
    }

    #[test]
    fn parse_error_reports_the_line_number() {
        let e = sum_file(&tmp("bad", "1\\n2\\nx\\n")).unwrap_err();
        assert_eq!(e.to_string(), "line 3 is not a number");
        assert_eq!(e.exit_code(), 4);
        assert!(matches!(e, AppError::Parse { line: 3, .. }));
    }

    #[test]
    fn exit_codes_are_distinct_and_stable() {
        let usage = AppError::Usage("missing file".into());
        assert_eq!((usage.exit_code(), report(&usage)), (2, "Error: usage error: missing file".to_string()));
    }

    #[test]
    fn question_mark_converts_via_map_err_and_is_boxable() {
        fn run() -> Result<(), Box<dyn StdError>> {
            sum_file(Path::new("/nope"))?;
            Ok(())
        }
        assert!(run().unwrap_err().source().is_some());
    }
}
'''}, RS, assumptions=RSA, compat={"rust": ">=1.70"},
   tags=["rust", "error-handling", "cli", "result", "thiserror", "anyhow", "exit-code", "question-mark"],
   avoid="unwrap()/expect() on user input; `Box<dyn Error>` in library APIs; printing only the top-level error and losing the cause."),

RC("rs-cli-args-flags", "Hand-rolled CLI argument parser: flags, values, `--`, `--help` text, typed errors (Rust)", "rust",
   "A tiny Rust tool wants `-v`, `--count 3`, `--count=3` and positional files without pulling clap, with errors that say what to fix.",
   "Iterator over args into a typed Options struct, supports short bundles (`-vq`), `--key value` and `--key=value`, `--` ends options, unknown flags suggest the closest known flag, help text generated from one table so it cannot drift. For anything bigger use clap's derive API.",
   {"lib.rs": '''use std::fmt;

#[derive(Debug, PartialEq, Default)]
pub struct Options {
    pub verbose: u8,
    pub quiet: bool,
    pub count: usize,
    pub files: Vec<String>,
    pub help: bool,
}

#[derive(Debug, PartialEq)]
pub enum ArgError {
    Unknown { flag: String, suggestion: Option<String> },
    MissingValue(String),
    BadValue { flag: String, value: String },
}

impl fmt::Display for ArgError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ArgError::Unknown { flag, suggestion: Some(s) } => write!(f, "unknown option {flag} (did you mean {s}?)"),
            ArgError::Unknown { flag, .. } => write!(f, "unknown option {flag}"),
            ArgError::MissingValue(k) => write!(f, "option {k} needs a value"),
            ArgError::BadValue { flag, value } => write!(f, "option {flag}: {value:?} is not a valid number"),
        }
    }
}

/// One table drives parsing hints AND the help text.
const LONG: &[(&str, &str)] = &[("--verbose", "more output (repeatable: -vv)"), ("--quiet", "no output"), ("--count", "how many (default 1)"), ("--help", "show this help")];

pub fn help(prog: &str) -> String {
    let mut s = format!("Usage: {prog} [OPTIONS] [--] [FILES...]\\n\\nOptions:\\n");
    for (flag, desc) in LONG {
        s.push_str(&format!("  {flag:<10} {desc}\\n"));
    }
    s
}

fn edit_distance(a: &str, b: &str) -> usize {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let mut prev: Vec<usize> = (0..=b.len()).collect();
    for i in 1..=a.len() {
        let mut cur = vec![i];
        for j in 1..=b.len() {
            cur.push((prev[j] + 1).min(cur[j - 1] + 1).min(prev[j - 1] + usize::from(a[i - 1] != b[j - 1])));
        }
        prev = cur;
    }
    prev[b.len()]
}

fn suggest(flag: &str) -> Option<String> {
    LONG.iter().map(|(f, _)| (edit_distance(flag, f), *f)).min().filter(|(d, _)| *d <= 3).map(|(_, f)| f.to_string())
}

pub fn parse<I: IntoIterator<Item = String>>(args: I) -> Result<Options, ArgError> {
    let mut o = Options { count: 1, ..Default::default() };
    let mut it = args.into_iter();
    let mut only_files = false;
    while let Some(a) = it.next() {
        if only_files || a == "-" || !a.starts_with('-') {
            o.files.push(a);
        } else if a == "--" {
            only_files = true;
        } else if let Some(long) = a.strip_prefix("--") {
            let (key, inline) = match long.split_once('=') {
                Some((k, v)) => (format!("--{k}"), Some(v.to_string())),
                None => (a.clone(), None),
            };
            match key.as_str() {
                "--verbose" => o.verbose = o.verbose.saturating_add(1),
                "--quiet" => o.quiet = true,
                "--help" => o.help = true,
                "--count" => {
                    let v = inline.or_else(|| it.next()).ok_or_else(|| ArgError::MissingValue(key.clone()))?;
                    o.count = v.parse().map_err(|_| ArgError::BadValue { flag: key.clone(), value: v })?;
                }
                _ => return Err(ArgError::Unknown { suggestion: suggest(&key), flag: key }),
            }
        } else {
            for c in a[1..].chars() {
                match c {
                    'v' => o.verbose = o.verbose.saturating_add(1),
                    'q' => o.quiet = true,
                    'h' => o.help = true,
                    other => return Err(ArgError::Unknown { flag: format!("-{other}"), suggestion: None }),
                }
            }
        }
    }
    Ok(o)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn p(s: &str) -> Result<Options, ArgError> {
        parse(s.split_whitespace().map(String::from))
    }

    #[test]
    fn defaults_and_positionals() {
        let o = p("a.txt b.txt").unwrap();
        assert_eq!((o.count, o.verbose, o.files.len()), (1, 0, 2));
    }

    #[test]
    fn short_bundles_repeat_and_long_forms() {
        assert_eq!(p("-vv").unwrap().verbose, 2);
        let o = p("-vq --count 5").unwrap();
        assert_eq!((o.verbose, o.quiet, o.count), (1, true, 5));
        assert_eq!(p("--count=7").unwrap().count, 7);
    }

    #[test]
    fn double_dash_ends_options() {
        let o = p("-- -v --count").unwrap();
        assert_eq!(o.files, vec!["-v", "--count"]);
        assert_eq!(o.verbose, 0);
    }

    #[test]
    fn stdin_dash_is_a_file() {
        assert_eq!(p("-").unwrap().files, vec!["-"]);
    }

    #[test]
    fn errors_are_specific() {
        assert_eq!(p("--count").unwrap_err(), ArgError::MissingValue("--count".into()));
        assert!(matches!(p("--count abc").unwrap_err(), ArgError::BadValue { .. }));
        let e = p("--verbos").unwrap_err();
        assert_eq!(e.to_string(), "unknown option --verbos (did you mean --verbose?)");
        assert!(matches!(p("-x").unwrap_err(), ArgError::Unknown { .. }));
    }

    #[test]
    fn help_lists_every_option_from_the_same_table() {
        let h = help("tool");
        for (flag, _) in LONG {
            assert!(h.contains(flag));
        }
        assert!(h.starts_with("Usage: tool"));
    }
}
'''}, RS, assumptions=RSA, compat={"rust": ">=1.70"},
   tags=["rust", "cli", "arguments", "flags", "parser", "clap", "help", "getopts"],
   avoid="Indexing std::env::args() by position; help text maintained separately from the parser."),

RC("rs-lru-cache", "LRU cache with O(1) get/put using a slab-backed doubly linked list (Rust, no unsafe)", "rust",
   "An in-memory cache must evict the least recently used entry without O(n) scans and without unsafe pointer tricks.",
   "HashMap from key to slab index plus a doubly linked list stored in a Vec with index links and a free list; get moves to front, put updates or inserts and evicts the tail at capacity, capacity 0 handled. The `lru` crate does this with unsafe for speed; this safe version is fine below ~1M entries.",
   {"lib.rs": '''use std::collections::HashMap;
use std::hash::Hash;

const NIL: usize = usize::MAX;

struct Node<K, V> {
    key: K,
    val: V,
    prev: usize,
    next: usize,
}

pub struct Lru<K, V> {
    cap: usize,
    map: HashMap<K, usize>,
    nodes: Vec<Option<Node<K, V>>>,
    free: Vec<usize>,
    head: usize, // most recent
    tail: usize, // least recent
}

impl<K: Hash + Eq + Clone, V> Lru<K, V> {
    pub fn new(cap: usize) -> Self {
        Lru { cap, map: HashMap::new(), nodes: Vec::new(), free: Vec::new(), head: NIL, tail: NIL }
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    fn unlink(&mut self, i: usize) {
        let (p, n) = {
            let node = self.nodes[i].as_ref().unwrap();
            (node.prev, node.next)
        };
        if p != NIL { self.nodes[p].as_mut().unwrap().next = n } else { self.head = n }
        if n != NIL { self.nodes[n].as_mut().unwrap().prev = p } else { self.tail = p }
    }

    fn push_front(&mut self, i: usize) {
        {
            let node = self.nodes[i].as_mut().unwrap();
            node.prev = NIL;
            node.next = self.head;
        }
        if self.head != NIL { self.nodes[self.head].as_mut().unwrap().prev = i }
        self.head = i;
        if self.tail == NIL { self.tail = i }
    }

    pub fn get(&mut self, k: &K) -> Option<&V> {
        let i = *self.map.get(k)?;
        self.unlink(i);
        self.push_front(i);
        self.nodes[i].as_ref().map(|n| &n.val)
    }

    pub fn peek(&self, k: &K) -> Option<&V> {
        self.map.get(k).and_then(|&i| self.nodes[i].as_ref()).map(|n| &n.val)
    }

    /// Insert or update; returns the evicted (key, value) when the cache was full.
    pub fn put(&mut self, k: K, v: V) -> Option<(K, V)> {
        if self.cap == 0 {
            return Some((k, v));
        }
        if let Some(&i) = self.map.get(&k) {
            self.nodes[i].as_mut().unwrap().val = v;
            self.unlink(i);
            self.push_front(i);
            return None;
        }
        let mut evicted = None;
        if self.map.len() == self.cap {
            let t = self.tail;
            self.unlink(t);
            let old = self.nodes[t].take().unwrap();
            self.map.remove(&old.key);
            self.free.push(t);
            evicted = Some((old.key, old.val));
        }
        let node = Node { key: k.clone(), val: v, prev: NIL, next: NIL };
        let i = match self.free.pop() {
            Some(i) => { self.nodes[i] = Some(node); i }
            None => { self.nodes.push(Some(node)); self.nodes.len() - 1 }
        };
        self.map.insert(k, i);
        self.push_front(i);
        evicted
    }

    pub fn keys_recent_first(&self) -> Vec<&K> {
        let mut out = Vec::new();
        let mut i = self.head;
        while i != NIL {
            let n = self.nodes[i].as_ref().unwrap();
            out.push(&n.key);
            i = n.next;
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evicts_least_recently_used() {
        let mut c = Lru::new(2);
        c.put("a", 1);
        c.put("b", 2);
        c.get(&"a");
        assert_eq!(c.put("c", 3), Some(("b", 2)));
        assert!(c.peek(&"b").is_none());
        assert_eq!(c.keys_recent_first(), vec![&"c", &"a"]);
    }

    #[test]
    fn update_refreshes_recency_without_growing() {
        let mut c = Lru::new(2);
        c.put(1, "x");
        c.put(2, "y");
        assert_eq!(c.put(1, "z"), None);
        assert_eq!(c.len(), 2);
        assert_eq!(c.put(3, "w"), Some((2, "y")));
        assert_eq!(c.peek(&1), Some(&"z"));
    }

    #[test]
    fn capacity_zero_stores_nothing_and_capacity_one_works() {
        let mut z = Lru::new(0);
        assert_eq!(z.put(1, 1), Some((1, 1)));
        assert_eq!(z.len(), 0);
        let mut o = Lru::new(1);
        o.put(1, 'a');
        assert_eq!(o.put(2, 'b'), Some((1, 'a')));
        assert_eq!(o.get(&2), Some(&'b'));
    }

    #[test]
    fn slab_slots_are_reused() {
        let mut c = Lru::new(3);
        for i in 0..1000 {
            c.put(i, i);
        }
        assert_eq!(c.len(), 3);
        assert!(c.nodes.len() <= 4, "slab grew to {}", c.nodes.len());
        assert_eq!(c.keys_recent_first(), vec![&999, &998, &997]);
    }

    #[test]
    fn list_stays_consistent_under_random_use() {
        let mut c = Lru::new(5);
        let mut x: u64 = 12345;
        for _ in 0..5000 {
            x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            let k = (x >> 33) % 12;
            if x & 1 == 0 { c.put(k, k) } else { c.get(&k).map(|_| (k, k)) };
            assert!(c.len() <= 5);
            assert_eq!(c.keys_recent_first().len(), c.len());
        }
    }
}
'''}, RS, assumptions=RSA, compat={"rust": ">=1.65"},
   tags=["rust", "cache", "lru", "data-structure", "algorithm", "hashmap", "linked-list", "performance"],
   avoid="Vec::remove(0)/retain on every access (O(n)); Rc<RefCell<..>> linked lists; unbounded caches."),

RC("rs-token-bucket", "Token-bucket rate limiter with an injectable clock (Rust)", "rust",
   "A Rust service or client must limit request rate with bursts allowed, deterministic tests and no sleeping in unit tests.",
   "Tokens as f64 refilled lazily from elapsed time (no background thread), burst capacity, `try_acquire(n)` that never blocks and returns the wait time when refused, a `Clock` trait so tests control time, and saturation so long idle periods cannot overflow the bucket. The `governor` crate is the production choice for keyed limiters.",
   {"lib.rs": '''use std::time::Duration;

pub trait Clock {
    fn now(&self) -> Duration;
}

pub struct TokenBucket<C: Clock> {
    clock: C,
    rate: f64,     // tokens per second
    burst: f64,    // capacity
    tokens: f64,
    last: Duration,
}

#[derive(Debug, PartialEq)]
pub enum Decision {
    Allowed,
    Wait(Duration),
    TooLarge, // request can never succeed (n > burst)
}

impl<C: Clock> TokenBucket<C> {
    pub fn new(clock: C, rate: f64, burst: u32) -> Self {
        assert!(rate > 0.0 && burst > 0, "rate and burst must be positive");
        let last = clock.now();
        TokenBucket { clock, rate, burst: burst as f64, tokens: burst as f64, last }
    }

    fn refill(&mut self) {
        let now = self.clock.now();
        let dt = now.saturating_sub(self.last).as_secs_f64();      // a clock that steps backwards adds nothing
        self.tokens = (self.tokens + dt * self.rate).min(self.burst);
        self.last = now.max(self.last);
    }

    pub fn try_acquire(&mut self, n: u32) -> Decision {
        let need = n as f64;
        if need > self.burst {
            return Decision::TooLarge;
        }
        self.refill();
        if self.tokens >= need {
            self.tokens -= need;
            Decision::Allowed
        } else {
            Decision::Wait(Duration::from_secs_f64((need - self.tokens) / self.rate))
        }
    }

    pub fn available(&mut self) -> f64 {
        self.refill();
        self.tokens
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;
    use std::rc::Rc;

    #[derive(Clone)]
    struct Fake(Rc<Cell<Duration>>);
    impl Clock for Fake {
        fn now(&self) -> Duration {
            self.0.get()
        }
    }
    fn fake() -> Fake {
        Fake(Rc::new(Cell::new(Duration::from_secs(100))))
    }
    fn advance(c: &Fake, ms: u64) {
        c.0.set(c.0.get() + Duration::from_millis(ms));
    }

    #[test]
    fn burst_then_refused_then_refilled() {
        let c = fake();
        let mut b = TokenBucket::new(c.clone(), 2.0, 3);
        for _ in 0..3 {
            assert_eq!(b.try_acquire(1), Decision::Allowed);
        }
        assert_eq!(b.try_acquire(1), Decision::Wait(Duration::from_millis(500)));
        advance(&c, 500);
        assert_eq!(b.try_acquire(1), Decision::Allowed);
    }

    #[test]
    fn idle_time_cannot_exceed_burst() {
        let c = fake();
        let mut b = TokenBucket::new(c.clone(), 10.0, 5);
        advance(&c, 3_600_000);
        assert_eq!(b.available(), 5.0);
    }

    #[test]
    fn oversized_requests_are_distinguished() {
        let mut b = TokenBucket::new(fake(), 1.0, 2);
        assert_eq!(b.try_acquire(3), Decision::TooLarge);
        assert_eq!(b.try_acquire(2), Decision::Allowed);
    }

    #[test]
    fn wait_time_is_accurate_for_multi_token_requests() {
        let c = fake();
        let mut b = TokenBucket::new(c.clone(), 4.0, 4);
        assert_eq!(b.try_acquire(4), Decision::Allowed);
        assert_eq!(b.try_acquire(2), Decision::Wait(Duration::from_millis(500)));
        advance(&c, 500);
        assert_eq!(b.try_acquire(2), Decision::Allowed);
    }

    #[test]
    fn clock_going_backwards_is_harmless() {
        let c = fake();
        let mut b = TokenBucket::new(c.clone(), 1.0, 1);
        assert_eq!(b.try_acquire(1), Decision::Allowed);
        c.0.set(Duration::from_secs(50));
        assert!(matches!(b.try_acquire(1), Decision::Wait(_)));
    }

    #[test]
    #[should_panic]
    fn zero_rate_is_rejected() {
        TokenBucket::new(fake(), 0.0, 1);
    }
}
'''}, RS, assumptions=RSA, compat={"rust": ">=1.70"},
   tags=["rust", "rate-limit", "token-bucket", "api", "throttle", "clock", "testing", "backend"],
   avoid="Sleeping in tests; a refill thread per limiter; using Instant directly so tests need real time."),

RC("rs-crc8-frame-parser", "Binary frame parser for byte streams: SOF, length, CRC-8, resync (Rust, no_std-friendly)", "rust",
   "Firmware or a host tool reads framed packets from UART/TCP: partial reads, garbage and corruption must not crash or desync forever.",
   "Fixed-capacity buffer (no heap in the core), `push(&[u8])` returns complete payloads through a callback, CRC-8 (poly 0x07) check, drop one byte and rescan on a bad CRC, bounded length field, and no panics on any input (fuzz-style test with pseudo-random bytes). Mirrors the Python framing recipe so both ends of a link can share a spec.",
   {"lib.rs": '''pub const SOF: u8 = 0x7E;
pub const MAX_PAYLOAD: usize = 64;
const CAP: usize = MAX_PAYLOAD + 3;

pub fn crc8(data: &[u8]) -> u8 {
    let mut c = 0u8;
    for &b in data {
        c ^= b;
        for _ in 0..8 {
            c = if c & 0x80 != 0 { (c << 1) ^ 0x07 } else { c << 1 };
        }
    }
    c
}

/// Encode into `out`; returns the number of bytes written or None if it does not fit.
pub fn encode(payload: &[u8], out: &mut [u8]) -> Option<usize> {
    if payload.len() > MAX_PAYLOAD || out.len() < payload.len() + 3 {
        return None;
    }
    out[0] = SOF;
    out[1] = payload.len() as u8;
    out[2..2 + payload.len()].copy_from_slice(payload);
    out[2 + payload.len()] = crc8(&out[1..2 + payload.len()]);
    Some(payload.len() + 3)
}

pub struct Parser {
    buf: [u8; CAP],
    len: usize,
    pub errors: u32,
}

impl Parser {
    pub const fn new() -> Self {
        Parser { buf: [0; CAP], len: 0, errors: 0 }
    }

    fn drop_front(&mut self, n: usize) {
        self.buf.copy_within(n..self.len, 0);
        self.len -= n;
    }

    /// Feed bytes; `on_frame` is called with each CRC-valid payload. Never panics, whatever the input.
    pub fn push(&mut self, data: &[u8], mut on_frame: impl FnMut(&[u8])) {
        for &b in data {
            if self.len == CAP {
                self.drop_front(1);            // cannot happen with a valid length byte, but never overflow
            }
            self.buf[self.len] = b;
            self.len += 1;
            self.scan(&mut on_frame);
        }
    }

    fn scan(&mut self, on_frame: &mut impl FnMut(&[u8])) {
        loop {
            match self.buf[..self.len].iter().position(|&x| x == SOF) {
                None => { self.len = 0; return; }
                Some(i) if i > 0 => self.drop_front(i),
                Some(_) => {}
            }
            if self.len < 2 { return; }
            let n = self.buf[1] as usize;
            if n > MAX_PAYLOAD {
                self.errors += 1;
                self.drop_front(1);            // that 0x7E was noise: resync on the next one
                continue;
            }
            if self.len < n + 3 { return; }    // wait for the rest
            if crc8(&self.buf[1..2 + n]) == self.buf[2 + n] {
                on_frame(&self.buf[2..2 + n]);
                self.drop_front(n + 3);
            } else {
                self.errors += 1;
                self.drop_front(1);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(p: &[u8]) -> Vec<u8> {
        let mut out = [0u8; CAP];
        let n = encode(p, &mut out).unwrap();
        out[..n].to_vec()
    }

    fn run(chunks: &[&[u8]]) -> (Vec<Vec<u8>>, u32) {
        let mut p = Parser::new();
        let mut got = Vec::new();
        for c in chunks {
            p.push(c, |f| got.push(f.to_vec()));
        }
        (got, p.errors)
    }

    #[test]
    fn crc_check_value() {
        assert_eq!(crc8(b"123456789"), 0xF4);
    }

    #[test]
    fn round_trip_and_byte_at_a_time() {
        let f = frame(b"hello");
        assert_eq!(run(&[&f]).0, vec![b"hello".to_vec()]);
        let singles: Vec<&[u8]> = f.chunks(1).collect();
        assert_eq!(run(&singles).0, vec![b"hello".to_vec()]);
    }

    #[test]
    fn two_frames_and_leading_garbage() {
        let mut data = vec![0x00, 0x01];
        data.extend(frame(b"a"));
        data.extend(frame(b"bc"));
        assert_eq!(run(&[&data]).0, vec![b"a".to_vec(), b"bc".to_vec()]);
    }

    #[test]
    fn corrupted_frame_dropped_next_survives() {
        let mut bad = frame(b"zzz");
        bad[3] ^= 0xFF;
        bad.extend(frame(b"ok"));
        let (got, errors) = run(&[&bad]);
        assert_eq!(got, vec![b"ok".to_vec()]);
        assert!(errors >= 1);
    }

    #[test]
    fn sof_inside_payload_and_bogus_length() {
        assert_eq!(run(&[&frame(&[0x7E, 0x7E, 0x01])]).0, vec![vec![0x7E, 0x7E, 0x01]]);
        let mut d = vec![0x7E, 0xFF, 0xFF];
        d.extend(frame(b"ok"));
        assert_eq!(run(&[&d]).0, vec![b"ok".to_vec()]);
    }

    #[test]
    fn encode_rejects_oversize_and_small_buffers() {
        let mut out = [0u8; 8];
        assert_eq!(encode(&[0; 65], &mut [0u8; 100]), None);
        assert_eq!(encode(&[1, 2, 3, 4, 5, 6], &mut out), None);
        assert_eq!(encode(&[1, 2, 3], &mut out), Some(6));
    }

    #[test]
    fn never_panics_on_pseudo_random_input() {
        let mut p = Parser::new();
        let mut x: u32 = 0xC0FFEE;
        let mut frames = 0;
        for _ in 0..200_000 {
            x = x.wrapping_mul(1664525).wrapping_add(1013904223);
            let b = if x >> 28 == 0 { SOF } else { (x >> 16) as u8 };
            p.push(&[b], |_| frames += 1);
        }
        assert!(p.len <= CAP);
    }
}
'''}, RS, assumptions=RSA + ["logic only: no real UART/timing", "Core uses fixed arrays, so it ports to no_std; the tests use std"], compat={"rust": ">=1.70"},
   tags=["rust", "embedded", "serial", "uart", "crc", "framing", "protocol", "no_std", "parser", "firmware"],
   avoid="Trusting the length byte; Vec growth per byte on a microcontroller; assuming one read equals one frame."),
]
