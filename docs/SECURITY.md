# Security model

| Threat | Control |
|---|---|
| Malware in downloads | ClamAV scan of every fresh clone before reading (resumable, stamped per commit); final full-project scan → `security-report.md` |
| Running untrusted code | Only inside `bubblewrap`: no network, read-only root, private /tmp, `--die-with-parent`; refuses to run if bwrap missing. Install scripts / hooks / downloaded binaries are never executed (`git clone` with hooks disabled; `npm` lifecycle scripts rejected) |
| Path traversal / zip-slip | `safe_join` on every write; `safe_extract` skips symlinks/devices, caps file count & size |
| Secrets | Regex gate at admission and before harvest/lessons; placeholders ignored |
| Prompt injection via assets | All retrieved file text is wrapped as `UNTRUSTED ASSET CONTENT`; instruction-like phrases flagged and recorded; server instructions tell the client to treat it as data |
| Dangerous code patterns | `curl|sh`, reverse-shell, base64-eval etc. rejected; binaries/executables rejected |
| Vulnerable dependencies | License + registry resolution at admission; `audit_project` re-checks a project's manifests. (A CVE feed is not bundled; run `pip-audit`/`npm audit`/`govulncheck` on imported assets' deps.) |
| MCP input | IDs regex-validated, categories enumerated, project paths resolved and confined, limits clamped |

ClamAV is one layer, not a guarantee.

## Network and privacy
No telemetry. Outbound traffic happens only when you run: `populate` / `discover` / `ingest` / `mine` (git clones and `gh` calls to GitHub),
`update` (`gh`; plus api.osv.dev with `--osv`, package names only), and requests to the embedding server you configure (local by default).
`embed_mode=off` makes search fully offline. Config files holding an embed key are written `0600`.

## Reporting a vulnerability
Open a private security advisory on the GitHub repository (Security tab), or an issue with no exploit details. Please do not post working exploits publicly.

## Last release scan (2026-11-08, ClamAV 1.5.4, signatures 28177)
- Repository export (`git archive HEAD`, 148 files): 0 detections.
- Local asset library used during development (69,138 files, 237 MiB; not shipped): 0 detections.
- Git history: scanned with the bundled gitleaks-derived rules; 4 hits, all test fixtures / rule descriptions, no real credentials.
A clean scan is one layer of evidence, not a guarantee.
