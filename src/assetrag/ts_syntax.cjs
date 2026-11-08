// usage: node ts_syntax.cjs <typescript-dir> <dir> <files...>   prints syntax errors, exit 1 if any
const ts = require(process.argv[2]);
const fs = require("fs"), path = require("path");
const dir = process.argv[3];
let bad = [];
for (const f of process.argv.slice(4)) {
  const src = fs.readFileSync(path.join(dir, f), "utf8");
  const kind = f.endsWith("x") ? ts.ScriptKind.TSX : f.endsWith(".js") ? ts.ScriptKind.JS : ts.ScriptKind.TS;
  const sf = ts.createSourceFile(f, src, ts.ScriptTarget.Latest, true, kind);
  for (const d of sf.parseDiagnostics || []) bad.push(`${f}: ${ts.flattenDiagnosticMessageText(d.messageText, " ")}`);
}
if (bad.length) { console.log(bad.slice(0, 3).join("; ")); process.exit(1); }
