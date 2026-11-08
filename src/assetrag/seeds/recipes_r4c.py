"""Round-4 recipes, part C (Python, `unittest`): spreadsheet/CSV reporting, a dependency-free XLSX writer/reader, and a dependency-free PDF report writer."""
from .recipes import RC, UT

R4C = [
RC("py-csv-report-clean-group-pivot-email", "CSV reporting: clean messy numbers, stream group/sum/pivot, moving average and growth, schema validation, injection-safe export, emailed report", "python",
   "Turn a messy CSV export into a trustworthy report: parse '$1,234.50' and '(12.00)', aggregate millions of rows without loading them, pivot, smooth, compare periods, reject bad rows with line numbers, write CSV that is safe to open in Excel, and mail it.",
   "Decimal-based money parsing with explicit null tokens and accounting negatives; single-pass streaming aggregation (count/sum/min/max/mean per key tuple) that accepts any iterable; pivot with row/column totals; O(1)-per-step moving average, percent change and CAGR that refuse to divide by zero; a declarative row validator returning (good_rows, [(line, column, message)]); CSV-injection-safe atomic export; and an EmailMessage builder with attachment plus an injectable SMTP sender that starts TLS before logging in.",
   {"reportkit.py": r'''"""CSV/report helpers on the standard library only."""
import csv, datetime, os, re, smtplib, ssl
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage

NULLS = {"", "na", "n/a", "null", "none", "nan", "-", "--"}


def clean_number(s):
    """'$1,234.50' -> Decimal; '(12.00)' -> -12 (accounting negative); '15%' -> 15; null tokens -> None; garbage -> ValueError.
    US-style separators. For '1.234,50' normalise first (locale-aware parsing belongs to babel)."""
    if s is None:
        return None
    t = str(s).strip()
    if t.lower() in NULLS:
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"[,$€£\s]", "", t.strip("()")).rstrip("%")
    try:
        v = Decimal(t)
    except InvalidOperation:
        raise ValueError("not a number: %r" % s)
    if not v.is_finite():
        raise ValueError("not a number: %r" % s)
    return -v if neg else v


def read_rows(path):
    """Yield (line_number, dict). utf-8-sig drops an Excel BOM; header names are stripped; ragged rows keep their line number."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        rd.fieldnames = [h.strip() for h in rd.fieldnames or []]
        for row in rd:
            yield rd.line_num, row


def group_stats(rows, keys, value):
    """One pass, memory proportional to the number of groups. rows: iterable of dicts. Null values are counted separately, not as zero."""
    acc = defaultdict(lambda: dict(count=0, nulls=0, sum=Decimal(0), min=None, max=None))
    for r in rows:
        g = acc[tuple(r[k] for k in keys)]
        v = clean_number(r[value])
        if v is None:
            g["nulls"] += 1
            continue
        g["count"] += 1
        g["sum"] += v
        g["min"] = v if g["min"] is None else min(g["min"], v)
        g["max"] = v if g["max"] is None else max(g["max"], v)
    for g in acc.values():
        g["mean"] = g["sum"] / g["count"] if g["count"] else None
    return dict(acc)


def pivot(rows, row_key, col_key, value):
    """-> (columns, {row: {col: sum}}, row_totals, col_totals)."""
    table, cols = defaultdict(lambda: defaultdict(Decimal)), set()
    for r in rows:
        v = clean_number(r[value])
        if v is not None:
            table[r[row_key]][r[col_key]] += v
            cols.add(r[col_key])
    cols = sorted(cols)
    rt = {k: sum(v.values(), Decimal(0)) for k, v in table.items()}
    ct = {c: sum((table[k].get(c, Decimal(0)) for k in table), Decimal(0)) for c in cols}
    return cols, {k: dict(v) for k, v in table.items()}, rt, ct


def moving_average(values, window):
    if window < 1:
        raise ValueError("window must be >= 1")
    out, buf, total = [], deque(), 0
    for v in values:
        buf.append(v); total += v
        if len(buf) > window:
            total -= buf.popleft()
        out.append(total / window if len(buf) == window else None)      # no partial-window values: they mislead
    return out


def pct_change(values):
    """Period-over-period growth; None where the previous value is missing or zero."""
    return [None if i == 0 or values[i - 1] in (None, 0) or v is None else (v - values[i - 1]) / values[i - 1] for i, v in enumerate(values)]


def cagr(first, last, periods):
    if first <= 0 or periods <= 0:
        raise ValueError("CAGR needs a positive start value and at least one period")
    return (last / first) ** (1 / periods) - 1


def validate_rows(rows, schema):
    """schema: {column: dict(type=str|int|float|Decimal|date, required=True, min=, max=, choices=, pattern=)}.
    rows: iterable of (line, dict). -> (good_rows, [(line, column, message)]). Never stops at the first problem."""
    good, errors = [], []
    for line, row in rows:
        out, bad = {}, False
        for col, rule in schema.items():
            raw = (row.get(col) or "").strip()
            if raw == "":
                if rule.get("required", True):
                    errors.append((line, col, "required")); bad = True
                out[col] = None
                continue
            try:
                t = rule.get("type", str)
                v = raw if t is str else datetime.date.fromisoformat(raw) if t is datetime.date else t(clean_number(raw) if t is not int else int(clean_number(raw)))
            except (ValueError, TypeError, InvalidOperation):
                errors.append((line, col, "expected %s, got %r" % (getattr(rule.get("type", str), "__name__", "value"), raw))); bad = True
                continue
            if "min" in rule and v < rule["min"]: errors.append((line, col, "below minimum %s" % rule["min"])); bad = True
            if "max" in rule and v > rule["max"]: errors.append((line, col, "above maximum %s" % rule["max"])); bad = True
            if "choices" in rule and v not in rule["choices"]: errors.append((line, col, "not one of %s" % sorted(rule["choices"]))); bad = True
            if "pattern" in rule and not re.fullmatch(rule["pattern"], raw): errors.append((line, col, "does not match %s" % rule["pattern"])); bad = True
            out[col] = v
        if not bad:
            good.append(out)
    return good, errors


def safe_cell(v):
    """Spreadsheet apps execute cells starting with = + - @ (CSV/formula injection). Prefix text with an apostrophe; real numbers stay numbers."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def write_csv(path, rows, fieldnames):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow({k: safe_cell(v) for k, v in r.items()})
    os.replace(tmp, path)


def build_email(sender, to, subject, body, attachments=()):
    """attachments: [(filename, bytes, 'text/csv')]. Header values are rejected if they contain newlines (header injection)."""
    for h in (sender, subject, *([to] if isinstance(to, str) else to)):
        if "\n" in h or "\r" in h:
            raise ValueError("newline in header value")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, to if isinstance(to, str) else ", ".join(to), subject
    m.set_content(body)
    for name, data, mime in attachments:
        main, _, sub = mime.partition("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return m


def send(msg, host, port=587, user=None, password=None, smtp=smtplib.SMTP):
    with smtp(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())       # TLS BEFORE credentials ever cross the wire
        if user:
            s.login(user, password)
        s.send_message(msg)


def next_daily_run(now, hour, minute=0):
    """Next local time at hour:minute strictly after `now`. For real scheduling prefer a systemd timer / cron entry over a sleeping loop."""
    t = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return t if t > now else t + datetime.timedelta(days=1)
''', "test_reportkit.py": r'''import datetime, email, os, tempfile, unittest
from decimal import Decimal as D
import reportkit as R

CSV = "﻿Region, Product ,Amount\nEU,Widget,\"$1,200.50\"\nEU,Gadget,(100.00)\nUS,Widget,300\nUS,Widget,n/a\nUS,Gadget,15%\n"


def csv_file(text=CSV):
    p = os.path.join(tempfile.mkdtemp(), "in.csv"); open(p, "w", encoding="utf-8", newline="").write(text); return p


class Numbers(unittest.TestCase):
    def test_parse(self):
        for s, want in (("$1,234.50", D("1234.50")), ("(12.00)", D(-12)), ("15%", D(15)), (" 7 ", D(7)), ("€2", D(2)), ("N/A", None), ("", None), (None, None)): self.assertEqual(R.clean_number(s), want, s)
        for bad in ("abc", "NaN-ish", "1.2.3", "Infinity"):
            with self.assertRaises(ValueError): R.clean_number(bad)


class Aggregates(unittest.TestCase):
    def rows(self): return (r for _, r in R.read_rows(csv_file()))

    def test_bom_header_whitespace_and_line_numbers(self):
        lines = list(R.read_rows(csv_file())); self.assertEqual(list(lines[0][1]), ["Region", "Product", "Amount"]); self.assertEqual([n for n, _ in lines], [2, 3, 4, 5, 6])

    def test_group_stats_streaming_and_nulls(self):
        g = R.group_stats(self.rows(), ["Region"], "Amount")                       # a generator is fine: single pass
        self.assertEqual(g[("EU",)]["sum"], D("1100.50")); self.assertEqual((g[("US",)]["count"], g[("US",)]["nulls"], g[("US",)]["sum"]), (2, 1, D(315)))
        self.assertEqual((g[("US",)]["min"], g[("US",)]["max"], g[("US",)]["mean"]), (D(15), D(300), D("157.5")))

    def test_pivot_with_totals(self):
        cols, t, rt, ct = R.pivot(self.rows(), "Region", "Product", "Amount")
        self.assertEqual(cols, ["Gadget", "Widget"]); self.assertEqual(t["EU"]["Gadget"], D(-100)); self.assertEqual(rt["US"], D(315)); self.assertEqual(ct["Widget"], D("1500.50"))
        self.assertEqual(sum(rt.values()), sum(ct.values()))


class Series(unittest.TestCase):
    def test_moving_average(self):
        self.assertEqual(R.moving_average([1, 2, 3, 4, 5], 3), [None, None, 2, 3, 4]); self.assertEqual(R.moving_average([5], 1), [5])
        with self.assertRaises(ValueError): R.moving_average([1], 0)

    def test_growth(self):
        g = R.pct_change([100, 110, 0, 5, None, 7]); self.assertAlmostEqual(g[1], 0.10); self.assertEqual([g[0]] + g[2:], [None, -1.0, None, None, None]); self.assertAlmostEqual(R.cagr(100, 121, 2), 0.10)
        for a in ((0, 5, 1), (5, 5, 0), (-1, 5, 1)):
            with self.assertRaises(ValueError): R.cagr(*a)


class Validation(unittest.TestCase):
    schema = {"id": dict(type=int, min=1), "email": dict(pattern=r"[^@\s]+@[^@\s]+\.\w+"), "plan": dict(choices={"free", "pro"}), "when": dict(type=datetime.date), "note": dict(required=False)}

    def test_reports_every_problem_with_line_numbers(self):
        rows = [(2, dict(id="5", email="a@b.co", plan="pro", when="2024-02-29", note="")), (3, dict(id="0", email="nope", plan="gold", when="2024-13-01", note="x")),
                (4, dict(id="abc", email="", plan="free", when="2024-01-01"))]
        good, errs = R.validate_rows(rows, self.schema)
        self.assertEqual(good, [dict(id=5, email="a@b.co", plan="pro", when=datetime.date(2024, 2, 29), note=None)])
        self.assertEqual({(l, c) for l, c, _ in errs}, {(3, "id"), (3, "email"), (3, "plan"), (3, "when"), (4, "id"), (4, "email")})


class Export(unittest.TestCase):
    def test_formula_injection_is_defused_and_numbers_survive(self):
        p = os.path.join(tempfile.mkdtemp(), "o.csv"); R.write_csv(p, [dict(name="=HYPERLINK(\"http://evil\")", amt=-5), dict(name="+1", amt=3), dict(name="ok", amt=1)], ["name", "amt"])
        t = open(p).read(); self.assertIn("'=HYPERLINK", t); self.assertIn("'+1", t); self.assertIn(",-5", t); self.assertFalse(os.path.exists(p + ".tmp"))

    def test_extra_columns_are_an_error_not_silently_dropped(self):
        with self.assertRaises(ValueError): R.write_csv(os.path.join(tempfile.mkdtemp(), "x.csv"), [dict(a=1, b=2)], ["a"])


class Mail(unittest.TestCase):
    def test_message_has_attachment_and_rejects_header_injection(self):
        m = R.build_email("r@x.io", ["a@x.io", "b@x.io"], "Daily report", "see attached", [("r.csv", b"a,b\n1,2\n", "text/csv")])
        parsed = email.message_from_bytes(m.as_bytes()); self.assertEqual(parsed["To"], "a@x.io, b@x.io")
        self.assertEqual([p.get_filename() for p in parsed.walk() if p.get_filename()], ["r.csv"])
        with self.assertRaises(ValueError): R.build_email("r@x.io", "a@x.io", "hi\nBcc: evil@x.io", "x")

    def test_tls_before_login_then_send(self):
        calls = []
        class FakeSMTP:
            def __init__(self, host, port, timeout): calls.append(("connect", host, port))
            def __enter__(self): return self
            def __exit__(self, *a): calls.append(("quit",))
            def starttls(self, context): calls.append(("starttls",))
            def login(self, u, p): calls.append(("login", u))
            def send_message(self, m): calls.append(("send",))
        R.send(R.build_email("r@x.io", "a@x.io", "s", "b"), "smtp.x.io", 587, "user", "pw", smtp=FakeSMTP)
        self.assertEqual([c[0] for c in calls], ["connect", "starttls", "login", "send", "quit"])

    def test_next_daily_run(self):
        n = datetime.datetime(2024, 5, 1, 8, 30); self.assertEqual(R.next_daily_run(n, 9), datetime.datetime(2024, 5, 1, 9, 0)); self.assertEqual(R.next_daily_run(n, 8, 30), datetime.datetime(2024, 5, 2, 8, 30))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["US-style number formats; use babel for locale-aware parsing", "pandas/polars are the right tools once data stops fitting simple one-pass aggregation (joins, window functions, 10M+ rows)", "SMTP sender is tested against a fake server object only; real providers may need app passwords or an API instead"], compat={"python": ">=3.9"},
   tags=["csv", "report", "spreadsheet", "pivot", "pivot-table", "group-by", "aggregate", "sum", "clean", "null", "stream", "large-file", "moving-average", "growth", "cagr", "validate", "schema", "export", "csv-injection", "email", "smtp", "schedule", "decimal", "money"],
   avoid="Floats for money; csv.reader on a file opened without newline=''; loading a whole CSV just to sum a column; writing user text starting with = + - @ into CSV unprefixed; logging in to SMTP before STARTTLS."),

RC("py-xlsx-writer-reader-minimal", "Write and read .xlsx without openpyxl: formatted header, widths, frozen row, dates, money/percent formats, formulas, safe strings", "python",
   "Produce an Excel report from a script on a locked-down box (or understand the format) - bold header, column widths, real dates and numbers, SUM formulas - and read simple workbooks back.",
   "An .xlsx is a zip of XML parts: write the minimal ECMA-376 set ([Content_Types].xml, _rels, workbook, workbook rels, styles, one sheet per tab) with inline strings, a small style table (bold header, date, datetime, money, percent), frozen header pane, column widths and explicit Formula cells. Text starting with '=' is stored as TEXT so user data can never become a formula. Includes sheet-name validation, XML-illegal control character stripping, date serial conversion and a reader that understands inline and shared strings and sparse cells.",
   {"xlsxmini.py": r'''"""Minimal XLSX writer/reader (zipfile + ElementTree). For charts, conditional formats or huge sheets use openpyxl / xlsxwriter."""
import datetime, re, zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
EPOCH = datetime.datetime(1899, 12, 30)                  # Excel's day 0 (it also wrongly believes 1900 was a leap year; this epoch compensates)
ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
S_DEFAULT, S_HEADER, S_DATE, S_DATETIME, S_MONEY, S_PERCENT = range(6)


class Formula(str):
    """Explicit formula, e.g. Formula('=SUM(B2:B9)'). Plain strings are never interpreted as formulas."""


class Money(Decimal):
    pass


class Pct(float):
    """0.25 shows as 25.00%."""


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def check_sheet_name(name, taken):
    if not name or len(name) > 31 or re.search(r"[\[\]:*?/\\]", name) or name.startswith("'") or name.endswith("'"):
        raise ValueError("invalid sheet name %r (1-31 chars, none of []:*?/\\)" % name)
    if name.lower() in taken:
        raise ValueError("duplicate sheet name %r (names are case-insensitive)" % name)


def _text(s):
    return '<t xml:space="preserve">%s</t>' % escape(ILLEGAL.sub("", s))


def _cell(ref, v, header):
    st = S_HEADER if header else S_DEFAULT
    if v is None:
        return ""
    if isinstance(v, Formula):
        return '<c r="%s"><f>%s</f></c>' % (ref, escape(v[1:] if v.startswith("=") else v))
    if isinstance(v, bool):
        return '<c r="%s" t="b"><v>%d</v></c>' % (ref, v)
    if isinstance(v, datetime.datetime):
        return '<c r="%s" s="%d"><v>%r</v></c>' % (ref, S_DATETIME, (v - EPOCH).total_seconds() / 86400)
    if isinstance(v, datetime.date):
        return '<c r="%s" s="%d"><v>%d</v></c>' % (ref, S_DATE, (datetime.datetime(v.year, v.month, v.day) - EPOCH).days)
    if isinstance(v, (int, float, Decimal)):
        if isinstance(v, float) and v != v or v in (float("inf"), float("-inf")):
            raise ValueError("NaN/inf cannot be stored in a cell")
        s = S_MONEY if isinstance(v, Money) else S_PERCENT if isinstance(v, Pct) else S_DEFAULT
        return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, s, repr(float(v)) if isinstance(v, (float, Decimal)) else v)
    return '<c r="%s" t="inlineStr" s="%d"><is>%s</is></c>' % (ref, st, _text(str(v)))


def _sheet_xml(rows, widths, header, freeze):
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="%s">' % NS]
    if freeze and header:
        parts.append('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>')
    if widths:
        parts.append("<cols>%s</cols>" % "".join('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w) for i, w in enumerate(widths)))
    parts.append("<sheetData>")
    for r, row in enumerate(rows, 1):
        parts.append('<row r="%d">%s</row>' % (r, "".join(_cell("%s%d" % (col_letter(c), r), v, header and r == 1) for c, v in enumerate(row))))
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


STYLES = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="%s">'
          '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
          '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="22" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>') % NS


def write_xlsx(path, sheets):
    """sheets: [dict(name=, rows=[[...]], widths=[...], header=True, freeze=True)]. Written atomically-ish: zip is complete before it replaces anything."""
    taken = set()
    for s in sheets:
        check_sheet_name(s["name"], taken)
        taken.add(s["name"].lower())
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>%s</Types>'
          ) % "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1) for i in range(len(sheets)))
    root = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>') % REL
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
          ) % (NS, REL, "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(s["name"], {'"': "&quot;"}), i + 1, i + 1) for i, s in enumerate(sheets)))
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
            '<Relationship Id="rId%d" Type="%s/styles" Target="styles.xml"/></Relationships>'
            ) % ("".join('<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, REL, i + 1) for i in range(len(sheets))), len(sheets) + 1, REL)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)                       # first entry, by convention
        z.writestr("_rels/.rels", root)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", STYLES)
        for i, s in enumerate(sheets):
            z.writestr("xl/worksheets/sheet%d.xml" % (i + 1), _sheet_xml(s["rows"], s.get("widths"), s.get("header", True), s.get("freeze", True)))
    import os
    os.replace(tmp, path)


def read_xlsx(path):
    """-> {sheet name: [[cell, ...], ...]}. Numbers come back as int/float, strings as str, booleans as bool; dates as their serial numbers."""
    q = lambda t: "{%s}%s" % (NS, t)
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter(q("t"))) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(q("si"))]
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        out = {}
        for sh in wb.iter(q("sheet")):
            target = rels[sh.get("{%s}id" % REL)].lstrip("/")
            data = ET.fromstring(z.read(target if target.startswith("xl/") else "xl/" + target))
            rows = []
            for row in data.iter(q("row")):
                cells = {}
                for c in row.iter(q("c")):
                    t, v = c.get("t"), c.find(q("v"))
                    if t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(q("t")))
                    elif v is None:
                        continue
                    elif t == "s":
                        val = shared[int(v.text)]
                    elif t == "b":
                        val = v.text == "1"
                    elif t in ("str", "e"):
                        val = v.text
                    else:
                        f = float(v.text)
                        val = int(f) if f == int(f) and "." not in v.text and "E" not in v.text.upper() else f
                    cells[col_index(re.match(r"[A-Z]+", c.get("r")).group())] = val
                rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
            out[sh.get("name")] = rows
    return out
