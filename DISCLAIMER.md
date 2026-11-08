# Disclaimer

**Read this before you use AssetRAG or anything it hands to your AI agent.**

AssetRAG is free software provided **"AS IS", without warranty of any kind** (see the Apache-2.0 `LICENSE`, sections 7 and 8).
The author and contributors are **not responsible** for anything that happens as a result of using it.

## Not legal advice
- Nothing in this repository, its output, its documentation or its licence checks is legal advice. The author is not a lawyer.
- The licence gate is **engineering tooling**. It fingerprints LICENSE text and applies an allowlist. It can be wrong: a repository can contain
  vendored code under another licence, change its licence later, or carry a LICENSE file that does not match reality.
- **You** are responsible for checking the licence, copyright and attribution requirements of every asset, package, snippet or template you
  or your AI agent copy into a project, before you ship it. If it matters (commercial product, client work, anything public), ask a lawyer.

## Third-party content is not mine and not endorsed
- AssetRAG ships **no** third-party assets. It indexes content fetched on your machine from public repositories. That content belongs to
  its authors, stays under their licences, and may be wrong, outdated, insecure or malicious.
- Mention of a project, package, font or service is a pointer, not a recommendation, endorsement or guarantee that it is safe or suitable.
- All retrieved text and code is untrusted data. Review it before running it. The ClamAV scan and the sandbox reduce risk; they do not remove it.

## AI agents
- If an AI agent (Claude Code or any other) uses AssetRAG to find, import or adapt code, **the author is not responsible for what the agent
  chooses, imports, writes, runs, deletes or ships**. You are responsible for reviewing the agent's work.
- Search results, "why it matched" lines, verdicts and the benchmark numbers are heuristics. They can be wrong.

## Privacy and network use
- AssetRAG has no telemetry. Network use is: fetching sources you ask it to ingest (GitHub clones and `gh` API calls, which reveal your IP
  address to GitHub), the optional `assetrag update --osv` call (sends package names only to api.osv.dev), and your own embedding server
  (local by default). Fonts, scripts or trackers loaded by imported web assets may contact third parties when *your site* is visited;
  `assetrag audit` and `import` warn about those references.
- Downloading anything from the internet exposes your IP address to the host. If you need to hide it, use your own VPN or proxy
  (the standard `HTTPS_PROXY` / `git config http.proxy` settings are honoured by the tools AssetRAG calls). AssetRAG does not provide anonymity.

## Liability
To the maximum extent permitted by law, the author is not liable for any claim, damages or other liability, whether in contract, tort or otherwise,
arising from the software or its use, including infringement claims over code you chose to copy.
