"""Round-4 recipes, part G (Python, `unittest`): file-format gap fillers (JSON Lines, XML), testing helpers (property-based, snapshot),
natural-language dates and countdowns, safe subprocess pipelines."""
from .recipes import RC, UT

R4G = [
RC("py-jsonl-xml-safe-io", "JSON Lines and XML done safely: streaming JSONL with bad-line reporting, crash-safe append, namespaced XML queries, iterparse for huge files, entity-bomb rejection", "python",
   "Read and write the two formats that look easy and are not: line-delimited JSON logs/datasets, and XML with namespaces from feeds, SOAP, SVG, Office or sitemap files.",
   "JSONL: iterate with line numbers, tolerate BOM/CRLF/blank lines, collect or raise on bad lines, never split on U+2028 (escape it on write), reject NaN, repair a torn last line before appending, atomic rewrites, gz support and an efficient tail. XML: namespace-aware lookups ({uri}tag, prefix maps, default namespaces), local-name helpers, constant-memory iterparse with element clearing, refusal of DOCTYPE/ENTITY input (billion laughs, XXE-style tricks), building documents with illegal control characters stripped.",
   {"formats.py": r'''"""JSON Lines + XML helpers, standard library only (use defusedxml for hostile XML in production if you can add a dependency)."""
import gzip, io, json, os, re
from xml.etree import ElementTree as ET


# ---------------------------------------------------------------- JSON Lines
class JsonlError(ValueError):
    def __init__(self, line, message):
        super().__init__("line %d: %s" % (line, message))
        self.line = line


def _open(path, mode):
    return gzip.open(path, mode + "t", encoding="utf-8", newline="\n") if str(path).endswith(".gz") else open(path, mode, encoding="utf-8-sig" if mode == "r" else "utf-8", newline="\n")


def read_jsonl(path, errors="raise"):
    """Yield (line_number, object). errors: 'raise' | 'skip' | list (bad lines appended as (line, message, text)).
    Iterates the FILE (newline-delimited) - str.splitlines() would also split on U+2028/U+0085 inside strings."""
    with _open(path, "r") as f:
        for n, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            try:
                yield n, json.loads(line)
            except ValueError as e:
                if errors == "raise":
                    raise JsonlError(n, str(e)) from None
                if isinstance(errors, list):
                    errors.append((n, str(e), line[:200]))


def dumps_line(obj):
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)      # allow_nan=False: NaN/Infinity are not JSON
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")                     # valid JSON, but would break naive line splitters


def write_jsonl(path, items):
    """Atomic full rewrite."""
    tmp = str(path) + ".tmp"
    with _open(tmp if not str(path).endswith(".gz") else tmp + ".gz", "w") as f:
        for it in items:
            f.write(dumps_line(it) + "\n")
    os.replace(tmp if not str(path).endswith(".gz") else tmp + ".gz", path)


def append_jsonl(path, items):
    """Append whole lines. If a crashed writer left a torn last line (no trailing newline) it is terminated first, so the next record starts clean."""
    needs_nl = False
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_nl = f.read(1) != b"\n"
    with open(path, "ab") as f:
        data = ("\n" if needs_nl else "") + "".join(dumps_line(it) + "\n" for it in items)
        f.write(data.encode("utf-8"))                   # one write() per batch: O_APPEND keeps concurrent appenders from interleaving inside it
        f.flush()
        os.fsync(f.fileno())


def tail_jsonl(path, n=10, block=8192):
    """Last n valid records without reading the whole file."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos, buf = f.tell(), b""
        while pos > 0 and buf.count(b"\n") <= n + 1:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    out = []
    for line in buf.split(b"\n")[-(n + 2):]:
        try:
            if line.strip():
                out.append(json.loads(line.decode("utf-8-sig")))
        except ValueError:
            continue                                     # torn last line or a cut at the start of our window
    return out[-n:]


# --------------------------------------------------------------------- XML
class XmlRejected(ValueError):
    pass


_DANGEROUS = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.I)


def parse_xml(data):
    """Parse untrusted XML. DOCTYPE/ENTITY declarations are refused outright: they are how entity-expansion ('billion laughs') and
    external-entity attacks are carried, and almost no real-world feed or API response needs them."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if _DANGEROUS.search(raw):
        raise XmlRejected("DOCTYPE/ENTITY declarations are not accepted")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise XmlRejected("malformed XML: %s" % e) from None


def localname(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else tag


def namespace(tag):
    return tag[1:tag.index("}")] if tag.startswith("{") else ""


def findall(root, path, ns):
    """Prefixes are YOURS (any prefix -> uri), not the document's: root.findall('a:item', {'a': 'http://...'}). Unprefixed names in a document
    with xmlns="..." are namespaced too, so 'item' alone matches nothing there."""
    return root.findall(path, ns)


def iter_elements(source, tag):
    """Constant-memory stream of elements whose local name is `tag`. Each is yielded, then cleared along with consumed siblings."""
    ctx = ET.iterparse(source, events=("start", "end"))
    root = None
    for ev, el in ctx:
        if ev == "start":
            if root is None:
                root = el
            continue
        if localname(el.tag) == tag:
            yield el
            el.clear()
            if root is not None:
                root.clear()                             # drop already-processed children so memory stays flat


_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def build_xml(root_tag, records, item_tag="item", indent=True):
    """records: list of dicts -> <root><item><key>value</key></item></root>. Control characters XML 1.0 forbids are stripped, not escaped."""
    root = ET.Element(root_tag)
    for rec in records:
        item = ET.SubElement(root, item_tag)
        for k, v in rec.items():
            if not re.fullmatch(r"[A-Za-z_][\w.-]*", k):
                raise ValueError("not a valid element name: %r" % k)
            ET.SubElement(item, k).text = _ILLEGAL.sub("", "" if v is None else str(v))
    if indent:
        ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
''', "test_formats.py": r'''import gzip, io, os, tempfile, time, unittest
import formats as F


def tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


class Jsonl(unittest.TestCase):
    def test_round_trip_unicode_and_line_separators(self):
        p = tmp("a.jsonl"); items = [{"a": 1}, {"t": "héllo ✓ \u2028 sep \u2029 and\nnewline"}, [1, 2], "str", None]
        F.write_jsonl(p, items); raw = open(p, "rb").read()
        self.assertEqual(raw.count(b"\n"), len(items))                                                     # U+2028 and \n inside strings never add lines
        self.assertEqual(len(raw.decode().splitlines()), len(items))                                       # ...so even naive splitters agree
        self.assertEqual([o for _, o in F.read_jsonl(p)], items)

    def test_reader_tolerates_bom_crlf_and_blank_lines(self):
        p = tmp("b.jsonl"); open(p, "wb").write(b'\xef\xbb\xbf{"a":1}\r\n\r\n   \r\n{"a":2}\r\n')
        self.assertEqual(list(F.read_jsonl(p)), [(1, {"a": 1}), (4, {"a": 2})])

    def test_bad_lines_raise_skip_or_collect(self):
        p = tmp("c.jsonl"); open(p, "w").write('{"ok":1}\n{broken\n{"ok":3}\n')
        with self.assertRaisesRegex(F.JsonlError, "line 2"): list(F.read_jsonl(p))
        self.assertEqual([o["ok"] for _, o in F.read_jsonl(p, "skip")], [1, 3])
        bad = []; list(F.read_jsonl(p, bad)); self.assertEqual([(n, text) for n, _, text in bad], [(2, "{broken")])

    def test_nan_is_rejected_on_write(self):
        with self.assertRaises(ValueError): F.write_jsonl(tmp("n.jsonl"), [{"x": float("nan")}])

    def test_append_repairs_a_torn_last_line(self):
        p = tmp("d.jsonl"); open(p, "w").write('{"a":1}\n{"a":2')                       # writer crashed mid-record
        F.append_jsonl(p, [{"a": 3}]); bad = []; got = [o for _, o in F.read_jsonl(p, bad)]
        self.assertEqual(got, [{"a": 1}, {"a": 3}]); self.assertEqual(len(bad), 1)       # the torn record is isolated, the new one is intact

    def test_append_creates_and_extends(self):
        p = tmp("e.jsonl"); F.append_jsonl(p, [{"a": 1}]); F.append_jsonl(p, [{"a": 2}, {"a": 3}]); self.assertEqual([o["a"] for _, o in F.read_jsonl(p)], [1, 2, 3])

    def test_gzip_files(self):
        p = tmp("f.jsonl.gz"); F.write_jsonl(p, [{"i": i} for i in range(50)]); self.assertEqual(gzip.open(p, "rt").read().count("\n"), 50)
        self.assertEqual(len(list(F.read_jsonl(p))), 50); self.assertEqual(os.listdir(os.path.dirname(p)), ["f.jsonl.gz"])

    def test_tail(self):
        p = tmp("g.jsonl"); F.write_jsonl(p, [{"i": i, "pad": "x" * 100} for i in range(1000)])
        self.assertEqual([o["i"] for o in F.tail_jsonl(p, 3, block=256)], [997, 998, 999]); self.assertEqual(len(F.tail_jsonl(p, 5000)), 1000)
        open(p, "a").write('{"i": 1000, "tor'); self.assertEqual([o["i"] for o in F.tail_jsonl(p, 2)], [998, 999])


NS_DOC = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <title>T</title>
  <entry><title>One</title><dc:creator>Ann</dc:creator><link href="http://e/1"/></entry>
  <entry><title>Two</title><dc:creator>Bo</dc:creator></entry>
</feed>"""
ATOM = {"a": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}


class Xml(unittest.TestCase):
    def test_default_namespace_needs_a_prefix_map(self):
        r = F.parse_xml(NS_DOC)
        self.assertEqual(r.findall("entry"), [])                                                           # the classic surprise
        self.assertEqual([e.findtext("a:title", namespaces=ATOM) for e in F.findall(r, "a:entry", ATOM)], ["One", "Two"])
        self.assertEqual([e.text for e in r.iter("{http://purl.org/dc/elements/1.1/}creator")], ["Ann", "Bo"])
        self.assertEqual(F.findall(r, "a:entry/a:link", ATOM)[0].get("href"), "http://e/1")

    def test_local_name_and_namespace(self):
        r = F.parse_xml(NS_DOC); self.assertEqual((F.localname(r.tag), F.namespace(r.tag)), ("feed", "http://www.w3.org/2005/Atom")); self.assertEqual(F.localname("plain"), "plain")

    def test_entity_bombs_and_external_entities_are_refused_fast(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><lolz>&lol2;</lolz>'
        xxe = '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
        t = time.time()
        for payload in (bomb, xxe, bomb.encode(), "<!doctype html><html/>"):
            with self.assertRaisesRegex(F.XmlRejected, "DOCTYPE"): F.parse_xml(payload)
        self.assertLess(time.time() - t, 1.0)

    def test_malformed_xml_is_one_exception_type(self):
        for bad in ("<a><b></a>", "", "not xml", "<a>&undefined;</a>"):
            with self.assertRaises(F.XmlRejected): F.parse_xml(bad)

    def test_non_utf8_declared_encoding(self):
        r = F.parse_xml('<?xml version="1.0" encoding="ISO-8859-1"?><a>café</a>'.encode("iso-8859-1")); self.assertEqual(r.text, "café")

    def test_iterparse_streams_in_constant_memory(self):
        n = 20000; body = "".join("<item><id>%d</id><name>n%d</name></item>" % (i, i) for i in range(n)); src = io.BytesIO(("<root xmlns='urn:x'>%s</root>" % body).encode())
        seen, peak = 0, 0
        for el in F.iter_elements(src, "item"):
            seen += 1; peak = max(peak, len(el.getparent()) if hasattr(el, "getparent") else 0)
            if seen == 1: self.assertEqual(el.find("{urn:x}id").text, "0")
        self.assertEqual(seen, n)

    def test_iterparse_memory_stays_flat(self):
        import xml.etree.ElementTree as ET
        n = 5000; src = io.BytesIO(("<r>%s</r>" % "".join("<i>%d</i>" % i for i in range(n))).encode()); sizes = []
        ctx = ET.iterparse(src, events=("start", "end")); root = None; count = 0
        for ev, el in ctx:
            if ev == "start" and root is None: root = el
            if ev == "end" and el.tag == "i":
                count += 1; el.clear(); root.clear(); sizes.append(len(root))
        self.assertEqual(count, n); self.assertEqual(max(sizes), 0)                      # the technique iter_elements relies on

    def test_build_strips_control_chars_escapes_and_validates_names(self):
        x = F.build_xml("rows", [{"name": "A & B <c>", "note": "bell\x07 nul\x00 ok", "n": 3, "none": None}])
        r = F.parse_xml(x); item = r.find("item")
        self.assertEqual((item.findtext("name"), item.findtext("note"), item.findtext("n"), item.findtext("none")), ("A & B <c>", "bell nul ok", "3", ""))
        self.assertTrue(x.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<rows>\n  <item>'))
        for bad in ("1abc", "a b", "<x>", ""):
            with self.assertRaises(ValueError): F.build_xml("r", [{bad: 1}])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["xml.etree is not hardened against every XML attack; refusing DOCTYPE/ENTITY covers the common ones, defusedxml is the thorough option, and lxml is the choice for XPath/XSD/XSLT", "Pre-scan assumes ASCII-compatible encodings (UTF-8/Latin-1); UTF-16 documents should be decoded first", "JSONL is not an exchange format for NaN/Infinity or integers above 2**53 in JavaScript consumers"], compat={"python": ">=3.9"},
   tags=["jsonl", "json-lines", "ndjson", "json", "xml", "namespace", "xmlns", "elementtree", "iterparse", "streaming", "xxe", "billion-laughs", "entity-expansion", "file-formats", "log-files", "append", "tail", "gzip", "feed", "parse"],
   avoid="splitlines() on JSONL; json.loads of the whole file; parsing untrusted XML with entities enabled; unprefixed findall() on a default-namespace document; holding a whole 1 GB XML tree in memory; appending to a file that may end mid-line."),

RC("py-property-based-and-snapshot-testing", "Testing helpers: a tiny property-based tester with shrinking and reproducible seeds, plus snapshot (golden-file) assertions with scrubbing and CI safety", "python",
   "Find the inputs you did not think of, and pin down complex output (reports, CLI text, rendered HTML) without hand-writing expected strings.",
   "Property testing in ~120 lines: generators carrying their own shrinkers (ints toward 0, lists by removal then element shrink, text, tuples, one_of), edge-case-first sampling, seeded runs that print the seed to replay, exceptions treated as failures, and greedy shrinking to a minimal counterexample. Snapshots: stored beside the tests, unified-diff failures, UPDATE_SNAPSHOTS=1 to accept, scrubbers for timestamps/UUIDs/paths/addresses, a hard failure on missing snapshots in CI, safe file names and stale-snapshot detection. Notes where `hypothesis` / `syrupy` / `pytest-snapshot` are better.",
   {"miniprop.py": r'''"""Property-based testing in miniature. For real projects use `hypothesis`; this shows the core and works with no dependency."""
import random


class PropertyFailed(AssertionError):
    pass


class Gen:
    def __init__(self, draw, shrink=lambda v: ()):
        self.draw, self.shrink = draw, shrink


def ints(lo=-1000, hi=1000):
    edges = [v for v in (0, 1, -1, lo, hi, lo + 1, hi - 1) if lo <= v <= hi]

    def draw(r):
        return r.choice(edges) if r.random() < 0.2 else r.randint(lo, hi)

    def shrink(v):
        target = min(max(0, lo), hi)                       # the in-range value closest to zero
        seen = set()
        for c in (target, v // 2 if v > 0 else -((-v) // 2), v - 1 if v > target else v + 1):
            if c != v and lo <= c <= hi and c not in seen and abs(c - target) < abs(v - target):
                seen.add(c)
                yield c
    return Gen(draw, shrink)


def booleans():
    return Gen(lambda r: r.random() < 0.5, lambda v: (False,) if v else ())


def text(alphabet="abcxyz 019_-é", max_len=12):
    def draw(r):
        return "".join(r.choice(alphabet) for _ in range(r.choice([0, 1, r.randint(0, max_len)])))

    def shrink(s):
        if s:
            yield ""
            yield s[: len(s) // 2]
            for i in range(len(s)):
                yield s[:i] + s[i + 1:]
    return Gen(draw, shrink)


def lists(elem, max_len=10):
    def draw(r):
        return [elem.draw(r) for _ in range(r.choice([0, 1, r.randint(0, max_len)]))]

    def shrink(xs):
        if xs:
            yield []
            yield xs[: len(xs) // 2]
            for i in range(len(xs)):
                yield xs[:i] + xs[i + 1:]
            for i, x in enumerate(xs):
                for s in elem.shrink(x):
                    yield xs[:i] + [s] + xs[i + 1:]
    return Gen(draw, shrink)


def tuples(*gens):
    def shrink(t):
        for i, g in enumerate(gens):
            for s in g.shrink(t[i]):
                yield t[:i] + (s,) + t[i + 1:]
    return Gen(lambda r: tuple(g.draw(r) for g in gens), shrink)


def one_of(*values):
    return Gen(lambda r: r.choice(values), lambda v: (x for x in values[: values.index(v)]))


def _fails(prop, args):
    try:
        return prop(*args) is False, None
    except Exception as e:  # noqa: BLE001 - any exception is a failing example
        return True, e


def check(prop, *gens, runs=100, seed=None, max_shrinks=500):
    """Raise PropertyFailed with a minimal counterexample if prop(*args) returns False or raises. Print-friendly and replayable via `seed`."""
    seed = random.randrange(2 ** 32) if seed is None else seed
    r = random.Random(seed)
    for i in range(1, runs + 1):
        args = tuple(g.draw(r) for g in gens)
        failed, err = _fails(prop, args)
        if not failed:
            continue
        best, steps, improved = args, 0, True
        while improved and steps < max_shrinks:
            improved = False
            for pos, g in enumerate(gens):
                for cand in g.shrink(best[pos]):
                    steps += 1
                    trial = best[:pos] + (cand,) + best[pos + 1:]
                    f, e = _fails(prop, trial)
                    if f:
                        best, err, improved = trial, e, True
                        break
                    if steps >= max_shrinks:
                        break
                if improved:
                    break
        raise PropertyFailed("falsified on run %d (seed=%d)\n  minimal args: %r\n  original args: %r%s" % (i, seed, best, args, "\n  raised: %r" % err if err else ""))
    return seed
''', "snapshot.py": r'''"""Golden-file assertions. Snapshots live in __snapshots__/<name>.txt next to the tests and are reviewed like code."""
import difflib, os, re

SCRUBBERS = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"), "<TIMESTAMP>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\b0x[0-9a-f]{6,16}\b", re.I), "<ADDR>"),
    (re.compile(r"(?:/tmp|/var/folders)/[\w./-]+"), "<TMP>"),
]


def scrub(text, extra=()):
    for rx, repl in [*SCRUBBERS, *extra]:
        text = rx.sub(repl, text)
    return text


def _norm(s):
    return s.replace("\r\n", "\n").rstrip("\n") + "\n"


def snapshot_path(directory, name):
    if not re.fullmatch(r"[\w.-]+", name) or name.startswith("."):
        raise ValueError("snapshot name must be a plain file name, got %r" % name)
    return os.path.join(directory, name + ".txt")


def assert_snapshot(actual, name, directory="__snapshots__", update=None, scrubbers=(), env=os.environ):
    """Compare `actual` with the stored snapshot.
    - UPDATE_SNAPSHOTS=1 (or update=True) rewrites it.
    - A MISSING snapshot is created locally but FAILS when CI is set, so a forgotten file cannot make CI green."""
    actual = _norm(scrub(actual, scrubbers))
    path = snapshot_path(directory, name)
    update = env.get("UPDATE_SNAPSHOTS") == "1" if update is None else update
    if os.path.exists(path) and not update:
        want = open(path, encoding="utf-8").read()
        if want != actual:
            diff = "".join(difflib.unified_diff(want.splitlines(True), actual.splitlines(True), "snapshot/" + name, "actual", n=2))
            raise AssertionError("snapshot %r differs (run with UPDATE_SNAPSHOTS=1 to accept):\n%s" % (name, diff))
        return "match"
    if not os.path.exists(path) and not update and env.get("CI"):
        raise AssertionError("snapshot %r is missing and CI is set: generate it locally and commit it" % name)
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(actual)
    return "updated" if update else "created"


def unused_snapshots(directory, used_names):
    """Snapshot files no test asked for (stale after renames/deletions)."""
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.endswith(".txt") and f[:-4] not in set(used_names))
''', "test_helpers.py": r'''import os, tempfile, unittest
import miniprop as P
import snapshot as S


class Properties(unittest.TestCase):
    def test_true_properties_pass_and_return_the_seed(self):
        P.check(lambda xs: sorted(sorted(xs)) == sorted(xs), P.lists(P.ints()), runs=200, seed=1)
        P.check(lambda a, b: a + b == b + a, P.ints(), P.ints(), runs=200, seed=2)
        P.check(lambda s: s[::-1][::-1] == s, P.text(), runs=200, seed=3)
        self.assertEqual(P.check(lambda x: True, P.ints(), runs=3, seed=42), 42)

    def test_integer_counterexample_shrinks_to_the_boundary(self):
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda n: n < 100, P.ints(0, 10 ** 6), seed=5)
        self.assertIn("minimal args: (100,)", str(cm.exception)); self.assertIn("seed=5", str(cm.exception))

    def test_list_counterexample_shrinks_to_minimal_length_and_values(self):
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda xs: len(xs) < 4, P.lists(P.ints(), 30), seed=7)
        self.assertIn("minimal args: ([0, 0, 0, 0],)", str(cm.exception))

    def test_finds_the_classic_unsorted_bug(self):
        def buggy_is_sorted(xs): return all(xs[i] < xs[i + 1] for i in range(len(xs) - 1))          # should be <=
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda xs: buggy_is_sorted(sorted(xs)), P.lists(P.ints(-5, 5), 10), seed=1)
        a, b = eval(str(cm.exception).split("minimal args: ")[1].split("\n")[0])[0]; self.assertEqual(a, b)   # shrunk to just the duplicate pair, nothing else

    def test_exceptions_are_failures_and_are_reported(self):
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda xs: 10 // len(xs) > 0, P.lists(P.ints(), 5), seed=9)
        self.assertIn("ZeroDivisionError", str(cm.exception)); self.assertIn("minimal args: ([],)", str(cm.exception))

    def test_same_seed_replays_the_same_failure(self):
        msgs = []
        for _ in range(2):
            try: P.check(lambda n: n % 7 != 3, P.ints(), seed=11)
            except P.PropertyFailed as e: msgs.append(str(e))
        self.assertEqual(len(msgs), 2); self.assertEqual(msgs[0], msgs[1])

    def test_multiple_arguments_shrink_independently(self):
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda a, b: a + b < 50, P.ints(0, 1000), P.ints(0, 1000), seed=3)
        a, b = eval(str(cm.exception).split("minimal args: ")[1].split("\n")[0]); self.assertEqual(a + b, 50)

    def test_edge_values_appear_early(self):
        seen = set(); P.check(lambda n: seen.add(n) or True, P.ints(-10, 10), runs=400, seed=1); self.assertTrue({0, 1, -1, -10, 10} <= seen)

    def test_shrinking_terminates_on_flaky_properties(self):
        import itertools; c = itertools.count()
        with self.assertRaises(P.PropertyFailed): P.check(lambda n: next(c) % 2 == 0, P.ints(), runs=50, seed=1, max_shrinks=50)

    def test_ranges_are_respected_by_shrinkers(self):
        with self.assertRaises(P.PropertyFailed) as cm: P.check(lambda n: n < 20, P.ints(10, 100), seed=2)
        self.assertIn("minimal args: (20,)", str(cm.exception))