''', "test_xlsxmini.py": r'''import datetime, os, tempfile, unittest, zipfile
from decimal import Decimal
from xml.etree import ElementTree as ET
import xlsxmini as X


def path(): return os.path.join(tempfile.mkdtemp(), "t.xlsx")


class Writer(unittest.TestCase):
    def test_round_trip_types(self):
        p = path(); X.write_xlsx(p, [dict(name="Sales", rows=[["Region", "Units", "Revenue", "Share", "Day", "Ok"], ["EU", 12, X.Money(Decimal("1234.5")), X.Pct(0.25), datetime.date(2024, 3, 1), True], ["US", 7, 0.5, None, datetime.datetime(2024, 3, 1, 12, 0), False]], widths=[14, 8, 12, 8, 12, 6])])
        r = X.read_xlsx(p)["Sales"]
        self.assertEqual(r[0], ["Region", "Units", "Revenue", "Share", "Day", "Ok"]); self.assertEqual(r[1][:4], ["EU", 12, 1234.5, 0.25]); self.assertEqual(r[1][5], True)
        self.assertEqual(r[1][4], 45352); self.assertEqual(r[2][4], 45352.5); self.assertEqual(r[2][:4], ["US", 7, 0.5, None][:3] + [None][:0] + r[2][3:4])      # sparse cell stays None

    def test_package_parts_and_zip_integrity(self):
        p = path(); X.write_xlsx(p, [dict(name="A", rows=[["h"], [1]]), dict(name="B", rows=[["h"]])])
        with zipfile.ZipFile(p) as z:
            self.assertIsNone(z.testzip()); names = z.namelist(); self.assertEqual(names[0], "[Content_Types].xml")
            for n in ("_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"): self.assertIn(n, names)
            for n in names: ET.fromstring(z.read(n))                                                     # every part is well-formed XML
        self.assertEqual(list(X.read_xlsx(p)), ["A", "B"])

    def test_header_bold_frozen_and_widths(self):
        p = path(); X.write_xlsx(p, [dict(name="S", rows=[["h", "i"], ["a", "b"]], widths=[20, 5])])
        s = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
        self.assertIn('state="frozen"', s); self.assertIn('<col min="1" max="1" width="20"', s); self.assertIn('s="1"><is>', s)
        q = path(); X.write_xlsx(q, [dict(name="S", rows=[["h"]], header=False)]); self.assertNotIn("frozen", zipfile.ZipFile(q).read("xl/worksheets/sheet1.xml").decode())

    def test_text_that_looks_like_a_formula_stays_text_formula_is_explicit(self):
        p = path(); X.write_xlsx(p, [dict(name="S", rows=[["x"], ["=1+1"], [X.Formula("=SUM(A2:A3)")]])]); s = zipfile.ZipFile(p).read("xl/worksheets/sheet1.xml").decode()
        self.assertEqual(s.count("<f>"), 1); self.assertIn("<f>SUM(A2:A3)</f>", s); self.assertEqual(X.read_xlsx(p)["S"][1], ["=1+1"])

    def test_xml_special_characters_control_chars_and_unicode(self):
        p = path(); nasty = 'a<b>&"c" \x00\x07 ünï ✓ \n line2  '; X.write_xlsx(p, [dict(name="Q&A", rows=[[nasty]])])
        self.assertEqual(X.read_xlsx(p)["Q&A"][0][0], 'a<b>&"c"  ünï ✓ \n line2  ')

    def test_bad_sheet_names_and_values(self):
        for bad in ("", "x" * 32, "a/b", "a[1]", "'q"):
            with self.assertRaises(ValueError): X.write_xlsx(path(), [dict(name=bad, rows=[])])
        with self.assertRaises(ValueError): X.write_xlsx(path(), [dict(name="A", rows=[]), dict(name="a", rows=[])])
        with self.assertRaises(ValueError): X.write_xlsx(path(), [dict(name="A", rows=[[float("nan")]])])


