"""CLI-grade algorithms in Go and TypeScript (stdlib only, tested). TypeScript recipes use erasable syntax only and run with
`node --test` (type stripping); they are not type-checked by tsc here."""
from .recipes import GOT, RC

NODE_TS = ["node", "--test"]
TSA = ["Node >= 22.18 (native type stripping; erasable TypeScript syntax only)", "types are not checked by tsc in the sandbox"]

ALGO = [
# ------------------------------------------------------------------ fuzzy
RC("go-fuzzy-match", "Fuzzy subsequence matching with scoring and match positions (Go)", "go",
   "Command palettes, file pickers and TUI filters need typo-tolerant ranking, not just substring search.",
   "Dynamic programming over (pattern index, text index) maximising a score: base per match, bonuses for consecutive runs, word boundaries and camelCase, small penalties for gaps and a late start; returns the matched positions for highlighting.",
   {"fuzzy.go": '''// Package fuzzy ranks strings by how well they match a pattern as a case-insensitive subsequence.
package fuzzy

import (
	"sort"
	"strings"
	"unicode"
)

const (
	scoreMatch       = 16
	bonusConsecutive = 24
	bonusBoundary    = 18
	bonusCamel       = 14
	gapPenalty       = 2
	leadPenalty      = 1
	neg              = -1 << 30
)

func boundary(rs []rune, i int) int {
	if i == 0 {
		return bonusBoundary
	}
	switch p := rs[i-1]; {
	case strings.ContainsRune(" /_-.:\\\\", p):
		return bonusBoundary
	case unicode.IsLower(p) && unicode.IsUpper(rs[i]):
		return bonusCamel
	}
	return 0
}

// Score returns the best score and the matched rune positions. ok is false when pattern is not a subsequence of text.
func Score(pattern, text string) (score int, positions []int, ok bool) {
	p, t := []rune(strings.ToLower(pattern)), []rune(text)
	lt := []rune(strings.ToLower(text))
	m, n := len(p), len(t)
	if m == 0 {
		return 0, nil, true
	}
	if m > n {
		return 0, nil, false
	}
	dp := make([][]int, m)
	par := make([][]int, m)
	for j := range dp {
		dp[j], par[j] = make([]int, n), make([]int, n)
		for i := range dp[j] {
			dp[j][i], par[j][i] = neg, -1
		}
	}
	for i := 0; i < n; i++ {
		if lt[i] == p[0] {
			dp[0][i] = scoreMatch + boundary(t, i) - leadPenalty*min(i, 10)
		}
	}
	for j := 1; j < m; j++ {
		g, gk := neg, -1 // best dp[j-1][k] - gapPenalty*(i-k-1) over k <= i-2
		for i := j; i < n; i++ {
			// extend the gap candidate with k = i-2
			if i >= 2 && dp[j-1][i-2] > neg {
				if c := dp[j-1][i-2]; c >= g {
					g, gk = c, i-2
				}
			}
			if i >= 3 {
				// g currently holds the value valid for index i-1; decay one step when moving to i
			}
			if lt[i] != p[j] {
				g -= 0
				if g > neg {
					g -= gapPenalty
				}
				continue
			}
			best, from := neg, -1
			if dp[j-1][i-1] > neg {
				best, from = dp[j-1][i-1]+bonusConsecutive, i-1
			}
			if g > neg && g > best {
				best, from = g, gk
			}
			if from >= 0 {
				dp[j][i], par[j][i] = best+scoreMatch+boundary(t, i), from
			}
			if g > neg {
				g -= gapPenalty
			}
		}
	}
	bi, bs := -1, neg
	for i := 0; i < n; i++ {
		if dp[m-1][i] > bs {
			bi, bs = i, dp[m-1][i]
		}
	}
	if bi < 0 {
		return 0, nil, false
	}
	positions = make([]int, m)
	for j, i := m-1, bi; j >= 0; j-- {
		positions[j] = i
		i = par[j][i]
	}
	return bs, positions, true
}

type Result struct {
	Item      string
	Score     int
	Positions []int
}

// Rank returns the matching items best first (ties: shorter, then original order).
func Rank(pattern string, items []string) []Result {
	var out []Result
	for _, it := range items {
		if s, pos, ok := Score(pattern, it); ok {
			out = append(out, Result{it, s, pos})
		}
	}
	sort.SliceStable(out, func(a, b int) bool {
		if out[a].Score != out[b].Score {
			return out[a].Score > out[b].Score
		}
		return len(out[a].Item) < len(out[b].Item)
	})
	return out
}
''', "fuzzy_test.go": '''package fuzzy

import (
	"reflect"
	"testing"
)

func TestSubsequenceAndPositions(t *testing.T) {
	_, pos, ok := Score("fb", "foo_bar")
	if !ok || !reflect.DeepEqual(pos, []int{0, 4}) {
		t.Fatal(pos, ok)
	}
	if _, _, ok := Score("bf", "foo_bar"); ok {
		t.Fatal("order matters")
	}
	if _, _, ok := Score("xyz", "foo"); ok {
		t.Fatal("not a subsequence")
	}
	if s, p, ok := Score("", "anything"); !ok || s != 0 || p != nil {
		t.Fatal("empty pattern matches everything")
	}
	if _, _, ok := Score("longer", "lo"); ok {
		t.Fatal("pattern longer than text")
	}
}

func TestCaseInsensitive(t *testing.T) {
	if _, _, ok := Score("README", "readme.md"); !ok {
		t.Fatal()
	}
}

func TestPrefersBoundariesAndConsecutive(t *testing.T) {
	sBoundary, _, _ := Score("fb", "foo_bar")
	sBuried, _, _ := Score("fb", "afoobar")
	if sBoundary <= sBuried {
		t.Fatalf("boundary %d should beat buried %d", sBoundary, sBuried)
	}
	sRun, _, _ := Score("bar", "xbarx")
	sSplit, _, _ := Score("bar", "xbxaxrx")
	if sRun <= sSplit {
		t.Fatalf("consecutive %d should beat split %d", sRun, sSplit)
	}
	sCamel, _, _ := Score("gt", "getTitle")
	sFlat, _, _ := Score("gt", "gettitle")
	if sCamel <= sFlat {
		t.Fatalf("camelCase boundary %d should beat flat %d", sCamel, sFlat)
	}
}

func TestRankOrder(t *testing.T) {
	got := Rank("main", []string{"domain.go", "src/main.go", "main.go", "remaining.txt", "nothing"})
	if len(got) != 4 || got[0].Item != "main.go" {
		t.Fatalf("%+v", got)
	}
	for _, r := range got {
		if r.Item == "nothing" {
			t.Fatal("non-match returned")
		}
	}
}

func TestUnicodeAndLong(t *testing.T) {
	if _, pos, ok := Score("é", "café"); !ok || pos[0] != 3 {
		t.Fatal(pos, ok)
	}
	long := make([]rune, 5000)
	for i := range long {
		long[i] = 'a'
	}
	if _, _, ok := Score("aaab", string(long)); ok {
		t.Fatal("no b present")
	}
	if _, _, ok := Score("aaa", string(long)); !ok {
		t.Fatal()
	}
}
'''}, GOT, assumptions=["Go >= 1.21 (builtin min)", "O(len(pattern) x len(text)) memory per call; fine for names/paths, not megabyte texts"],
   compat={"go": ">=1.21"}, tags=["fuzzy", "subsequence", "match", "search", "ranking", "picker", "tui", "algorithm", "autocomplete"],
   avoid="Plain strings.Contains ranking, or a greedy left-most match that misses the better alignment."),

RC("ts-fuzzy-match", "Fuzzy subsequence matching with scoring and match positions (TypeScript)", "typescript",
   "Command palettes, pickers and filters need typo-tolerant ranking with highlightable positions.",
   "Dynamic programming over (pattern, text) maximising a score with bonuses for consecutive runs, word boundaries and camelCase and penalties for gaps; returns positions; Rank sorts by score then length.",
   {"fuzzy.ts": '''export interface Match { score: number; positions: number[] }
export interface Ranked extends Match { item: string }

const SCORE_MATCH = 16, BONUS_CONSEC = 24, BONUS_BOUNDARY = 18, BONUS_CAMEL = 14, GAP = 2, LEAD = 1
const NEG = -1e9
const SEPARATORS = ' /_-.:\\\\'

function boundary(t: string[], i: number): number {
  if (i === 0) return BONUS_BOUNDARY
  const p = t[i - 1], c = t[i]
  if (SEPARATORS.includes(p)) return BONUS_BOUNDARY
  if (p !== p.toUpperCase() && c !== c.toLowerCase()) return BONUS_CAMEL // lower -> Upper
  return 0
}

/** Best subsequence alignment of `pattern` in `text` (case-insensitive), or null when it is not a subsequence. */
export function score(pattern: string, text: string): Match | null {
  const p = Array.from(pattern.toLowerCase()), t = Array.from(text), lt = Array.from(text.toLowerCase())
  const m = p.length, n = t.length
  if (m === 0) return { score: 0, positions: [] }
  if (m > n) return null
  const dp: number[][] = Array.from({ length: m }, () => new Array(n).fill(NEG))
  const par: number[][] = Array.from({ length: m }, () => new Array(n).fill(-1))
  for (let i = 0; i < n; i++) if (lt[i] === p[0]) dp[0][i] = SCORE_MATCH + boundary(t, i) - LEAD * Math.min(i, 10)
  for (let j = 1; j < m; j++) {
    let g = NEG, gk = -1 // best dp[j-1][k] - GAP*(i-k-1) over k <= i-2
    for (let i = j; i < n; i++) {
      if (i >= 2 && dp[j - 1][i - 2] > NEG && dp[j - 1][i - 2] >= g) { g = dp[j - 1][i - 2]; gk = i - 2 }
      if (lt[i] === p[j]) {
        let best = NEG, from = -1
        if (dp[j - 1][i - 1] > NEG) { best = dp[j - 1][i - 1] + BONUS_CONSEC; from = i - 1 }
        if (g > NEG && g > best) { best = g; from = gk }
        if (from >= 0) { dp[j][i] = best + SCORE_MATCH + boundary(t, i); par[j][i] = from }
      }
      if (g > NEG) g -= GAP
    }
  }
  let bi = -1, bs = NEG
  for (let i = 0; i < n; i++) if (dp[m - 1][i] > bs) { bs = dp[m - 1][i]; bi = i }
  if (bi < 0) return null
  const positions = new Array<number>(m)
  for (let j = m - 1, i = bi; j >= 0; j--) { positions[j] = i; i = par[j][i] }
  return { score: bs, positions }
}

export function rank(pattern: string, items: string[]): Ranked[] {
  const out: Ranked[] = []
  for (const item of items) { const m = score(pattern, item); if (m) out.push({ item, ...m }) }
  return out.map((r, idx) => ({ r, idx }))
    .sort((a, b) => b.r.score - a.r.score || a.r.item.length - b.r.item.length || a.idx - b.idx)
    .map((x) => x.r)
}
''', "fuzzy.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { score, rank } from './fuzzy.ts'

test('subsequence and positions', () => {
  assert.deepEqual(score('fb', 'foo_bar')?.positions, [0, 4])
  assert.equal(score('bf', 'foo_bar'), null)
  assert.equal(score('xyz', 'foo'), null)
  assert.deepEqual(score('', 'anything'), { score: 0, positions: [] })
  assert.equal(score('longer', 'lo'), null)
})

test('case-insensitive', () => { assert.ok(score('README', 'readme.md')) })

test('prefers boundaries, runs and camelCase', () => {
  assert.ok(score('fb', 'foo_bar')!.score > score('fb', 'afoobar')!.score)
  assert.ok(score('bar', 'xbarx')!.score > score('bar', 'xbxaxrx')!.score)
  assert.ok(score('gt', 'getTitle')!.score > score('gt', 'gettitle')!.score)
})

test('rank orders and drops non-matches', () => {
  const got = rank('main', ['domain.ts', 'src/main.ts', 'main.ts', 'remaining.txt', 'nothing'])
  assert.equal(got.length, 4)
  assert.equal(got[0].item, 'main.ts')
  assert.ok(!got.some((r) => r.item === 'nothing'))
})

test('unicode code points and long input', () => {
  assert.deepEqual(score('é', 'café')?.positions, [3])
  assert.equal(score('aaab', 'a'.repeat(3000)), null)
  assert.ok(score('aaa', 'a'.repeat(3000)))
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["fuzzy", "subsequence", "match", "search", "ranking", "picker", "algorithm", "autocomplete", "typescript"],
   avoid="Ranking by indexOf only; ignoring word boundaries."),

# ------------------------------------------------------------------ trie
RC("go-trie", "Trie (prefix tree) with prefix search and delete (Go)", "go",
   "Autocomplete, command/word lookup and prefix queries over many strings.",
   "Rune-keyed nodes, Insert/Has/Delete with pruning of empty branches, WithPrefix returning sorted results with a limit.",
   {"trie.go": '''// Package trie is a rune-based prefix tree.
package trie

import "sort"

type node struct {
	kids map[rune]*node
	end  bool
}

type Trie struct {
	root node
	n    int
}

func New() *Trie { return &Trie{root: node{kids: map[rune]*node{}}} }

func (t *Trie) Len() int { return t.n }

func (t *Trie) Insert(s string) bool {
	cur := &t.root
	for _, r := range s {
		nx := cur.kids[r]
		if nx == nil {
			nx = &node{kids: map[rune]*node{}}
			cur.kids[r] = nx
		}
		cur = nx
	}
	if cur.end {
		return false
	}
	cur.end = true
	t.n++
	return true
}

func (t *Trie) find(s string) *node {
	cur := &t.root
	for _, r := range s {
		if cur = cur.kids[r]; cur == nil {
			return nil
		}
	}
	return cur
}

func (t *Trie) Has(s string) bool { n := t.find(s); return n != nil && n.end }

// Delete removes s and prunes branches that no longer lead to any word. Reports whether s was present.
func (t *Trie) Delete(s string) bool {
	rs := []rune(s)
	path := []*node{&t.root}
	for _, r := range rs {
		nx := path[len(path)-1].kids[r]
		if nx == nil {
			return false
		}
		path = append(path, nx)
	}
	last := path[len(path)-1]
	if !last.end {
		return false
	}
	last.end = false
	t.n--
	for i := len(rs); i > 0; i-- {
		n := path[i]
		if n.end || len(n.kids) > 0 {
			break
		}
		delete(path[i-1].kids, rs[i-1])
	}
	return true
}

// WithPrefix returns up to limit words (limit <= 0: all) starting with prefix, in lexicographic (rune) order.
func (t *Trie) WithPrefix(prefix string, limit int) []string {
	start := t.find(prefix)
	if start == nil {
		return nil
	}
	var out []string
	var walk func(n *node, buf []rune) bool
	walk = func(n *node, buf []rune) bool {
		if n.end {
			out = append(out, string(buf))
			if limit > 0 && len(out) >= limit {
				return true
			}
		}
		keys := make([]rune, 0, len(n.kids))
		for r := range n.kids {
			keys = append(keys, r)
		}
		sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
		for _, r := range keys {
			if walk(n.kids[r], append(buf, r)) {
				return true
			}
		}
		return false
	}
	walk(start, []rune(prefix))
	return out
}
''', "trie_test.go": '''package trie

import (
	"reflect"
	"testing"
)

func TestInsertHasLen(t *testing.T) {
	tr := New()
	for _, w := range []string{"car", "card", "care", "cat", "dog", ""} {
		if !tr.Insert(w) {
			t.Fatal(w)
		}
	}
	if tr.Insert("car") || tr.Len() != 6 {
		t.Fatal("duplicate insert must be a no-op", tr.Len())
	}
	if !tr.Has("car") || tr.Has("ca") || tr.Has("cars") || !tr.Has("") {
		t.Fatal("Has wrong")
	}
}

func TestWithPrefixSortedAndLimited(t *testing.T) {
	tr := New()
	for _, w := range []string{"card", "care", "car", "cat", "dog", "über", "übung"} {
		tr.Insert(w)
	}
	if got := tr.WithPrefix("car", 0); !reflect.DeepEqual(got, []string{"car", "card", "care"}) {
		t.Fatal(got)
	}
	if got := tr.WithPrefix("car", 2); !reflect.DeepEqual(got, []string{"car", "card"}) {
		t.Fatal(got)
	}
	if tr.WithPrefix("zzz", 0) != nil {
		t.Fatal("unknown prefix")
	}
	if got := tr.WithPrefix("ü", 0); len(got) != 2 {
		t.Fatal("unicode prefix", got)
	}
	if got := tr.WithPrefix("", 3); len(got) != 3 {
		t.Fatal(got)
	}
}

func TestDeletePrunes(t *testing.T) {
	tr := New()
	tr.Insert("car")
	tr.Insert("card")
	if tr.Delete("ca") || tr.Delete("cat") {
		t.Fatal("deleting a non-word must fail")
	}
	if !tr.Delete("card") || tr.Has("card") || !tr.Has("car") || tr.Len() != 1 {
		t.Fatal("delete leaf")
	}
	if tr.find("card") != nil {
		t.Fatal("empty branch must be pruned")
	}
	if !tr.Delete("car") || len(tr.root.kids) != 0 {
		t.Fatal("all branches pruned")
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Not safe for concurrent use without a lock"], compat={"go": ">=1.16"},
   tags=["trie", "prefix tree", "autocomplete", "prefix", "algorithm", "data-structure"],
   avoid="Scanning a sorted slice for every keystroke on large vocabularies."),

RC("ts-trie", "Trie (prefix tree) with prefix search and delete (TypeScript)", "typescript",
   "Autocomplete and prefix lookup over many strings.",
   "Code-point keyed nodes in Maps, insert/has/delete with pruning, withPrefix sorted by code point with a limit.",
   {"trie.ts": '''class Node { kids = new Map<string, Node>(); end = false }

export class Trie {
  private root = new Node()
  private count = 0
  get size(): number { return this.count }

  insert(word: string): boolean {
    let cur = this.root
    for (const ch of word) {
      let nx = cur.kids.get(ch)
      if (!nx) { nx = new Node(); cur.kids.set(ch, nx) }
      cur = nx
    }
    if (cur.end) return false
    cur.end = true; this.count++
    return true
  }

  private find(s: string): Node | undefined {
    let cur: Node | undefined = this.root
    for (const ch of s) { cur = cur.kids.get(ch); if (!cur) return undefined }
    return cur
  }

  has(word: string): boolean { return this.find(word)?.end === true }

  /** Removes word and prunes branches that no longer lead to a word. */
  delete(word: string): boolean {
    const chars = Array.from(word), path: Node[] = [this.root]
    for (const ch of chars) {
      const nx = path[path.length - 1].kids.get(ch)
      if (!nx) return false
      path.push(nx)
    }
    const last = path[path.length - 1]
    if (!last.end) return false
    last.end = false; this.count--
    for (let i = chars.length; i > 0; i--) {
      const n = path[i]
      if (n.end || n.kids.size > 0) break
      path[i - 1].kids.delete(chars[i - 1])
    }
    return true
  }

  /** Up to `limit` words (0 = all) that start with prefix, sorted by code point. */
  withPrefix(prefix: string, limit = 0): string[] {
    const start = this.find(prefix)
    if (!start) return []
    const out: string[] = []
    const walk = (n: Node, buf: string): boolean => {
      if (n.end) { out.push(buf); if (limit > 0 && out.length >= limit) return true }
      const keys = [...n.kids.keys()].sort((a, b) => a.codePointAt(0)! - b.codePointAt(0)!)
      for (const k of keys) if (walk(n.kids.get(k)!, buf + k)) return true
      return false
    }
    walk(start, prefix)
    return out
  }
}
''', "trie.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Trie } from './trie.ts'

test('insert, has, size', () => {
  const t = new Trie()
  for (const w of ['car', 'card', 'care', 'cat', 'dog', '']) assert.equal(t.insert(w), true)
  assert.equal(t.insert('car'), false)
  assert.equal(t.size, 6)
  assert.ok(t.has('car') && t.has('') && !t.has('ca') && !t.has('cars'))
})

test('withPrefix sorted, limited, unicode', () => {
  const t = new Trie()
  for (const w of ['card', 'care', 'car', 'cat', 'dog', 'über', 'übung', '😀a', '😀b']) t.insert(w)
  assert.deepEqual(t.withPrefix('car'), ['car', 'card', 'care'])
  assert.deepEqual(t.withPrefix('car', 2), ['car', 'card'])
  assert.deepEqual(t.withPrefix('zzz'), [])
  assert.equal(t.withPrefix('ü').length, 2)
  assert.deepEqual(t.withPrefix('😀'), ['😀a', '😀b'])
  assert.equal(t.withPrefix('', 3).length, 3)
})

test('delete prunes', () => {
  const t = new Trie()
  t.insert('car'); t.insert('card')
  assert.equal(t.delete('ca'), false)
  assert.equal(t.delete('cat'), false)
  assert.ok(t.delete('card') && !t.has('card') && t.has('car') && t.size === 1)
  assert.ok(t.delete('car') && t.size === 0)
  assert.deepEqual(t.withPrefix(''), [])
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["trie", "prefix tree", "autocomplete", "prefix", "algorithm", "data-structure", "typescript"],
   avoid="Filtering a whole array on each keystroke when the vocabulary is large."),

# ------------------------------------------------------------------ ring buffer
RC("go-ring-buffer", "Fixed-size ring buffer (circular buffer) with overwrite (Go generics)", "go",
   "Keeping the last N log lines, samples or events with O(1) push and no allocation after creation.",
   "Fixed backing slice with head and length; Push overwrites the oldest when full; At/Slice read oldest-first; Reset clears and zeroes references for the GC.",
   {"ring.go": '''// Package ring is a fixed-capacity circular buffer that overwrites the oldest element when full.
package ring

type Ring[T any] struct {
	buf  []T
	head int // index of the oldest element
	n    int
}

func New[T any](capacity int) *Ring[T] {
	if capacity < 1 {
		panic("ring: capacity must be >= 1")
	}
	return &Ring[T]{buf: make([]T, capacity)}
}

func (r *Ring[T]) Cap() int { return len(r.buf) }
func (r *Ring[T]) Len() int { return r.n }

// Push appends v and returns the element it overwrote (ok=false when nothing was dropped).
func (r *Ring[T]) Push(v T) (dropped T, ok bool) {
	if r.n < len(r.buf) {
		r.buf[(r.head+r.n)%len(r.buf)] = v
		r.n++
		return dropped, false
	}
	dropped = r.buf[r.head]
	r.buf[r.head] = v
	r.head = (r.head + 1) % len(r.buf)
	return dropped, true
}

// At returns the i-th oldest element (0 = oldest).
func (r *Ring[T]) At(i int) T {
	if i < 0 || i >= r.n {
		panic("ring: index out of range")
	}
	return r.buf[(r.head+i)%len(r.buf)]
}

// Slice copies the contents, oldest first.
func (r *Ring[T]) Slice() []T {
	out := make([]T, r.n)
	for i := range out {
		out[i] = r.At(i)
	}
	return out
}

func (r *Ring[T]) Reset() {
	var zero T
	for i := range r.buf {
		r.buf[i] = zero // drop references so the GC can reclaim them
	}
	r.head, r.n = 0, 0
}
''', "ring_test.go": '''package ring

import (
	"reflect"
	"testing"
)

func TestFillAndWrap(t *testing.T) {
	r := New[int](3)
	for i := 1; i <= 3; i++ {
		if _, ok := r.Push(i); ok {
			t.Fatal("nothing dropped yet")
		}
	}
	if !reflect.DeepEqual(r.Slice(), []int{1, 2, 3}) || r.Len() != 3 {
		t.Fatal(r.Slice())
	}
	d, ok := r.Push(4)
	if !ok || d != 1 || !reflect.DeepEqual(r.Slice(), []int{2, 3, 4}) {
		t.Fatal(d, ok, r.Slice())
	}
	for i := 5; i <= 10; i++ {
		r.Push(i)
	}
	if !reflect.DeepEqual(r.Slice(), []int{8, 9, 10}) || r.At(0) != 8 || r.At(2) != 10 {
		t.Fatal(r.Slice())
	}
}

func TestResetAndEdges(t *testing.T) {
	r := New[string](2)
	r.Push("a")
	r.Reset()
	if r.Len() != 0 || len(r.Slice()) != 0 {
		t.Fatal()
	}
	r.Push("b")
	if r.At(0) != "b" {
		t.Fatal()
	}
	mustPanic := func(f func()) {
		defer func() {
			if recover() == nil {
				t.Fatal("expected panic")
			}
		}()
		f()
	}
	mustPanic(func() { r.At(1) })
	mustPanic(func() { r.At(-1) })
	mustPanic(func() { New[int](0) })
}

func TestCapacityOne(t *testing.T) {
	r := New[int](1)
	r.Push(1)
	r.Push(2)
	if !reflect.DeepEqual(r.Slice(), []int{2}) {
		t.Fatal(r.Slice())
	}
}
'''}, GOT, assumptions=["Go >= 1.18 (generics)", "Not safe for concurrent use without a lock"], compat={"go": ">=1.18"},
   tags=["ring buffer", "circular buffer", "queue", "log tail", "samples", "algorithm", "data-structure", "generics"],
   avoid="Appending to a slice and re-slicing forever (memory never released)."),

RC("ts-ring-buffer", "Fixed-size ring buffer (circular buffer) with overwrite (TypeScript)", "typescript",
   "Keeping the last N log lines, samples or events with O(1) push.",
   "Fixed array with head and length; push overwrites the oldest when full and returns it; iterable oldest-first; clear releases references.",
   {"ring.ts": '''export class Ring<T> implements Iterable<T> {
  private buf: (T | undefined)[]
  private head = 0
  private n = 0
  readonly capacity: number // declared explicitly: parameter properties are not erasable TypeScript
  constructor(capacity: number) {
    this.capacity = capacity
    if (!Number.isInteger(capacity) || capacity < 1) throw new RangeError('capacity must be an integer >= 1')
    this.buf = new Array(capacity)
  }
  get length(): number { return this.n }

  /** Appends v; returns the element it overwrote, or undefined when nothing was dropped. */
  push(v: T): T | undefined {
    if (this.n < this.capacity) { this.buf[(this.head + this.n) % this.capacity] = v; this.n++; return undefined }
    const dropped = this.buf[this.head]
    this.buf[this.head] = v
    this.head = (this.head + 1) % this.capacity
    return dropped
  }

  /** i-th oldest element (0 = oldest). */
  at(i: number): T {
    if (!Number.isInteger(i) || i < 0 || i >= this.n) throw new RangeError('index out of range')
    return this.buf[(this.head + i) % this.capacity] as T
  }

  toArray(): T[] { return Array.from(this) }
  *[Symbol.iterator](): Iterator<T> { for (let i = 0; i < this.n; i++) yield this.at(i) }
  clear(): void { this.buf = new Array(this.capacity); this.head = 0; this.n = 0 }
}
''', "ring.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { Ring } from './ring.ts'

test('fill, wrap and overwrite', () => {
  const r = new Ring<number>(3)
  for (let i = 1; i <= 3; i++) assert.equal(r.push(i), undefined)
  assert.deepEqual(r.toArray(), [1, 2, 3])
  assert.equal(r.push(4), 1)
  assert.deepEqual(r.toArray(), [2, 3, 4])
  for (let i = 5; i <= 10; i++) r.push(i)
  assert.deepEqual([...r], [8, 9, 10])
  assert.equal(r.at(0), 8); assert.equal(r.at(2), 10); assert.equal(r.length, 3)
})

test('clear, bounds, capacity validation', () => {
  const r = new Ring<string>(2)
  r.push('a'); r.clear()
  assert.equal(r.length, 0); assert.deepEqual(r.toArray(), [])
  r.push('b'); assert.equal(r.at(0), 'b')
  assert.throws(() => r.at(1), RangeError)
  assert.throws(() => r.at(-1), RangeError)
  assert.throws(() => new Ring(0), RangeError)
  assert.throws(() => new Ring(1.5), RangeError)
})

test('capacity one', () => {
  const r = new Ring<number>(1); r.push(1); r.push(2)
  assert.deepEqual(r.toArray(), [2])
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["ring buffer", "circular buffer", "queue", "log tail", "samples", "algorithm", "data-structure", "typescript"],
   avoid="array.push + shift on large buffers (O(n) shift)."),

# ------------------------------------------------------------------ glob
RC("go-glob-match", "Glob matching with *, ?, [classes] and ** across path segments (Go)", "go",
   "Include/exclude patterns for file tools without regex conversion bugs or exponential backtracking.",
   "Split on '/', treat '**' as zero or more segments, match each segment with an iterative two-pointer algorithm supporting *, ?, [a-z], [!x] and backslash escapes; an unterminated '[' is a literal.",
   {"glob.go": '''// Package glob matches slash-separated paths against shell-style patterns.
//
//	*      any run of characters except '/'
//	?      one character except '/'
//	[a-z]  one character from the class; [!x] or [^x] negates; ']' first in the class is literal
//	**     as a whole segment: zero or more path segments
//	\\\\     escapes the next character
//
// Dotfiles get no special treatment; brace expansion ({a,b}) is not supported.
package glob

import "strings"

func Match(pattern, name string) bool {
	return matchParts(strings.Split(pattern, "/"), strings.Split(name, "/"))
}

func matchParts(p, n []string) bool {
	for len(p) > 0 {
		if p[0] == "**" {
			for len(p) > 1 && p[1] == "**" {
				p = p[1:]
			}
			if len(p) == 1 {
				return true
			}
			for i := 0; i <= len(n); i++ {
				if matchParts(p[1:], n[i:]) {
					return true
				}
			}
			return false
		}
		if len(n) == 0 || !matchSegment([]rune(p[0]), []rune(n[0])) {
			return false
		}
		p, n = p[1:], n[1:]
	}
	return len(n) == 0
}

// matchClass matches c against the class starting at p[0]=='['. valid is false for an unterminated class.
func matchClass(p []rune, c rune) (ok bool, consumed int, valid bool) {
	i, neg := 1, false
	if i < len(p) && (p[i] == '!' || p[i] == '^') {
		neg = true
		i++
	}
	first := true
	for i < len(p) {
		if p[i] == ']' && !first {
			return ok != neg, i + 1, true
		}
		first = false
		lo := p[i]
		if lo == '\\\\' && i+1 < len(p) {
			i++
			lo = p[i]
		}
		hi := lo
		if i+2 < len(p) && p[i+1] == '-' && p[i+2] != ']' {
			hi = p[i+2]
			i += 2
		}
		if lo <= c && c <= hi {
			ok = true
		}
		i++
	}
	return false, 0, false
}

// matchSegment is the classic iterative wildcard matcher: on a mismatch it retries from the last '*' (linear-ish, no recursion).
func matchSegment(p, s []rune) bool {
	pi, si, star, mark := 0, 0, -1, 0
	for si < len(s) {
		if pi < len(p) {
			switch p[pi] {
			case '*':
				star, mark = pi, si
				pi++
				continue
			case '?':
				pi++
				si++
				continue
			case '[':
				if ok, n, valid := matchClass(p[pi:], s[si]); valid {
					if ok {
						pi += n
						si++
						continue
					}
				} else if s[si] == '[' {
					pi++
					si++
					continue
				}
			case '\\\\':
				if pi+1 < len(p) && p[pi+1] == s[si] {
					pi += 2
					si++
					continue
				}
			default:
				if p[pi] == s[si] {
					pi++
					si++
					continue
				}
			}
		}
		if star >= 0 {
			mark++
			si, pi = mark, star+1
			continue
		}
		return false
	}
	for pi < len(p) && p[pi] == '*' {
		pi++
	}
	return pi == len(p)
}
''', "glob_test.go": '''package glob

import (
	"strings"
	"testing"
)

func TestTable(t *testing.T) {
	cases := []struct {
		pat, name string
		want      bool
	}{
		{"*.go", "main.go", true}, {"*.go", "a/main.go", false}, {"*.go", "main.gox", false},
		{"**/*.go", "main.go", true}, {"**/*.go", "a/b/c.go", true}, {"**/*.go", "a/b/c.js", false},
		{"a/**/b", "a/b", true}, {"a/**/b", "a/x/y/b", true}, {"a/**/b", "a/x/y/c", false},
		{"src/**", "src/a/b", true}, {"src/**", "src", true}, {"src/**", "other/a", false},
		{"**", "", true}, {"**", "any/thing", true},
		{"?.txt", "a.txt", true}, {"?.txt", "ab.txt", false}, {"?.txt", "/.txt", false},
		{"[abc].txt", "b.txt", true}, {"[abc].txt", "d.txt", false}, {"[a-c]x", "bx", true}, {"[!a]x", "bx", true}, {"[!a]x", "ax", false},
		{"[^a-c]x", "dx", true}, {"[]]x", "]x", true},
		{"a\\\\*b", "a*b", true}, {"a\\\\*b", "axb", false},
		{"[abc", "[abc", true}, {"[abc", "a", false},
		{"*", "", true}, {"a*", "a", true}, {"*a*b*c*", "xaxbxcx", true}, {"*a*b*c*", "xcxbxax", false},
		{"data/*/file_?.csv", "data/2026/file_1.csv", true}, {"data/*/file_?.csv", "data/2026/x/file_1.csv", false},
		{"日*", "日本語", true}, {"?", "日", true},
	}
	for _, c := range cases {
		if got := Match(c.pat, c.name); got != c.want {
			t.Errorf("Match(%q, %q) = %v, want %v", c.pat, c.name, got, c.want)
		}
	}
}

func TestNoCatastrophicBacktracking(t *testing.T) {
	name := strings.Repeat("a", 2000)
	if Match(strings.Repeat("a*", 200)+"b", name) {
		t.Fatal("must not match")
	}
	if !Match(strings.Repeat("a*", 200), name) {
		t.Fatal("must match")
	}
}
'''}, GOT, assumptions=["Go >= 1.16"], compat={"go": ">=1.16"},
   tags=["glob", "wildcard", "pattern", "match", "gitignore", "path", "algorithm", "doublestar"],
   avoid="Converting globs to regexes by string replacement (ReDoS and escaping bugs)."),

RC("ts-glob-match", "Glob matching with *, ?, [classes] and ** across path segments (TypeScript)", "typescript",
   "Include/exclude patterns for file tools without regex conversion bugs or exponential backtracking.",
   "Split on '/', '**' matches zero or more segments, each segment matched by an iterative two-pointer matcher with *, ?, [a-z], [!x] and escapes; unterminated '[' is literal.",
   {"glob.ts": '''/**
 * Shell-style glob matching on '/'-separated paths.
 * `*` any run except '/', `?` one char, `[a-z]` / `[!x]` classes, `**` as a whole segment = zero or more segments, `\\\\` escapes.
 * No brace expansion; dotfiles are not special.
 */
export function match(pattern: string, name: string): boolean {
  return matchParts(pattern.split('/'), name.split('/'))
}

function matchParts(p: string[], n: string[]): boolean {
  while (p.length > 0) {
    if (p[0] === '**') {
      while (p.length > 1 && p[1] === '**') p = p.slice(1)
      if (p.length === 1) return true
      for (let i = 0; i <= n.length; i++) if (matchParts(p.slice(1), n.slice(i))) return true
      return false
    }
    if (n.length === 0 || !matchSegment(Array.from(p[0]), Array.from(n[0]))) return false
    p = p.slice(1); n = n.slice(1)
  }
  return n.length === 0
}

function matchClass(p: string[], c: string): { ok: boolean; consumed: number } | null {
  let i = 1, neg = false, ok = false
  if (i < p.length && (p[i] === '!' || p[i] === '^')) { neg = true; i++ }
  let first = true
  while (i < p.length) {
    if (p[i] === ']' && !first) return { ok: ok !== neg, consumed: i + 1 }
    first = false
    let lo = p[i]
    if (lo === '\\\\' && i + 1 < p.length) { i++; lo = p[i] }
    let hi = lo
    if (i + 2 < p.length && p[i + 1] === '-' && p[i + 2] !== ']') { hi = p[i + 2]; i += 2 }
    if (lo.codePointAt(0)! <= c.codePointAt(0)! && c.codePointAt(0)! <= hi.codePointAt(0)!) ok = true
    i++
  }
  return null // unterminated
}

function matchSegment(p: string[], s: string[]): boolean {
  let pi = 0, si = 0, star = -1, mark = 0
  while (si < s.length) {
    let advanced = false
    if (pi < p.length) {
      const ch = p[pi]
      if (ch === '*') { star = pi; mark = si; pi++; continue }
      if (ch === '?') { pi++; si++; continue }
      if (ch === '[') {
        const cls = matchClass(p.slice(pi), s[si])
        if (cls) { if (cls.ok) { pi += cls.consumed; si++; advanced = true } }
        else if (s[si] === '[') { pi++; si++; advanced = true }
      } else if (ch === '\\\\') {
        if (pi + 1 < p.length && p[pi + 1] === s[si]) { pi += 2; si++; advanced = true }
      } else if (ch === s[si]) { pi++; si++; advanced = true }
    }
    if (advanced) continue
    if (star >= 0) { mark++; si = mark; pi = star + 1; continue }
    return false
  }
  while (pi < p.length && p[pi] === '*') pi++
  return pi === p.length
}
''', "glob.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { match } from './glob.ts'

const cases: [string, string, boolean][] = [
  ['*.ts', 'main.ts', true], ['*.ts', 'a/main.ts', false], ['*.ts', 'main.tsx', false],
  ['**/*.ts', 'main.ts', true], ['**/*.ts', 'a/b/c.ts', true], ['**/*.ts', 'a/b/c.js', false],
  ['a/**/b', 'a/b', true], ['a/**/b', 'a/x/y/b', true], ['a/**/b', 'a/x/y/c', false],
  ['src/**', 'src/a/b', true], ['src/**', 'src', true], ['src/**', 'other/a', false],
  ['**', '', true], ['**', 'any/thing', true],
  ['?.txt', 'a.txt', true], ['?.txt', 'ab.txt', false], ['?.txt', '/.txt', false],
  ['[abc].txt', 'b.txt', true], ['[abc].txt', 'd.txt', false], ['[a-c]x', 'bx', true], ['[!a]x', 'bx', true], ['[!a]x', 'ax', false],
  ['[^a-c]x', 'dx', true], ['[]]x', ']x', true],
  ['a\\\\*b', 'a*b', true], ['a\\\\*b', 'axb', false],
  ['[abc', '[abc', true], ['[abc', 'a', false],
  ['*', '', true], ['a*', 'a', true], ['*a*b*c*', 'xaxbxcx', true], ['*a*b*c*', 'xcxbxax', false],
  ['data/*/file_?.csv', 'data/2026/file_1.csv', true], ['data/*/file_?.csv', 'data/2026/x/file_1.csv', false],
  ['日*', '日本語', true], ['?', '日', true], ['?', '😀', true],
]
for (const [pat, name, want] of cases) test(`match(${JSON.stringify(pat)}, ${JSON.stringify(name)}) = ${want}`, () => assert.equal(match(pat, name), want))

test('no catastrophic backtracking', () => {
  const name = 'a'.repeat(2000)
  assert.equal(match('a*'.repeat(200) + 'b', name), false)
  assert.equal(match('a*'.repeat(200), name), true)
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["glob", "wildcard", "pattern", "match", "gitignore", "path", "algorithm", "doublestar", "typescript"],
   avoid="Converting globs to regexes by string replacement."),

# ------------------------------------------------------------------ duration
RC("go-parse-duration", "Parse and format human durations like 1h30m, 2d, 1.5h, 1w (Go)", "go",
   "CLI flags and config values like --since 2d12h or timeout=90s that time.ParseDuration cannot parse (no days/weeks).",
   "Regexp-driven component parser (number + unit: ns us µs ms s m h d w, optional spaces and leading sign), overflow checked; Format renders the compact form.",
   {"duration.go": '''// Package duration parses human durations including days (d) and weeks (w), which time.ParseDuration lacks.
package duration

import (
	"errors"
	"fmt"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var units = map[string]float64{"ns": 1, "us": 1e3, "µs": 1e3, "ms": 1e6, "s": 1e9, "m": 60e9, "h": 3600e9, "d": 86400e9, "w": 7 * 86400e9}

var part = regexp.MustCompile(`^\\s*(\\d+(?:\\.\\d*)?|\\.\\d+)\\s*(ns|us|µs|ms|s|m|h|d|w)`)

// Parse accepts "1h30m", "1d 12h", "-90s", "1.5h", "2w". A bare number without a unit is an error (ambiguous).
func Parse(s string) (time.Duration, error) {
	orig := s
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, errors.New("duration: empty string")
	}
	sign := 1.0
	if s[0] == '-' || s[0] == '+' {
		if s[0] == '-' {
			sign = -1
		}
		s = s[1:]
	}
	total := 0.0
	for strings.TrimSpace(s) != "" {
		m := part.FindStringSubmatch(s)
		if m == nil {
			return 0, fmt.Errorf("duration: invalid or missing unit in %q", orig)
		}
		v, err := strconv.ParseFloat(m[1], 64)
		if err != nil {
			return 0, fmt.Errorf("duration: %w", err)
		}
		total += v * units[m[2]]
		if total > math.MaxInt64 {
			return 0, fmt.Errorf("duration: %q overflows", orig)
		}
		s = s[len(m[0]):]
	}
	return time.Duration(sign * math.Round(total)), nil
}

// Format renders d compactly: 36h -> "1d12h", 1500ms -> "1s500ms", 0 -> "0s".
func Format(d time.Duration) string {
	if d == 0 {
		return "0s"
	}
	var b strings.Builder
	if d < 0 {
		b.WriteByte('-')
		d = -d
	}
	for _, u := range []struct {
		name string
		size time.Duration
	}{{"w", 7 * 24 * time.Hour}, {"d", 24 * time.Hour}, {"h", time.Hour}, {"m", time.Minute}, {"s", time.Second}, {"ms", time.Millisecond}} {
		if d >= u.size {
			fmt.Fprintf(&b, "%d%s", d/u.size, u.name)
			d %= u.size
		}
	}
	if d > 0 {
		fmt.Fprintf(&b, "%dns", d) // sub-millisecond remainder
	}
	return b.String()
}
''', "duration_test.go": '''package duration

import (
	"testing"
	"time"
)

func TestParse(t *testing.T) {
	ok := map[string]time.Duration{
		"90s": 90 * time.Second, "1h30m": 90 * time.Minute, "2d": 48 * time.Hour, "1w": 7 * 24 * time.Hour,
		"1d 12h": 36 * time.Hour, "1.5h": 90 * time.Minute, "-90s": -90 * time.Second, "+5m": 5 * time.Minute, "500ms": 500 * time.Millisecond,
		"1h30m15s": 90*time.Minute + 15*time.Second, " 10m ": 10 * time.Minute, "100us": 100 * time.Microsecond, "100µs": 100 * time.Microsecond,
		".5s": 500 * time.Millisecond, "2w3d": 17 * 24 * time.Hour,
	}
	for in, want := range ok {
		if got, err := Parse(in); err != nil || got != want {
			t.Errorf("Parse(%q) = %v, %v; want %v", in, got, err, want)
		}
	}
	for _, in := range []string{"", "  ", "10", "h", "1x", "1h 30", "abc", "1..5h", "--5s", "1h-30m", "9999999999999999w"} {
		if _, err := Parse(in); err == nil {
			t.Errorf("Parse(%q) should fail", in)
		}
	}
}

func TestFormatRoundTrip(t *testing.T) {
	cases := map[time.Duration]string{0: "0s", 36 * time.Hour: "1d12h", 90 * time.Minute: "1h30m", 1500 * time.Millisecond: "1s500ms",
		-2 * time.Hour: "-2h", 8 * 24 * time.Hour: "1w1d", 1500 * time.Nanosecond: "1500ns", time.Hour + time.Second: "1h1s"}
	for d, want := range cases {
		if got := Format(d); got != want {
			t.Errorf("Format(%v) = %q, want %q", d, got, want)
		}
		if back, err := Parse(Format(d)); err != nil || back != d {
			t.Errorf("round trip %v -> %q -> %v (%v)", d, Format(d), back, err)
		}
	}
}
'''}, GOT, assumptions=["Go >= 1.16"], compat={"go": ">=1.16"},
   tags=["duration", "parse", "time", "human", "since", "timeout", "cli", "flags", "days", "weeks"],
   avoid="time.ParseDuration for user input that may contain days; treating a bare number as seconds silently."),

RC("ts-parse-duration", "Parse and format human durations like 1h30m, 2d, 1.5h, 1w (TypeScript)", "typescript",
   "CLI flags and config values like --since 2d12h or timeout=90s.",
   "Regexp component parser (number + unit ns us ms s m h d w, optional spaces and sign) returning milliseconds with safe-integer checks; format renders the compact form.",
   {"duration.ts": '''const UNITS: Record<string, number> = { ns: 1e-6, us: 1e-3, 'µs': 1e-3, ms: 1, s: 1e3, m: 60e3, h: 3600e3, d: 86400e3, w: 7 * 86400e3 }
const PART = /^\\s*(\\d+(?:\\.\\d*)?|\\.\\d+)\\s*(ns|us|µs|ms|s|m|h|d|w)/

/** Parses "1h30m", "1d 12h", "-90s", "1.5h" into milliseconds. A bare number without a unit throws (ambiguous). */
export function parseDuration(input: string): number {
  let s = input.trim()
  if (s === '') throw new Error('duration: empty string')
  let sign = 1
  if (s[0] === '-' || s[0] === '+') { if (s[0] === '-') sign = -1; s = s.slice(1) }
  let total = 0
  while (s.trim() !== '') {
    const m = PART.exec(s)
    if (!m) throw new Error(`duration: invalid or missing unit in "${input}"`)
    total += parseFloat(m[1]) * UNITS[m[2]]
    if (!Number.isSafeInteger(Math.round(total))) throw new Error(`duration: "${input}" is too large`)
    s = s.slice(m[0].length)
  }
  return sign * Math.round(total)
}

/** 36h -> "1d12h", 1500 -> "1s500ms", 0 -> "0s". */
export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) throw new Error('duration: not finite')
  ms = Math.round(ms)
  if (ms === 0) return '0s'
  let out = ms < 0 ? '-' : ''
  ms = Math.abs(ms)
  for (const [name, size] of [['w', 7 * 86400e3], ['d', 86400e3], ['h', 3600e3], ['m', 60e3], ['s', 1e3], ['ms', 1]] as const) {
    if (ms >= size) { out += `${Math.floor(ms / size)}${name}`; ms %= size }
  }
  return out
}
''', "duration.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseDuration, formatDuration } from './duration.ts'

test('parse valid', () => {
  const ok: Record<string, number> = {
    '90s': 90e3, '1h30m': 5400e3, '2d': 172800e3, '1w': 604800e3, '1d 12h': 129600e3, '1.5h': 5400e3, '-90s': -90e3, '+5m': 300e3,
    '500ms': 500, ' 10m ': 600e3, '100us': 0.1, '.5s': 500, '2w3d': 17 * 86400e3, '1h30m15s': 5415e3,
  }
  for (const [input, want] of Object.entries(ok)) assert.equal(parseDuration(input), Math.round(want), input)
})

test('parse invalid', () => {
  for (const bad of ['', '  ', '10', 'h', '1x', '1h 30', 'abc', '--5s', '1h-30m', '9'.repeat(30) + 'w']) {
    assert.throws(() => parseDuration(bad), Error, JSON.stringify(bad))
  }
})

test('format and round trip', () => {
  const cases: [number, string][] = [[0, '0s'], [129600e3, '1d12h'], [5400e3, '1h30m'], [1500, '1s500ms'], [-7200e3, '-2h'], [8 * 86400e3, '1w1d'], [3601e3, '1h1s']]
  for (const [ms, want] of cases) {
    assert.equal(formatDuration(ms), want)
    assert.equal(parseDuration(formatDuration(ms)), ms)
  }
  assert.throws(() => formatDuration(NaN))
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["duration", "parse", "time", "human", "since", "timeout", "cli", "flags", "days", "weeks", "typescript"],
   avoid="Treating a bare number as seconds silently; floating-point ms without rounding."),

# ------------------------------------------------------------------ semver
RC("go-semver", "Semantic version parsing, comparison and sorting (Go)", "go",
   "Comparing versions correctly (1.10 > 1.9, prereleases before releases) for update checks and dependency tools.",
   "Strict SemVer 2.0.0 parser (optional leading v, no leading zeros) and the spec's precedence rules: numeric vs alphanumeric prerelease identifiers, build metadata ignored.",
   {"semver.go": '''// Package semver implements Semantic Versioning 2.0.0 parsing and precedence.
package semver

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type Version struct {
	Major, Minor, Patch uint64
	Pre                 []string
	Build               string
}

var re = regexp.MustCompile(`^v?(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-((?:0|[1-9]\\d*|\\d*[A-Za-z-][0-9A-Za-z-]*)(?:\\.(?:0|[1-9]\\d*|\\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\\+([0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*))?$`)

func Parse(s string) (Version, error) {
	m := re.FindStringSubmatch(strings.TrimSpace(s))
	if m == nil {
		return Version{}, fmt.Errorf("semver: invalid version %q", s)
	}
	var v Version
	var err error
	if v.Major, err = strconv.ParseUint(m[1], 10, 64); err != nil {
		return Version{}, err
	}
	if v.Minor, err = strconv.ParseUint(m[2], 10, 64); err != nil {
		return Version{}, err
	}
	if v.Patch, err = strconv.ParseUint(m[3], 10, 64); err != nil {
		return Version{}, err
	}
	if m[4] != "" {
		v.Pre = strings.Split(m[4], ".")
	}
	v.Build = m[5]
	return v, nil
}

func (v Version) String() string {
	s := fmt.Sprintf("%d.%d.%d", v.Major, v.Minor, v.Patch)
	if len(v.Pre) > 0 {
		s += "-" + strings.Join(v.Pre, ".")
	}
	if v.Build != "" {
		s += "+" + v.Build
	}
	return s
}

func cmpU(a, b uint64) int {
	switch {
	case a < b:
		return -1
	case a > b:
		return 1
	}
	return 0
}

func isNum(s string) bool { _, err := strconv.ParseUint(s, 10, 64); return err == nil }

// Compare returns -1, 0, 1. Build metadata is ignored; a prerelease sorts before its release.
func Compare(a, b Version) int {
	if c := cmpU(a.Major, b.Major); c != 0 {
		return c
	}
	if c := cmpU(a.Minor, b.Minor); c != 0 {
		return c
	}
	if c := cmpU(a.Patch, b.Patch); c != 0 {
		return c
	}
	switch {
	case len(a.Pre) == 0 && len(b.Pre) == 0:
		return 0
	case len(a.Pre) == 0:
		return 1
	case len(b.Pre) == 0:
		return -1
	}
	for i := 0; i < len(a.Pre) && i < len(b.Pre); i++ {
		x, y := a.Pre[i], b.Pre[i]
		xn, yn := isNum(x), isNum(y)
		switch {
		case xn && yn:
			xv, _ := strconv.ParseUint(x, 10, 64)
			yv, _ := strconv.ParseUint(y, 10, 64)
			if c := cmpU(xv, yv); c != 0 {
				return c
			}
		case xn:
			return -1 // numeric identifiers sort before alphanumeric
		case yn:
			return 1
		default:
			if c := strings.Compare(x, y); c != 0 {
				return c
			}
		}
	}
	return cmpU(uint64(len(a.Pre)), uint64(len(b.Pre))) // a longer set of identifiers wins when all earlier ones are equal
}

func Sort(vs []Version) { sort.SliceStable(vs, func(i, j int) bool { return Compare(vs[i], vs[j]) < 0 }) }
''', "semver_test.go": '''package semver

import "testing"

func mustParse(t *testing.T, s string) Version {
	t.Helper()
	v, err := Parse(s)
	if err != nil {
		t.Fatal(s, err)
	}
	return v
}

func TestSpecOrdering(t *testing.T) {
	order := []string{"1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
		"1.0.1", "1.9.0", "1.10.0", "2.0.0"}
	for i := range order {
		for j := range order {
			want := 0
			if i < j {
				want = -1
			} else if i > j {
				want = 1
			}
			if got := Compare(mustParse(t, order[i]), mustParse(t, order[j])); got != want {
				t.Errorf("Compare(%s, %s) = %d, want %d", order[i], order[j], got, want)
			}
		}
	}
}

func TestParseAndString(t *testing.T) {
	v := mustParse(t, "v1.2.3-rc.1+build.5")
	if v.Major != 1 || v.Minor != 2 || v.Patch != 3 || len(v.Pre) != 2 || v.Build != "build.5" || v.String() != "1.2.3-rc.1+build.5" {
		t.Fatalf("%+v %s", v, v)
	}
	for _, bad := range []string{"", "1", "1.2", "1.2.3.4", "01.2.3", "1.2.03", "1.2.3-", "1.2.3-01", "1.2.3+", "a.b.c", "1.2.3-rc..1", "v"} {
		if _, err := Parse(bad); err == nil {
			t.Errorf("Parse(%q) should fail", bad)
		}
	}
}

func TestBuildIgnoredAndSort(t *testing.T) {
	if Compare(mustParse(t, "1.0.0+a"), mustParse(t, "1.0.0+b")) != 0 {
		t.Fatal("build metadata must be ignored")
	}
	vs := []Version{mustParse(t, "1.10.0"), mustParse(t, "1.9.0"), mustParse(t, "1.10.0-rc.1"), mustParse(t, "0.9.9")}
	Sort(vs)
	got := ""
	for _, v := range vs {
		got += v.String() + " "
	}
	if got != "0.9.9 1.9.0 1.10.0-rc.1 1.10.0 " {
		t.Fatal(got)
	}
}
'''}, GOT, assumptions=["Go >= 1.16"], compat={"go": ">=1.16"},
   tags=["semver", "version", "compare", "sort", "prerelease", "update", "dependency", "algorithm"],
   avoid="Comparing versions as strings or splitting on '.' and comparing ints without handling prereleases."),

RC("ts-semver", "Semantic version parsing, comparison and sorting (TypeScript)", "typescript",
   "Comparing versions correctly (1.10 > 1.9, prereleases before releases).",
   "Strict SemVer 2.0.0 regexp parser and precedence rules; numeric identifiers before alphanumeric; build metadata ignored; compare usable directly in Array.sort.",
   {"semver.ts": '''export interface Version { major: number; minor: number; patch: number; pre: string[]; build: string }

const RE = /^v?(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)(?:-((?:0|[1-9]\\d*|\\d*[A-Za-z-][0-9A-Za-z-]*)(?:\\.(?:0|[1-9]\\d*|\\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\\+([0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*))?$/

export function parse(input: string): Version {
  const m = RE.exec(input.trim())
  if (!m) throw new Error(`semver: invalid version "${input}"`)
  const nums = [m[1], m[2], m[3]].map(Number)
  if (!nums.every(Number.isSafeInteger)) throw new Error(`semver: number too large in "${input}"`)
  return { major: nums[0], minor: nums[1], patch: nums[2], pre: m[4] ? m[4].split('.') : [], build: m[5] ?? '' }
}

export function format(v: Version): string {
  return `${v.major}.${v.minor}.${v.patch}${v.pre.length ? '-' + v.pre.join('.') : ''}${v.build ? '+' + v.build : ''}`
}

const isNum = (s: string) => /^\\d+$/.test(s)
const cmp = (a: number, b: number): -1 | 0 | 1 => (a < b ? -1 : a > b ? 1 : 0)

/** -1 | 0 | 1 by SemVer precedence; build metadata ignored; a prerelease sorts before its release. */
export function compare(a: Version, b: Version): -1 | 0 | 1 {
  const base = cmp(a.major, b.major) || cmp(a.minor, b.minor) || cmp(a.patch, b.patch)
  if (base) return base
  if (!a.pre.length && !b.pre.length) return 0
  if (!a.pre.length) return 1
  if (!b.pre.length) return -1
  for (let i = 0; i < Math.min(a.pre.length, b.pre.length); i++) {
    const x = a.pre[i], y = b.pre[i]
    if (x === y) continue
    const xn = isNum(x), yn = isNum(y)
    if (xn && yn) return cmp(Number(x), Number(y))
    if (xn) return -1
    if (yn) return 1
    return x < y ? -1 : 1
  }
  return cmp(a.pre.length, b.pre.length)
}

export function sort(versions: string[]): string[] {
  return versions.map(parse).sort(compare).map(format)
}
''', "semver.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, format, compare, sort } from './semver.ts'

test('spec ordering', () => {
  const order = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta', '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0', '1.0.1', '1.9.0', '1.10.0', '2.0.0']
  for (let i = 0; i < order.length; i++) for (let j = 0; j < order.length; j++) {
    assert.equal(compare(parse(order[i]), parse(order[j])), Math.sign(i - j), `${order[i]} vs ${order[j]}`)
  }
})

test('parse, format, invalid', () => {
  const v = parse('v1.2.3-rc.1+build.5')
  assert.deepEqual([v.major, v.minor, v.patch, v.pre, v.build], [1, 2, 3, ['rc', '1'], 'build.5'])
  assert.equal(format(v), '1.2.3-rc.1+build.5')
  for (const bad of ['', '1', '1.2', '1.2.3.4', '01.2.3', '1.2.03', '1.2.3-', '1.2.3-01', '1.2.3+', 'a.b.c', '1.2.3-rc..1', 'v', '99999999999999999999.0.0']) {
    assert.throws(() => parse(bad), Error, bad)
  }
})

test('build ignored and sort', () => {
  assert.equal(compare(parse('1.0.0+a'), parse('1.0.0+b')), 0)
  assert.deepEqual(sort(['1.10.0', '1.9.0', '1.10.0-rc.1', '0.9.9']), ['0.9.9', '1.9.0', '1.10.0-rc.1', '1.10.0'])
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["semver", "version", "compare", "sort", "prerelease", "update", "dependency", "algorithm", "typescript"],
   avoid="Comparing version strings lexicographically."),

# ------------------------------------------------------------------ diff
RC("go-myers-diff", "Myers O(ND) line diff with unified output (Go)", "go",
   "Showing what changed between two texts or states (config preview, dry-run output, test failure diffs) without shelling out to diff.",
   "Myers' greedy shortest-edit-script algorithm with a trace for backtracking, producing Equal/Insert/Delete edits; Unified groups edits into hunks with context and GNU-style @@ headers.",
   {"diff.go": '''// Package diff computes a minimal line diff (Myers, O(ND)) and renders unified hunks.
package diff

import (
	"fmt"
	"strings"
)

type Op int

const (
	Equal Op = iota
	Insert
	Delete
)

type Edit struct {
	Op   Op
	Line string
	A, B int // index in a / b before this edit is applied
}

// Diff returns a shortest edit script turning a into b.
func Diff(a, b []string) []Edit {
	n, m := len(a), len(b)
	max := n + m
	if max == 0 {
		return nil
	}
	off := max
	v := make([]int, 2*max+2)
	var trace [][]int
	for d := 0; d <= max; d++ {
		snap := make([]int, len(v))
		copy(snap, v)
		trace = append(trace, snap) // state before round d
		for k := -d; k <= d; k += 2 {
			var x int
			if k == -d || (k != d && v[off+k-1] < v[off+k+1]) {
				x = v[off+k+1]
			} else {
				x = v[off+k-1] + 1
			}
			y := x - k
			for x < n && y < m && a[x] == b[y] {
				x++
				y++
			}
			v[off+k] = x
			if x >= n && y >= m {
				return backtrack(a, b, trace, d, off)
			}
		}
	}
	return nil
}

func backtrack(a, b []string, trace [][]int, D, off int) []Edit {
	x, y := len(a), len(b)
	var rev []Edit
	for d := D; d > 0; d-- {
		v := trace[d]
		k := x - y
		var pk int
		if k == -d || (k != d && v[off+k-1] < v[off+k+1]) {
			pk = k + 1
		} else {
			pk = k - 1
		}
		px := v[off+pk]
		py := px - pk
		for x > px && y > py { // the diagonal (snake) of equal lines
			x--
			y--
			rev = append(rev, Edit{Equal, a[x], x, y})
		}
		if x == px { // came from above: insertion
			y--
			rev = append(rev, Edit{Insert, b[y], x, y})
		} else { // came from the left: deletion
			x--
			rev = append(rev, Edit{Delete, a[x], x, y})
		}
	}
	for x > 0 && y > 0 { // leading snake
		x--
		y--
		rev = append(rev, Edit{Equal, a[x], x, y})
	}
	out := make([]Edit, len(rev))
	for i, e := range rev {
		out[len(rev)-1-i] = e
	}
	return out
}

func rng(start, count int) string {
	switch count {
	case 0:
		return fmt.Sprintf("%d,0", start)
	case 1:
		return fmt.Sprintf("%d", start+1)
	}
	return fmt.Sprintf("%d,%d", start+1, count)
}

// Unified renders GNU-style unified diff text ("" when a and b are equal).
func Unified(aName, bName string, a, b []string, context int) string {
	edits := Diff(a, b)
	var changes [][2]int // inclusive edit-index ranges of non-equal runs
	for i := 0; i < len(edits); {
		if edits[i].Op == Equal {
			i++
			continue
		}
		j := i
		for j+1 < len(edits) && edits[j+1].Op != Equal {
			j++
		}
		changes = append(changes, [2]int{i, j})
		i = j + 1
	}
	if len(changes) == 0 {
		return ""
	}
	var groups [][2]int
	cur := changes[0]
	for _, c := range changes[1:] {
		if c[0]-cur[1]-1 <= 2*context {
			cur[1] = c[1]
		} else {
			groups = append(groups, cur)
			cur = c
		}
	}
	groups = append(groups, cur)
	var sb strings.Builder
	fmt.Fprintf(&sb, "--- %s\\n+++ %s\\n", aName, bName)
	for _, g := range groups {
		lo, hi := max(0, g[0]-context), min(len(edits)-1, g[1]+context)
		ac, bc := 0, 0
		for _, e := range edits[lo : hi+1] {
			if e.Op != Insert {
				ac++
			}
			if e.Op != Delete {
				bc++
			}
		}
		fmt.Fprintf(&sb, "@@ -%s +%s @@\\n", rng(edits[lo].A, ac), rng(edits[lo].B, bc))
		for _, e := range edits[lo : hi+1] {
			p := " "
			if e.Op == Insert {
				p = "+"
			} else if e.Op == Delete {
				p = "-"
			}
			sb.WriteString(p + e.Line + "\\n")
		}
	}
	return sb.String()
}
''', "diff_test.go": '''package diff

import (
	"math/rand"
	"reflect"
	"strings"
	"testing"
)

func apply(a []string, es []Edit) (gotA, gotB []string) {
	for _, e := range es {
		if e.Op != Insert {
			gotA = append(gotA, e.Line)
		}
		if e.Op != Delete {
			gotB = append(gotB, e.Line)
		}
	}
	return
}

func lcs(a, b []string) int {
	dp := make([][]int, len(a)+1)
	for i := range dp {
		dp[i] = make([]int, len(b)+1)
	}
	for i := 1; i <= len(a); i++ {
		for j := 1; j <= len(b); j++ {
			if a[i-1] == b[j-1] {
				dp[i][j] = dp[i-1][j-1] + 1
			} else {
				dp[i][j] = max(dp[i-1][j], dp[i][j-1])
			}
		}
	}
	return dp[len(a)][len(b)]
}

func TestPropertyReproducesBothSidesAndIsMinimal(t *testing.T) {
	r := rand.New(rand.NewSource(1))
	for iter := 0; iter < 500; iter++ {
		a, b := make([]string, r.Intn(13)), make([]string, r.Intn(13))
		for i := range a {
			a[i] = string(rune('a' + r.Intn(4)))
		}
		for i := range b {
			b[i] = string(rune('a' + r.Intn(4)))
		}
		es := Diff(a, b)
		ga, gb := apply(a, es)
		if !reflect.DeepEqual(append([]string{}, ga...), append([]string{}, a...)) || !reflect.DeepEqual(append([]string{}, gb...), append([]string{}, b...)) {
			t.Fatalf("edits do not reproduce inputs: a=%v b=%v edits=%v", a, b, es)
		}
		changes := 0
		for _, e := range es {
			if e.Op != Equal {
				changes++
			}
		}
		if want := len(a) + len(b) - 2*lcs(a, b); changes != want {
			t.Fatalf("not minimal: %d edits, want %d (a=%v b=%v)", changes, want, a, b)
		}
	}
}

func TestClassicExample(t *testing.T) {
	a, b := strings.Split("A B C A B B A", " "), strings.Split("C B A B A C", " ")
	changes := 0
	for _, e := range Diff(a, b) {
		if e.Op != Equal {
			changes++
		}
	}
	if changes != 5 { // Myers' paper: D = 5
		t.Fatal(changes)
	}
}

func TestUnified(t *testing.T) {
	a := strings.Split("a b c d e f g h", " ")
	b := strings.Split("a b c X e f g h", " ")
	want := "--- old\\n+++ new\\n@@ -1,7 +1,7 @@\\n a\\n b\\n c\\n-d\\n+X\\n e\\n f\\n g\\n"
	if got := Unified("old", "new", a, b, 3); got != want {
		t.Fatalf("got:\\n%s\\nwant:\\n%s", got, want)
	}
	if Unified("a", "b", a, a, 3) != "" {
		t.Fatal("equal inputs give empty diff")
	}
	if got := Unified("a", "b", nil, []string{"x"}, 3); !strings.Contains(got, "@@ -0,0 +1 @@\\n+x\\n") {
		t.Fatal(got)
	}
}

func TestUnifiedSplitsDistantHunks(t *testing.T) {
	a := make([]string, 30)
	for i := range a {
		a[i] = "line" + string(rune('A'+i)) // distinct lines: with identical lines Myers may place changes anywhere
	}
	b := append([]string{}, a...)
	b[2], b[27] = "X", "Y"
	if n := strings.Count(Unified("a", "b", a, b, 3), "@@ -"); n != 2 {
		t.Fatalf("want 2 hunks, got %d", n)
	}
	b2 := append([]string{}, a...)
	b2[2], b2[6] = "X", "Y" // gap of 3 lines <= 2*context: one merged hunk
	if n := strings.Count(Unified("a", "b", a, b2, 3), "@@ -"); n != 1 {
		t.Fatalf("want 1 hunk, got %d", n)
	}
}
'''}, GOT, assumptions=["Go >= 1.21 (builtin min/max)", "O((N+M)·D) memory for the trace: fine for files, use patience/histogram or limits for huge inputs"],
   compat={"go": ">=1.21"}, tags=["diff", "myers", "unified diff", "edit script", "compare", "text", "algorithm", "patch", "lcs"],
   avoid="Quadratic LCS tables for large files, or shelling out to diff(1) from library code."),

RC("ts-myers-diff", "Myers O(ND) line diff with unified output (TypeScript)", "typescript",
   "Showing what changed between two texts or states (config preview, dry-run output, test diffs).",
   "Myers' shortest-edit-script algorithm with a trace for backtracking; edits are equal/insert/delete; unified() groups hunks with context and GNU-style @@ headers.",
   {"diff.ts": '''export type Op = 'equal' | 'insert' | 'delete'
export interface Edit { op: Op; line: string; a: number; b: number } // a/b = index in each input before this edit

export function diff(a: string[], b: string[]): Edit[] {
  const n = a.length, m = b.length, max = n + m
  if (max === 0) return []
  const off = max
  const v = new Array<number>(2 * max + 2).fill(0)
  const trace: number[][] = []
  for (let d = 0; d <= max; d++) {
    trace.push(v.slice()) // state before round d
    for (let k = -d; k <= d; k += 2) {
      let x = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1])) ? v[off + k + 1] : v[off + k - 1] + 1
      let y = x - k
      while (x < n && y < m && a[x] === b[y]) { x++; y++ }
      v[off + k] = x
      if (x >= n && y >= m) return backtrack(a, b, trace, d, off)
    }
  }
  return []
}

function backtrack(a: string[], b: string[], trace: number[][], D: number, off: number): Edit[] {
  let x = a.length, y = b.length
  const rev: Edit[] = []
  for (let d = D; d > 0; d--) {
    const v = trace[d], k = x - y
    const pk = (k === -d || (k !== d && v[off + k - 1] < v[off + k + 1])) ? k + 1 : k - 1
    const px = v[off + pk], py = px - pk
    while (x > px && y > py) { x--; y--; rev.push({ op: 'equal', line: a[x], a: x, b: y }) }
    if (x === px) { y--; rev.push({ op: 'insert', line: b[y], a: x, b: y }) }
    else { x--; rev.push({ op: 'delete', line: a[x], a: x, b: y }) }
  }
  while (x > 0 && y > 0) { x--; y--; rev.push({ op: 'equal', line: a[x], a: x, b: y }) }
  return rev.reverse()
}

const range = (start: number, count: number) => (count === 0 ? `${start},0` : count === 1 ? `${start + 1}` : `${start + 1},${count}`)

/** GNU-style unified diff; '' when a and b are equal. */
export function unified(aName: string, bName: string, a: string[], b: string[], context = 3): string {
  const edits = diff(a, b)
  const changes: [number, number][] = []
  for (let i = 0; i < edits.length;) {
    if (edits[i].op === 'equal') { i++; continue }
    let j = i
    while (j + 1 < edits.length && edits[j + 1].op !== 'equal') j++
    changes.push([i, j]); i = j + 1
  }
  if (!changes.length) return ''
  const groups: [number, number][] = []
  let cur = changes[0]
  for (const c of changes.slice(1)) {
    if (c[0] - cur[1] - 1 <= 2 * context) cur = [cur[0], c[1]]
    else { groups.push(cur); cur = c }
  }
  groups.push(cur)
  let out = `--- ${aName}\\n+++ ${bName}\\n`
  for (const [s, e] of groups) {
    const lo = Math.max(0, s - context), hi = Math.min(edits.length - 1, e + context)
    const hunk = edits.slice(lo, hi + 1)
    const ac = hunk.filter((x) => x.op !== 'insert').length, bc = hunk.filter((x) => x.op !== 'delete').length
    out += `@@ -${range(hunk[0].a, ac)} +${range(hunk[0].b, bc)} @@\\n`
    for (const x of hunk) out += (x.op === 'insert' ? '+' : x.op === 'delete' ? '-' : ' ') + x.line + '\\n'
  }
  return out
}
''', "diff.test.ts": '''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { diff, unified } from './diff.ts'

function lcs(a: string[], b: string[]): number {
  const dp = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0))
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1])
  }
  return dp[a.length][b.length]
}