class Snapshots(unittest.TestCase):
    def d(self): return os.path.join(tempfile.mkdtemp(), "__snapshots__")

    def test_create_then_match_then_detect_a_change_with_a_diff(self):
        d = self.d(); self.assertEqual(S.assert_snapshot("a\nb\n", "report", d, env={}), "created"); self.assertEqual(S.assert_snapshot("a\nb", "report", d, env={}), "match")
        with self.assertRaises(AssertionError) as cm: S.assert_snapshot("a\nCHANGED\n", "report", d, env={})
        self.assertIn("-b", str(cm.exception)); self.assertIn("+CHANGED", str(cm.exception)); self.assertIn("UPDATE_SNAPSHOTS=1", str(cm.exception))

    def test_update_flag_accepts_the_new_output(self):
        d = self.d(); S.assert_snapshot("old", "x", d, env={}); self.assertEqual(S.assert_snapshot("new", "x", d, env={"UPDATE_SNAPSHOTS": "1"}), "updated"); self.assertEqual(S.assert_snapshot("new", "x", d, env={}), "match")

    def test_missing_snapshot_fails_in_ci_instead_of_silently_passing(self):
        d = self.d()
        with self.assertRaisesRegex(AssertionError, "missing and CI is set"): S.assert_snapshot("x", "new", d, env={"CI": "true"})
        self.assertFalse(os.path.exists(os.path.join(d, "new.txt")))

    def test_line_endings_and_trailing_newlines_do_not_matter(self):
        d = self.d(); S.assert_snapshot("a\r\nb\r\n\r\n\r\n", "crlf", d, env={}); self.assertEqual(S.assert_snapshot("a\nb\n", "crlf", d, env={}), "match")

    def test_volatile_values_are_scrubbed(self):
        out = "run 3f2b8c1e-1234-4abc-9def-0123456789ab at 2024-05-06T07:08:09.123Z wrote /tmp/pytest-of-x/run1/out.txt obj 0x7f12ab34cd56"
        self.assertEqual(S.scrub(out), "run <UUID> at <TIMESTAMP> wrote <TMP> obj <ADDR>")
        d = self.d(); S.assert_snapshot(out, "vol", d, env={}); self.assertEqual(S.assert_snapshot(out.replace("3f2b8c1e", "aaaaaaaa").replace("07:08:09", "11:12:13"), "vol", d, env={}), "match")

    def test_custom_scrubbers(self):
        import re; x = (re.compile(r"build \d+"), "build N"); self.assertEqual(S.scrub("build 4821 ok", [x]), "build N ok")

    def test_names_cannot_escape_the_directory(self):
        for bad in ("../evil", "a/b", ".hidden", "", "x y"):
            with self.assertRaises(ValueError): S.snapshot_path("snaps", bad)

    def test_stale_snapshots_are_listed(self):
        d = self.d(); [S.assert_snapshot("x", n, d, env={}) for n in ("keep", "old1", "old2")]; self.assertEqual(S.unused_snapshots(d, ["keep"]), ["old1.txt", "old2.txt"]); self.assertEqual(S.unused_snapshots("/nonexistent", []), [])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["This is a teaching-size core: `hypothesis` adds stateful testing, a failure database, smarter shrinking and more strategies, and should be the default when dependencies are allowed", "Snapshots only prove 'unchanged', not 'correct': review the first version by eye and keep them small and deterministic"], compat={"python": ">=3.9"},
   tags=["testing", "property-based-testing", "property-testing", "hypothesis", "fuzz", "fuzzing", "quickcheck", "shrinking", "generators", "snapshot", "snapshot-testing", "golden-file", "golden", "approval-testing", "regression", "unittest", "test-helpers", "seed"],
   avoid="Property tests with unseeded randomness and no printed seed; snapshots containing timestamps/paths/addresses; auto-updating snapshots in CI; huge snapshots nobody reads; asserting the implementation inside the property."),