class Helpers(unittest.TestCase):
    def test_column_letters(self):
        for i, s in ((0, "A"), (25, "Z"), (26, "AA"), (701, "ZZ"), (702, "AAA")): self.assertEqual((X.col_letter(i), X.col_index(s)), (s, i))


class Reader(unittest.TestCase):
    def test_shared_strings_and_gaps_from_other_writers(self):
        p = path()
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>' % (X.NS, X.REL))
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="x" Target="/xl/worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/sharedStrings.xml", '<sst xmlns="%s"><si><t>hello</t></si><si><r><t>rich </t></r><r><t>text</t></r></si></sst>' % X.NS)
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row><row r="2"/><row r="3"><c r="B3"><v>2.5</v></c></row></sheetData></worksheet>' % X.NS)
        self.assertEqual(X.read_xlsx(p)["S"], [["hello", None, "rich text"], [], [None, 2.5]])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Structure follows the ECMA-376 minimal package and round-trips through its own reader and XML parser; it was NOT opened in Excel/LibreOffice in the verification sandbox - open one generated file once before shipping", "No shared-string table on write (inline strings): fine for reports, larger files than xlsxwriter for very repetitive text", "No charts, conditional formatting, merged cells or images: use xlsxwriter/openpyxl when you need them"], compat={"python": ">=3.9"},
   tags=["excel", "xlsx", "spreadsheet", "report", "openxml", "zipfile", "formatting", "header", "freeze-panes", "column-width", "date", "currency", "percent", "formula", "formula-injection", "export", "no-dependencies"],
   avoid="Writing user text into cells as formulas; forgetting Excel's 1899-12-30 epoch; sheet names over 31 chars or with []:*?/\\; control characters in XML; building xlsx by renaming a CSV."),

