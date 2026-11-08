"""Round-4 recipes, part E: TypeScript language tooling and static-site building blocks (node --test), Go concurrency pipeline."""
from .recipes import GOT, RC
from .recipes_algo import NODE_TS, TSA

R4E = [
RC("ts-lexer-pratt-parser-safe-evaluator", "Expression language: tokenizer with positions, Pratt parser (precedence, right-assoc power, ternary, calls), AST walk/format, safe evaluator, line:col errors", "typescript",
   "Let users type formulas, filters or rules (`price * qty > 100 ? 'big' : 'small'`) without ever calling eval(), and report mistakes precisely.",
   "A hand-written lexer that records every token's offset, a Pratt (precedence-climbing) parser driven by one binding-power table (left-assoc by default, `^` right-assoc, unary minus binding looser than `^` so -2^2 = -4, lazy ternary), an AST as a discriminated union with a printer and variable collector, an evaluator that only reaches own properties of a variables object and a whitelist of functions (so `constructor`/`__proto__` are just unknown names), short-circuit logic, division-by-zero errors, and limits on length/tokens/nesting so hostile input cannot hang or overflow the stack. Every error carries line, column and a caret excerpt.",
   {"expr.ts": r'''export class ExprError extends Error {
  pos: number
  line: number
  col: number
  excerpt: string
  constructor(message: string, source: string, pos: number) {
    super(message)
    this.name = 'ExprError'
    this.pos = pos
    const before = source.slice(0, pos)
    this.line = before.split('\n').length
    this.col = pos - (before.lastIndexOf('\n') + 1) + 1
    this.excerpt = `${source.split('\n')[this.line - 1] ?? ''}\n${' '.repeat(this.col - 1)}^`
  }
  format(): string { return `${this.message} at ${this.line}:${this.col}\n${this.excerpt}` }
}

export type Tok = { k: 'num' | 'id' | 'op' | 'eof'; s: string; pos: number; n?: number }
export type Node =
  | { t: 'num'; v: number; pos: number }
  | { t: 'var'; name: string; pos: number }
  | { t: 'un'; op: '-' | '!'; arg: Node; pos: number }
  | { t: 'bin'; op: string; l: Node; r: Node; pos: number }
  | { t: 'cond'; test: Node; then: Node; else: Node; pos: number }
  | { t: 'call'; fn: string; args: Node[]; pos: number }

const MAX_LEN = 10_000, MAX_TOKENS = 2_000, MAX_DEPTH = 100
const OPS = ['<=', '>=', '==', '!=', '&&', '||', '**', '+', '-', '*', '/', '%', '^', '(', ')', ',', '?', ':', '<', '>', '!']
const NUM = /(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y
const ID = /[A-Za-z_][A-Za-z0-9_]*/y

export function tokenize(src: string): Tok[] {
  if (src.length > MAX_LEN) throw new ExprError(`expression longer than ${MAX_LEN} characters`, src, 0)
  const out: Tok[] = []
  let i = 0
  while (i < src.length) {
    if (/\s/.test(src[i])) { i++; continue }
    if (out.length >= MAX_TOKENS) throw new ExprError('expression too complex', src, i)
    NUM.lastIndex = i
    const n = NUM.exec(src)
    if (n) { out.push({ k: 'num', s: n[0], pos: i, n: Number(n[0]) }); i += n[0].length; continue }
    ID.lastIndex = i
    const id = ID.exec(src)
    if (id) { out.push({ k: 'id', s: id[0], pos: i }); i += id[0].length; continue }
    const op = OPS.find(o => src.startsWith(o, i))
    if (!op) throw new ExprError(`unexpected character '${src[i]}'`, src, i)
    out.push({ k: 'op', s: op === '**' ? '^' : op, pos: i })
    i += op.length
  }
  out.push({ k: 'eof', s: '', pos: src.length })
  return out
}

// binding powers: higher binds tighter. Ternary is 5, unary is 70, calls are handled in prefix().
const BP: Record<string, number> = { '||': 10, '&&': 20, '==': 30, '!=': 30, '<': 40, '>': 40, '<=': 40, '>=': 40, '+': 50, '-': 50, '*': 60, '/': 60, '%': 60, '^': 80 }

export function parse(src: string): Node {
  const toks = tokenize(src)
  let p = 0, depth = 0
  const peek = () => toks[p]
  const isOp = (s: string) => peek().k === 'op' && peek().s === s
  const expect = (s: string) => {
    const t = toks[p++]
    if (t.k !== 'op' || t.s !== s) throw new ExprError(t.k === 'eof' ? `expected '${s}' but the expression ended` : `expected '${s}'`, src, t.pos)
  }

  function prefix(): Node {
    const t = toks[p++]
    if (t.k === 'num') return { t: 'num', v: t.n as number, pos: t.pos }
    if (t.k === 'id') {
      if (!isOp('(')) return { t: 'var', name: t.s, pos: t.pos }
      p++
      const args: Node[] = []
      if (!isOp(')')) { do { args.push(expr(0)) } while (isOp(',') && ++p) }
      expect(')')
      return { t: 'call', fn: t.s, args, pos: t.pos }
    }
    if (t.k === 'op') {
      if (t.s === '(') { const e = expr(0); expect(')'); return e }
      if (t.s === '-' || t.s === '!') return { t: 'un', op: t.s, arg: expr(70), pos: t.pos }
      if (t.s === '+') return expr(70)
    }
    throw new ExprError(t.k === 'eof' ? 'unexpected end of expression' : `unexpected '${t.s}'`, src, t.pos)
  }

  function expr(min: number): Node {
    if (++depth > MAX_DEPTH) throw new ExprError('expression nested too deeply', src, peek().pos)
    let left = prefix()
    for (;;) {
      const t = peek()
      if (t.k !== 'op') break
      if (t.s === '?' && min < 5) {
        p++
        const a = expr(0)
        expect(':')
        left = { t: 'cond', test: left, then: a, else: expr(4), pos: t.pos }   // else-branch may itself be a ternary: right-assoc
        continue
      }
      const bp = BP[t.s]
      if (bp === undefined || bp <= min) break
      p++
      left = { t: 'bin', op: t.s, l: left, r: expr(t.s === '^' ? bp - 1 : bp), pos: t.pos }   // bp-1 lets the same operator continue: right-assoc
    }
    depth--
    return left
  }

  const ast = expr(0)
  if (peek().k !== 'eof') throw new ExprError(`unexpected '${peek().s}'`, src, peek().pos)
  return ast
}

export function format(n: Node): string {
  switch (n.t) {
    case 'num': return String(n.v)
    case 'var': return n.name
    case 'un': return `(${n.op === '-' ? 'neg' : 'not'} ${format(n.arg)})`
    case 'bin': return `(${n.op} ${format(n.l)} ${format(n.r)})`
    case 'cond': return `(? ${format(n.test)} ${format(n.then)} ${format(n.else)})`
    case 'call': return `(${n.fn}${n.args.map(a => ' ' + format(a)).join('')})`
  }
}

export function walk(n: Node, visit: (n: Node) => void): void {
  visit(n)
  if (n.t === 'un') walk(n.arg, visit)
  else if (n.t === 'bin') { walk(n.l, visit); walk(n.r, visit) }
  else if (n.t === 'cond') { walk(n.test, visit); walk(n.then, visit); walk(n.else, visit) }
  else if (n.t === 'call') n.args.forEach(a => walk(a, visit))
}

export const variables = (n: Node): string[] => { const s = new Set<string>(); walk(n, x => { if (x.t === 'var') s.add(x.name) }); return [...s] }

export type Fns = Record<string, (...a: number[]) => number>
export const DEFAULT_FNS: Fns = { abs: Math.abs, min: Math.min, max: Math.max, sqrt: Math.sqrt, round: Math.round, floor: Math.floor, ceil: Math.ceil, pow: Math.pow, log: Math.log, exp: Math.exp }

export function compile(src: string, fns: Fns = DEFAULT_FNS): (vars?: Record<string, number>) => number {
  const ast = parse(src)
  const fail = (m: string, pos: number): never => { throw new ExprError(m, src, pos) }
  const run = (n: Node, env: Record<string, number>): number => {
    switch (n.t) {
      case 'num': return n.v
      case 'var': {
        if (!Object.hasOwn(env, n.name)) return fail(`unknown variable '${n.name}'`, n.pos)    // own properties only: 'constructor', '__proto__', 'toString' are unknown
        const v = env[n.name]
        return typeof v === 'number' && !Number.isNaN(v) ? v : fail(`variable '${n.name}' is not a number`, n.pos)
      }
      case 'un': { const v = run(n.arg, env); return n.op === '-' ? -v : v === 0 ? 1 : 0 }
      case 'cond': return run(n.test, env) !== 0 ? run(n.then, env) : run(n.else, env)
      case 'call': {
        if (!Object.hasOwn(fns, n.fn)) return fail(`unknown function '${n.fn}'`, n.pos)
        return fns[n.fn](...n.args.map(a => run(a, env)))
      }
      case 'bin': {
        if (n.op === '&&') return run(n.l, env) !== 0 && run(n.r, env) !== 0 ? 1 : 0     // short-circuit
        if (n.op === '||') return run(n.l, env) !== 0 || run(n.r, env) !== 0 ? 1 : 0
        const a = run(n.l, env), b = run(n.r, env)
        switch (n.op) {
          case '+': return a + b
          case '-': return a - b
          case '*': return a * b
          case '/': return b === 0 ? fail('division by zero', n.pos) : a / b
          case '%': return b === 0 ? fail('modulo by zero', n.pos) : a % b
          case '^': return a ** b
          case '<': return a < b ? 1 : 0
          case '>': return a > b ? 1 : 0
          case '<=': return a <= b ? 1 : 0
          case '>=': return a >= b ? 1 : 0
          case '==': return a === b ? 1 : 0
          default: return a !== b ? 1 : 0
        }
      }
    }
  }
  return (vars = {}) => run(ast, vars)
}

export const evaluate = (src: string, vars: Record<string, number> = {}): number => compile(src)(vars)
''', "expr.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { ExprError, compile, evaluate, format, parse, tokenize, variables } from './expr.ts'

const shape = (s: string) => format(parse(s))

test('precedence and associativity', () => {
  assert.equal(shape('1+2*3'), '(+ 1 (* 2 3))')
  assert.equal(shape('a-b-c'), '(- (- a b) c)')              // left-assoc
  assert.equal(shape('2^3^2'), '(^ 2 (^ 3 2))')              // right-assoc
  assert.equal(shape('2**3'), '(^ 2 3)')
  assert.equal(shape('-2^2'), '(neg (^ 2 2))')               // unary binds looser than ^
  assert.equal(shape('!a&&b'), '(&& (not a) b)')
  assert.equal(shape('a||b&&c'), '(|| a (&& b c))')
  assert.equal(shape('1+2<4==1'), '(== (< (+ 1 2) 4) 1)')
  assert.equal(shape('a?b:c?d:e'), '(? a b (? c d e))')      // right-assoc ternary
  assert.equal(shape('1+(a?2:3)*4'), '(+ 1 (* (? a 2 3) 4))')
  assert.equal(shape('max(1, 2+3, f(x))'), '(max 1 (+ 2 3) (f x))')
  assert.equal(shape('f()'), '(f)')
})

test('evaluation', () => {
  const cases: [string, number][] = [['1+2*3', 7], ['(1+2)*3', 9], ['2^3^2', 512], ['-2^2', -4], ['10%4', 2], ['1e3+.5', 1000.5], ['max(1,2,3)', 3], ['sqrt(16)+abs(-2)', 6], ['!0', 1], ['!5', 0],
    ['1<2&&2<3', 1], ['0||0', 0], ['3>=3', 1], ['2!=2', 0], ['1?10:20', 10], ['0?10:20', 20], ['--3', 3], ['+4', 4]]
  for (const [src, want] of cases) assert.equal(evaluate(src), want, src)
  assert.equal(evaluate('price * qty > 100 ? 1 : 0', { price: 30, qty: 4 }), 1)
  assert.equal(compile('x*x')({ x: 7 }), 49)
})

test('short-circuit and lazy ternary do not evaluate the dead branch', () => {
  assert.equal(evaluate('0 && nope(1)'), 0)
  assert.equal(evaluate('1 || 1/0'), 1)
  assert.equal(evaluate('1 ? 5 : 1/0'), 5)
  assert.throws(() => evaluate('1 && 1/0'), /division by zero/)
})

test('errors have line, column and a caret', () => {
  const e = (() => { try { parse('1 +\n  * 2'); } catch (x) { return x as ExprError } })()!
  assert.equal(e.message, "unexpected '*'"); assert.deepEqual([e.line, e.col], [2, 3]); assert.equal(e.excerpt, '  * 2\n  ^')
  assert.equal(e.format(), "unexpected '*' at 2:3\n  * 2\n  ^")
  assert.throws(() => parse('1 +'), (x: ExprError) => x.message === 'unexpected end of expression' && x.col === 4)
  assert.throws(() => parse('(1+2'), (x: ExprError) => /expected '\)'/.test(x.message) && x.col === 5)
  assert.throws(() => parse('2 3'), (x: ExprError) => x.message === "unexpected '3'" && x.col === 3)
  assert.throws(() => parse('1 @ 2'), (x: ExprError) => x.message === "unexpected character '@'" && x.col === 3)
  assert.throws(() => parse('a ? b'), /expected ':'/)
  assert.throws(() => parse(''), /unexpected end/)
  assert.throws(() => parse('f(1,)'), /unexpected '\)'/)
})

test('evaluation errors point at the offending name or operator', () => {
  assert.throws(() => evaluate('1 + foo'), (x: ExprError) => x.message === "unknown variable 'foo'" && x.col === 5)
  assert.throws(() => evaluate('nope(1)'), /unknown function 'nope'/)
  assert.throws(() => evaluate('8 / (4-4)'), (x: ExprError) => x.message === 'division by zero' && x.col === 3)
  assert.throws(() => evaluate('x', { x: '1' as unknown as number }), /not a number/)
  assert.throws(() => evaluate('x', { x: NaN }), /not a number/)
})

test('prototype names are just unknown, never reachable', () => {
  for (const name of ['constructor', '__proto__', 'toString', 'hasOwnProperty', 'valueOf']) assert.throws(() => evaluate(name), /unknown variable/, name)
  assert.throws(() => evaluate('constructor(1)'), /unknown function/)
  assert.equal(evaluate('constructor', { constructor: 9 }), 9)         // explicitly provided own property is fine
})

test('hostile input is bounded, not fatal', () => {
  assert.throws(() => parse('('.repeat(500) + '1' + ')'.repeat(500)), /nested too deeply/)
  assert.throws(() => parse('-'.repeat(500) + '1'), /nested too deeply/)
  assert.throws(() => parse('1+'.repeat(1500) + '1'), /too complex/)
  assert.throws(() => parse('1'.repeat(10_001)), /longer than/)
  assert.equal(evaluate('1+'.repeat(900) + '1'), 901)                    // long but legal chains are fine
})

test('tokens and variables', () => {
  assert.deepEqual(tokenize('a1 + 2.5e2').map(t => t.s), ['a1', '+', '2.5e2', ''])
  assert.deepEqual(variables(parse('a*b + max(a, c) + (d ? e : 1)')).sort(), ['a', 'b', 'c', 'd', 'e'])
  assert.equal(tokenize('.5')[0].n, 0.5)
})
'''}, NODE_TS, assumptions=TSA + ["Numbers only (booleans are 1/0); add a string/array value type before exposing it to templates", "Operator set and precedence are a design choice: copy the BP table, not the numbers, if your users expect another language's rules", "For a real language use a generated parser (peggy, chevrotain, tree-sitter); this covers the 'small expression field' case"], compat={"node": ">=22.18"},
   tags=["parser", "lexer", "tokenizer", "pratt", "recursive-descent", "expression", "calculator", "evaluator", "ast", "visitor", "precedence", "operator-precedence", "formula", "safe-eval", "no-eval", "interpreter", "language", "dsl", "error-reporting", "line-column"],
   avoid="eval/new Function on user expressions; recursion without a depth cap; looking variables up with `in` or plain indexing (prototype keys); making `^` left-associative; error messages with no position."),

RC("ts-template-engine-safe", "Template engine without eval: {{ var | filter }} with auto-escaping, if/elif/else, for with loop vars, whitespace control, comments, strict mode, line-numbered errors", "typescript",
   "Render emails, reports or pages from user-editable templates safely, with the speed of compile-once/render-many.",
   "A tokenizer over `{{ }}`, `{{{ }}}`, `{% %}` and `{# #}` that tracks line numbers, a recursive block parser that rejects unclosed/unknown/mismatched tags with the template line, a tree-walking renderer (no `new Function`), dotted-path lookup that only follows own properties (so `{{ user.constructor }}` is empty), HTML auto-escaping by default with explicit `safe`/triple-brace opt-out, a small filter set (upper, lower, trim, length, join, default, truncate, json that is safe inside <script>), and loop context (index, first, last, length) with an else branch for empty lists.",
   {"tmpl.ts": r'''export class TemplateError extends Error {
  line: number
  constructor(message: string, line: number) {
    super(`${message} (line ${line})`)
    this.name = 'TemplateError'
    this.line = line
  }
}

type Cond = { not: boolean; left: Operand; op?: '==' | '!='; right?: Operand }
type Operand = { path: string[] } | { lit: string | number }
type Filter = { name: string; arg?: string | number }
type Node =
  | { t: 'text'; s: string }
  | { t: 'out'; path: string[]; filters: Filter[]; raw: boolean; line: number }
  | { t: 'if'; branches: { cond: Cond | null; body: Node[] }[] }
  | { t: 'for'; v: string; path: string[]; body: Node[]; empty: Node[]; line: number }

const TOKEN = /\{\{\{([\s\S]*?)\}\}\}|\{\{([\s\S]*?)\}\}|\{%(-?)([\s\S]*?)(-?)%\}|\{#[\s\S]*?#\}/g
export const escapeHtml = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string)

function lookup(ctx: unknown, path: string[]): unknown {
  let cur: unknown = ctx
  for (const seg of path) {
    if (cur === null || cur === undefined || !Object.hasOwn(Object(cur), seg)) return undefined   // own properties only: no prototype walking
    cur = (cur as Record<string, unknown>)[seg]
  }
  return cur
}

function parsePath(s: string, line: number): string[] {
  if (!/^[A-Za-z_][\w]*(\.[\w]+)*$/.test(s)) throw new TemplateError(`bad variable name '${s}'`, line)
  return s.split('.')
}

function parseOperand(s: string, line: number): Operand {
  s = s.trim()
  const q = /^(['"])(.*)\1$/.exec(s)
  if (q) return { lit: q[2] }
  if (/^-?\d+(\.\d+)?$/.test(s)) return { lit: Number(s) }
  return { path: parsePath(s, line) }
}

function parseCond(s: string, line: number): Cond {
  let rest = s.trim(), not = false
  if (/^not\s+/.test(rest)) { not = true; rest = rest.replace(/^not\s+/, '') }
  const m = /^(.+?)\s*(==|!=)\s*(.+)$/.exec(rest)
  return m ? { not, left: parseOperand(m[1], line), op: m[2] as '==' | '!=', right: parseOperand(m[3], line) } : { not, left: parseOperand(rest, line) }
}

function splitPipes(s: string): string[] {
  const parts: string[] = []
  let cur = '', q = ''
  for (const c of s) {
    if (q) { cur += c; if (c === q) q = '' } else if (c === '"' || c === "'") { q = c; cur += c } else if (c === '|') { parts.push(cur); cur = '' } else cur += c
  }
  parts.push(cur)
  return parts.map(p => p.trim())
}

function parseOut(src: string, raw: boolean, line: number): Node {
  const [head, ...fs] = splitPipes(src)
  const filters = fs.map((f): Filter => {
    const m = /^(\w+)(?:\((.*)\))?$/.exec(f)
    if (!m || !FILTERS[m[1]]) throw new TemplateError(`unknown filter '${f}'`, line)
    const a = m[2]?.trim()
    return { name: m[1], arg: a === undefined ? undefined : /^(['"])(.*)\1$/.test(a) ? a.slice(1, -1) : /^-?\d+(\.\d+)?$/.test(a) ? Number(a) : a }
  })
  return { t: 'out', path: parsePath(head, line), filters, raw, line }
}

const FILTERS: Record<string, (v: unknown, arg?: string | number) => unknown> = {
  upper: v => String(v ?? '').toUpperCase(),
  lower: v => String(v ?? '').toLowerCase(),
  trim: v => String(v ?? '').trim(),
  length: v => (typeof v === 'string' || Array.isArray(v) ? v.length : 0),
  join: (v, a = ', ') => (Array.isArray(v) ? v.join(String(a)) : v),
  default: (v, a = '') => (v === undefined || v === null || v === '' ? a : v),
  truncate: (v, a = 80) => { const s = String(v ?? ''), n = Number(a); return s.length > n ? s.slice(0, Math.max(0, n - 1)) + '…' : s },
  json: v => JSON.stringify(v ?? null).replace(/</g, '\\u003c').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029'),   // safe inside <script>
  safe: v => v,
  escape: v => escapeHtml(String(v ?? '')),
}

export function compile(src: string): Template {
  type Frame = { kind: 'root' | 'if' | 'for'; line: number; body: Node[]; node?: Node }
  const root: Frame = { kind: 'root', line: 1, body: [] }
  const stack: Frame[] = [root]
  const top = () => stack[stack.length - 1]
  const lineAt = (i: number) => src.slice(0, i).split('\n').length
  let last = 0, trimNext = false
  const text = (s: string) => { if (trimNext) { s = s.replace(/^\s+/, ''); trimNext = false } if (s) top().body.push({ t: 'text', s }) }

  for (const m of src.matchAll(TOKEN)) {
    const line = lineAt(m.index)
    let before = src.slice(last, m.index)
    if (m[3] === '-') before = before.replace(/\s+$/, '')
    text(before)
    last = m.index + m[0].length
    if (m[1] !== undefined) { top().body.push(parseOut(m[1].trim(), true, line)); continue }
    if (m[2] !== undefined) { const o = parseOut(m[2].trim(), false, line) as Extract<Node, { t: 'out' }>; o.raw = o.filters.some(f => f.name === 'safe' || f.name === 'json'); top().body.push(o); continue }
    if (m[4] === undefined) continue                                    // {# comment #}
    if (m[5] === '-') trimNext = true
    const tag = m[4].trim(), [kw, ...restParts] = tag.split(/\s+/), rest = restParts.join(' ')
    if (kw === 'if') {
      const node: Node = { t: 'if', branches: [{ cond: parseCond(rest, line), body: [] }] }
      top().body.push(node); stack.push({ kind: 'if', line, body: node.branches[0].body, node })
    } else if (kw === 'elif' || kw === 'else') {
      const f = top()
      if (f.kind === 'for' && kw === 'else') { const n = f.node as Extract<Node, { t: 'for' }>; f.body = n.empty }
      else if (f.kind === 'if') { const n = f.node as Extract<Node, { t: 'if' }>; if (n.branches[n.branches.length - 1].cond === null) throw new TemplateError(`'${kw}' after 'else'`, line); const b = { cond: kw === 'elif' ? parseCond(rest, line) : null, body: [] as Node[] }; n.branches.push(b); f.body = b.body }
      else throw new TemplateError(`'${kw}' without a matching if/for`, line)
    } else if (kw === 'for') {
      const fm = /^(\w+)\s+in\s+(.+)$/.exec(rest)
      if (!fm) throw new TemplateError("bad for tag, expected 'for x in list'", line)
      const node: Node = { t: 'for', v: fm[1], path: parsePath(fm[2].trim(), line), body: [], empty: [], line }
      top().body.push(node); stack.push({ kind: 'for', line, body: node.body, node })
    } else if (kw === 'endif' || kw === 'endfor') {
      const f = top(), want = kw === 'endif' ? 'if' : 'for'
      if (f.kind !== want) throw new TemplateError(f.kind === 'root' ? `'${kw}' without a matching ${want}` : `'${kw}' closes a '${f.kind}' opened on line ${f.line}`, line)
      stack.pop()
    } else throw new TemplateError(`unknown tag '${kw}'`, line)
  }
  text(src.slice(last))
  if (stack.length > 1) throw new TemplateError(`unclosed '${top().kind}'`, top().line)
  return new Template(root.body)
}

export class Template {
  body: Node[]
  constructor(body: Node[]) { this.body = body }

  render(ctx: Record<string, unknown> = {}, opts: { strict?: boolean } = {}): string {
    const truthy = (v: unknown) => (Array.isArray(v) ? v.length > 0 : Boolean(v))
    const val = (o: Operand, c: unknown) => ('lit' in o ? o.lit : lookup(c, o.path))
    const run = (nodes: Node[], c: Record<string, unknown>): string => nodes.map(n => {
      switch (n.t) {
        case 'text': return n.s
        case 'out': {
          let v = lookup(c, n.path)
          if (v === undefined && opts.strict) throw new TemplateError(`undefined variable '${n.path.join('.')}'`, n.line)
          for (const f of n.filters) v = FILTERS[f.name](v, f.arg)
          const s = v === undefined || v === null ? '' : String(v)
          return n.raw ? s : escapeHtml(s)
        }
        case 'if': {
          for (const b of n.branches) {
            if (b.cond === null) return run(b.body, c)
            const { left, op, right, not } = b.cond
            let ok = op ? (op === '==' ? val(left, c) === val(right as Operand, c) : val(left, c) !== val(right as Operand, c)) : truthy(val(left, c))
            if (not) ok = !ok
            if (ok) return run(b.body, c)
          }
          return ''
        }
        case 'for': {
          const list = lookup(c, n.path)
          if (!Array.isArray(list) || list.length === 0) return run(n.empty, c)
          return list.map((item, i) => run(n.body, { ...c, [n.v]: item, loop: { index: i + 1, index0: i, first: i === 0, last: i === list.length - 1, length: list.length } })).join('')
        }
      }
    }).join('')
    return run(this.body, { ...ctx })
  }
}

export const render = (src: string, ctx: Record<string, unknown> = {}, opts: { strict?: boolean } = {}) => compile(src).render(ctx, opts)
''', "tmpl.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { TemplateError, compile, render } from './tmpl.ts'

test('variables, paths and auto-escaping', () => {
  assert.equal(render('Hi {{ user.name }}!', { user: { name: 'Ann' } }), 'Hi Ann!')
  assert.equal(render('{{ x }}', { x: '<script>alert("x")</script> & \'q\'' }), '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#39;q&#39;')
  assert.equal(render('{{{ x }}}|{{ x | safe }}|{{ x }}', { x: '<b>' }), '<b>|<b>|&lt;b&gt;')
  assert.equal(render('{{ items.1 }} {{ items.length }}', { items: ['a', 'b'] }), 'b 2')
  assert.equal(render('[{{ missing.deep.path }}][{{ n }}]', { n: 0 }), '[][0]')
})

test('prototype and inherited properties are unreachable', () => {
  for (const t of ['{{ user.constructor }}', '{{ __proto__ }}', '{{ user.__proto__.polluted }}', '{{ items.map }}', '{{ user.toString }}', '{{ x.constructor.name }}'])
    assert.equal(render(t, { user: { a: 1 }, items: [1], x: 'str' }), '', t)
})

test('filters', () => {
  assert.equal(render('{{ n | upper }} {{ n | lower }} {{ s | trim }} {{ l | join(" / ") }} {{ l | length }}', { n: 'Ab', s: '  hi ', l: ['x', 'y'] }), 'AB ab hi x / y 2')
  assert.equal(render('{{ a | default("none") }} {{ b | default("none") }} {{ c | default(5) }}', { b: 'set' }), 'none set 5')
  assert.equal(render('{{ t | truncate(5) }}|{{ t | truncate(50) }}', { t: 'abcdefghij' }), 'abcd…|abcdefghij')
  assert.equal(render('{{ x | default("a|b") | upper }}', {}), 'A|B')                                          // pipe inside quotes
  assert.equal(render('<script>var d = {{ d | json }}</script>', { d: { x: '</script><!--' } }), '<script>var d = {"x":"\\u003c/script>\\u003c!--"}</script>')
})

test('if / elif / else and comparisons', () => {
  const t = '{% if n == 1 %}one{% elif n == 2 %}two{% elif not n %}zero{% else %}many{% endif %}'
  assert.deepEqual([1, 2, 0, 9].map(n => render(t, { n })), ['one', 'two', 'zero', 'many'])
  assert.equal(render('{% if role == "admin" %}A{% endif %}{% if role != "admin" %}U{% endif %}', { role: 'admin' }), 'A')
  assert.equal(render('{% if items %}has{% else %}none{% endif %}', { items: [] }), 'none')
  assert.equal(render('{% if a %}{% if b %}AB{% else %}A{% endif %}{% endif %}', { a: 1 }), 'A')
})

test('for loops with loop variable, else and nesting', () => {
  assert.equal(render('{% for x in xs %}{{ loop.index }}:{{ x }}{% if not loop.last %},{% endif %}{% endfor %}', { xs: ['a', 'b', 'c'] }), '1:a,2:b,3:c')
  assert.equal(render('{% for x in xs %}{{ x }}{% else %}empty{% endfor %}', { xs: [] }), 'empty')
  assert.equal(render('{% for x in nope %}{{ x }}{% else %}missing{% endfor %}', {}), 'missing')
  assert.equal(render('{% for r in rows %}{% for c in r %}{{ c }}{% endfor %};{% endfor %}', { rows: [[1, 2], [3]] }), '12;3;')
  assert.equal(render('{% for u in users %}{{ u.name }} {% endfor %}{{ u }}', { users: [{ name: 'a' }, { name: 'b' }] }), 'a b ')       // loop variable does not leak
})

test('whitespace control and comments', () => {
  assert.equal(render('a  {%- if x -%}  b  {%- endif -%}  c', { x: 1 }), 'abc')
  assert.equal(render('1{# hidden {{ x }} #}2', { x: 'no' }), '12')
})

test('strict mode reports the line of an undefined variable', () => {
  assert.throws(() => render('ok\n\n{{ who }}', {}, { strict: true }), (e: TemplateError) => e instanceof TemplateError && e.line === 3 && /undefined variable 'who'/.test(e.message))
  assert.equal(render('{{ n }}', { n: 0 }, { strict: true }), '0')
})

test('compile errors carry line numbers', () => {
  const bad: [string, RegExp, number][] = [['a\n{% if x %}b', /unclosed 'if'/, 2], ['{% endif %}', /without a matching if/, 1], ['{% for x in y %}\n{% endif %}', /closes a 'for' opened on line 1/, 2],
    ['{% if x %}{% else %}{% else %}{% endif %}', /after 'else'/, 1], ['x\n{% wat %}', /unknown tag 'wat'/, 2], ['{{ a | nope }}', /unknown filter/, 1], ['{% for x of y %}{% endfor %}', /bad for tag/, 1],
    ['{{ a b }}', /bad variable name/, 1], ['{% else %}', /without a matching/, 1]]
  for (const [src, re, line] of bad) assert.throws(() => compile(src), (e: TemplateError) => e instanceof TemplateError && re.test(e.message) && e.line === line, src)
})

test('compile once, render many; templates do not share state', () => {
  const t = compile('{% for x in xs %}{{ x }}{% endfor %}')
  assert.equal(t.render({ xs: [1, 2] }), '12'); assert.equal(t.render({ xs: [3] }), '3'); assert.equal(t.render({}), '')
})

test('literal braces and unmatched text are left alone', () => {
  assert.equal(render('{ not } a {tag} {{ x }}', { x: 1 }), '{ not } a {tag} 1')
})
'''}, NODE_TS, assumptions=TSA + ["Logic is verified; output is plain strings, not rendered in a browser", "Conditions are intentionally tiny (path, not, ==, !=); extend parseCond rather than embedding a JS evaluator - pair it with the expression-evaluator recipe if you need arithmetic", "Auto-escaping is HTML-text/attribute safe; URLs, CSS and JS contexts need their own escaping (the `json` filter is output unescaped and is for <script> data blocks only, never attributes)"], compat={"node": ">=22.18"},
   tags=["template", "templating", "template-engine", "mustache", "handlebars", "jinja", "liquid", "render", "escape", "html-escape", "xss", "autoescape", "variable-substitution", "filters", "loop", "conditional", "email-template", "no-eval", "safe"],
   avoid="Template engines built on new Function/eval over user templates; ${} interpolation of user data into HTML; walking the prototype chain in path lookups; silent output when a tag is unclosed."),

RC("ts-static-site-slugs-frontmatter-feeds", "Static-site toolkit: slugify with uniqueness, front matter parser, markdown TOC, RSS/Atom feeds, sitemap.xml (chunked), Open Graph tags", "typescript",
   "The glue every static site generator or blog needs besides a markdown renderer: stable URLs, metadata at the top of files, a table of contents, feeds, sitemaps and share cards.",
   "Pure string-in/string-out functions: Unicode-aware slugify (strips diacritics, keeps non-Latin letters, never returns an empty slug) plus a Slugger that de-duplicates; a front matter parser (CRLF/BOM safe, scalars, inline and block lists, quotes, clear error when unterminated); heading extraction that ignores fenced code and strips inline markdown, with nesting; RSS 2.0 and Atom generators with correct XML escaping, RFC-822/ISO dates and newest-first ordering; sitemap generation that validates absolute URLs and splits at the 50,000-URL limit with an index; and escaped Open Graph/Twitter meta tags.",
   {"site.ts": r'''const XML_BAD = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffe\uffff]/g
export const escapeXml = (s: string) => s.replace(XML_BAD, '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[c] as string)
export const escapeAttr = (s: string) => s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] as string)

// ---- slugs --------------------------------------------------------------------------------------
export function slugify(s: string, fallback = 'untitled'): string {
  const slug = s.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').replace(/ß/g, 'ss').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '')
  return slug || fallback
}

export class Slugger {
  used: Set<string> = new Set()
  unique(s: string): string {
    const base = slugify(s)
    let slug = base, n = 0
    while (this.used.has(slug)) slug = `${base}-${++n}`            // also skips a literal "hello-1" already taken by a real title
    this.used.add(slug)
    return slug
  }
}

// ---- front matter -----------------------------------------------------------------------------------
export class FrontMatterError extends Error {}
export type Scalar = string | number | boolean | null

function scalar(v: string): Scalar | Scalar[] {
  v = v.trim()
  if (v === '') return ''
  if (/^(["']).*\1$/s.test(v)) return v.slice(1, -1)
  if (v === 'true') return true
  if (v === 'false') return false
  if (v === 'null' || v === '~') return null
  if (/^-?\d+(\.\d+)?$/.test(v)) return Number(v)
  if (v.startsWith('[') && v.endsWith(']')) {
    const inner = v.slice(1, -1).trim()
    return inner === '' ? [] : (inner.match(/"[^"]*"|'[^']*'|[^,]+/g) ?? []).map(x => scalar(x) as Scalar)
  }
  return v
}

export function parseFrontMatter(text: string): { data: Record<string, Scalar | Scalar[]>; body: string } {
  const src = text.replace(/^\ufeff/, '').replace(/\r\n?/g, '\n')
  if (!/^---[ \t]*\n/.test(src)) return { data: {}, body: src }
  const rest = src.replace(/^---[ \t]*\n/, '')
  const end = /^---[ \t]*$/m.exec(rest)
  if (!end) throw new FrontMatterError('front matter starts with --- but has no closing ---')
  const data: Record<string, Scalar | Scalar[]> = {}
  let listKey: string | null = null
  for (const [i, line] of rest.slice(0, end.index).split('\n').entries()) {
    if (!line.trim() || line.trim().startsWith('#')) continue
    const item = /^\s+-\s+(.*)$/.exec(line)
    if (item && listKey) { (data[listKey] as Scalar[]).push(scalar(item[1]) as Scalar); continue }
    const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line)
    if (!kv) throw new FrontMatterError(`front matter line ${i + 2}: expected "key: value", got "${line}"`)
    listKey = kv[2].trim() === '' ? kv[1] : null
    data[kv[1]] = listKey ? [] : scalar(kv[2])
  }
  return { data, body: rest.slice(end.index + end[0].length).replace(/^\n/, '') }
}

// ---- table of contents --------------------------------------------------------------------------------
export interface Heading { level: number; text: string; id: string }
export interface TocNode extends Heading { children: TocNode[] }
const plain = (s: string) => s.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/[`*_~]/g, '').replace(/\s+#+\s*$/, '').trim()

export function headings(md: string, min = 2, max = 4): Heading[] {
  const slugger = new Slugger(), out: Heading[] = []
  let fence: string | null = null
  for (const line of md.replace(/\r\n?/g, '\n').split('\n')) {
    const f = /^\s{0,3}(`{3,}|~{3,})/.exec(line)
    if (f) { fence = fence === null ? f[1][0] : f[1][0] === fence ? null : fence; continue }
    if (fence) continue
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(line)
    if (!m) continue
    const text = plain(m[2]), id = slugger.unique(text)            // ids are assigned to EVERY heading so they match the rendered anchors
    if (m[1].length >= min && m[1].length <= max) out.push({ level: m[1].length, text, id })
  }
  return out
}

export function nest(hs: Heading[]): TocNode[] {
  const root: TocNode[] = [], stack: TocNode[] = []
  for (const h of hs) {
    const node: TocNode = { ...h, children: [] }
    while (stack.length && stack[stack.length - 1].level >= h.level) stack.pop()
    ;(stack.length ? stack[stack.length - 1].children : root).push(node)
    stack.push(node)
  }
  return root
}

// ---- feeds -------------------------------------------------------------------------------------------------
export interface Entry { title: string; link: string; date: Date; summary?: string; id?: string }
export interface Feed { title: string; link: string; description: string; entries: Entry[] }
const newestFirst = (e: Entry[]) => [...e].sort((a, b) => b.date.getTime() - a.date.getTime())

export function rss(f: Feed): string {
  const es = newestFirst(f.entries)
  const items = es.map(e => `    <item>\n      <title>${escapeXml(e.title)}</title>\n      <link>${escapeXml(e.link)}</link>\n      <guid isPermaLink="${e.id ? 'false' : 'true'}">${escapeXml(e.id ?? e.link)}</guid>\n      <pubDate>${e.date.toUTCString()}</pubDate>${e.summary ? `\n      <description>${escapeXml(e.summary)}</description>` : ''}\n    </item>`)
  return `<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">\n  <channel>\n    <title>${escapeXml(f.title)}</title>\n    <link>${escapeXml(f.link)}</link>\n    <description>${escapeXml(f.description)}</description>\n${es.length ? `    <lastBuildDate>${es[0].date.toUTCString()}</lastBuildDate>\n` : ''}${items.join('\n')}${items.length ? '\n' : ''}  </channel>\n</rss>\n`
}

export function atom(f: Feed, selfUrl: string): string {
  const es = newestFirst(f.entries)
  const entries = es.map(e => `  <entry>\n    <title>${escapeXml(e.title)}</title>\n    <link href="${escapeXml(e.link)}"/>\n    <id>${escapeXml(e.id ?? e.link)}</id>\n    <updated>${e.date.toISOString()}</updated>${e.summary ? `\n    <summary>${escapeXml(e.summary)}</summary>` : ''}\n  </entry>`)
  return `<?xml version="1.0" encoding="UTF-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">\n  <title>${escapeXml(f.title)}</title>\n  <link href="${escapeXml(f.link)}"/>\n  <link rel="self" href="${escapeXml(selfUrl)}"/>\n  <id>${escapeXml(f.link)}</id>\n  <updated>${(es[0]?.date ?? new Date(0)).toISOString()}</updated>\n${entries.join('\n')}${entries.length ? '\n' : ''}</feed>\n`
}

// ---- sitemap -------------------------------------------------------------------------------------------------
export interface UrlEntry { loc: string; lastmod?: Date | string; changefreq?: 'always' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'yearly' | 'never'; priority?: number }
const SM_NS = 'http://www.sitemaps.org/schemas/sitemap/0.9'
const w3cDate = (d: Date | string) => (typeof d === 'string' ? d : d.toISOString().slice(0, 10))

function checkUrl(u: string): string {
  let url: URL
  try { url = new URL(u) } catch { throw new Error(`sitemap URL must be absolute: ${u}`) }
  if (!/^https?:$/.test(url.protocol)) throw new Error(`sitemap URL must be http(s): ${u}`)
  if (u.length > 2048) throw new Error('sitemap URL longer than 2048 characters')
  return u
}

export function sitemapChunks(urls: UrlEntry[], max = 50_000): string[] {
  const chunks: string[] = []
  for (let i = 0; i < Math.max(urls.length, 1); i += max) {
    const body = urls.slice(i, i + max).map(u => {
      if (u.priority !== undefined && (u.priority < 0 || u.priority > 1)) throw new Error('priority must be between 0 and 1')
      return `  <url>\n    <loc>${escapeXml(checkUrl(u.loc))}</loc>${u.lastmod ? `\n    <lastmod>${w3cDate(u.lastmod)}</lastmod>` : ''}${u.changefreq ? `\n    <changefreq>${u.changefreq}</changefreq>` : ''}${u.priority !== undefined ? `\n    <priority>${u.priority.toFixed(1)}</priority>` : ''}\n  </url>`
    })
    chunks.push(`<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="${SM_NS}">\n${body.join('\n')}${body.length ? '\n' : ''}</urlset>\n`)
  }
  return chunks
}

export const sitemapIndex = (locs: string[], lastmod?: Date) =>
  `<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="${SM_NS}">\n${locs.map(l => `  <sitemap>\n    <loc>${escapeXml(checkUrl(l))}</loc>${lastmod ? `\n    <lastmod>${w3cDate(lastmod)}</lastmod>` : ''}\n  </sitemap>`).join('\n')}\n</sitemapindex>\n`

// ---- social / SEO tags -----------------------------------------------------------------------------------------
export interface Meta { title: string; description?: string; url: string; image?: string; siteName?: string; type?: 'website' | 'article' }
export function metaTags(m: Meta): string {
  const tag = (k: string, v: string | undefined, attr = 'property') => (v ? `<meta ${attr}="${k}" content="${escapeAttr(v)}">` : '')
  return [`<title>${escapeAttr(m.title)}</title>`, tag('description', m.description, 'name'), `<link rel="canonical" href="${escapeAttr(m.url)}">`,
    tag('og:title', m.title), tag('og:description', m.description), tag('og:url', m.url), tag('og:type', m.type ?? 'website'), tag('og:site_name', m.siteName), tag('og:image', m.image),
    tag('twitter:card', m.image ? 'summary_large_image' : 'summary', 'name')].filter(Boolean).join('\n')
}
''', "site.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { FrontMatterError, Slugger, atom, escapeXml, headings, metaTags, nest, parseFrontMatter, rss, sitemapChunks, sitemapIndex, slugify } from './site.ts'

test('slugify', () => {
  assert.equal(slugify('Hello, World!'), 'hello-world')
  assert.equal(slugify('  Crème Brûlée — Straße & Co.  '), 'creme-brulee-strasse-co')
  assert.equal(slugify('日本語 タイトル'), '日本語-タイトル')
  assert.equal(slugify('v1.2.3 release'), 'v1-2-3-release')
  assert.equal(slugify('!!!'), 'untitled'); assert.equal(slugify(''), 'untitled')
})

test('Slugger de-duplicates, including against literal suffixes', () => {
  const s = new Slugger()
  assert.deepEqual(['Intro', 'Intro', 'intro', 'Intro 1', 'Intro'].map(x => s.unique(x)), ['intro', 'intro-1', 'intro-2', 'intro-1-1', 'intro-3'])
})

test('front matter', () => {
  const doc = '\ufeff---\r\ntitle: "Hello: World"\r\ndraft: false\r\nweight: 3\r\nrating: 4.5\r\ntags: [a, "b c", 3]\r\nauthors:\r\n  - Ann\r\n  - \'Bo B\'\r\nnothing: null\r\n# comment\r\nplain: just text\r\n---\r\n\r\n# Body\r\ntext\r\n'
  const { data, body } = parseFrontMatter(doc)
  assert.deepEqual(data, { title: 'Hello: World', draft: false, weight: 3, rating: 4.5, tags: ['a', 'b c', 3], authors: ['Ann', 'Bo B'], nothing: null, plain: 'just text' })
  assert.equal(body, '\n# Body\ntext\n')
  assert.deepEqual(parseFrontMatter('# no front matter\n---\nnot: fm\n---'), { data: {}, body: '# no front matter\n---\nnot: fm\n---' })
  assert.deepEqual(parseFrontMatter('---\n---\nbody'), { data: {}, body: 'body' })
  assert.deepEqual(parseFrontMatter('---\ntags: []\n---\n').data, { tags: [] })
})

test('front matter errors are explicit', () => {
  assert.throws(() => parseFrontMatter('---\ntitle: x\nbody'), FrontMatterError)
  assert.throws(() => parseFrontMatter('---\ntitle x\n---\n'), /line 2: expected "key: value"/)
})

test('headings: ignore code fences, strip markdown, unique ids, nest', () => {
  const md = '# Title\n\n## Setup\n\n```bash\n# not a heading\n## nor this\n```\n\n## Usage `code` & [link](http://x)\n\n### Details\n\n~~~\n## fenced tilde\n~~~\n\n## Setup\n\n#### Deep ##\n\n##### Too deep\n'
  const hs = headings(md)
  assert.deepEqual(hs.map(h => [h.level, h.text, h.id]), [[2, 'Setup', 'setup'], [2, 'Usage code & link', 'usage-code-link'], [3, 'Details', 'details'], [2, 'Setup', 'setup-1'], [4, 'Deep', 'deep']])
  const tree = nest(hs)
  assert.deepEqual(tree.map(t => [t.text, t.children.map(c => c.text)]), [['Setup', []], ['Usage code & link', ['Details']], ['Setup', ['Deep']]])
  assert.equal(headings('# A\n# A', 1, 1).map(h => h.id).join(), 'a,a-1')
})

const feed = { title: 'Dev & Ops', link: 'https://e.com/', description: 'Notes <b>', entries: [
  { title: 'Old <post>', link: 'https://e.com/old?a=1&b=2', date: new Date('2024-01-02T03:04:05Z'), summary: 'x & y' },
  { title: 'New\u0007', link: 'https://e.com/new', date: new Date('2024-03-04T00:00:00Z'), id: 'tag:e.com,2024:new' }] }

test('rss: escaping, order, dates, guid', () => {
  const x = rss(feed)
  assert.ok(x.indexOf('New') < x.indexOf('Old &lt;post&gt;'))
  assert.match(x, /<title>Dev &amp; Ops<\/title>/); assert.match(x, /<description>Notes &lt;b&gt;<\/description>/)
  assert.match(x, /<link>https:\/\/e\.com\/old\?a=1&amp;b=2<\/link>/); assert.match(x, /<pubDate>Tue, 02 Jan 2024 03:04:05 GMT<\/pubDate>/)
  assert.match(x, /<guid isPermaLink="false">tag:e\.com,2024:new<\/guid>/); assert.match(x, /<guid isPermaLink="true">https:\/\/e\.com\/old/)
  assert.match(x, /<lastBuildDate>Mon, 04 Mar 2024 00:00:00 GMT<\/lastBuildDate>/); assert.ok(!x.includes('\u0007'))
  assert.match(rss({ ...feed, entries: [] }), /<\/description>\n  <\/channel>/)
})

test('atom', () => {
  const x = atom(feed, 'https://e.com/atom.xml')
  assert.match(x, /<feed xmlns="http:\/\/www\.w3\.org\/2005\/Atom">/); assert.match(x, /<updated>2024-03-04T00:00:00\.000Z<\/updated>/); assert.match(x, /rel="self" href="https:\/\/e\.com\/atom\.xml"/)
  assert.match(x, /<summary>x &amp; y<\/summary>/); assert.match(atom({ ...feed, entries: [] }, 's'), /<updated>1970-01-01T00:00:00\.000Z<\/updated>/)
})

test('sitemap', () => {
  const [x] = sitemapChunks([{ loc: 'https://e.com/a?x=1&y=2', lastmod: new Date('2024-05-06T23:59:00Z'), changefreq: 'weekly', priority: 0.8 }, { loc: 'https://e.com/' }])
  assert.match(x, /<urlset xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9">/); assert.match(x, /<loc>https:\/\/e\.com\/a\?x=1&amp;y=2<\/loc>/)
  assert.match(x, /<lastmod>2024-05-06<\/lastmod>/); assert.match(x, /<priority>0\.8<\/priority>/); assert.equal((x.match(/<url>/g) ?? []).length, 2)
})

test('sitemap validation and chunking', () => {
  for (const bad of ['/relative', 'ftp://e.com/x', 'not a url']) assert.throws(() => sitemapChunks([{ loc: bad }]), /absolute|http/, bad)
  assert.throws(() => sitemapChunks([{ loc: 'https://e.com/', priority: 2 }]), /priority/)
  const urls = Array.from({ length: 5 }, (_, i) => ({ loc: `https://e.com/${i}` }))
  const chunks = sitemapChunks(urls, 2); assert.deepEqual(chunks.map(c => (c.match(/<url>/g) ?? []).length), [2, 2, 1])
  assert.match(sitemapIndex(['https://e.com/sitemap-1.xml', 'https://e.com/sitemap-2.xml'], new Date('2024-01-01')), /<sitemap>\n    <loc>https:\/\/e\.com\/sitemap-1\.xml<\/loc>\n    <lastmod>2024-01-01<\/lastmod>/)
  assert.equal(sitemapChunks([]).length, 1)
})

test('meta tags are attribute-escaped', () => {
  const t = metaTags({ title: 'A "quoted" <title>', description: "it's & more", url: 'https://e.com/p?a=1&b=2', image: 'https://e.com/i.png', siteName: 'Site' })
  assert.match(t, /<meta property="og:title" content="A &quot;quoted&quot; &lt;title&gt;">/); assert.match(t, /content="it&#39;s &amp; more"/)
  assert.match(t, /<link rel="canonical" href="https:\/\/e\.com\/p\?a=1&amp;b=2">/); assert.match(t, /twitter:card" content="summary_large_image"/)
  assert.match(metaTags({ title: 't', url: 'https://e.com/' }), /twitter:card" content="summary"/); assert.ok(!metaTags({ title: 't', url: 'u' }).includes('og:image'))
})

test('escapeXml removes characters XML 1.0 forbids', () => { assert.equal(escapeXml('a\u0000b\u000bc&'), 'abc&amp;') })
'''}, NODE_TS, assumptions=TSA + ["String generation is checked by exact/regex assertions, not by an XML validator or a feed reader", "Markdown rendering itself is out of scope - use marked/markdown-it/remark and feed it the slugs from `headings()` so anchors match", "Front matter is a YAML subset (flat keys, scalars, inline/block lists); use gray-matter or a YAML library for nested data"], compat={"node": ">=22.18"},
   tags=["static-site", "ssg", "blog", "markdown", "slug", "slugify", "frontmatter", "front-matter", "toc", "table-of-contents", "headings", "rss", "atom", "feed", "sitemap", "sitemap-xml", "seo", "open-graph", "opengraph", "meta-tags", "twitter-card", "xml-escape"],
   avoid="Slugs that collide silently; stripping non-Latin letters so titles become empty; ids in the TOC that differ from the rendered anchors; unescaped & in feed URLs; headings parsed inside code fences; sitemaps with relative URLs or over 50,000 entries."),

RC("go-pipeline-workers-fanin-ordered", "Go concurrency pipeline: bounded worker pool, ordered or unordered Map, fan-in Merge, first-error cancellation, panic capture, leak-free shutdown", "go",
   "Process a stream of work concurrently with a limit, stop everything on the first error or context cancel, and be sure no goroutine is left blocked.",
   "A tiny errgroup-style Group (first error wins and cancels a shared context), generic Gen/Map/MapOrdered/Merge/Collect over channels, workers that always select on ctx.Done() when sending, MapOrdered tagging items with their index and re-sequencing through a small buffer so output order equals input order even when workers finish out of order, panics in the worker function converted to errors, and tests that measure peak concurrency and prove goroutines return to baseline after errors and cancellation.",
   {"pipeline.go": '''// Package pipeline composes channel stages with bounded concurrency and clean cancellation. Needs Go 1.18+ (generics).
package pipeline

import (
	"context"
	"fmt"
	"sync"
)

// Group runs goroutines, remembers the first error and cancels the shared context when one fails (a minimal golang.org/x/sync/errgroup).
type Group struct {
	ctx    context.Context
	cancel context.CancelFunc
	wg     sync.WaitGroup
	once   sync.Once
	err    error
}

// WithContext returns a Group and a context that is cancelled on the first error or when Wait returns. Pass THAT context to every stage.
func WithContext(parent context.Context) (*Group, context.Context) {
	ctx, cancel := context.WithCancel(parent)
	return &Group{ctx: ctx, cancel: cancel}, ctx
}

func (g *Group) Go(f func() error) {
	g.wg.Add(1)
	go func() {
		defer g.wg.Done()
		if err := f(); err != nil {
			g.once.Do(func() { g.err = err; g.cancel() })
		}
	}()
}

// Wait blocks until every goroutine has returned. Call it after you have drained (or abandoned and cancelled) the output channel.
// It returns the first stage error, or the parent's cancellation error if the run was cut short (so a truncated stream is never mistaken for success).
func (g *Group) Wait() error {
	g.wg.Wait()
	g.once.Do(func() { g.err = g.ctx.Err() }) // nil unless the parent context was cancelled/expired
	g.cancel()
	return g.err
}

// Gen emits items and stops early if ctx is cancelled.
func Gen[T any](ctx context.Context, items ...T) <-chan T {
	out := make(chan T)
	go func() {
		defer close(out)
		for _, it := range items {
			select {
			case out <- it:
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

func safe[I, O any](ctx context.Context, fn func(context.Context, I) (O, error), v I) (o O, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("pipeline: worker panic: %v", r)
		}
	}()
	return fn(ctx, v)
}

// Map applies fn to every item using `workers` goroutines. Output order is NOT preserved. The first error cancels the group.
func Map[I, O any](ctx context.Context, g *Group, in <-chan I, workers int, fn func(context.Context, I) (O, error)) <-chan O {
	if workers < 1 {
		workers = 1
	}
	out := make(chan O)
	var wg sync.WaitGroup
	wg.Add(workers)
	for i := 0; i < workers; i++ {
		g.Go(func() error {
			defer wg.Done()
			for v := range in {
				o, err := safe(ctx, fn, v)
				if err != nil {
					return err
				}
				select {
				case out <- o:
				case <-ctx.Done(): // consumer gone or group cancelled: never block forever on a send
					return ctx.Err()
				}
			}
			return nil
		})
	}
	go func() { wg.Wait(); close(out) }()
	return out
}

type indexed[T any] struct {
	i int
	v T
}

// MapOrdered is Map with output in input order: results wait in a small buffer until the next expected index arrives.
func MapOrdered[I, O any](ctx context.Context, g *Group, in <-chan I, workers int, fn func(context.Context, I) (O, error)) <-chan O {
	tagged := make(chan indexed[I])
	g.Go(func() error {
		defer close(tagged)
		i := 0
		for v := range in {
			select {
			case tagged <- indexed[I]{i, v}:
				i++
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		return nil
	})
	mid := Map(ctx, g, tagged, workers, func(c context.Context, t indexed[I]) (indexed[O], error) {
		o, err := fn(c, t.v)
		return indexed[O]{t.i, o}, err
	})
	out := make(chan O)
	g.Go(func() error {
		defer close(out)
		buf, next := map[int]O{}, 0
		for r := range mid {
			buf[r.i] = r.v
			for {
				v, ok := buf[next]
				if !ok {
					break
				}
				select {
				case out <- v:
				case <-ctx.Done():
					return ctx.Err()
				}
				delete(buf, next)
				next++
			}
		}
		return nil
	})
	return out
}

// Merge fans several channels into one; it closes when all inputs are closed.
func Merge[T any](ctx context.Context, ins ...<-chan T) <-chan T {
	out := make(chan T)
	var wg sync.WaitGroup
	wg.Add(len(ins))
	for _, c := range ins {
		go func(c <-chan T) {
			defer wg.Done()
			for v := range c {
				select {
				case out <- v:
				case <-ctx.Done():
					return
				}
			}
		}(c)
	}
	go func() { wg.Wait(); close(out) }()
	return out
}

// Collect drains a channel into a slice.
func Collect[T any](ch <-chan T) []T {
	var all []T
	for v := range ch {
		all = append(all, v)
	}
	return all
}
''', "pipeline_test.go": '''package pipeline

import (
	"context"
	"errors"
	"runtime"
	"sort"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func noLeak(t *testing.T, base int) {
	t.Helper()
	for i := 0; i < 200; i++ {
		if runtime.NumGoroutine() <= base {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	buf := make([]byte, 1<<16)
	t.Fatalf("goroutines leaked: %d > %d\\n%s", runtime.NumGoroutine(), base, buf[:runtime.Stack(buf, true)])
}

func ints(n int) []int {
	s := make([]int, n)
	for i := range s {
		s[i] = i
	}
	return s
}

func TestMapProcessesEverythingWithBoundedConcurrency(t *testing.T) {
	base := runtime.NumGoroutine()
	g, ctx := WithContext(context.Background())
	var now, peak int32
	out := Map(ctx, g, Gen(ctx, ints(60)...), 4, func(_ context.Context, v int) (int, error) {
		n := atomic.AddInt32(&now, 1)
		for {
			p := atomic.LoadInt32(&peak)
			if n <= p || atomic.CompareAndSwapInt32(&peak, p, n) {
				break
			}
		}
		time.Sleep(5 * time.Millisecond)
		atomic.AddInt32(&now, -1)
		return v * v, nil
	})
	got := Collect(out)
	if err := g.Wait(); err != nil {
		t.Fatal(err)
	}
	sort.Ints(got)
	if len(got) != 60 || got[7] != 49 || got[59] != 59*59 {
		t.Fatalf("bad results: %v", got)
	}
	if peak < 2 || peak > 4 {
		t.Fatalf("peak concurrency %d, want 2..4", peak)
	}
	noLeak(t, base)
}

func TestMapOrderedKeepsInputOrderEvenWhenEarlyItemsAreSlow(t *testing.T) {
	base := runtime.NumGoroutine()
	g, ctx := WithContext(context.Background())
	out := MapOrdered(ctx, g, Gen(ctx, ints(40)...), 8, func(_ context.Context, v int) (int, error) {
		if v < 3 {
			time.Sleep(60 * time.Millisecond)
		}
		return v * 10, nil
	})
	got := Collect(out)
	if err := g.Wait(); err != nil {
		t.Fatal(err)
	}
	for i, v := range got {
		if v != i*10 {
			t.Fatalf("out of order at %d: %v", i, got)
		}
	}
	if len(got) != 40 {
		t.Fatalf("lost items: %d", len(got))
	}
	noLeak(t, base)
}

func TestFirstErrorCancelsEverythingAndLeaksNothing(t *testing.T) {
	base := runtime.NumGoroutine()
	boom := errors.New("boom")
	g, ctx := WithContext(context.Background())
	var processed int32
	out := Map(ctx, g, Gen(ctx, ints(100000)...), 4, func(_ context.Context, v int) (int, error) {
		atomic.AddInt32(&processed, 1)
		if v == 5 {
			return 0, boom
		}
		time.Sleep(time.Millisecond)
		return v, nil
	})
	Collect(out)
	if err := g.Wait(); !errors.Is(err, boom) {
		t.Fatalf("want boom, got %v", err)
	}
	if processed > 1000 {
		t.Fatalf("kept working after the error: %d items", processed)
	}
	noLeak(t, base)
}

func TestConsumerAbandonsAndCancelsWithoutDeadlock(t *testing.T) {
	base := runtime.NumGoroutine()
	parent, cancel := context.WithCancel(context.Background())
	g, ctx := WithContext(parent)
	out := MapOrdered(ctx, g, Gen(ctx, ints(1000000)...), 4, func(_ context.Context, v int) (int, error) { return v, nil })
	for i := 0; i < 10; i++ {
		<-out
	}
	cancel() // stop reading and cancel: producers blocked on send must wake up
	done := make(chan error)
	go func() { done <- g.Wait() }()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("want context.Canceled, got %v", err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("Wait deadlocked")
	}
	noLeak(t, base)
}

func TestPanicBecomesError(t *testing.T) {
	g, ctx := WithContext(context.Background())
	out := Map(ctx, g, Gen(ctx, 1, 2, 3), 2, func(_ context.Context, v int) (int, error) {
		if v == 2 {
			var m map[string]int
			m["x"] = 1 // nil map write panics
		}
		return v, nil
	})
	Collect(out)
	if err := g.Wait(); err == nil || err.Error()[:22] != "pipeline: worker panic" {
		t.Fatalf("want worker panic error, got %v", err)
	}
}

func TestMergeFansIn(t *testing.T) {
	base := runtime.NumGoroutine()
	ctx := context.Background()
	got := Collect(Merge(ctx, Gen(ctx, 1, 2, 3), Gen(ctx, 10, 20), Gen[int](ctx)))
	sort.Ints(got)
	want := []int{1, 2, 3, 10, 20}
	if len(got) != len(want) {
		t.Fatalf("got %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v", got)
		}
	}
	noLeak(t, base)
}

func TestEmptyInputAndSingleWorker(t *testing.T) {
	g, ctx := WithContext(context.Background())
	if got := Collect(Map(ctx, g, Gen[int](ctx), 0, func(_ context.Context, v int) (int, error) { return v, nil })); len(got) != 0 {
		t.Fatalf("got %v", got)
	}
	if err := g.Wait(); err != nil {
		t.Fatal(err)
	}
}

func TestSerialWorkerRunsInOrder(t *testing.T) {
	g, ctx := WithContext(context.Background())
	var mu sync.Mutex
	var seen []int
	Collect(Map(ctx, g, Gen(ctx, ints(10)...), 1, func(_ context.Context, v int) (int, error) {
		mu.Lock()
		seen = append(seen, v)
		mu.Unlock()
		return v, nil
	}))
	g.Wait()
	for i, v := range seen {
		if v != i {
			t.Fatalf("one worker must process sequentially: %v", seen)
		}
	}
}
'''}, GOT, assumptions=["Go >= 1.18 (generics); go.mod in the sandbox declares 1.21", "Channels add per-item overhead (~100s of ns): batch tiny work items, or use plain loops when work is CPU-trivial", "Unbuffered stages give natural backpressure; add buffers only after measuring", "golang.org/x/sync/errgroup is the maintained alternative to Group (adds SetLimit and TryGo)"], compat={"go": ">=1.18"},
   tags=["go", "golang", "concurrency", "worker-pool", "pipeline", "fan-out", "fan-in", "channels", "goroutines", "context", "cancellation", "errgroup", "bounded-parallelism", "generics", "ordered", "leak", "backpressure", "parallel"],
   avoid="Unbounded `go f()` per item; sends on a channel without selecting on ctx.Done(); forgetting to close the output channel; goroutines blocked forever after an early return; recovering panics silently instead of returning them as errors."),
]