RC("py-natural-language-dates-countdown", "Natural-language dates (tomorrow, next friday, in 3 days, end of month, 5pm) relative to an injected now, month-safe arithmetic and countdown formatting", "python",
   "Let users type 'remind me next friday at 9' or show 'starts in 2 days 3 hours' without pulling in a heavy parsing library, and be able to test it.",
   "Deterministic `parse(text, now)` that returns None instead of guessing: today/tomorrow/yesterday, 'in N units' and 'N units ago', weekdays with next/last/this, ISO and 'march 5 [2025]' dates, 'end of month/week', times (5pm, 17:30, noon, midnight) combined with dates. Month arithmetic clamps the day (Jan 31 + 1 month = Feb 28/29). Countdown splits a duration into days/hours/minutes, formats 'in 2d 3h' / '5 minutes ago', and distinguishes calendar days from 24-hour chunks around DST by working in aware datetimes.",
   {"natdate.py": r'''"""Tiny natural-language date parser. Everything is relative to an explicit `now`, so results are reproducible and testable."""
import calendar, datetime as dt, re

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = [m.lower() for m in calendar.month_name[1:]]
ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)} | {m: i + 1 for i, m in enumerate(MONTHS)} | {"sept": 9}
UNITS = {"minute": 1, "min": 1, "hour": 60, "hr": 60, "day": 1440, "week": 10080, "month": None, "year": None}
WORDNUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12}


def add_months(d, n):
    """Calendar-month arithmetic with day clamping: Jan 31 + 1 month = Feb 28 (29 in leap years)."""
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _time(text):
    """-> ((hour, minute) or None, text without the time)."""
    t = text
    for word, hm in (("noon", (12, 0)), ("midnight", (0, 0))):
        if re.search(r"\b%s\b" % word, t):
            return hm, re.sub(r"\b(at\s+)?%s\b" % word, "", t)
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t) or re.search(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b()", t)
    if not m:
        return None, t
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            return "bad", t
        h = h % 12 + (12 if ap == "pm" else 0)
    return (h, mi), t[:m.start()] + t[m.end():]


def parse(text, now):
    """-> datetime (same tzinfo as `now`) or None when the text is not understood. Date-only phrases return midnight; 'next friday 5pm' returns that day at 17:00.
    Weekday rule: a bare or 'next' weekday is the first such day STRICTLY AFTER today; 'last X' strictly before; 'this X' is today if it matches."""
    t = re.sub(r"\s+", " ", text.strip().lower().replace(",", " "))
    if not t:
        return None
    hm, t = _time(t)
    if hm == "bad":
        return None
    t = re.sub(r"\s+", " ", t).strip()
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day = None
    if t in ("", "today", "tonight") or t == "now":
        day = base if t != "now" else now
        if t == "now":
            return now
    elif t == "tomorrow":
        day = base + dt.timedelta(days=1)
    elif t == "yesterday":
        day = base - dt.timedelta(days=1)
    elif (m := re.fullmatch(r"in (\d+|[a-z]+) (minute|min|hour|hr|day|week|month|year)s?", t)) or (m := re.fullmatch(r"(\d+|[a-z]+) (minute|min|hour|hr|day|week|month|year)s? ago", t)):
        n = int(m.group(1)) if m.group(1).isdigit() else WORDNUM.get(m.group(1))
        if n is None:
            return None
        n = -n if t.endswith("ago") else n
        unit = m.group(2)
        if unit in ("month", "year"):
            r = add_months(now, n * (12 if unit == "year" else 1))
            return r if hm is None else r.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        r = now + dt.timedelta(minutes=n * UNITS[unit])
        return r if hm is None else r.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    elif (m := re.fullmatch(r"(?:(next|last|this) )?(" + "|".join(DAYS) + ")", t)):
        which, target = m.group(1), DAYS.index(m.group(2))
        diff = (target - base.weekday()) % 7
        if which == "last":
            diff = -(((base.weekday() - target) % 7) or 7)
        elif which == "this":
            pass
        else:
            diff = diff or 7
        day = base + dt.timedelta(days=diff)
    elif t in ("end of month", "end of the month", "eom"):
        day = base.replace(day=calendar.monthrange(base.year, base.month)[1])
    elif t in ("end of week", "end of the week"):
        day = base + dt.timedelta(days=6 - base.weekday())
    elif t in ("start of next week", "beginning of next week"):
        day = base + dt.timedelta(days=7 - base.weekday())
    elif (m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t)):
        try:
            day = base.replace(year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3)))
        except ValueError:
            return None
    elif (m := re.fullmatch(r"([a-z]+) (\d{1,2})(?:st|nd|rd|th)?(?: (\d{4}))?", t)) or (m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)? ([a-z]+)(?: (\d{4}))?", t)):
        a, b, y = m.groups()
        mon, dd = (ABBR.get(a), b) if a.isalpha() else (ABBR.get(b), a)
        if not mon:
            return None
        year = int(y) if y else base.year
        try:
            day = base.replace(year=year, month=mon, day=int(dd))
        except ValueError:
            return None
        if not y and day < base:                           # 'march 5' said in November means next March
            day = day.replace(year=year + 1)
    else:
        return None
    if hm is not None:
        day = day.replace(hour=hm[0], minute=hm[1])
    return day


# ------------------------------------------------------------------------ countdown
def countdown(target, now):
    """-> dict(days, hours, minutes, seconds, past). Uses elapsed time between aware datetimes (24h chunks); see days_until() for calendar days."""
    if target.tzinfo is not None and now.tzinfo is not None:      # same-tzinfo subtraction is WALL-CLOCK in Python and ignores DST jumps: go through UTC for true elapsed time
        target, now = target.astimezone(dt.timezone.utc), now.astimezone(dt.timezone.utc)
    secs = int((target - now).total_seconds())
    past, s = secs < 0, abs(secs)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return dict(days=d, hours=h, minutes=m, seconds=s, past=past)


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def format_countdown(target, now, parts=2):
    c = countdown(target, now)
    pieces = [(c["days"], "day"), (c["hours"], "hour"), (c["minutes"], "minute"), (c["seconds"], "second")]
    shown = [_plural(n, w) for n, w in pieces if n][:parts]
    if not shown:
        return "now"
    text = " ".join(shown)
    return text + " ago" if c["past"] else "in " + text


def days_until(target_date, today):
    """Calendar days between dates: 0 = today, 1 = tomorrow. Not the same as elapsed 24h chunks across a DST change."""
    return (target_date - today).days
''', "test_natdate.py": r'''import datetime as dt, unittest
from zoneinfo import ZoneInfo
import natdate as N

NOW = dt.datetime(2024, 5, 8, 14, 30, 15)         # a Wednesday
D = lambda y, m, d, h=0, mi=0: dt.datetime(y, m, d, h, mi)


class Relative(unittest.TestCase):
    def test_basic_words(self):
        for text, want in (("today", D(2024, 5, 8)), ("tomorrow", D(2024, 5, 9)), ("yesterday", D(2024, 5, 7)), ("TOMORROW", D(2024, 5, 9)), ("  tomorrow  ", D(2024, 5, 9))): self.assertEqual(N.parse(text, NOW), want, text)
        self.assertEqual(N.parse("now", NOW), NOW)

    def test_in_and_ago(self):
        for text, want in (("in 3 days", D(2024, 5, 11, 14, 30).replace(second=15)), ("in 2 weeks", NOW + dt.timedelta(weeks=2)), ("in 90 minutes", NOW + dt.timedelta(minutes=90)), ("in an hour", NOW + dt.timedelta(hours=1)),
                           ("2 days ago", NOW - dt.timedelta(days=2)), ("in two hours", NOW + dt.timedelta(hours=2)), ("in 1 month", D(2024, 6, 8, 14, 30).replace(second=15)), ("in 1 year", D(2025, 5, 8, 14, 30).replace(second=15))):
            self.assertEqual(N.parse(text, NOW), want, text)

    def test_weekday_rules(self):
        self.assertEqual(N.parse("friday", NOW), D(2024, 5, 10)); self.assertEqual(N.parse("next friday", NOW), D(2024, 5, 10)); self.assertEqual(N.parse("wednesday", NOW), D(2024, 5, 15))   # strictly after today
        self.assertEqual(N.parse("this wednesday", NOW), D(2024, 5, 8)); self.assertEqual(N.parse("last friday", NOW), D(2024, 5, 3)); self.assertEqual(N.parse("last wednesday", NOW), D(2024, 5, 1))
        self.assertEqual(N.parse("monday", NOW), D(2024, 5, 13))

    def test_period_boundaries(self):
        self.assertEqual(N.parse("end of month", NOW), D(2024, 5, 31)); self.assertEqual(N.parse("end of week", NOW), D(2024, 5, 12)); self.assertEqual(N.parse("start of next week", NOW), D(2024, 5, 13))
        self.assertEqual(N.parse("end of month", D(2024, 2, 10)), D(2024, 2, 29))


class Absolute(unittest.TestCase):
    def test_iso_and_month_names(self):
        self.assertEqual(N.parse("2025-01-31", NOW), D(2025, 1, 31)); self.assertEqual(N.parse("march 5 2026", NOW), D(2026, 3, 5)); self.assertEqual(N.parse("5th of march", NOW) or N.parse("5 march", NOW), D(2025, 3, 5))
        self.assertEqual(N.parse("dec 25", NOW), D(2024, 12, 25)); self.assertEqual(N.parse("may 8", NOW), D(2024, 5, 8)); self.assertEqual(N.parse("may 7", NOW), D(2025, 5, 7))      # already passed -> next year

    def test_impossible_dates_are_none(self):
        for bad in ("2024-02-30", "feb 30", "2024-13-01", "31 april"): self.assertIsNone(N.parse(bad, NOW), bad)


class Times(unittest.TestCase):
    def test_combined_with_dates(self):
        for text, want in (("tomorrow at 5pm", D(2024, 5, 9, 17)), ("tomorrow 5pm", D(2024, 5, 9, 17)), ("next friday 17:30", D(2024, 5, 10, 17, 30)), ("today at noon", D(2024, 5, 8, 12)),
                           ("tomorrow midnight", D(2024, 5, 9, 0)), ("12am tomorrow", D(2024, 5, 9, 0)), ("12:15pm today", D(2024, 5, 8, 12, 15)), ("march 5 2026 9am", D(2026, 3, 5, 9)), ("at 9pm", D(2024, 5, 8, 21)), ("in 2 days at 8am", D(2024, 5, 10, 8))):
            self.assertEqual(N.parse(text, NOW), want, text)

    def test_bad_times(self):
        for bad in ("tomorrow 13pm", "tomorrow 0am"): self.assertIsNone(N.parse(bad, NOW), bad)


class Refusals(unittest.TestCase):
    def test_does_not_guess(self):
        for bad in ("", "   ", "sometime", "next blursday", "in many days", "the 5th", "banana"): self.assertIsNone(N.parse(bad, NOW), bad)

    def test_keeps_timezone(self):
        now = dt.datetime(2024, 5, 8, 14, 0, tzinfo=ZoneInfo("America/New_York")); r = N.parse("tomorrow 9am", now)
        self.assertEqual((r.tzinfo, r.hour, r.day), (now.tzinfo, 9, 9))


class MonthMath(unittest.TestCase):
    def test_day_clamping(self):
        self.assertEqual(N.add_months(D(2024, 1, 31), 1), D(2024, 2, 29)); self.assertEqual(N.add_months(D(2023, 1, 31), 1), D(2023, 2, 28)); self.assertEqual(N.add_months(D(2024, 3, 31), -1), D(2024, 2, 29))
        self.assertEqual(N.add_months(D(2024, 11, 30), 3), D(2025, 2, 28)); self.assertEqual(N.add_months(D(2024, 2, 29), 12), D(2025, 2, 28)); self.assertEqual(N.add_months(D(2024, 5, 15), 0), D(2024, 5, 15))


class Countdown(unittest.TestCase):
    def test_parts_and_format(self):
        t = NOW + dt.timedelta(days=2, hours=3, minutes=4, seconds=5); self.assertEqual(N.countdown(t, NOW), dict(days=2, hours=3, minutes=4, seconds=5, past=False))
        self.assertEqual(N.format_countdown(t, NOW), "in 2 days 3 hours"); self.assertEqual(N.format_countdown(t, NOW, parts=4), "in 2 days 3 hours 4 minutes 5 seconds")
        self.assertEqual(N.format_countdown(NOW - dt.timedelta(minutes=5), NOW), "5 minutes ago"); self.assertEqual(N.format_countdown(NOW + dt.timedelta(hours=1), NOW), "in 1 hour")
        self.assertEqual(N.format_countdown(NOW, NOW), "now"); self.assertEqual(N.format_countdown(NOW + dt.timedelta(days=1, minutes=1), NOW), "in 1 day 1 minute")

    def test_calendar_days_versus_elapsed_across_dst(self):
        tz = ZoneInfo("Europe/Berlin"); now = dt.datetime(2024, 3, 30, 12, 0, tzinfo=tz); target = dt.datetime(2024, 3, 31, 12, 0, tzinfo=tz)       # clocks spring forward on the 31st
        self.assertEqual(N.countdown(target, now)["hours"], 23); self.assertEqual(N.days_until(target.date(), now.date()), 1)                            # 23 elapsed hours, but tomorrow is 1 calendar day

    def test_naive_and_aware_cannot_be_mixed(self):
        with self.assertRaises(TypeError): N.countdown(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc), dt.datetime(2024, 1, 1))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["English only; no locale-aware formats (dd/mm vs mm/dd is deliberately NOT parsed - ambiguity should be a UI choice)", "For the full zoo of human input use dateparser / parsedatetime / Duckling; this core is for apps that want predictable, testable behaviour", "Dates past `now` for month-day-only input roll to next year; adjust if you want past dates"], compat={"python": ">=3.9"},
   tags=["date", "natural-language", "nlp", "parse-date", "next-friday", "tomorrow", "relative-date", "countdown", "timer", "remaining-time", "time-ago", "month-arithmetic", "dst", "timezone", "datetime", "reminder", "scheduling", "human-readable"],
   avoid="Adding 30 days for 'a month'; guessing at ambiguous input instead of returning None; datetime.now() hidden inside the parser (untestable); naive/aware datetime mixing; showing '23 hours' to users who expect 'tomorrow'."),

RC("py-subprocess-safe-run-pipeline", "Run external commands safely: no shell, timeouts that kill the whole process group, output caps, clean env, missing-tool errors, shell-free pipelines with pipefail", "python",
   "Call git, ffmpeg, samtools, ripgrep or any CLI from Python without injection, hangs, zombie children, runaway output or confusing errors.",
   "An argv-list-only runner (never shell=True): resolves the executable up front and raises ToolNotFound with an install hint, runs in a new session so a timeout can SIGTERM then SIGKILL the entire process group (grandchildren included), reads stdout/stderr on threads with a hard byte cap (kills the child instead of exhausting memory), decodes with errors='replace', builds a minimal explicit environment, and returns one Result object. `pipeline()` wires `a | b | c` with OS pipes and no shell, reports every stage's exit code and applies pipefail semantics.",
   {"procrun.py": r'''"""Safe subprocess helpers for Linux/macOS (process groups and signals; not for Windows)."""
import os, shutil, signal, subprocess, threading, time
from dataclasses import dataclass, field


class ToolNotFound(FileNotFoundError):
    pass


@dataclass
class Result:
    argv: list
    code: int
    out: str = ""
    err: str = ""
    timed_out: bool = False
    truncated: bool = False
    seconds: float = 0.0
    stage_codes: list = field(default_factory=list)

    @property
    def ok(self):
        return self.code == 0 and not self.timed_out and not self.truncated


def clean_env(extra=None, keep=("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER")):
    """Minimal environment: only what the tool plausibly needs plus explicit extras. Secrets in your own environment do not leak to children."""
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env.update(extra or {})
    return env


def resolve(argv, env=None):
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) for a in argv):
        raise TypeError("argv must be a non-empty list of strings (a shell string would be an injection risk)")
    if "\0" in "".join(argv):
        raise ValueError("NUL byte in argument")
    exe = shutil.which(argv[0], path=(env or os.environ).get("PATH"))
    if not exe:
        raise ToolNotFound("%r not found on PATH - is it installed?" % argv[0])
    return [exe, *argv[1:]]