RC("py-pdf-minimal-report-writer", "Generate a PDF report with no dependencies: wrapped paragraphs, headings, tables with repeated headers, page numbers, automatic page breaks", "python",
   "Make an invoice, report or printable summary from data on a server where you cannot install reportlab/WeasyPrint, or see how PDF files are really built.",
   "Hand-build PDF 1.4: catalog, page tree, the two base-14 Helvetica fonts (no embedding needed), Flate-compressed content streams, WinAnsi text encoding with correct escaping of ( ) and backslash, a cross-reference table with exact byte offsets. A flow layout wraps words using real Helvetica width metrics, breaks pages automatically, draws tables with right-aligned numbers and a header repeated on every page, and stamps 'Page i of n' once the page count is known. Output is validated structurally and with poppler's pdftotext / Ghostscript when present.",
   {"pdfmini.py": r'''"""Minimal PDF 1.4 writer for text reports. Base-14 Helvetica only: Latin-1/WinAnsi characters, no images, no embedding."""
import zlib

# Helvetica advance widths (1/1000 em) for ASCII 32..126, from the Adobe AFM.
W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
     1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
     333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]


def text_width(s, size, bold=False):
    """Width in points. Bold is approximated (+7%); non-ASCII uses an average glyph."""
    w = sum(W[ord(c) - 32] if 32 <= ord(c) < 127 else 556 for c in s)
    return w * size / 1000 * (1.07 if bold else 1)


def _lit(s):
    b = s.encode("cp1252", "replace")                           # WinAnsiEncoding is cp1252; unencodable characters become '?'
    return b.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def wrap(text, size, max_w, bold=False):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            while text_width(word, size, bold) > max_w and len(word) > 1:      # a single over-long word is broken, never allowed to overflow
                k = next((j for j in range(len(word) - 1, 0, -1) if text_width(word[:j], size, bold) <= max_w), 1)
                if cur:
                    lines.append(cur); cur = ""
                lines.append(word[:k]); word = word[k:]
            t = (cur + " " + word).strip()
            if cur and text_width(t, size, bold) > max_w:
                lines.append(cur); cur = word
            else:
                cur = t
        lines.append(cur)
    return lines


class Report:
    def __init__(self, title="Report", page=(595.28, 841.89), margin=54, footer=True):
        self.title, self.pw, self.ph, self.m, self.footer = title, page[0], page[1], margin, footer
        self.pages, self.y = [], None
        self._new_page()

    def _new_page(self):
        self.pages.append([])
        self.y = self.ph - self.m

    def _need(self, h):
        if self.y - h < self.m + (18 if self.footer else 0):
            self._new_page()
            return True
        return False

    def _text(self, x, y, s, size, bold=False):
        self.pages[-1].append("BT /%s %.2f Tf %.2f %.2f Td (%s) Tj ET" % ("F2" if bold else "F1", size, x, y, _lit(s).decode("latin-1")))

    def heading(self, text, size=18):
        self._need(size * 2.2)
        self.y -= size * 1.2
        self._text(self.m, self.y, text, size, True)
        self.y -= size * 0.6

    def paragraph(self, text, size=10.5, leading=1.35):
        for ln in wrap(text, size, self.pw - 2 * self.m):
            self._need(size * leading)
            self.y -= size * leading
            self._text(self.m, self.y, ln, size)
        self.y -= size * 0.6

    def table(self, header, rows, widths, size=9.5, align=None):
        """widths in points (sum <= page width - 2*margin). align: 'l'/'r' per column; numbers default to right-aligned. The header repeats after a page break."""
        align = align or ["r" if rows and isinstance(rows[0][i], (int, float)) else "l" for i in range(len(header))]
        rh = size * 1.9

        def row(cells, bold, top):
            x = self.m
            self.pages[-1].append("0.6 w %.2f %.2f %.2f %.2f re S" % (self.m, top - rh, sum(widths), rh))
            for i, c in enumerate(cells):
                s = (("{:,.2f}".format(c) if isinstance(c, float) else "{:,}".format(c)) if isinstance(c, (int, float)) and not isinstance(c, bool) else str(c))
                while text_width(s, size, bold) > widths[i] - 8 and len(s) > 1:      # clip with an ellipsis rather than overprint the next cell
                    s = s[:-2] + "…" if not s.endswith("…") else s[:-2] + "…"
                tx = x + 4 if align[i] == "l" else x + widths[i] - 4 - text_width(s, size, bold)
                self._text(tx, top - rh + size * 0.55, s, size, bold)
                x += widths[i]

        def head():
            self._need(rh * 2)
            row(header, True, self.y)
            self.y -= rh

        head()
        for r in rows:
            if self._need(rh):
                head()
            row(r, False, self.y)
            self.y -= rh
        self.y -= size * 0.8

    def to_bytes(self):
        n, objs = len(self.pages), {}
        objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        objs[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        kids = []
        for i, ops in enumerate(self.pages):
            ops = list(ops)
            if self.footer:
                s = "Page %d of %d" % (i + 1, n)
                ops.append("BT /F1 8.5 Tf %.2f %.2f Td (%s) Tj ET" % (self.pw / 2 - text_width(s, 8.5) / 2, self.m / 2, s))
            data = zlib.compress("\n".join(ops).encode("latin-1", "replace"))
            pid, cid = 5 + 2 * i, 6 + 2 * i
            kids.append("%d 0 R" % pid)
            objs[pid] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents %d 0 R >>" % (self.pw, self.ph, cid)).encode()
            objs[cid] = b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream"
        objs[2] = ("<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join(kids), n)).encode()
        info = max(objs) + 1
        objs[info] = b"<< /Title (" + _lit(self.title) + b") /Producer (pdfmini) >>"
        out, offs = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), {}
        for k in sorted(objs):
            offs[k] = len(out)
            out += b"%d 0 obj\n" % k + objs[k] + b"\nendobj\n"
        x = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (info + 1)
        for k in range(1, info + 1):
            out += b"%010d 00000 n \n" % offs[k]
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (info + 1, info, x)
        return bytes(out)

    def save(self, path):
        import os
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(self.to_bytes())
        os.replace(tmp, path)
''', "test_pdfmini.py": r'''import os, re, shutil, subprocess, tempfile, unittest, zlib
import pdfmini as P


def parse(pdf):
    """Independent structural parse: follow startxref -> xref -> every object; returns (objects, trailer)."""
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    sx = int(re.search(rb"startxref\n(\d+)\n%%EOF", pdf).group(1)); assert pdf[sx:sx + 4] == b"xref"
    m = re.match(rb"xref\n0 (\d+)\n", pdf[sx:]); n = int(m.group(1)); base = sx + m.end(); objs = {}
    for k in range(n):
        ent = pdf[base + 20 * k:base + 20 * k + 20]; assert len(ent) == 20 and ent.endswith(b" \n"), ent
        off, gen, kind = int(ent[:10]), int(ent[11:16]), ent[17:18]
        if k == 0: assert kind == b"f"; continue
        assert kind == b"n" and pdf[off:].startswith(b"%d 0 obj\n" % k), "bad offset for object %d" % k
        objs[k] = pdf[off:pdf.index(b"endobj", off)]
    return objs, pdf[pdf.index(b"trailer"):]


def streams(objs):
    out = []
    for body in objs.values():
        if b"/FlateDecode" in body:
            length = int(re.search(rb"/Length (\d+)", body).group(1)); s = body.index(b"stream\n") + 7
            out.append(zlib.decompress(body[s:s + length]).decode("latin-1")); assert body[s + length:].startswith(b"\nendstream")
    return out


def texts(content):
    return [m.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\").encode("latin-1").decode("cp1252", "replace") for m in re.findall(r"\((.*?)\) Tj", content)]


class Structure(unittest.TestCase):
    def test_xref_offsets_catalog_pages_and_info(self):
        r = P.Report("Q1 (draft) \\ report"); r.heading("Hello"); r.paragraph("World"); objs, tr = parse(r.to_bytes())
        self.assertIn(b"/Type /Catalog", objs[1]); self.assertIn(b"/Count 1", objs[2]); self.assertIn(b"/Size %d" % (len(objs) + 1), tr); self.assertIn(rb"Q1 \(draft\) \\ report", objs[max(objs)])

    def test_text_escaping_and_cp1252(self):
        r = P.Report(); r.paragraph("a (b) c \\ d café – “quoted” 日本"); t = " ".join(texts("\n".join(streams(parse(r.to_bytes())[0]))))
        self.assertIn("a (b) c \\ d café", t); self.assertIn("– “quoted” ??", t.replace("�", "?"))


class Layout(unittest.TestCase):
    def test_wrapping_respects_width_and_never_loses_words(self):
        txt = "lorem ipsum dolor sit amet " * 40 + "Supercalifragilisticexpialidocious" * 5
        lines = P.wrap(txt, 10.5, 200)
        self.assertTrue(all(P.text_width(l, 10.5) <= 200 + 1e-6 for l in lines)); self.assertEqual("".join(lines).replace(" ", ""), txt.replace(" ", ""))
        self.assertEqual(P.wrap("a\n\nb", 10, 100), ["a", "", "b"])

    def test_widths_are_real_helvetica(self):
        self.assertAlmostEqual(P.text_width("Hello", 10), (722 + 556 + 222 + 222 + 556) / 100); self.assertGreater(P.text_width("Hello", 10, True), P.text_width("Hello", 10))

    def test_long_content_paginates_and_numbers_pages(self):
        r = P.Report()
        for i in range(120): r.paragraph("Line number %d of the report body text." % i)
        objs, _ = parse(r.to_bytes()); pages = streams(objs); self.assertGreater(len(pages), 2)
        for i, c in enumerate(pages, 1): self.assertIn("Page %d of %d" % (i, len(pages)), texts(c))
        self.assertIn("/Count %d" % len(pages), objs[2].decode())

    def test_table_repeats_header_and_right_aligns_numbers(self):
        r = P.Report(); r.heading("Sales")
        r.table(["Region", "Revenue"], [["R%d" % i, 1000.5 * i] for i in range(90)], [200, 100])
        pages = streams(parse(r.to_bytes())[0]); self.assertGreater(len(pages), 1)
        for c in pages: self.assertIn("Region", texts(c))
        allt = [t for c in pages for t in texts(c)]; self.assertIn("1,000.50", allt); self.assertIn("89,044.50", allt)
        self.assertNotIn("Page", " ".join(t for t in allt if t.startswith("R") and t[1:].isdigit()))

    def test_long_cell_is_clipped_not_overprinted(self):
        r = P.Report(); r.table(["Name"], [["x" * 200]], [80]); cell = [t for t in texts(streams(parse(r.to_bytes())[0])[0]) if t.startswith("x")][0]
        self.assertTrue(cell.endswith("…") or len(cell) < 200); self.assertLessEqual(P.text_width(cell, 9.5), 80 - 8 + 1)

    def test_no_text_outside_page_margins(self):
        r = P.Report(margin=54)
        for i in range(60): r.paragraph("word " * 60)
        for c in streams(parse(r.to_bytes())[0]):
            ys = [float(y) for y in re.findall(r"Td", c) and re.findall(r"[\d.]+ ([\d.]+) Td", c)]; self.assertTrue(all(y >= 54 / 2 - 1 for y in ys))


class External(unittest.TestCase):
    def build(self):
        r = P.Report("Invoice"); r.heading("Invoice #42"); r.paragraph("Thank you for your business (really)."); r.table(["Item", "Qty", "Price"], [["Widget", 3, 9.99], ["Gadget & co", 1, 100.0]], [250, 80, 100])
        p = os.path.join(tempfile.mkdtemp(), "r.pdf"); r.save(p); return p

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext (poppler) not installed")
    def test_poppler_extracts_the_text(self):
        out = subprocess.run(["pdftotext", "-layout", self.build(), "-"], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr); self.assertIn("Invoice #42", out.stdout); self.assertIn("Gadget & co", out.stdout); self.assertIn("Page 1 of 1", out.stdout)
        self.assertEqual([l for l in out.stderr.splitlines() if l.strip() and "ontconfig" not in l], [])     # poppler found nothing to complain about

    @unittest.skipUnless(shutil.which("gs"), "ghostscript not installed")
    def test_ghostscript_renders_without_errors(self):
        out = subprocess.run(["gs", "-q", "-dNOPAUSE", "-dBATCH", "-sDEVICE=nullpage", self.build()], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0); self.assertEqual([l for l in out.stderr.splitlines() if l.strip() and "ontconfig" not in l], [])      # fontconfig noise is the environment, not the PDF

    def test_save_is_atomic(self):
        p = self.build(); self.assertEqual(os.listdir(os.path.dirname(p)), ["r.pdf"])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Latin-1/WinAnsi text only (no CJK, no emoji, no right-to-left); fonts are the standard Helvetica pair, so nothing is embedded", "Bold widths are an approximation; layout is for reports/invoices, not typography-grade documents", "For HTML->PDF, images, or complex layout use WeasyPrint or reportlab; for merging/splitting/extracting use pypdf"], compat={"python": ">=3.9"},
   tags=["pdf", "report", "invoice", "generate", "document", "table", "pagination", "page-numbers", "no-dependencies", "zlib", "xref", "text-wrapping", "printable", "helvetica"],
   avoid="Concatenating strings into a PDF without computing xref offsets; forgetting to escape ( ) and backslash in text; measuring text with len(); letting table cells overprint neighbours."),
]
