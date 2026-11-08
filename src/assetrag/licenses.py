"""Deterministic license policy. No model is involved in any decision here.

Three pieces:
  * `detect(text)`      - fingerprint license text -> SPDX id (or None)
  * `POLICY`            - SPDX id -> commercial-use facts
  * `check(spdx, prof)` - verdict for a distribution profile
Anything not explicitly listed is rejected (unknown == no permission).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lic:
    spdx: str
    attribution: bool        # notice/copyright must be preserved
    copyleft: str            # none | weak | strong | network
    note: str = ""


def _L(spdx, attr=True, cl="none", note=""): return Lic(spdx, attr, cl, note)

# Explicit allowlist. Everything else -> reject.
POLICY: dict[str, Lic] = {l.spdx: l for l in [
    _L("MIT"), _L("MIT-0", False), _L("ISC"), _L("BSD-2-Clause"), _L("BSD-3-Clause"),
    _L("Apache-2.0", note="keep NOTICE; state changes"), _L("0BSD", False),
    _L("Unlicense", False), _L("CC0-1.0", False), _L("Zlib"), _L("BSL-1.0", False),
    _L("CC-BY-4.0"), _L("CC-BY-3.0"), _L("OFL-1.1", note="fonts: do not sell font alone"),
    _L("MPL-2.0", cl="weak", note="file-level copyleft"),
    _L("LGPL-2.1-only", cl="weak"), _L("LGPL-3.0-only", cl="weak"),
    _L("GPL-2.0-only", cl="strong"), _L("GPL-3.0-only", cl="strong"),
    _L("AGPL-3.0-only", cl="network"),
    _L("Python-2.0"), _L("PSF-2.0"), _L("BlueOak-1.0.0"),
]}

ALIASES = {
    "mit": "MIT", "isc": "ISC", "apache-2.0": "Apache-2.0", "apache 2.0": "Apache-2.0",
    "apache2": "Apache-2.0", "bsd-2-clause": "BSD-2-Clause", "bsd-3-clause": "BSD-3-Clause",
    "bsd": "BSD-3-Clause", "0bsd": "0BSD", "unlicense": "Unlicense", "cc0-1.0": "CC0-1.0",
    "cc0": "CC0-1.0", "zlib": "Zlib", "bsl-1.0": "BSL-1.0", "cc-by-4.0": "CC-BY-4.0",
    "cc-by-3.0": "CC-BY-3.0", "ofl-1.1": "OFL-1.1", "mpl-2.0": "MPL-2.0",
    "lgpl-2.1": "LGPL-2.1-only", "lgpl-3.0": "LGPL-3.0-only", "gpl-2.0": "GPL-2.0-only",
    "gpl-3.0": "GPL-3.0-only", "agpl-3.0": "AGPL-3.0-only", "mit-0": "MIT-0",
    "psf-2.0": "PSF-2.0", "python-2.0": "Python-2.0", "blueoak-1.0.0": "BlueOak-1.0.0",
    "gpl-2.0-only": "GPL-2.0-only", "gpl-3.0-only": "GPL-3.0-only",
    "lgpl-2.1-only": "LGPL-2.1-only", "lgpl-3.0-only": "LGPL-3.0-only",
    "agpl-3.0-only": "AGPL-3.0-only", "gpl-2.0-or-later": "GPL-2.0-only",
    "gpl-3.0-or-later": "GPL-3.0-only", "agpl-3.0-or-later": "AGPL-3.0-only",
}

# Hard rejects, with the reason shown to users.
REJECT_HINTS = {
    "nc": "non-commercial restriction", "noncommercial": "non-commercial restriction",
    "nd": "no-derivatives restriction", "sspl": "SSPL (not an open license)",
    "busl": "BUSL (production use restricted)", "commons clause": "Commons Clause",
    "hippocratic": "Hippocratic (use restrictions)", "proprietary": "proprietary",
    "unlicensed": "no license", "see license": "unresolved license reference",
}

_SPDX_RE = re.compile(r"[\s()]+(?:and|or|with)[\s()]+|[()]", re.I)


def normalize_spdx(raw: str | None) -> str | None:
    """Map an arbitrary license string to a single SPDX id we know, else None.

    Compound expressions: 'A OR B' -> pick the first permitted alternative
    (the licensee may choose); 'A AND B' -> both must be permitted, we return
    the more restrictive one. 'WITH' exceptions are rejected (None)."""
    if not raw:
        return None
    s = raw.strip()
    if re.search(r"\bwith\b", s, re.I):
        return None
    if re.search(r"\bAND\b", s):
        ids = [normalize_spdx(p) for p in re.split(r"\s+AND\s+", s.strip("() "))]
        if any(i is None for i in ids):
            return None
        order = {"none": 0, "weak": 1, "strong": 2, "network": 3}
        return max(ids, key=lambda i: order[POLICY[i].copyleft])
    if re.search(r"\bOR\b", s):
        for part in re.split(r"\s+OR\s+", s.strip("() ")):
            i = normalize_spdx(part)
            if i:
                return i
        return None
    key = s.strip("() ").lower()
    if key in ALIASES:
        return ALIASES[key]
    for spdx in POLICY:
        if spdx.lower() == key:
            return spdx
    return None


def reject_reason(raw: str | None) -> str:
    if not raw:
        return "missing license"
    low = raw.lower()
    for k, v in REJECT_HINTS.items():
        if re.search(rf"(^|[-\s]){re.escape(k)}($|[-\s.])", low):
            return v
    return f"license not on commercial allowlist: {raw[:60]}"


# --- fingerprinting of license *text* -------------------------------------

def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t.lower())).strip()


_SIGS: list[tuple[str, list[str]]] = [
    # order matters: most specific first
    ("AGPL-3.0-only", ["gnu affero general public license", "version 3"]),
    ("LGPL-3.0-only", ["gnu lesser general public license", "version 3"]),
    ("LGPL-2.1-only", ["gnu lesser general public license", "version 2 1"]),
    ("GPL-3.0-only", ["gnu general public license", "version 3"]),
    ("GPL-2.0-only", ["gnu general public license", "version 2"]),
    ("MPL-2.0", ["mozilla public license version 2 0"]),
    ("Apache-2.0", ["apache license", "version 2 0", "terms and conditions for use reproduction and distribution"]),
    ("CC0-1.0", ["cc0 1 0 universal"]),
    ("CC-BY-4.0", ["creative commons attribution 4 0"]),
    ("CC-BY-3.0", ["creative commons attribution 3 0"]),
    ("OFL-1.1", ["sil open font license", "version 1 1"]),
    ("BSL-1.0", ["boost software license"]),
    ("Unlicense", ["this is free and unencumbered software released into the public domain"]),
    ("MIT-0", ["permission is hereby granted free of charge to any person obtaining a copy", "without restriction including without limitation"]),  # refined below
    ("ISC", ["permission to use copy modify and or distribute this software for any purpose with or without fee is hereby granted", "isc"]),
    ("Zlib", ["this software is provided as is without any express or implied warranty in no event will the authors be held liable", "altered source versions must be plainly marked"]),
]
_NC = re.compile(r"non[\s-]?commercial|noncommercial|no[\s-]?derivs|noderivatives|commons clause|hippocratic", re.I)


def detect(text: str) -> str | None:
    """Fingerprint license text. Returns None when unrecognised or when the
    text contains restriction markers (so a modified/odd license is rejected)."""
    if not text or len(text) < 40:
        return None
    n = _norm(text)
    if _NC.search(text):
        return None
    if "sspl" in n or "server side public license" in n or "business source license" in n:
        return None
    # BSD family (before MIT: BSD text also has 'permission' phrases)
    if "redistribution and use in source and binary forms" in n:
        if "neither the name" in n or "may be used to endorse or promote" in n:
            return "BSD-3-Clause"
        if "this list of conditions" in n:
            return "BSD-2-Clause"
    # A licence text may legitimately *mention* others (MPL-2.0 names the GNU licences as "Secondary Licenses", GPL-3.0 §13
    # names the AGPL). The licence being granted is the one whose name appears first in the file.
    hits = [(min(n.find(x) for x in needles), i, spdx) for i, (spdx, needles) in enumerate(_SIGS)
            if spdx not in ("MIT-0", "ISC") and all(x in n for x in needles)]
    if hits:
        return min(hits)[2]
    pos = {}
    i = n.find("permission is hereby granted free of charge to any person obtaining a copy")
    if i >= 0 and "without restriction including without limitation" in n:
        pos["MIT" if "the above copyright notice and this permission notice shall be included" in n else "MIT-0"] = i
    i = n.find("permission to use copy modify and or distribute this software for any purpose with or without fee is hereby granted")
    if i >= 0:
        pos["ISC" if "provided that the above copyright notice" in n else "0BSD"] = i
    if pos:
        return min(pos, key=pos.get)   # first license in the file governs
    if "python software foundation license" in n:
        return "PSF-2.0"
    return None


def evidence_hash(text: str) -> str:
    return hashlib.sha256(_norm(text).encode()).hexdigest()


# --- compatibility verdicts -------------------------------------------------

PROFILES = {
    # closed-source product distributed to users (binary/app)
    "closed_source": {"allow_copyleft": {"none", "weak"}, "allow_attribution": True},
    # hosted service; AGPL still triggers via network use
    "saas": {"allow_copyleft": {"none", "weak", "strong"}, "allow_attribution": True},
    # project itself released under an open license
    "open_source": {"allow_copyleft": {"none", "weak", "strong", "network"}, "allow_attribution": True},
}


@dataclass
class Verdict:
    ok: bool
    spdx: str | None
    reason: str
    obligations: list[str] = field(default_factory=list)


def check(raw_license: str | None, profile: str = "closed_source", *,
          allow_notices: bool = True, dep_licenses: dict[str, str] | None = None) -> Verdict:
    spdx = normalize_spdx(raw_license)
    if spdx is None:
        return Verdict(False, None, reject_reason(raw_license))
    lic, prof = POLICY[spdx], PROFILES.get(profile)
    if prof is None:
        return Verdict(False, spdx, f"unknown profile {profile!r}")
    obl: list[str] = []
    if lic.copyleft not in prof["allow_copyleft"]:
        return Verdict(False, spdx, f"{lic.copyleft} copyleft ({spdx}) is incompatible with profile '{profile}'")
    if lic.attribution:
        if not allow_notices:
            return Verdict(False, spdx, f"{spdx} requires retained notices but notices are prohibited")
        obl.append("retain copyright + license notice (put in THIRD_PARTY_NOTICES)")
    if lic.copyleft != "none":
        obl.append(f"{lic.copyleft} copyleft: share source of derivative works as {spdx} requires")
    if lic.note:
        obl.append(lic.note)
    for dep, dl in sorted((dep_licenses or {}).items()):
        dv = check(dl, profile, allow_notices=allow_notices)
        if not dv.ok:
            return Verdict(False, spdx, f"dependency {dep}: {dv.reason}")
    return Verdict(True, spdx, "ok", obl)