def _kill_group(proc, grace=1.0):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(proc.pid, 0)                            # group still has members?
        except ProcessLookupError:
            return
        time.sleep(0.02)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _reader(stream, limit, sink, state):
    got = 0
    for chunk in iter(lambda: stream.read(65536), b""):
        room = limit - got
        sink.append(chunk[:room])
        got += min(len(chunk), room)
        if len(chunk) > room:
            state["truncated"] = True
            state["stop"].set()
            return                                            # stop reading; the supervisor kills the child


def run(argv, *, timeout=30, input=None, cwd=None, env=None, max_output=10 * 1024 * 1024, check=False):
    """Run argv (list) and return Result. Never raises for a failing command unless check=True; always raises for misuse (bad argv, missing tool)."""
    exe = resolve(argv, env)
    t0 = time.monotonic()
    proc = subprocess.Popen(exe, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=cwd, env=env, start_new_session=True)
    state = {"truncated": False, "stop": threading.Event()}
    outb, errb = [], []
    threads = [threading.Thread(target=_reader, args=(proc.stdout, max_output, outb, state), daemon=True),
               threading.Thread(target=_reader, args=(proc.stderr, max_output, errb, state), daemon=True)]
    for t in threads:
        t.start()
    if input is not None:
        def feed():
            try:
                proc.stdin.write(input.encode() if isinstance(input, str) else input)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass                                          # child exited without reading: fine
        threading.Thread(target=feed, daemon=True).start()
    timed_out = False
    deadline = t0 + timeout
    while proc.poll() is None:
        if state["stop"].is_set():
            _kill_group(proc)
            break
        if time.monotonic() > deadline:
            timed_out = True
            _kill_group(proc)
            break
        time.sleep(0.01)
    code = proc.wait()
    for t in threads:
        t.join(timeout=2)
    res = Result(list(argv), code, b"".join(outb).decode("utf-8", "replace"), b"".join(errb).decode("utf-8", "replace"), timed_out, state["truncated"], time.monotonic() - t0)
    if check and not res.ok:
        raise subprocess.CalledProcessError(code, argv, res.out, res.err)
    return res


