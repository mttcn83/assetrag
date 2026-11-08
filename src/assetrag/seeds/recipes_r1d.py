"""Round-1 persona recipes, part D: Go stream reading, Python data helpers, small UI state machines (TypeScript) and
infrastructure/config files with *structural* tests (the sandbox has no terraform/ansible/renovate binaries, so the files are
parsed and their properties asserted - the evidence says exactly that)."""
from .recipes import RC, UT, GOT
from .recipes_algo import NODE_TS, TSA
from .recipes_cfg import PYT, STRUCT

R1D = [
RC("go-stream-lines-stdin", "Read stdin line by line efficiently: long lines, CRLF, no trailing newline, cancellation (Go)", "go",
   "A Go filter/CLI processes huge piped input; bufio.Scanner silently fails on long lines and loading everything in memory hurts.",
   "bufio.Reader.ReadLine-style loop through a Scanner with a raised buffer cap and an explicit ErrTooLong error, CRLF trimmed, last line without newline kept, context cancellation checked between lines, callback style so memory stays flat, plus a worker-friendly Lines iterator.",
   {"lines.go": '''package main

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
)

// ErrLineTooLong is returned instead of Scanner's silent stop when a single line exceeds max bytes.
var ErrLineTooLong = errors.New("line too long")

// EachLine calls fn for every line (without \\n or \\r\\n). Memory use is O(max line), not O(input).
func EachLine(ctx context.Context, r io.Reader, max int, fn func(n int, line []byte) error) error {
	sc := bufio.NewScanner(r)
	initial := 64 * 1024
	if max < initial {
		initial = max // Scanner's limit is the LARGER of max and the initial capacity, so keep the initial buffer small
	}
	sc.Buffer(make([]byte, 0, initial), max)
	n := 0
	for sc.Scan() {
		if err := ctx.Err(); err != nil {
			return err
		}
		n++
		if err := fn(n, bytes.TrimSuffix(sc.Bytes(), []byte("\\r"))); err != nil {
			return err
		}
	}
	if err := sc.Err(); err != nil {
		if errors.Is(err, bufio.ErrTooLong) {
			return fmt.Errorf("%w (line %d exceeds %d bytes)", ErrLineTooLong, n+1, max)
		}
		return err
	}
	return nil
}

// Lines returns all lines as strings - convenient for small inputs and tests only.
func Lines(r io.Reader, max int) ([]string, error) {
	var out []string
	err := EachLine(context.Background(), r, max, func(_ int, l []byte) error { out = append(out, string(l)); return nil })
	return out, err
}
'''
    , "lines_test.go": '''package main

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestLinesVariants(t *testing.T) {
	cases := map[string][]string{
		"a\\nb\\nc\\n":   {"a", "b", "c"},
		"a\\r\\nb\\r\\n": {"a", "b"},
		"a\\nb":         {"a", "b"},
		"":              nil,
		"\\n\\nx":         {"", "", "x"},
	}
	for in, want := range cases {
		got, err := Lines(strings.NewReader(in), 1024)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Errorf("%q: got %q err %v want %q", in, got, err, want)
		}
	}
}

func TestLongLineIsAnErrorNotSilence(t *testing.T) {
	_, err := Lines(strings.NewReader("ok\\n"+strings.Repeat("x", 5000)+"\\nafter\\n"), 1000)
	if !errors.Is(err, ErrLineTooLong) || !strings.Contains(err.Error(), "line 2") {
		t.Fatalf("want ErrLineTooLong naming line 2, got %v", err)
	}
	got, err := Lines(strings.NewReader(strings.Repeat("x", 500000)+"\\n"), 1<<20)
	if err != nil || len(got) != 1 || len(got[0]) != 500000 {
		t.Fatalf("raised cap must accept long lines: %v", err)
	}
}

func TestCancellationStopsEarly(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	seen := 0
	err := EachLine(ctx, strings.NewReader(strings.Repeat("l\\n", 100)), 1024, func(n int, _ []byte) error {
		seen = n
		if n == 3 {
			cancel()
		}
		return nil
	})
	if !errors.Is(err, context.Canceled) || seen != 3 {
		t.Fatalf("seen=%d err=%v", seen, err)
	}
}

func TestCallbackErrorStops(t *testing.T) {
	stop := errors.New("stop")
	err := EachLine(context.Background(), strings.NewReader("a\\nb\\n"), 10, func(n int, _ []byte) error { return stop })
	if err != stop {
		t.Fatal("callback error must propagate")
	}
}
'''
    , "main.go": "package main\n\nfunc main() {}\n"}, GOT, assumptions=["Text input; for binary framing use bufio.Reader.Read with your own delimiter"], compat={"go": ">=1.21"},
   tags=["cli", "stdin", "pipe", "bufio", "scanner", "streaming", "lines", "go", "unix-filter"],
   avoid="bufio.Scanner with the default 64 KB limit (stops silently); io.ReadAll on stdin for large inputs; ignoring CRLF."),

RC("py-svg-histogram-scatter", "Dependency-free SVG histogram and scatter plot with axes and ticks", "python",
   "A script, report generator or CI job needs a quick chart as a file and matplotlib is overkill or unavailable.",
   "Nice-number axis ticks, histogram binning (Freedman-Diaconis or fixed bins, right-open with the last bin closed), linear scales into an SVG viewBox, escaped labels, output validated as XML in tests; deterministic so charts diff cleanly.",
   {"svgplot.py": '''"""Tiny SVG charts. No dependencies; output is deterministic text."""
import math
from xml.sax.saxutils import escape


def nice_ticks(lo, hi, n=5):
    if lo == hi: lo, hi = lo - 1, hi + 1
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.5:
        ticks.append(round(v, 10)); v += step
    return ticks


def histogram(values, bins=None):
    """-> (edges, counts). Bins are [a, b) except the last which is [a, b]."""
    xs = sorted(v for v in values if v is not None and not math.isnan(v))
    if not xs: return [], []
    lo, hi = xs[0], xs[-1]
    if bins is None:                                           # Freedman-Diaconis
        q = lambda p: xs[min(len(xs) - 1, int(p * (len(xs) - 1)))]
        iqr = q(0.75) - q(0.25)
        bins = max(1, math.ceil((hi - lo) / (2 * iqr * len(xs) ** (-1 / 3)))) if iqr > 0 and hi > lo else 10
    if hi == lo: hi = lo + 1
    w = (hi - lo) / bins
    edges = [lo + i * w for i in range(bins + 1)]
    counts = [0] * bins
    for v in xs:
        counts[min(bins - 1, int((v - lo) / w))] += 1
    return edges, counts


def _frame(width, height, margin, xt, yt, xs, ys, title, xlabel, ylabel):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" font-family="sans-serif" font-size="11">',
           f'<rect width="{width}" height="{height}" fill="white"/>']
    if title: out.append(f'<text x="{width / 2}" y="16" text-anchor="middle" font-size="14">{escape(title)}</text>')
    for t in xt: out.append(f'<line x1="{xs(t):.1f}" x2="{xs(t):.1f}" y1="{margin}" y2="{height - margin}" stroke="#eee"/><text x="{xs(t):.1f}" y="{height - margin + 14}" text-anchor="middle">{t:g}</text>')
    for t in yt: out.append(f'<line y1="{ys(t):.1f}" y2="{ys(t):.1f}" x1="{margin}" x2="{width - margin}" stroke="#eee"/><text x="{margin - 6}" y="{ys(t) + 4:.1f}" text-anchor="end">{t:g}</text>')
    out.append(f'<rect x="{margin}" y="{margin}" width="{width - 2 * margin}" height="{height - 2 * margin}" fill="none" stroke="#333"/>')
    if xlabel: out.append(f'<text x="{width / 2}" y="{height - 6}" text-anchor="middle">{escape(xlabel)}</text>')
    if ylabel: out.append(f'<text transform="translate(12 {height / 2}) rotate(-90)" text-anchor="middle">{escape(ylabel)}</text>')
    return out


def histogram_svg(values, bins=None, title="", xlabel="", ylabel="count", width=480, height=320, margin=44):
    edges, counts = histogram(values, bins)
    if not counts: return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 40"><text x="8" y="24">no data</text></svg>'
    xt, yt = nice_ticks(edges[0], edges[-1]), nice_ticks(0, max(counts))
    x0, x1, y1 = min(xt[0], edges[0]), max(xt[-1], edges[-1]), yt[-1]
    xs = lambda v: margin + (v - x0) / (x1 - x0) * (width - 2 * margin)
    ys = lambda v: height - margin - v / y1 * (height - 2 * margin)
    out = _frame(width, height, margin, xt, yt, xs, ys, title, xlabel, ylabel)
    for i, c in enumerate(counts):
        out.append(f'<rect x="{xs(edges[i]):.1f}" y="{ys(c):.1f}" width="{max(0.5, xs(edges[i + 1]) - xs(edges[i]) - 1):.1f}" height="{height - margin - ys(c):.1f}" fill="#4c78a8"><title>{edges[i]:g} to {edges[i + 1]:g}: {c}</title></rect>')
    return "\\n".join(out + ["</svg>"])


def scatter_svg(points, title="", xlabel="", ylabel="", width=480, height=320, margin=44, r=3):
    pts = [(x, y) for x, y in points if None not in (x, y) and not (math.isnan(x) or math.isnan(y))]
    if not pts: return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 40"><text x="8" y="24">no data</text></svg>'
    xt, yt = nice_ticks(min(p[0] for p in pts), max(p[0] for p in pts)), nice_ticks(min(p[1] for p in pts), max(p[1] for p in pts))
    xs = lambda v: margin + (v - xt[0]) / (xt[-1] - xt[0]) * (width - 2 * margin)
    ys = lambda v: height - margin - (v - yt[0]) / (yt[-1] - yt[0]) * (height - 2 * margin)
    out = _frame(width, height, margin, xt, yt, xs, ys, title, xlabel, ylabel)
    out += [f'<circle cx="{xs(x):.1f}" cy="{ys(y):.1f}" r="{r}" fill="#e45756" fill-opacity="0.7"/>' for x, y in pts]
    return "\\n".join(out + ["</svg>"])
'''
    , "test_svgplot.py": '''import unittest, math
import xml.etree.ElementTree as ET
from svgplot import nice_ticks, histogram, histogram_svg, scatter_svg


class T(unittest.TestCase):
    def test_nice_ticks(self):
        self.assertEqual(nice_ticks(0, 10), [0, 2, 4, 6, 8, 10]); self.assertEqual(nice_ticks(3, 3)[0] < 3 < nice_ticks(3, 3)[-1], True)
        t = nice_ticks(0.13, 0.97); self.assertLessEqual(t[0], 0.13); self.assertGreaterEqual(t[-1], 0.97)

    def test_histogram_counts_everything_once(self):
        vals = [1, 2, 2, 3, 3, 3, 4, 4, 4, 4, 10]
        edges, counts = histogram(vals, bins=3)
        self.assertEqual(sum(counts), len(vals)); self.assertEqual(len(edges), 4); self.assertEqual(counts[-1], 1)      # max value lands in the last bin

    def test_histogram_edge_cases(self):
        self.assertEqual(histogram([]), ([], [])); e, c = histogram([5, 5, 5]); self.assertEqual(sum(c), 3)
        e, c = histogram([1, float("nan"), None, 2]); self.assertEqual(sum(c), 2)

    def test_auto_bins_reasonable(self):
        e, c = histogram([i % 17 for i in range(1000)]); self.assertTrue(3 <= len(c) <= 100)

    def test_svgs_are_valid_xml_and_escape(self):
        s = histogram_svg([1, 2, 2, 3, 9], title="A <b> & c")
        root = ET.fromstring(s); self.assertTrue(root.tag.endswith("svg")); self.assertIn("A &lt;b&gt; &amp; c", s)
        p = scatter_svg([(1, 2), (2, 4), (3, 5), (None, 1)], xlabel="x", ylabel="y"); ET.fromstring(p); self.assertEqual(p.count("<circle"), 3)

    def test_empty_data_has_a_message(self):
        for s in (histogram_svg([]), scatter_svg([])): self.assertIn("no data", s); ET.fromstring(s)

    def test_deterministic(self):
        self.assertEqual(histogram_svg([1, 2, 3, 4]), histogram_svg([1, 2, 3, 4]))


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Static charts; use matplotlib/plotly for interactivity or large point counts"], compat={"python": ">=3.9"},
   tags=["plot", "chart", "histogram", "scatter", "svg", "data-science", "report", "visualization", "matplotlib-free"],
   avoid="Hand-picking tick values; dropping the max value from the last histogram bin; unescaped labels in SVG."),

RC("py-tokenize-wordfreq", "Unicode-aware tokenizer and word frequency with stopwords, n-grams and stable ties", "python",
   "Counting words, finding keywords or prepping text for search/ML: naive split() breaks on punctuation, case, accents and apostrophes.",
   "Unicode NFKC + casefold, tokens are runs of letters/digits with inner apostrophes/hyphens kept, optional stopword set, deterministic top-k (count desc then token asc), bigrams, and a streaming counter for large files.",
   {"words.py": '''"""Tokenise and count. Deterministic output; no dependencies."""
import re, unicodedata
from collections import Counter

TOKEN = re.compile(r"[^\\W_]+(?:['\\u2019-][^\\W_]+)*", re.UNICODE)
STOP = frozenset("a an and are as at be by for from has he in is it its of on or that the to was were will with".split())


def tokens(text, stop=frozenset(), min_len=1):
    t = unicodedata.normalize("NFKC", text).casefold().replace("\\u2019", "'")
    return [w for w in TOKEN.findall(t) if len(w) >= min_len and w not in stop]


def top(counter, k=10):
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:k]


def word_freq(text, k=10, stop=STOP, min_len=2):
    return top(Counter(tokens(text, stop, min_len)), k)


def bigrams(toks):
    return list(zip(toks, toks[1:]))


def count_stream(lines, stop=STOP, min_len=2):
    c = Counter()
    for line in lines:
        c.update(tokens(line, stop, min_len))
    return c
'''
    , "test_words.py": '''import unittest
from words import tokens, word_freq, bigrams, count_stream, top
from collections import Counter


class T(unittest.TestCase):
    def test_case_punctuation_and_apostrophes(self):
        self.assertEqual(tokens("Don't STOP, can\\u2019t stop! well-known."), ["don't", "stop", "can't", "stop", "well-known"])

    def test_unicode_and_accents_kept(self):
        self.assertEqual(tokens("Caf\\u00e9 \\u00dcber \\u3053\\u3093\\u306b\\u3061\\u306f 42"), ["caf\\u00e9", "\\u00fcber", "\\u3053\\u3093\\u306b\\u3061\\u306f", "42"])
        self.assertEqual(tokens("\\uff21\\uff22\\uff23"), ["abc"])            # full-width folded

    def test_underscores_and_symbols_split(self):
        self.assertEqual(tokens("snake_case $5 #tag"), ["snake", "case", "5", "tag"])

    def test_stopwords_and_min_len(self):
        self.assertEqual(word_freq("the cat and the hat and a cat", k=5), [("cat", 2), ("hat", 1)])

    def test_ties_are_deterministic(self):
        self.assertEqual(top(Counter("b a c b a c".split()), 3), [("a", 2), ("b", 2), ("c", 2)])

    def test_bigrams(self):
        self.assertEqual(bigrams(["a", "b", "c"]), [("a", "b"), ("b", "c")]); self.assertEqual(bigrams(["a"]), [])

    def test_streaming_matches_whole(self):
        text = "red fish blue fish\\none fish two fish"
        self.assertEqual(count_stream(text.splitlines()), Counter(tokens(text, min_len=2, stop=__import__("words").STOP)))
        self.assertEqual(count_stream(text.splitlines())["fish"], 4)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["English stopwords; no stemming/lemmatisation (use spaCy/NLTK for that)", "CJK text needs a segmenter (runs of characters are one token here)"], compat={"python": ">=3.9"},
   tags=["nlp", "tokenize", "word-count", "text", "frequency", "keywords", "data-science", "search", "ngram"],
   avoid="str.split() on raw text; sorting ties by hash order; lowercase() instead of casefold()."),

# ------------------------------------------------------------------ UI state logic (TypeScript)
RC("ts-responsive-nav-state", "Responsive navbar state: mobile menu open/close, Escape, outside click, resize, scroll lock (TypeScript)", "typescript",
   "A site header with a hamburger menu needs the behaviour right: aria-expanded, Escape closes, resize to desktop resets, no stuck scroll lock.",
   "A tiny reducer-style controller with open/toggle/close/resize/escape/route-change events, derived ARIA attributes, body scroll-lock counter (safe with several overlays), focus return target, and a breakpoint check from a matchMedia-like object.",
   {"nav.ts": r'''export interface NavState { open: boolean; lastTrigger: string | null }
export type NavEvent = { type: 'toggle'; trigger?: string } | { type: 'close' } | { type: 'escape' } | { type: 'resize'; width: number } | { type: 'route' } | { type: 'outside'; insideMenu: boolean }
export const BREAKPOINT = 768

export const initial = (): NavState => ({ open: false, lastTrigger: null })

export function reduce(s: NavState, e: NavEvent): NavState {
  switch (e.type) {
    case 'toggle': return { open: !s.open, lastTrigger: e.trigger ?? s.lastTrigger }
    case 'close': case 'route': case 'escape': return s.open ? { ...s, open: false } : s
    case 'resize': return e.width >= BREAKPOINT && s.open ? { ...s, open: false } : s            // desktop layout: menu is always visible
    case 'outside': return e.insideMenu || !s.open ? s : { ...s, open: false }
  }
}

export const triggerAttrs = (s: NavState, menuId: string) => ({ 'aria-expanded': String(s.open), 'aria-controls': menuId, 'aria-label': s.open ? 'Close menu' : 'Open menu' })
export const menuAttrs = (s: NavState) => ({ hidden: !s.open, 'data-state': s.open ? 'open' : 'closed' })

/** Reference-counted scroll lock: two overlays open at once must not unlock the page when the first closes. */
export class ScrollLock {
  private n = 0
  private apply: (locked: boolean) => void
  constructor(apply: (locked: boolean) => void) { this.apply = apply }
  lock() { if (this.n++ === 0) this.apply(true) }
  unlock() { if (this.n > 0 && --this.n === 0) this.apply(false) }
  get locked() { return this.n > 0 }
}

/** Escape should return focus to the button that opened the menu. */
export const focusTargetOnClose = (before: NavState, after: NavState) => (before.open && !after.open ? before.lastTrigger : null)
''', "nav.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { reduce, initial, triggerAttrs, menuAttrs, ScrollLock, focusTargetOnClose } from './nav.ts'

test('toggle, escape, route change and outside clicks', () => {
  let s = reduce(initial(), { type: 'toggle', trigger: 'burger' }); assert.equal(s.open, true)
  assert.equal(reduce(s, { type: 'outside', insideMenu: true }).open, true); assert.equal(reduce(s, { type: 'outside', insideMenu: false }).open, false)
  assert.equal(reduce(s, { type: 'escape' }).open, false); assert.equal(reduce(s, { type: 'route' }).open, false)
})
test('resizing to desktop closes the menu, resizing within mobile does not', () => {
  const open = reduce(initial(), { type: 'toggle' }); assert.equal(reduce(open, { type: 'resize', width: 1024 }).open, false); assert.equal(reduce(open, { type: 'resize', width: 500 }).open, true)
})
test('no-op events keep object identity (cheap re-renders)', () => { const s = initial(); assert.equal(reduce(s, { type: 'escape' }), s); assert.equal(reduce(s, { type: 'resize', width: 2000 }), s) })
test('aria attributes reflect state', () => {
  const s = reduce(initial(), { type: 'toggle' }); assert.deepEqual(triggerAttrs(s, 'menu'), { 'aria-expanded': 'true', 'aria-controls': 'menu', 'aria-label': 'Close menu' }); assert.equal(menuAttrs(initial()).hidden, true)
})
test('scroll lock is reference counted', () => {
  const calls: boolean[] = []; const l = new ScrollLock((v) => calls.push(v)); l.lock(); l.lock(); l.unlock(); assert.equal(l.locked, true); l.unlock(); assert.deepEqual(calls, [true, false]); l.unlock(); assert.deepEqual(calls, [true, false])
})
test('focus returns to the trigger on close only', () => {
  const o = reduce(initial(), { type: 'toggle', trigger: 'burger' }); assert.equal(focusTargetOnClose(o, reduce(o, { type: 'escape' })), 'burger'); assert.equal(focusTargetOnClose(initial(), o), null)
})
'''}, NODE_TS, assumptions=TSA + ["Markup/CSS comes from your component library; this is the behaviour layer for React/Vue/Svelte/vanilla"], compat={"node": ">=22.18"},
   tags=["navbar", "navigation", "mobile-menu", "hamburger", "accessibility", "aria", "header", "ui", "responsive"],
   avoid="Toggling a CSS class without aria-expanded; a boolean scroll lock shared by several overlays."),

RC("ts-skeleton-delay", "Loading state timing: show skeletons only after a delay and keep them a minimum time (TypeScript)", "typescript",
   "Skeleton placeholders flash for 80 ms on fast loads and make the UI feel worse; spinners that vanish instantly flicker.",
   "A small controller: `start()` schedules the skeleton after `delayMs` (so fast responses never show it), once shown it stays at least `minMs`, `stop()` resolves after the minimum has elapsed; timers injected; errors stop immediately so the error state is not delayed.",
   {"loading.ts": r'''export interface Clock { now(): number; set(fn: () => void, ms: number): unknown; clear(h: unknown): void }
export type Phase = 'idle' | 'pending' | 'skeleton'

export class LoadingGate {
  phase: Phase = 'idle'
  private shownAt = 0
  private showTimer: unknown = null
  private hideTimer: unknown = null
  private clock: Clock
  private delayMs: number
  private minMs: number
  private onChange: (p: Phase) => void
  constructor(onChange: (p: Phase) => void, clock: Clock, delayMs = 200, minMs = 400) { this.onChange = onChange; this.clock = clock; this.delayMs = delayMs; this.minMs = minMs }

  private go(p: Phase) { if (this.phase !== p) { this.phase = p; this.onChange(p) } }

  start() {
    if (this.phase !== 'idle') return
    this.go('pending')
    this.showTimer = this.clock.set(() => { this.shownAt = this.clock.now(); this.go('skeleton') }, this.delayMs)
  }
  /** Data arrived. Content replaces the skeleton immediately unless the skeleton just appeared. */
  stop(opts: { error?: boolean } = {}) {
    this.clock.clear(this.showTimer); this.showTimer = null
    if (this.phase !== 'skeleton' || opts.error) { this.clock.clear(this.hideTimer); return this.go('idle') }
    const remaining = this.minMs - (this.clock.now() - this.shownAt)
    if (remaining <= 0) return this.go('idle')
    this.hideTimer = this.clock.set(() => this.go('idle'), remaining)
  }
}
''', "loading.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { LoadingGate, type Phase } from './loading.ts'

function rig(delay = 200, min = 400) {
  let t = 0, id = 0; const timers = new Map<number, { at: number; fn: () => void }>(); const log: Phase[] = []
  const clock = { now: () => t, set: (fn: () => void, ms: number) => { timers.set(++id, { at: t + ms, fn }); return id }, clear: (h: unknown) => void timers.delete(h as number) }
  const gate = new LoadingGate((p) => log.push(p), clock, delay, min)
  const advance = (ms: number) => {                               // step through timers in order so now() is right when each fires
    const target = t + ms
    for (;;) {
      const next = [...timers].filter(([, v]) => v.at <= target).sort((x, y) => x[1].at - y[1].at)[0]
      if (!next) break
      t = next[1].at; timers.delete(next[0]); next[1].fn()
    }
    t = target
  }
  return { gate, advance, log }
}

test('fast loads never show the skeleton', () => { const { gate, advance, log } = rig(); gate.start(); advance(150); gate.stop(); advance(1000); assert.deepEqual(log, ['pending', 'idle']) })
test('slow loads show it after the delay', () => { const { gate, advance, log } = rig(); gate.start(); advance(250); assert.deepEqual(log, ['pending', 'skeleton']) })
test('once shown, it stays for the minimum time', () => {
  const { gate, advance, log } = rig(); gate.start(); advance(200); advance(50); gate.stop(); assert.equal(gate.phase, 'skeleton'); advance(349); assert.equal(gate.phase, 'skeleton'); advance(1); assert.equal(gate.phase, 'idle'); assert.deepEqual(log, ['pending', 'skeleton', 'idle'])
})
test('long loads hide immediately when data arrives', () => { const { gate, advance } = rig(); gate.start(); advance(2000); gate.stop(); assert.equal(gate.phase, 'idle') })
test('errors skip the minimum time', () => { const { gate, advance } = rig(); gate.start(); advance(210); gate.stop({ error: true }); assert.equal(gate.phase, 'idle') })
test('start is idempotent and restartable', () => { const { gate, advance, log } = rig(); gate.start(); gate.start(); advance(10); gate.stop(); gate.start(); assert.deepEqual(log, ['pending', 'idle', 'pending']) })
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["skeleton", "loading", "spinner", "ux", "ui", "react", "suspense", "placeholder", "performance"],
   avoid="Showing a loader instantly on every fetch; removing a skeleton after 60 ms."),

RC("ts-newsletter-signup", "Newsletter signup with double opt-in tokens, unsubscribe links and disposable-domain checks (TypeScript)", "typescript",
   "A site footer collects emails and must not become a spam relay or violate consent: confirm before sending, one-click unsubscribe.",
   "Normalise and validate the address, signed time-limited confirmation tokens (HMAC), idempotent subscribe (re-subscribing an unconfirmed address re-sends rather than duplicating), unsubscribe tokens that never expire, a small disposable-domain list, and no user enumeration in responses.",
   {"signup.ts": r'''import { createHmac, timingSafeEqual } from 'node:crypto'

const DISPOSABLE = new Set(['mailinator.com', 'guerrillamail.com', '10minutemail.com', 'tempmail.com', 'trashmail.com'])
export const normalise = (e: string) => e.normalize('NFKC').trim().toLowerCase()
export function validate(raw: string): string | null {
  const e = normalise(raw)
  if (e.length > 254 || !/^[^\s@]+@[^\s@.]+(\.[^\s@.]+)+$/.test(e)) return null
  return DISPOSABLE.has(e.split('@')[1]) ? null : e
}

const b64 = (b: Buffer) => b.toString('base64url')
export function makeToken(secret: string, purpose: 'confirm' | 'unsub', email: string, issuedAt: number): string {
  const body = b64(Buffer.from(JSON.stringify([purpose, email, issuedAt])))
  return body + '.' + b64(createHmac('sha256', secret).update(body).digest())
}
export function readToken(secret: string, token: string, purpose: 'confirm' | 'unsub', now: number, maxAgeMs = 48 * 3600_000): string | null {
  const [body, sig] = token.split('.'); if (!body || !sig) return null
  const want = createHmac('sha256', secret).update(body).digest(), got = Buffer.from(sig, 'base64url')
  if (got.length !== want.length || !timingSafeEqual(got, want)) return null
  try {
    const [p, email, iat] = JSON.parse(Buffer.from(body, 'base64url').toString())
    if (p !== purpose) return null
    if (purpose === 'confirm' && (now - iat > maxAgeMs || iat > now + 60_000)) return null          // unsubscribe links must keep working
    return email
  } catch { return null }
}

export interface Subscriber { email: string; confirmed: boolean }
export class List {
  private subs = new Map<string, Subscriber>()
  private secret: string
  outbox: { to: string; kind: 'confirm'; link: string }[] = []
  constructor(secret: string) { this.secret = secret }
  /** Same response for new, existing and invalid-but-plausible addresses: do not reveal who is subscribed. */
  subscribe(raw: string, now: number, base = 'https://site.dev') {
    const email = validate(raw); if (!email) return { ok: false as const, message: 'Please enter a valid email address.' }
    const cur = this.subs.get(email)
    if (!cur) this.subs.set(email, { email, confirmed: false })
    if (!cur?.confirmed) this.outbox.push({ to: email, kind: 'confirm', link: `${base}/confirm?t=${makeToken(this.secret, 'confirm', email, now)}` })
    return { ok: true as const, message: 'Check your inbox to confirm your subscription.' }
  }
  confirm(token: string, now: number) { const e = readToken(this.secret, token, 'confirm', now); if (!e || !this.subs.has(e)) return false; this.subs.get(e)!.confirmed = true; return true }
  unsubscribeLink(email: string, now: number, base = 'https://site.dev') { return `${base}/unsubscribe?t=${makeToken(this.secret, 'unsub', email, now)}` }
  unsubscribe(token: string) { const e = readToken(this.secret, token, 'unsub', 0); return e ? this.subs.delete(e) || true : false }
  recipients() { return [...this.subs.values()].filter((s) => s.confirmed).map((s) => s.email) }
}
''', "signup.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { validate, makeToken, readToken, List } from './signup.ts'

const token = (link: string) => new URL(link).searchParams.get('t')!

test('validation normalises and rejects junk and disposable domains', () => {
  assert.equal(validate('  Ada@Example.COM '), 'ada@example.com'); for (const bad of ['a@b', 'x', 'a@mailinator.com', 'a b@c.de', '']) assert.equal(validate(bad), null, bad)
})
test('tokens are tamper-proof, purpose-bound and expire (confirm only)', () => {
  const t = makeToken('s', 'confirm', 'a@b.co', 1000)
  assert.equal(readToken('s', t, 'confirm', 2000), 'a@b.co'); assert.equal(readToken('other', t, 'confirm', 2000), null); assert.equal(readToken('s', t, 'unsub', 2000), null)
  assert.equal(readToken('s', t, 'confirm', 1000 + 49 * 3600_000), null); assert.equal(readToken('s', t.slice(0, -2) + 'xx', 'confirm', 2000), null); assert.equal(readToken('s', 'garbage', 'confirm', 0), null)
  assert.equal(readToken('s', makeToken('s', 'unsub', 'a@b.co', 1), 'unsub', 1e15), 'a@b.co')            // unsubscribe never expires
})
test('double opt-in: nobody is mailed until they confirm', () => {
  const l = new List('s'); l.subscribe('a@b.co', 0); assert.deepEqual(l.recipients(), []); assert.equal(l.confirm(token(l.outbox[0].link), 10), true); assert.deepEqual(l.recipients(), ['a@b.co'])
})
test('re-subscribing resends confirmation but never duplicates; confirmed addresses are not re-mailed; responses do not leak', () => {
  const l = new List('s'); const r1 = l.subscribe('a@b.co', 0), r2 = l.subscribe('A@B.co', 5); assert.equal(l.outbox.length, 2); assert.deepEqual(r1, r2)
  l.confirm(token(l.outbox[1].link), 6); const r3 = l.subscribe('a@b.co', 7); assert.equal(l.outbox.length, 2); assert.deepEqual(r3, r1); assert.equal(l.recipients().length, 1)
})
test('one-click unsubscribe', () => {
  const l = new List('s'); l.subscribe('a@b.co', 0); l.confirm(token(l.outbox[0].link), 1)
  assert.equal(l.unsubscribe(token(l.unsubscribeLink('a@b.co', 0))), true); assert.deepEqual(l.recipients(), []); assert.equal(l.unsubscribe('nope'), false)
})
test('invalid addresses get a helpful message', () => assert.equal(new List('s').subscribe('nope', 0).ok, false))
'''}, NODE_TS, assumptions=TSA + ["Sending mail and storing the list are out of scope; add List-Unsubscribe headers to every campaign", "Consent rules (GDPR/CAN-SPAM) vary - this implements double opt-in and one-click opt-out"], compat={"node": ">=22.18"},
   tags=["newsletter", "email", "signup", "double-opt-in", "gdpr", "unsubscribe", "footer", "marketing", "forms"],
   avoid="Single opt-in to unverified addresses; revealing whether an address is already subscribed; expiring unsubscribe links."),

# ------------------------------------------------------------------ infra / config
RC("cfg-renovate-config", "Renovate config: grouped updates, automerge safe bumps, pinned actions, security alerts (renovate.json)", "json",
   "Dependency PRs should be useful instead of noisy: group them, automerge low-risk ones, pin actions to digests, schedule them.",
   "Extend config:recommended, weekly schedule, group minor/patch per ecosystem, automerge dev-dependency and github-action minor/patch only after CI passes, pin GitHub Actions digests, never automerge majors, separate security updates that bypass the schedule, limit concurrent PRs. Includes the equivalent dependabot.yml.",
   {"renovate.json": '''{
  "$schema": "https://docs.renovatebot.com/renovate-schema.json",
  "extends": ["config:recommended", "helpers:pinGitHubActionDigests", ":semanticCommits"],
  "timezone": "UTC",
  "schedule": ["before 6am on monday"],
  "prConcurrentLimit": 5,
  "prHourlyLimit": 2,
  "labels": ["dependencies"],
  "packageRules": [
    {"description": "group minor and patch bumps per ecosystem", "matchUpdateTypes": ["minor", "patch"], "matchCurrentVersion": "!/^0/", "groupName": "non-major dependencies"},
    {"description": "automerge dev tooling minor/patch once CI is green", "matchDepTypes": ["devDependencies"], "matchUpdateTypes": ["minor", "patch"], "automerge": true},
    {"description": "automerge GitHub Actions minor/patch (digest pinned)", "matchManagers": ["github-actions"], "matchUpdateTypes": ["minor", "patch", "digest"], "automerge": true},
    {"description": "majors always get a human", "matchUpdateTypes": ["major"], "automerge": false, "labels": ["dependencies", "major"]}
  ],
  "vulnerabilityAlerts": {"labels": ["security"], "schedule": ["at any time"], "automerge": false},
  "lockFileMaintenance": {"enabled": true, "schedule": ["before 6am on the first day of the month"]}
}
''', ".github/dependabot.yml": '''version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: {interval: "weekly", day: "monday"}
    groups:
      actions: {patterns: ["*"]}
  - package-ecosystem: "npm"
    directory: "/"
    schedule: {interval: "weekly", day: "monday"}
    open-pull-requests-limit: 5
    groups:
      minor-and-patch: {update-types: ["minor", "patch"]}
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]
  - package-ecosystem: "gomod"
    directory: "/"
    schedule: {interval: "weekly", day: "monday"}
    groups:
      go-minor: {update-types: ["minor", "patch"]}
''', "test_cfg.py": '''import json, unittest, yaml


class T(unittest.TestCase):
    def setUp(self):
        self.r = json.load(open("renovate.json")); self.d = yaml.safe_load(open(".github/dependabot.yml"))

    def test_renovate_is_valid_and_extends_recommended(self):
        self.assertIn("config:recommended", self.r["extends"]); self.assertIn("helpers:pinGitHubActionDigests", self.r["extends"])

    def test_majors_never_automerge(self):
        rules = [p for p in self.r["packageRules"] if "major" in p.get("matchUpdateTypes", [])]
        self.assertTrue(rules and all(p.get("automerge") is False for p in rules))
        for p in self.r["packageRules"]:
            if p.get("automerge"): self.assertNotIn("major", p["matchUpdateTypes"])

    def test_automerge_only_for_low_risk_scopes(self):
        for p in self.r["packageRules"]:
            if p.get("automerge"): self.assertTrue(p.get("matchDepTypes") == ["devDependencies"] or p.get("matchManagers") == ["github-actions"], p["description"])

    def test_noise_controls_and_schedule(self):
        self.assertLessEqual(self.r["prConcurrentLimit"], 10); self.assertTrue(self.r["schedule"])
        self.assertEqual(self.r["vulnerabilityAlerts"]["schedule"], ["at any time"]); self.assertFalse(self.r["vulnerabilityAlerts"].get("automerge"))

    def test_dependabot_covers_actions_and_groups_updates(self):
        eco = {u["package-ecosystem"]: u for u in self.d["updates"]}
        self.assertIn("github-actions", eco); self.assertTrue(all("groups" in u for u in eco.values()))
        self.assertEqual(self.d["version"], 2)
        self.assertIn("version-update:semver-major", eco["npm"]["ignore"][0]["update-types"])


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=STRUCT + ["Renovate/Dependabot are not run in the sandbox; validate with `npx renovate-config-validator` and GitHub's config check", "Use either Renovate or Dependabot, not both, for the same ecosystem"], compat={"renovate": "current", "dependabot": "v2"},
   tags=["renovate", "dependabot", "dependencies", "automation", "ci", "github", "updates", "security", "devops"],
   avoid="Automerging majors; one PR per package on every run; unpinned GitHub Actions."),

RC("cfg-terraform-vps-bucket", "Terraform for a small VPS plus object-storage bucket (pinned providers, variables, no secrets)", "hcl",
   "Provision one server and a private bucket reproducibly with state kept safe, without leaking tokens or opening every port.",
   "versions.tf pins terraform and provider versions, typed variables with validation, secrets only via environment/sensitive variables, firewall allowing 22 from a variable CIDR plus 80/443, bucket private with versioning and no public ACL, outputs marked sensitive where needed, remote state backend block documented, .gitignore for state. Provider shown: Hetzner + S3-compatible; the structure transfers.",
   {"versions.tf": '''terraform {
  required_version = ">= 1.6.0"
  required_providers {
    hcloud = { source = "hetznercloud/hcloud", version = "~> 1.48" }
    aws    = { source = "hashicorp/aws", version = "~> 5.60" }
  }
  # Remote state: uncomment after creating the bucket by hand once.
  # backend "s3" { bucket = "my-tf-state" key = "vps/terraform.tfstate" region = "eu-central-1" encrypt = true use_lockfile = true }
}
''', "variables.tf": '''variable "hcloud_token" {
  type      = string
  sensitive = true
}

variable "ssh_public_key" {
  type = string
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) ", var.ssh_public_key))
    error_message = "Provide an ed25519 or rsa OpenSSH public key."
  }
}

variable "admin_cidr" {
  type        = string
  description = "CIDR allowed to SSH in, e.g. 203.0.113.7/32"
  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR and must not be 0.0.0.0/0."
  }
}