function lcg(seed: number) { let s = seed; return () => (s = (s * 1664525 + 1013904223) % 4294967296) / 4294967296 }

test('property: reproduces both sides and is minimal', () => {
  const rnd = lcg(1)
  for (let iter = 0; iter < 500; iter++) {
    const mk = () => Array.from({ length: Math.floor(rnd() * 13) }, () => String.fromCharCode(97 + Math.floor(rnd() * 4)))
    const a = mk(), b = mk(), es = diff(a, b)
    assert.deepEqual(es.filter((e) => e.op !== 'insert').map((e) => e.line), a)
    assert.deepEqual(es.filter((e) => e.op !== 'delete').map((e) => e.line), b)
    assert.equal(es.filter((e) => e.op !== 'equal').length, a.length + b.length - 2 * lcs(a, b), JSON.stringify([a, b]))
  }
})

test('classic Myers example has D = 5', () => {
  const es = diff('A B C A B B A'.split(' '), 'C B A B A C'.split(' '))
  assert.equal(es.filter((e) => e.op !== 'equal').length, 5)
})

test('unified output', () => {
  const a = 'a b c d e f g h'.split(' '), b = 'a b c X e f g h'.split(' ')
  assert.equal(unified('old', 'new', a, b), '--- old\\n+++ new\\n@@ -1,7 +1,7 @@\\n a\\n b\\n c\\n-d\\n+X\\n e\\n f\\n g\\n')
  assert.equal(unified('a', 'b', a, a), '')
  assert.ok(unified('a', 'b', [], ['x']).includes('@@ -0,0 +1 @@\\n+x\\n'))
})

test('distant changes split into hunks, near ones merge', () => {
  const a = Array.from({ length: 30 }, (_, i) => `line${i}`) // distinct lines: identical lines would let Myers place changes anywhere
  const far = a.slice(); far[2] = 'X'; far[27] = 'Y'
  assert.equal(unified('a', 'b', a, far).split('@@ -').length - 1, 2)
  const near = a.slice(); near[2] = 'X'; near[6] = 'Y'
  assert.equal(unified('a', 'b', a, near).split('@@ -').length - 1, 1)
})
'''}, NODE_TS, assumptions=TSA, compat={"node": ">=22.18"},
   tags=["diff", "myers", "unified diff", "edit script", "compare", "text", "algorithm", "patch", "lcs", "typescript"],
   avoid="Quadratic LCS tables for large inputs."),
]