def pipeline(stages, *, timeout=30, input=None, env=None, max_output=10 * 1024 * 1024):
    """`a | b | c` without a shell. The Result is the LAST stage's output; code is the first non-zero stage code (pipefail), stage_codes lists them all."""
    exes = [resolve(s, env) for s in stages]
    procs, prev = [], None
    t0 = time.monotonic()
    try:
        for i, exe in enumerate(exes):
            last = i == len(exes) - 1
            p = subprocess.Popen(exe, stdin=(prev.stdout if prev else (subprocess.PIPE if input is not None else subprocess.DEVNULL)), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE if last else subprocess.DEVNULL, env=env, start_new_session=True)
            if prev:
                prev.stdout.close()                           # parent must drop its copy so upstream gets SIGPIPE when downstream exits
            procs.append(p)
            prev = p
        if input is not None:
            try:
                procs[0].stdin.write(input.encode() if isinstance(input, str) else input)
                procs[0].stdin.close()
            except (BrokenPipeError, OSError):
                pass
        timed_out = truncated = False
        outb, errb, state = [], [], {"truncated": False, "stop": threading.Event()}
        ths = [threading.Thread(target=_reader, args=(procs[-1].stdout, max_output, outb, state), daemon=True),
               threading.Thread(target=_reader, args=(procs[-1].stderr, max_output, errb, state), daemon=True)]
        for t in ths:
            t.start()
        deadline = t0 + timeout
        while any(p.poll() is None for p in procs):
            if state["stop"].is_set() or time.monotonic() > deadline:
                timed_out = not state["stop"].is_set()
                break
            time.sleep(0.01)
        if any(p.poll() is None for p in procs):
            for p in procs:
                _kill_group(p)
        codes = [p.wait() for p in procs]
        for t in ths:
            t.join(timeout=2)
    finally:
        for p in procs:
            if p.poll() is None:
                _kill_group(p)
    code = next((c for c in codes if c != 0), 0)
    return Result([list(s) for s in stages], code, b"".join(outb).decode("utf-8", "replace"), b"".join(errb).decode("utf-8", "replace"), timed_out, state["truncated"], time.monotonic() - t0, codes)