variable "server_type" {
  type    = string
  default = "cx22"
}
''', "main.tf": '''provider "hcloud" {
  token = var.hcloud_token
}

resource "hcloud_ssh_key" "admin" {
  name       = "admin"
  public_key = var.ssh_public_key
}

resource "hcloud_firewall" "web" {
  name = "web"
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = [var.admin_cidr]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "80"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "443"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

resource "hcloud_server" "app" {
  name         = "app-1"
  server_type  = var.server_type
  image        = "debian-12"
  location     = "fsn1"
  ssh_keys     = [hcloud_ssh_key.admin.id]
  firewall_ids = [hcloud_firewall.web.id]
  backups      = true
  labels       = { managed_by = "terraform" }
  lifecycle {
    ignore_changes = [ssh_keys]
  }
}

resource "aws_s3_bucket" "assets" {
  bucket = "my-app-assets-${hcloud_server.app.id}"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "assets" {
  bucket = aws_s3_bucket.assets.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "assets" {
  bucket = aws_s3_bucket.assets.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
''', "outputs.tf": '''output "server_ipv4" {
  value = hcloud_server.app.ipv4_address
}

output "bucket" {
  value = aws_s3_bucket.assets.bucket
}
''', ".gitignore": '''.terraform/
*.tfstate
*.tfstate.*
*.tfvars
!example.tfvars
crash.log
.terraform.lock.hcl.bak
''', "test_tf.py": '''import glob, re, unittest

SRC = {f: open(f).read() for f in glob.glob("*.tf")}
ALL = "\\n".join(SRC.values())


def blocks(kind):
    return re.findall(kind + r'\\s+"([^"]+)"\\s+"([^"]+)"\\s*\\{', ALL)


class T(unittest.TestCase):
    def test_braces_balance_in_every_file(self):
        for f, t in SRC.items():
            t = re.sub(r'"(?:\\\\.|[^"\\\\])*"', '""', re.sub(r"#.*", "", t))
            self.assertEqual(t.count("{"), t.count("}"), f)

    def test_versions_pinned(self):
        v = SRC["versions.tf"]; self.assertRegex(v, r'required_version\\s*=\\s*">= 1\\.\\d')
        providers = re.findall(r'(\\w+)\\s*=\\s*\\{\\s*source\\s*=\\s*"([^"]+)",\\s*version\\s*=\\s*"([^"]+)"', v)
        self.assertGreaterEqual(len(providers), 2); self.assertTrue(all(re.match(r"[~>=]", ver) for _, _, ver in providers))

    def test_no_secrets_in_files(self):
        for f, t in SRC.items():
            self.assertNotRegex(t, r'(?i)(token|secret|password|access_key)\\s*=\\s*"[^$"][^"]*"', f)
        self.assertRegex(SRC["variables.tf"], r'variable "hcloud_token" \\{[^}]*sensitive\\s*=\\s*true')

    def test_ssh_not_open_to_the_world(self):
        self.assertIn("var.admin_cidr", ALL); fw = ALL[ALL.index('resource "hcloud_firewall"'):ALL.index('resource "hcloud_server"')]
        ssh = fw[fw.index('port       = "22"'):fw.index('rule', fw.index('port       = "22"'))]
        self.assertNotIn("0.0.0.0/0", ssh); self.assertIn("0.0.0.0/0", fw)
        self.assertIn("!= \\"0.0.0.0/0\\"", SRC["variables.tf"])

    def test_bucket_is_private_versioned_encrypted(self):
        names = {n for _, n in blocks("resource")}; kinds = {k for k, _ in blocks("resource")}
        for k in ("aws_s3_bucket_public_access_block", "aws_s3_bucket_versioning", "aws_s3_bucket_server_side_encryption_configuration"): self.assertIn(k, kinds)
        pab = ALL[ALL.index("aws_s3_bucket_public_access_block"):]; pab = pab[:pab.index("}")]
        self.assertEqual(len(re.findall(r"=\\s*true", pab)), 4); self.assertNotIn("acl", ALL.replace("block_public_acls", "").replace("ignore_public_acls", ""))

    def test_resources_referenced_not_hardcoded(self):
        self.assertIn("hcloud_ssh_key.admin.id", ALL); self.assertIn("aws_s3_bucket.assets.id", ALL)

    def test_state_is_gitignored(self):
        g = open(".gitignore").read(); self.assertIn("*.tfstate", g); self.assertIn("*.tfvars", g); self.assertIn(".terraform/", g)

    def test_variables_are_typed(self):
        for name in re.findall(r'variable "(\\w+)"', ALL):
            self.assertRegex(ALL[ALL.index(f'variable "{name}"'):], r"type\\s*=")


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=STRUCT + ["terraform/tofu is not installed in the sandbox: run `terraform fmt -check && terraform validate && terraform plan` locally before applying", "Provider arguments (server types, locations) change: check current provider docs", "Terraform itself is BSL-licensed; OpenTofu (MPL-2.0) runs the same files"], compat={"terraform": ">=1.6", "hcloud": "~>1.48", "aws": "~>5.60"},
   tags=["terraform", "opentofu", "iac", "vps", "hetzner", "s3", "bucket", "infrastructure", "devops", "firewall"],
   avoid="Committing tfstate or tfvars; SSH open to 0.0.0.0/0; unpinned providers; secrets as default values."),

RC("cfg-ansible-baseline-server", "Ansible playbook: baseline server hardening and app deploy (idempotent)", "yaml",
   "Configure a fresh Debian/Ubuntu server repeatably: updates, non-root deploy user, SSH hardening, firewall, unattended upgrades, a systemd app.",
   "One playbook with named tasks and handlers, FQCN modules, `become` only where needed, SSH config via validated lineinfile (`validate: sshd -t -f %s`), ufw default deny with explicit allows, no passwords in plain text (ansible-vault variables), idempotent apt/user/copy/systemd modules, tags for partial runs.",
   {"site.yml": '''---
- name: Baseline server
  hosts: web
  become: true
  vars:
    deploy_user: deploy
    app_name: myapp
    ssh_port: 22
  tasks:
    - name: Install baseline packages
      ansible.builtin.apt:
        name: [ufw, unattended-upgrades, fail2ban, curl]
        state: present
        update_cache: true
        cache_valid_time: 3600
      tags: [packages]

    - name: Create deploy user
      ansible.builtin.user:
        name: "{{ deploy_user }}"
        shell: /bin/bash
        groups: [sudo]
        append: true
      tags: [users]

    - name: Authorise the deploy key
      ansible.posix.authorized_key:
        user: "{{ deploy_user }}"
        key: "{{ lookup('file', deploy_public_key_file) }}"
      tags: [users]

    - name: Harden sshd
      ansible.builtin.lineinfile:
        path: /etc/ssh/sshd_config
        regexp: "^#?{{ item.key }}\\\\s"
        line: "{{ item.key }} {{ item.value }}"
        validate: "sshd -t -f %s"
      loop:
        - {key: PermitRootLogin, value: "no"}
        - {key: PasswordAuthentication, value: "no"}
        - {key: KbdInteractiveAuthentication, value: "no"}
      notify: Restart sshd
      tags: [ssh]

    - name: Firewall default deny incoming
      community.general.ufw:
        default: deny
        direction: incoming
      tags: [firewall]

    - name: Firewall allow services
      community.general.ufw:
        rule: allow
        port: "{{ item }}"
        proto: tcp
      loop: ["{{ ssh_port }}", "80", "443"]
      tags: [firewall]

    - name: Enable firewall
      community.general.ufw:
        state: enabled
      tags: [firewall]

    - name: Enable unattended security upgrades
      ansible.builtin.copy:
        dest: /etc/apt/apt.conf.d/20auto-upgrades
        content: |
          APT::Periodic::Update-Package-Lists "1";
          APT::Periodic::Unattended-Upgrade "1";
        mode: "0644"
      tags: [packages]

    - name: Install the app unit
      ansible.builtin.template:
        src: app.service.j2
        dest: "/etc/systemd/system/{{ app_name }}.service"
        mode: "0644"
      notify: Restart app
      tags: [app]

    - name: Start and enable the app
      ansible.builtin.systemd_service:
        name: "{{ app_name }}"
        state: started
        enabled: true
        daemon_reload: true
      tags: [app]

  handlers:
    - name: Restart sshd
      ansible.builtin.service:
        name: ssh
        state: restarted

    - name: Restart app
      ansible.builtin.systemd_service:
        name: "{{ app_name }}"
        state: restarted
''', "templates/app.service.j2": '''[Unit]
Description={{ app_name }}
After=network-online.target

[Service]
User={{ deploy_user }}
ExecStart=/opt/{{ app_name }}/bin/{{ app_name }}
Restart=on-failure
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes

[Install]
WantedBy=multi-user.target
''', "inventory.example.ini": '''[web]
app-1 ansible_host=203.0.113.10 ansible_user=root

[web:vars]
deploy_public_key_file=~/.ssh/id_ed25519.pub
''', "test_playbook.py": '''import re, unittest, yaml

PLAY = yaml.safe_load(open("site.yml"))[0]
TASKS = PLAY["tasks"]
MODULES = [next(k for k in t if k not in ("name", "tags", "notify", "loop", "become", "vars", "when", "register")) for t in TASKS]


class T(unittest.TestCase):
    def test_every_task_is_named_and_uses_fqcn(self):
        for t, m in zip(TASKS, MODULES):
            self.assertTrue(t.get("name"), t); self.assertGreaterEqual(m.count("."), 2, f"{m} should be a fully qualified collection name")

    def test_notified_handlers_exist(self):
        handlers = {h["name"] for h in PLAY["handlers"]}
        for t in TASKS:
            if "notify" in t: self.assertIn(t["notify"], handlers)

    def test_ssh_is_hardened_and_validated(self):
        t = next(t for t in TASKS if t["name"] == "Harden sshd"); self.assertIn("sshd -t", t["ansible.builtin.lineinfile"]["validate"])
        pairs = {i["key"]: i["value"] for i in t["loop"]}; self.assertEqual((pairs["PermitRootLogin"], pairs["PasswordAuthentication"]), ("no", "no"))

    def test_firewall_denies_by_default_and_enables_after_allowing_ssh(self):
        names = [t["name"] for t in TASKS]; self.assertLess(names.index("Firewall allow services"), names.index("Enable firewall"))
        allow = next(t for t in TASKS if t["name"] == "Firewall allow services"); self.assertIn("{{ ssh_port }}", allow["loop"])
        deny = next(t for t in TASKS if t["name"] == "Firewall default deny incoming"); self.assertEqual(deny["community.general.ufw"]["default"], "deny")

    def test_no_plaintext_secrets_or_shell_escape_hatches(self):
        text = open("site.yml").read(); self.assertNotRegex(text, r"(?i)(password|secret|token)\\s*:\\s*[^{\\s]"); self.assertNotIn("ansible.builtin.shell", text); self.assertNotIn("ansible.builtin.command", text)

    def test_tasks_are_idempotent_modules_with_explicit_state_or_mode(self):
        for t, m in zip(TASKS, MODULES):
            args = t[m]; self.assertIsInstance(args, dict, t["name"])

    def test_template_uses_variables_and_hardening(self):
        u = open("templates/app.service.j2").read(); self.assertIn("{{ app_name }}", u); self.assertIn("NoNewPrivileges=yes", u); self.assertNotIn("User=root", u)

    def test_tags_allow_partial_runs(self):
        tags = {x for t in TASKS for x in t.get("tags", [])}; self.assertTrue({"ssh", "firewall", "app", "packages", "users"} <= tags)


if __name__ == "__main__": unittest.main()
'''}, PYT, assumptions=STRUCT + ["ansible is not installed in the sandbox: run `ansible-lint site.yml` and `ansible-playbook --check --diff` against a throwaway host", "Needs collections ansible.posix and community.general (`ansible-galaxy collection install`)", "Debian/Ubuntu; the ssh service is named `ssh` there (`sshd` on RHEL)", "Ansible core is GPL-3.0; playbooks you write are your own files"], compat={"ansible-core": ">=2.15"},
   tags=["ansible", "configuration-management", "server", "hardening", "ssh", "ufw", "devops", "playbook", "provisioning"],
   avoid="shell/command modules for things with real modules; enabling the firewall before allowing SSH (locks you out); password auth left on."),
]
