# Licensing policy (as implemented)

Decisions are made by `licenses.py`, deterministically. No model output is ever treated as legal proof.

1. **Evidence, not claims.** The license *text* found at the source (LICENSE/COPYING) is fingerprinted to an SPDX id.
   Manifest/registry/GitHub claims are only cross-checks; disagreement → rejected ("conflicting license claims").
   A repo with only `"license": "MIT"` in package.json and no license file is rejected (no verifiable text).
2. **Allowlist.** MIT, MIT-0, ISC, BSD-2/3, Apache-2.0, 0BSD, Unlicense, CC0, Zlib, BSL-1.0, CC-BY-3.0/4.0,
   OFL-1.1, MPL-2.0 (weak copyleft), LGPL (weak), GPL/AGPL (strong/network copyleft; stored but only offered to
   `open_source`/`saas` profiles as appropriate). Everything else - NC, ND, SSPL, BUSL, Commons Clause, Hippocratic,
   dual "free for OSS / paid for commercial" (e.g. Hover.css), unknown text - is rejected.
3. **Dependencies.** Each declared dependency's license is resolved (npm / PyPI / crates.io / GitHub LICENSE text)
   and must pass; unresolved = `UNKNOWN` = rejected.
4. **Profiles.** `closed_source` (permissive + weak copyleft), `saas` (adds GPL, still excludes AGPL),
   `open_source` (everything on the allowlist). Search and `import_asset` apply the profile, including
   dependency licenses.
5. **Notices.** `import_asset` writes `THIRD_PARTY_NOTICES.md` (license text + copyright + source) - the place
   for legally required notices when you don't want credits in the README. `allow_notices=false` excludes every
   asset whose license requires a notice.
6. **Rejections are not queued.** Rejected candidates are only counted by reason (`reject_stats`).
7. **Provenance.** UI/template/animation assets must be `human`-provenance (original authors' repos);
   `generated` visual assets are rejected at the gate. Only backend/cli/algorithm code may be `generated`/`harvested`.
   No AI-detector is used or trusted.

Limits: README says "MIT" in a repo whose individual files carry other notices (vendored third-party code inside
a repo) cannot be fully detected; `ASSET.json` + `LICENSE.evidence` record exactly what was verified. This is
engineering tooling, not legal advice.

## What this repository itself contains
- Code: Apache-2.0 (`LICENSE`). The knowledge seeds (recipes, bug fixes, rules, playbooks, package guidance) are **authored text and code** written for this
  project; they are not copied from third-party repositories. Package guidance names a repository and describes it in our own words.
- Not shipped: any ingested asset, template, icon, font or mined bug fix. Those are fetched on the user's machine and stay under their own licences.
- `src/assetrag/data/gitleaks_rules.json` is derived from gitleaks (MIT), see `NOTICE`.

## Known limits (read with `DISCLAIMER.md`)
- Allowlist entries with copyleft (MPL, LGPL, GPL, AGPL) exist only for the `saas`/`open_source` profiles; the default `closed_source` profile excludes strong copyleft.
- A licence can change after ingestion. `assetrag update` re-checks upstream and quarantines anything that is no longer permitted; it cannot know about a
  change that happened after its last run.
- Icons/fonts/illustrations: the gate reads the repository licence. Trademarks, logos and brand marks inside an icon set are *not* covered by an open licence.
- Web assets may load fonts/scripts from third-party hosts (visitor IPs leak). `import_asset` and `audit` warn; self-host instead.