''', "test_procrun.py": r'''import os, signal, subprocess, tempfile, time, unittest
import procrun as P


class Run(unittest.TestCase):
    def test_success_output_and_separate_stderr(self):
        r = P.run(["sh", "-c", "echo out; echo err >&2"]); self.assertEqual((r.code, r.out, r.err, r.ok), (0, "out\n", "err\n", True))

    def test_exit_code_and_check(self):
        r = P.run(["sh", "-c", "exit 3"]); self.assertEqual((r.code, r.ok), (3, False))
        with self.assertRaises(subprocess.CalledProcessError): P.run(["false"], check=True)

    def test_arguments_are_data_not_shell(self):
        marker = os.path.join(tempfile.mkdtemp(), "pwned"); r = P.run(["printf", "%s", "; touch %s #" % marker])
        self.assertIn("touch", r.out); self.assertFalse(os.path.exists(marker))

    def test_misuse_is_rejected_loudly(self):
        for bad in ("echo hi", [], ["echo", 1], None):
            with self.assertRaises(TypeError): P.run(bad)
        with self.assertRaises(ValueError): P.run(["echo", "a\0b"])

    def test_missing_tool_has_a_helpful_error(self):
        with self.assertRaisesRegex(P.ToolNotFound, "not found on PATH"): P.run(["definitely-not-installed-xyz"])

    def test_stdin_and_cwd(self):
        self.assertEqual(P.run(["cat"], input="hello").out, "hello"); d = tempfile.mkdtemp(); self.assertEqual(P.run(["pwd"], cwd=d).out.strip(), os.path.realpath(d))
        self.assertEqual(P.run(["sh", "-c", "exit 0"], input="ignored by the child").code, 0)                     # child never reads stdin: no BrokenPipe crash

    def test_environment_is_minimal_by_default_when_requested(self):
        os.environ["SUPER_SECRET_TOKEN"] = "hunter2"
        try:
            leaky = P.run(["sh", "-c", "echo ${SUPER_SECRET_TOKEN:-unset}"]).out.strip(); clean = P.run(["sh", "-c", "echo ${SUPER_SECRET_TOKEN:-unset}"], env=P.clean_env({"X": "1"})).out.strip()
            self.assertEqual((leaky, clean), ("hunter2", "unset")); self.assertEqual(P.run(["sh", "-c", "echo $X"], env=P.clean_env({"X": "1"})).out.strip(), "1")
        finally: del os.environ["SUPER_SECRET_TOKEN"]

    def test_non_utf8_output_does_not_crash(self):
        r = P.run(["printf", r"\377\376ok"]); self.assertTrue(r.out.endswith("ok")); self.assertIn("\ufffd", r.out)


