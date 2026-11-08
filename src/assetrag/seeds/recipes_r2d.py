"""Round-2 recipes, part D: browser/editor extension and tooling logic (TypeScript, node --test, erasable syntax only).
Extension host APIs (chrome.*, vscode.*) are injected as small interfaces, so what is tested is the logic - not a real browser or VS Code."""
from .recipes import RC
from .recipes_algo import NODE_TS, TSA

HOST = "host APIs are faked: the real browser / VS Code / language client is not run in the sandbox"

R2D = [
RC("ts-chrome-mv3-extension-messaging", "Chrome extension (Manifest V3): minimal manifest, manifest linter, typed message router (content script <-> service worker)", "typescript",
   "Starting a browser extension: the manifest, the service worker, and messaging between content script and background - where `return true` for async replies is the classic bug.",
   "A least-privilege MV3 `manifest.json`, a `validateManifest()` that flags MV2 leftovers, broad host permissions and unsafe CSP, and a `createRouter(handlers)` that validates message shape, supports sync and async handlers, keeps the channel open (returns true) only for async replies and converts throws into `{ok:false,error}`.",
   {"manifest.json": '''{
  "manifest_version": 3,
  "name": "Example Extension",
  "version": "0.1.0",
  "description": "Replace me.",
  "action": { "default_title": "Example", "default_popup": "popup.html" },
  "background": { "service_worker": "background.js", "type": "module" },
  "content_scripts": [{ "matches": ["https://example.com/*"], "js": ["content.js"], "run_at": "document_idle" }],
  "permissions": ["storage", "activeTab"],
  "host_permissions": ["https://example.com/*"],
  "content_security_policy": { "extension_pages": "script-src 'self'; object-src 'self'" }
}
''', "mv3.ts": r'''export interface Reply<T = unknown> { ok: boolean; data?: T; error?: string }
export type Handler = (msg: any, sender: { tabId?: number }) => unknown | Promise<unknown>

/** Pass to chrome.runtime.onMessage.addListener(createRouter({...})). Returning true keeps sendResponse alive for async replies. */
export function createRouter(handlers: Record<string, Handler>) {
  return function onMessage(msg: unknown, sender: { tabId?: number }, sendResponse: (r: Reply) => void): boolean {
    const type = (msg as { type?: unknown } | null)?.type
    if (typeof type !== 'string') { sendResponse({ ok: false, error: 'bad message' }); return false }
    if (!Object.hasOwn(handlers, type)) { sendResponse({ ok: false, error: `unknown message type: ${type}` }); return false }
    let out: unknown
    try { out = handlers[type](msg, sender) } catch (e) { sendResponse({ ok: false, error: String((e as Error)?.message ?? e) }); return false }
    if (out instanceof Promise) {
      out.then((data) => sendResponse({ ok: true, data }), (e) => sendResponse({ ok: false, error: String((e as Error)?.message ?? e) }))
      return true
    }
    sendResponse({ ok: true, data: out }); return false
  }
}

const SENSITIVE = ['tabs', 'history', 'cookies', 'webRequest', 'management', 'debugger', 'downloads', 'bookmarks']

/** Returns a list of problems (empty = fine). Meant for CI before you zip the extension. */
export function validateManifest(m: any): string[] {
  const p: string[] = []
  if (m.manifest_version !== 3) p.push('manifest_version must be 3')
  if (!m.background?.service_worker) p.push('background.service_worker is required in MV3')
  if (m.background?.scripts || m.background?.persistent !== undefined) p.push('MV2 background.scripts/persistent are not valid in MV3')
  if (m.browser_action || m.page_action) p.push('browser_action/page_action became "action"')
  if (typeof m.content_security_policy === 'string') p.push('content_security_policy must be an object in MV3')
  const csp = m.content_security_policy?.extension_pages ?? ''
  if (/unsafe-eval|unsafe-inline/.test(csp)) p.push('CSP allows unsafe-eval/unsafe-inline (store review will reject it)')
  if (/https?:\/\/[^/]*\*\//.test(csp) || /script-src[^;]*\bhttps?:/.test(csp)) p.push('CSP loads remote scripts; bundle them instead')
  for (const h of [...(m.host_permissions ?? []), ...(m.content_scripts ?? []).flatMap((c: any) => c.matches ?? [])]) {
    if (h === '<all_urls>' || h === '*://*/*' || h === 'https://*/*' || h === 'http://*/*') p.push(`broad host pattern ${h}: list only the sites you need`)
  }
  for (const perm of m.permissions ?? []) if (SENSITIVE.includes(perm)) p.push(`permission "${perm}" triggers extra review/warnings: is it needed (try activeTab)?`)
  if ((m.permissions ?? []).some((x: string) => x.includes('://'))) p.push('host patterns belong in host_permissions, not permissions')
  return p
}
''', "mv3.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRouter, validateManifest, type Reply } from './mv3.ts'

const call = (r: ReturnType<typeof createRouter>, msg: unknown) => {
  const replies: Reply[] = []
  const keepOpen = r(msg, { tabId: 7 }, (x) => replies.push(x))
  return { keepOpen, replies }
}

test('shipped manifest passes its own linter', () => {
  const m = JSON.parse(readFileSync(new URL('./manifest.json', import.meta.url), 'utf8'))
  assert.deepEqual(validateManifest(m), [])
})

test('linter catches MV2 leftovers, broad hosts, unsafe CSP, sensitive permissions', () => {
  const bad = { manifest_version: 2, background: { scripts: ['b.js'], persistent: true }, browser_action: {}, content_security_policy: "script-src 'self' 'unsafe-eval'",
    host_permissions: ['<all_urls>'], permissions: ['tabs', 'https://x.com/*'] }
  const out = validateManifest(bad).join('\n')
  for (const needle of ['manifest_version must be 3', 'service_worker is required', 'MV2 background', 'browser_action', 'must be an object', '<all_urls>', '"tabs"', 'host_permissions, not permissions']) assert.match(out, new RegExp(needle.replace(/[.*+?^${}()|[\]\\<>]/g, '\\$&')), needle)
  assert.match(validateManifest({ manifest_version: 3, background: { service_worker: 'b.js' }, content_security_policy: { extension_pages: "script-src 'self' 'unsafe-eval'" } }).join(), /unsafe-eval/)
})

test('sync handler replies immediately and closes the channel', () => {
  const { keepOpen, replies } = call(createRouter({ PING: () => 'pong' }), { type: 'PING' })
  assert.equal(keepOpen, false); assert.deepEqual(replies, [{ ok: true, data: 'pong' }])
})

test('async handler keeps the channel open (return true) and replies later', async () => {
  const { keepOpen, replies } = call(createRouter({ SLOW: async () => { await null; return 42 } }), { type: 'SLOW' })
  assert.equal(keepOpen, true); assert.deepEqual(replies, []); await new Promise((r) => setImmediate(r)); assert.deepEqual(replies, [{ ok: true, data: 42 }])
})

test('errors become replies, never uncaught exceptions', async () => {
  const r = createRouter({ BOOM: () => { throw new Error('nope') }, REJECT: async () => { throw new Error('later') } })
  assert.deepEqual(call(r, { type: 'BOOM' }).replies, [{ ok: false, error: 'nope' }])
  const a = call(r, { type: 'REJECT' }); await new Promise((x) => setImmediate(x)); assert.deepEqual(a.replies, [{ ok: false, error: 'later' }])
})

test('malformed or unknown messages are answered, prototype keys are not handlers', () => {
  const r = createRouter({ A: () => 1 })
  for (const m of [null, undefined, 'str', {}, { type: 5 }]) assert.equal(call(r, m).replies[0].ok, false)
  assert.match(call(r, { type: 'B' }).replies[0].error!, /unknown message type: B/)
  assert.match(call(r, { type: 'constructor' }).replies[0].error!, /unknown message type/)
})

test('handler receives sender for per-tab logic', () => {
  let seen: number | undefined
  call(createRouter({ WHO: (_m, s) => { seen = s.tabId } }), { type: 'WHO' }); assert.equal(seen, 7)
})
'''}, NODE_TS, assumptions=TSA + [HOST, "Chrome/Edge/Firefox (webextension-polyfill) share the shape; Safari needs the Xcode wrapper"], compat={"node": ">=22.18", "chrome": "MV3"},
   tags=["chrome-extension", "browser-extension", "manifest-v3", "mv3", "service-worker", "content-script", "message-passing", "webextension", "firefox", "extension"],
   avoid="Forgetting `return true` for async sendResponse (the port closes and the caller gets undefined); `<all_urls>` by default; remote-hosted code; background persistent pages (MV2)."),

RC("ts-vscode-extension-commands-settings", "VS Code extension: commands + settings with injected API, manifest/command consistency check", "typescript",
   "A VS Code extension needs commands that never throw into the host, typed settings with sane clamping, and `package.json` contributions that match what the code registers.",
   "`activate(ctx, api)` takes a narrow API interface instead of importing `vscode` directly (so logic is unit-testable), registers commands pushing disposables to `ctx.subscriptions`, wraps every command so failures become `showErrorMessage`, reads settings through one `readSettings()` that clamps and defaults, and a test asserting every registered command id is declared in `package.json` `contributes.commands` (the bug the marketplace users notice first).",
   {"package.json": '''{
  "name": "word-tools",
  "displayName": "Word Tools",
  "version": "0.1.0",
  "publisher": "example",
  "engines": { "vscode": "^1.90.0" },
  "categories": ["Other"],
  "main": "./out/extension.js",
  "activationEvents": [],
  "contributes": {
    "commands": [
      { "command": "wordTools.count", "title": "Word Tools: Count Words" },
      { "command": "wordTools.shout", "title": "Word Tools: Uppercase Selection" }
    ],
    "configuration": {
      "title": "Word Tools",
      "properties": {
        "wordTools.minWordLength": { "type": "number", "default": 1, "minimum": 1, "maximum": 20, "description": "Ignore words shorter than this." },
        "wordTools.showInStatusBar": { "type": "boolean", "default": true }
      }
    }
  }
}
''', "extension.ts": r'''export interface Disposable { dispose(): void }
export interface Api {
  registerCommand(id: string, fn: (...args: unknown[]) => unknown): Disposable
  config(section: string): { get<T>(key: string, fallback: T): T }
  info(msg: string): unknown
  error(msg: string): unknown
  selectionText(): string | undefined
  replaceSelection(text: string): Promise<boolean>
}
export interface Settings { minWordLength: number; showInStatusBar: boolean }

export const COMMANDS = ['wordTools.count', 'wordTools.shout'] as const

export function readSettings(api: Api): Settings {
  const c = api.config('wordTools')
  const raw = Number(c.get<number>('minWordLength', 1))
  return { minWordLength: Number.isFinite(raw) ? Math.min(20, Math.max(1, Math.round(raw))) : 1, showInStatusBar: c.get<boolean>('showInStatusBar', true) === true }
}

export function countWords(text: string, min = 1): number {
  return (text.match(/[\p{L}\p{N}'’-]+/gu) ?? []).filter((w) => w.length >= min).length
}

export function activate(ctx: { subscriptions: Disposable[] }, api: Api): void {
  // one wrapper so a thrown error becomes a message instead of the generic "command failed" toast
  const safe = (fn: () => unknown) => async () => { try { return await fn() } catch (e) { api.error(`Word Tools: ${(e as Error).message}`) } }
  const need = () => { const t = api.selectionText(); if (t === undefined || t === '') throw new Error('select some text first'); return t }
  ctx.subscriptions.push(
    api.registerCommand('wordTools.count', safe(() => api.info(`${countWords(need(), readSettings(api).minWordLength)} words`))),
    api.registerCommand('wordTools.shout', safe(async () => { if (!(await api.replaceSelection(need().toUpperCase()))) throw new Error('editor refused the edit') })),
  )
}
''', "extension.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { activate, readSettings, countWords, COMMANDS, type Api } from './extension.ts'

function fakeApi(cfg: Record<string, unknown> = {}, selection: string | null = 'hello brave new world', replaced = true) {
  const registered = new Map<string, (...a: unknown[]) => unknown>(), log: string[] = [], edits: string[] = []
  const api: Api = {
    registerCommand: (id, fn) => { registered.set(id, fn); return { dispose: () => registered.delete(id) } },
    config: () => ({ get: <T>(k: string, d: T) => (k in cfg ? (cfg[k] as T) : d) }),
    info: (m) => log.push('info:' + m), error: (m) => log.push('error:' + m),
    selectionText: () => selection ?? undefined, replaceSelection: async (t) => { edits.push(t); return replaced },
  }
  return { api, registered, log, edits }
}

test('every registered command is declared in package.json (and vice versa)', () => {
  const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
  const declared = pkg.contributes.commands.map((c: { command: string }) => c.command).sort()
  const { api, registered } = fakeApi(); activate({ subscriptions: [] }, api)
  assert.deepEqual([...registered.keys()].sort(), declared); assert.deepEqual([...COMMANDS].sort(), declared)
})

test('settings in code match the defaults and limits declared in package.json', () => {
  const props = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')).contributes.configuration.properties
  const s = readSettings(fakeApi().api)
  assert.equal(s.minWordLength, props['wordTools.minWordLength'].default); assert.equal(s.showInStatusBar, props['wordTools.showInStatusBar'].default)
})

test('settings are clamped and garbage-proof', () => {
  assert.equal(readSettings(fakeApi({ minWordLength: 500 }).api).minWordLength, 20); assert.equal(readSettings(fakeApi({ minWordLength: -3 }).api).minWordLength, 1)
  assert.equal(readSettings(fakeApi({ minWordLength: 'abc' }).api).minWordLength, 1); assert.equal(readSettings(fakeApi({ minWordLength: 2.6 }).api).minWordLength, 3)
  assert.equal(readSettings(fakeApi({ showInStatusBar: 'yes' }).api).showInStatusBar, false)
})

test('count command reports and respects minWordLength', async () => {
  const { api, registered, log } = fakeApi({ minWordLength: 4 }); activate({ subscriptions: [] }, api)
  await registered.get('wordTools.count')!(); assert.deepEqual(log, ['info:3 words'])
})

test('failures become messages, not exceptions', async () => {
  const none = fakeApi({}, null); activate({ subscriptions: [] }, none.api)
  await none.registered.get('wordTools.count')!(); assert.deepEqual(none.log, ['error:Word Tools: select some text first'])
  const refused = fakeApi({}, 'abc', false); activate({ subscriptions: [] }, refused.api)
  await refused.registered.get('wordTools.shout')!(); assert.match(refused.log[0], /editor refused/)
})

test('shout edits the selection; disposables are collected for cleanup', async () => {
  const f = fakeApi({}, 'quiet please'); const ctx = { subscriptions: [] as { dispose(): void }[] }; activate(ctx, f.api)
  await f.registered.get('wordTools.shout')!(); assert.deepEqual(f.edits, ['QUIET PLEASE']); assert.equal(ctx.subscriptions.length, 2)
  ctx.subscriptions.forEach((d) => d.dispose()); assert.equal(f.registered.size, 0)
})

test('word counting handles unicode, apostrophes and hyphens', () => {
  assert.equal(countWords("naïve café — it's well-known 42"), 5); assert.equal(countWords(''), 0)
})
'''}, NODE_TS, assumptions=TSA + [HOST, "Bundle with esbuild and publish with `vsce`; the real `vscode` module is adapted into `Api` in a 15-line shim"], compat={"node": ">=22.18", "vscode": "^1.90"},
   tags=["vscode", "vscode-extension", "extension", "commands", "settings", "configuration", "package-json", "editor", "marketplace", "activation"],
   avoid="Importing `vscode` everywhere (untestable); commands registered in code but missing from contributes (invisible in the palette); trusting settings values without clamping."),

RC("ts-debounce-throttle-timers", "debounce / throttle / once with leading+trailing options, cancel and flush (injectable timers)", "typescript",
   "Search boxes, resize handlers and save-on-type need rate limiting that is predictable and testable - not a copy-pasted snippet with the wrong edge behaviour.",
   "`debounce(fn, ms, {leading, trailing})` and `throttle(fn, ms, {leading, trailing})` with `cancel()`/`flush()`/`pending()`, last-args semantics, timers and clock injected so tests are exact and instant; throttle implemented separately (not as debounce with maxWait) to keep its guarantees obvious.",
   {"rate.ts": r'''export interface Timers { set(fn: () => void, ms: number): unknown; clear(h: unknown): void; now(): number }
export const realTimers: Timers = { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>), now: () => Date.now() }
export interface Opts { leading?: boolean; trailing?: boolean }
export interface Limited<A extends unknown[]> { (...a: A): void; cancel(): void; flush(): void; pending(): boolean }

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number, { leading = false, trailing = true }: Opts = {}, t: Timers = realTimers): Limited<A> {
  let h: unknown = undefined, args: A | undefined
  const fire = () => { const a = args; args = undefined; h = undefined; if (a) fn(...a) }
  const limited = ((...a: A) => {
    const first = h === undefined
    if (first && leading) fn(...a); else if (trailing) args = a
    if (h !== undefined) t.clear(h)
    h = t.set(() => { if (!trailing) args = undefined; fire() }, ms)
  }) as Limited<A>
  limited.cancel = () => { if (h !== undefined) t.clear(h); h = undefined; args = undefined }
  limited.flush = () => { if (h !== undefined) { t.clear(h); fire() } }
  limited.pending = () => h !== undefined
  return limited
}

export function throttle<A extends unknown[]>(fn: (...a: A) => void, ms: number, { leading = true, trailing = true }: Opts = {}, t: Timers = realTimers): Limited<A> {
  let last = -Infinity, h: unknown = undefined, args: A | undefined
  const run = (a: A) => { last = t.now(); fn(...a) }
  const limited = ((...a: A) => {
    const wait = last + ms - t.now()
    if (wait <= 0 && h === undefined && (leading || last !== -Infinity)) { run(a); return }
    if (!trailing && wait <= 0) { last = t.now(); return }
    if (trailing) {
      args = a
      if (h === undefined) h = t.set(() => { h = undefined; const x = args; args = undefined; if (x) run(x) }, Math.max(0, wait))
    }
  }) as Limited<A>
  limited.cancel = () => { if (h !== undefined) t.clear(h); h = undefined; args = undefined }
  limited.flush = () => { if (h !== undefined) { t.clear(h); h = undefined; const x = args; args = undefined; if (x) run(x) } }
  limited.pending = () => h !== undefined
  return limited
}

export function once<A extends unknown[], R>(fn: (...a: A) => R): (...a: A) => R {
  let done = false, value: R
  return (...a: A) => { if (!done) { value = fn(...a); done = true } return value }
}
''', "rate.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { debounce, throttle, once, type Timers } from './rate.ts'

function clock() {
  let now = 0, id = 0; const q = new Map<number, { at: number; fn: () => void }>()
  const t: Timers = { now: () => now, set: (fn, ms) => { q.set(++id, { at: now + ms, fn }); return id }, clear: (h) => { q.delete(h as number) } }
  const advance = (ms: number) => {
    const end = now + ms
    for (;;) { const due = [...q.entries()].filter(([, v]) => v.at <= end).sort((a, b) => a[1].at - b[1].at)[0]; if (!due) break; now = due[1].at; q.delete(due[0]); due[1].fn() }
    now = end
  }
  return { t, advance }
}

test('debounce: trailing fires once with the last arguments', () => {
  const c = clock(), got: number[] = []; const d = debounce((n: number) => got.push(n), 100, {}, c.t)
  d(1); c.advance(50); d(2); c.advance(50); d(3); assert.deepEqual(got, []); c.advance(99); assert.deepEqual(got, []); c.advance(1); assert.deepEqual(got, [3])
})

test('debounce: leading fires immediately, trailing only if called again', () => {
  const c = clock(), got: string[] = []; const d = debounce((s: string) => got.push(s), 100, { leading: true }, c.t)
  d('a'); assert.deepEqual(got, ['a']); c.advance(100); assert.deepEqual(got, ['a'])
  d('b'); d('c'); assert.deepEqual(got, ['a', 'b']); c.advance(100); assert.deepEqual(got, ['a', 'b', 'c'])
})

test('debounce: cancel drops the pending call, flush runs it now', () => {
  const c = clock(), got: number[] = []; const d = debounce((n: number) => got.push(n), 100, {}, c.t)
  d(1); assert.equal(d.pending(), true); d.cancel(); c.advance(500); assert.deepEqual(got, []); assert.equal(d.pending(), false)
  d(2); d.flush(); assert.deepEqual(got, [2]); c.advance(500); assert.deepEqual(got, [2]); d.flush(); assert.deepEqual(got, [2])
})

test('throttle: leading + trailing, at most one call per window, last args win', () => {
  const c = clock(), got: number[] = []; const th = throttle((n: number) => got.push(n), 100, {}, c.t)
  th(1); th(2); th(3); assert.deepEqual(got, [1]); c.advance(100); assert.deepEqual(got, [1, 3])
  c.advance(300); th(4); assert.deepEqual(got, [1, 3, 4])
})

test('throttle: sustained calls run once per interval', () => {
  const c = clock(), at: number[] = []; const th = throttle(() => at.push(c.t.now()), 100, {}, c.t)
  for (let i = 0; i < 50; i++) { th(); c.advance(10) }
  c.advance(200); assert.ok(at.every((x, i) => i === 0 || x - at[i - 1] >= 100 - 1e-9), at.join()); assert.ok(at.length >= 5 && at.length <= 7, String(at.length))
})

test('throttle: trailing=false drops calls inside the window; leading=false waits', () => {
  const c = clock(), a: number[] = [], b: number[] = []
  const t1 = throttle((n: number) => a.push(n), 100, { trailing: false }, c.t); t1(1); t1(2); c.advance(200); assert.deepEqual(a, [1])
  const t2 = throttle((n: number) => b.push(n), 100, { leading: false }, c.t); t2(1); assert.deepEqual(b, []); c.advance(100); assert.deepEqual(b, [1])
})

test('throttle: cancel clears the trailing call', () => {
  const c = clock(), got: number[] = []; const th = throttle((n: number) => got.push(n), 100, {}, c.t); th(1); th(2); th.cancel(); c.advance(500); assert.deepEqual(got, [1])
})

test('once memoizes the first result', () => {
  let n = 0; const f = once((x: number) => { n++; return x * 2 }); assert.equal(f(2), 4); assert.equal(f(99), 4); assert.equal(n, 1)
})
'''}, NODE_TS, assumptions=TSA + ["lodash.debounce/throttle are the battle-tested alternative; this shows the semantics and is testable without real time"], compat={"node": ">=22.18"},
   tags=["debounce", "throttle", "rate-limit", "timers", "ui", "search-box", "resize", "performance", "events", "utility"],
   avoid="Creating a new debounced function on every React render; debouncing a handler that needs the first call immediately; tests that sleep."),

RC("ts-config-parser-line-col-errors", "Config/INI-style parser with line:column errors, source excerpts and aggregated diagnostics", "typescript",
   "A CLI or tool reads a hand-edited config file; 'invalid config' is useless - the user needs `file:3:7` and a caret under the problem, and all the errors at once.",
   "Line-based parser for `[section]`, `key = value`, comments, quoted strings with escapes, booleans/numbers, duplicate-key detection, errors with line/column and a caret excerpt, collected (not first-fail) and thrown as one `ConfigError`; pure function so it works for file, stdin or editor buffers.",
   {"conf.ts": r'''export interface Diag { line: number; col: number; message: string }
export class ConfigError extends Error {
  diags: Diag[]
  constructor(file: string, text: string, diags: Diag[]) {
    super(diags.map((d) => format(file, text, d)).join('\n\n')); this.name = 'ConfigError'; this.diags = diags
  }
}
export type Value = string | number | boolean
export type Config = Record<string, Record<string, Value>>

export function format(file: string, text: string, d: Diag): string {
  const src = text.split(/\r?\n/)[d.line - 1] ?? ''
  return `${file}:${d.line}:${d.col}: ${d.message}\n  ${src}\n  ${' '.repeat(Math.max(0, d.col - 1))}^`
}

function parseValue(raw: string, col: number): { v?: Value; err?: Diag; at?: number } {
  const s = raw.trim(), lead = raw.length - raw.trimStart().length
  if (s === '') return { err: { line: 0, col: col + raw.length, message: 'missing value' } }
  if (s[0] === '"') {
    let out = '', i = 1
    for (; i < s.length; i++) {
      if (s[i] === '\\') { const n = s[++i]; out += n === 'n' ? '\n' : n === 't' ? '\t' : n ?? ''; continue }
      if (s[i] === '"') break
      out += s[i]
    }
    if (i >= s.length) return { err: { line: 0, col: col + lead, message: 'unterminated string' } }
    const tail = s.slice(i + 1), rest = tail.replace(/\s+#.*$/, '').trim()
    if (rest) return { err: { line: 0, col: col + lead + i + 1 + (tail.length - tail.trimStart().length), message: `unexpected text after string: ${rest}` } }
    return { v: out }
  }
  const bare = s.replace(/\s+#.*$/, '').trim()
  if (/^(true|yes|on)$/i.test(bare)) return { v: true }
  if (/^(false|no|off)$/i.test(bare)) return { v: false }
  if (/^-?\d+(\.\d+)?$/.test(bare)) return { v: Number(bare) }
  return { v: bare }
}

export function parse(text: string, file = 'config'): Config {
  const out: Config = { '': {} }, diags: Diag[] = []; let section = ''
  text.split(/\r?\n/).forEach((line, idx) => {
    const n = idx + 1, t = line.trim()
    if (t === '' || t.startsWith('#') || t.startsWith(';')) return
    const sec = /^\[([^\]]*)\]\s*(#.*)?$/.exec(t)
    if (t.startsWith('[')) {
      if (!sec) { diags.push({ line: n, col: line.indexOf('[') + 1, message: 'malformed section header (expected [name])' }); return }
      const name = sec[1].trim()
      if (!name) { diags.push({ line: n, col: line.indexOf('[') + 2, message: 'empty section name' }); return }
      section = name; out[section] ??= {}; return
    }
    const eq = line.indexOf('=')
    if (eq < 0) { diags.push({ line: n, col: line.length - line.trimStart().length + 1, message: `expected "key = value", got "${t}"` }); return }
    const key = line.slice(0, eq).trim()
    if (!/^[A-Za-z_][\w.-]*$/.test(key)) { diags.push({ line: n, col: line.length - line.trimStart().length + 1, message: `invalid key "${key}"` }); return }
    const r = parseValue(line.slice(eq + 1), eq + 2)
    if (r.err) { diags.push({ ...r.err, line: n }); return }
    if (key in out[section]) { diags.push({ line: n, col: line.indexOf(key) + 1, message: `duplicate key "${key}" in [${section || 'top level'}]` }); return }
    out[section][key] = r.v as Value
  })
  if (diags.length) throw new ConfigError(file, text, diags)
  return out
}
''', "conf.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parse, ConfigError } from './conf.ts'

const GOOD = `# top comment
name = demo
retries = 3
ratio = -0.5

[server]
host = "127.0.0.1"   # trailing comment
debug = yes
motto = "say \\"hi\\"\\n"
; another comment
[db]
url = postgres://u@h/db
`

test('parses sections, types, quotes, comments', () => {
  const c = parse(GOOD)
  assert.deepEqual(c[''], { name: 'demo', retries: 3, ratio: -0.5 })
  assert.deepEqual(c.server, { host: '127.0.0.1', debug: true, motto: 'say "hi"\n' }); assert.equal(c.db.url, 'postgres://u@h/db')
})

function errors(text: string) { try { parse(text, 'app.conf'); assert.fail('should throw') } catch (e) { assert.ok(e instanceof ConfigError); return e as ConfigError } }

test('reports all errors with line and column, not just the first', () => {
  const e = errors('a = 1\nbroken line\n[sec\nb = "oops\nc = 1 2 3 # ok\nd = "x" y\n')
  assert.deepEqual(e.diags.map((d) => [d.line, d.col]), [[2, 1], [3, 1], [4, 5], [6, 9]])
  assert.match(e.diags[0].message, /expected "key = value"/); assert.match(e.diags[2].message, /unterminated/); assert.match(e.diags[3].message, /unexpected text after string: y/)
})

test('message carries file:line:col and a caret under the problem', () => {
  const e = errors('ok = 1\n  bad key = 2\n')
  assert.match(e.message, /app\.conf:2:3: invalid key "bad key"/); const lines = e.message.split('\n'); assert.equal(lines[1], '    bad key = 2'); assert.equal(lines[2], '    ^')
})

test('duplicate keys are per section; same key in another section is fine', () => {
  assert.doesNotThrow(() => parse('[a]\nx=1\n[b]\nx=2\n')); assert.match(errors('[a]\nx=1\nx=2\n').diags[0].message, /duplicate key "x" in \[a\]/)
  assert.equal(errors('x=1\nx=2\n').diags[0].line, 2)
})

test('empty value and bad headers', () => {
  assert.match(errors('k =\n').diags[0].message, /missing value/); assert.match(errors('[]\n').diags[0].message, /empty section/); assert.match(errors('[a] junk\n').diags[0].message, /malformed section/)
})

test('CRLF files and values containing = work', () => {
  const c = parse('a = x=y\r\n[s]\r\nb = 2\r\n'); assert.equal(c[''].a, 'x=y'); assert.equal(c.s.b, 2)
})
'''}, NODE_TS, assumptions=TSA + ["For real formats prefer TOML/YAML parsers; they report positions too (smol-toml, yaml)"], compat={"node": ">=22.18"},
   tags=["config", "parser", "ini", "error-messages", "diagnostics", "line-numbers", "cli", "developer-experience", "validation"],
   avoid="Throwing on the first error; messages without file:line:col; swallowing unknown lines silently."),

RC("ts-lsp-jsonrpc-framing-diagnostics", "Language Server basics: Content-Length framing parser (chunk-safe, UTF-8 aware) and a diagnostics provider", "typescript",
   "Writing a language server or any JSON-RPC-over-stdio tool: messages arrive split across chunks, glued together, and with multi-byte characters where Content-Length counts bytes.",
   "A `FrameParser` that buffers bytes, reads `Content-Length` headers, yields complete JSON messages regardless of chunk boundaries, and rejects malformed headers; `encode()` computes length in bytes; a small `lintTodos(text)` producing LSP `Diagnostic[]` with 0-based line/character ranges as the example feature.",
   {"lsp.ts": r'''export interface Message { jsonrpc: '2.0'; id?: number | string; method?: string; params?: unknown; result?: unknown; error?: { code: number; message: string } }

export function encode(msg: Message): Buffer {
  const body = Buffer.from(JSON.stringify(msg), 'utf8')           // length is BYTES, not string length
  return Buffer.concat([Buffer.from(`Content-Length: ${body.length}\r\n\r\n`, 'ascii'), body])
}

export class FrameParser {
  private buf: Buffer = Buffer.alloc(0)
  private static MAX = 64 * 1024 * 1024
  push(chunk: Uint8Array): Message[] {
    this.buf = Buffer.concat([this.buf, chunk])
    const out: Message[] = []
    for (;;) {
      const end = this.buf.indexOf('\r\n\r\n')
      if (end < 0) { if (this.buf.length > 8192) throw new Error('header too long'); return out }
      const header = this.buf.subarray(0, end).toString('ascii')
      const m = /^content-length:\s*(\d+)$/im.exec(header)
      if (!m) throw new Error(`missing Content-Length in header: ${JSON.stringify(header.slice(0, 80))}`)
      const len = Number(m[1])
      if (len > FrameParser.MAX) throw new Error('message too large')
      const start = end + 4
      if (this.buf.length < start + len) return out                // wait for the rest of the body
      out.push(JSON.parse(this.buf.subarray(start, start + len).toString('utf8')))
      this.buf = this.buf.subarray(start + len)
    }
  }
}

export interface Diagnostic { range: { start: { line: number; character: number }; end: { line: number; character: number } }; severity: 1 | 2 | 3 | 4; message: string; source: string }

/** Example language feature: flag TODO/FIXME markers. LSP positions are 0-based; `character` counts UTF-16 code units (JS string indices). */
export function lintTodos(text: string): Diagnostic[] {
  const out: Diagnostic[] = []
  text.split(/\r?\n/).forEach((line, i) => {
    for (const m of line.matchAll(/\b(TODO|FIXME)\b/g)) {
      out.push({ range: { start: { line: i, character: m.index! }, end: { line: i, character: m.index! + m[0].length } }, severity: m[0] === 'FIXME' ? 2 : 3, message: `${m[0]} left in code`, source: 'todo-lint' })
    }
  })
  return out
}
''', "lsp.test.ts": r'''import { test } from 'node:test'
import assert from 'node:assert/strict'
import { encode, FrameParser, lintTodos, type Message } from './lsp.ts'

const msgs: Message[] = [{ jsonrpc: '2.0', id: 1, method: 'initialize', params: { name: 'ünïcödé 日本語 🙂' } }, { jsonrpc: '2.0', method: 'initialized' }, { jsonrpc: '2.0', id: 2, result: null }]

test('round trip, one message per chunk', () => {
  const p = new FrameParser(); assert.deepEqual(msgs.flatMap((m) => p.push(encode(m))), msgs)
})

test('Content-Length counts bytes, not characters', () => {
  const m = msgs[0], buf = encode(m), declared = Number(/Content-Length: (\d+)/.exec(buf.toString('ascii', 0, 30))![1])
  assert.ok(declared > JSON.stringify(m).length); assert.equal(declared, Buffer.byteLength(JSON.stringify(m)))
})

test('any chunking works, including 1 byte at a time and several messages glued together', () => {
  const all = Buffer.concat(msgs.map(encode))
  for (const size of [1, 2, 3, 7, 50, all.length]) {
    const p = new FrameParser(), got: Message[] = []
    for (let i = 0; i < all.length; i += size) got.push(...p.push(all.subarray(i, i + size)))
    assert.deepEqual(got, msgs, `chunk size ${size}`)
  }
})

test('a multi-byte character split across chunks is not corrupted', () => {
  const buf = encode(msgs[0]), cut = buf.indexOf(Buffer.from('🙂')) + 2, p = new FrameParser()
  assert.deepEqual(p.push(buf.subarray(0, cut)), []); assert.deepEqual(p.push(buf.subarray(cut)), [msgs[0]])
})

test('extra headers are fine; malformed or missing ones are errors, not hangs', () => {
  const body = Buffer.from('{"jsonrpc":"2.0","method":"x"}'), good = Buffer.concat([Buffer.from(`Content-Type: application/vscode-jsonrpc; charset=utf-8\r\ncontent-length: ${body.length}\r\n\r\n`), body])
  assert.equal(new FrameParser().push(good)[0].method, 'x')
  assert.throws(() => new FrameParser().push(Buffer.from('Foo: bar\r\n\r\n{}')), /missing Content-Length/)
  assert.throws(() => new FrameParser().push(Buffer.from('x'.repeat(9000))), /header too long/)
  assert.throws(() => new FrameParser().push(Buffer.from('Content-Length: 999999999999\r\n\r\n')), /too large/)
})

test('lintTodos gives 0-based ranges and severities', () => {
  const d = lintTodos('ok\n  // TODO: later\nx = 1 // FIXME now TODO')
  assert.deepEqual(d.map((x) => [x.range.start.line, x.range.start.character, x.range.end.character, x.severity]), [[1, 5, 9, 3], [2, 9, 14, 2], [2, 19, 23, 3]])
  assert.deepEqual(lintTodos('nothing here'), []); assert.deepEqual(lintTodos('TODOS and FIXMEs are words'), [])
})
'''}, NODE_TS, assumptions=TSA + [HOST + " (framing and diagnostics are exercised, a real LSP client is not)", "vscode-languageserver-node implements the full protocol; use it unless you need custom transport"], compat={"node": ">=22.18"},
   tags=["lsp", "language-server", "json-rpc", "jsonrpc", "diagnostics", "stdio", "protocol", "framing", "editor", "extension", "parser"],
   avoid="Splitting stdin on newlines; using string length for Content-Length; decoding each chunk to a string before reassembling bytes."),
]