class Limits(unittest.TestCase):
    def test_timeout_kills_the_child_quickly(self):
        t = time.time(); r = P.run(["sleep", "30"], timeout=0.3); self.assertTrue(r.timed_out); self.assertFalse(r.ok); self.assertLess(time.time() - t, 3)

    def test_timeout_kills_grandchildren_too(self):
        d = tempfile.mkdtemp(); pidfile = os.path.join(d, "pid")
        r = P.run(["sh", "-c", "sleep 30 & echo $! > %s; wait" % pidfile], timeout=0.5); self.assertTrue(r.timed_out)
        pid = int(open(pidfile).read()); time.sleep(0.2)
        with self.assertRaises(ProcessLookupError): os.kill(pid, 0)                                               # the backgrounded sleep is gone, not orphaned

    def test_runaway_output_is_capped_and_the_child_is_stopped(self):
        t = time.time(); r = P.run(["yes"], max_output=100_000, timeout=10); self.assertTrue(r.truncated); self.assertLessEqual(len(r.out), 100_000); self.assertLess(time.time() - t, 5); self.assertFalse(r.ok)

    def test_large_output_under_the_cap_is_complete_and_does_not_deadlock(self):
        r = P.run(["sh", "-c", "head -c 3000000 /dev/zero | tr '\\0' x; head -c 3000000 /dev/zero | tr '\\0' y >&2"], timeout=20)
        self.assertEqual((len(r.out), len(r.err), r.ok), (3000000, 3000000, True))                                # both pipes drained concurrently


class Pipelines(unittest.TestCase):
    def test_a_pipe_b_pipe_c(self):
        r = P.pipeline([["printf", "b\\na\\nb\\n"], ["sort"], ["uniq", "-c"]]); self.assertEqual([l.split() for l in r.out.strip().splitlines()], [["1", "a"], ["2", "b"]]); self.assertEqual((r.code, r.stage_codes), (0, [0, 0, 0]))

    def test_pipefail_reports_the_first_failing_stage(self):
        r = P.pipeline([["sh", "-c", "echo data; exit 4"], ["cat"]]); self.assertEqual((r.out, r.code, r.stage_codes), ("data\n", 4, [4, 0]))
        r = P.pipeline([["false"], ["cat"]]); self.assertEqual(r.code, 1)

    def test_downstream_exit_does_not_hang_an_endless_producer(self):
        t = time.time(); r = P.pipeline([["yes"], ["head", "-n", "3"]], timeout=10); self.assertEqual(r.out, "y\ny\ny\n"); self.assertLess(time.time() - t, 5)

    def test_input_feeds_the_first_stage(self):
        self.assertEqual(P.pipeline([["tr", "a-z", "A-Z"], ["rev"]], input="abc\n").out, "CBA\n")

    def test_timeout_kills_every_stage(self):
        t = time.time(); r = P.pipeline([["sleep", "30"], ["cat"]], timeout=0.3); self.assertTrue(r.timed_out); self.assertLess(time.time() - t, 4)

    def test_missing_tool_in_any_stage_fails_before_anything_runs(self):
        with self.assertRaises(P.ToolNotFound): P.pipeline([["echo", "x"], ["no-such-tool-abc"]])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["POSIX only (process groups, SIGTERM/SIGKILL); Windows needs job objects", "Tests rely on coreutils (sh, cat, yes, head, sort, uniq, tr, rev, sleep, printf) being present", "Timeouts are wall-clock polling at 10 ms; for async code use asyncio.create_subprocess_exec with start_new_session=True and the same kill-the-group logic"], compat={"python": ">=3.9"},
   tags=["subprocess", "shell", "command", "pipeline", "pipe", "pipefail", "timeout", "process-group", "killpg", "popen", "run", "capture-output", "stdout", "stderr", "injection", "shell-injection", "environment", "bioinformatics", "cli-wrapper", "external-tool"],
   avoid="shell=True with f-strings; subprocess.run(..., capture_output=True) on endless output; timeouts that kill only the direct child; waiting for a process before draining its pipes (deadlock); inheriting secrets in the environment; unhelpful FileNotFoundError tracebacks."),
]
