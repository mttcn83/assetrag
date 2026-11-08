"""Find third-party hosts that a web project would contact on every page view (fonts, script CDNs, analytics).

Why it matters: a page that loads `fonts.googleapis.com` sends each visitor's IP address to a third party. Some EU courts have treated that as
a data-protection violation (LG Muenchen I, 3 O 17493/20), and it is a plain privacy leak anyway. Self-hosting the files removes the request.
Pure text matching; it reads files and never touches the network.
"""
from __future__ import annotations

import re
from pathlib import Path

HOSTS = [  # (regex, kind, advice)
    (r"fonts\.googleapis\.com|fonts\.gstatic\.com", "font", "download the font files (check their licence, most Google Fonts are OFL) and serve them from your own origin"),
    (r"use\.typekit\.net|use\.fontawesome\.com|kit\.fontawesome\.com|fast\.fonts\.net", "font", "self-host the font or use a licensed local copy"),
    (r"cdnjs\.cloudflare\.com|cdn\.jsdelivr\.net|unpkg\.com|ajax\.googleapis\.com|cdn\.tailwindcss\.com|code\.jquery\.com", "cdn",
     "install the package locally and bundle it; the CDN sees every visitor IP and can change the file under you"),
    (r"googletagmanager\.com|google-analytics\.com|connect\.facebook\.net|static\.hotjar\.com|cdn\.segment\.com|plausible\.io/js|clarity\.ms", "tracker",
     "analytics/tracking: needs a consent flow in many jurisdictions; remove it unless you really want it"),
]
_COMPILED = [(re.compile(rx, re.I), kind, advice) for rx, kind, advice in HOSTS]
TEXT_EXT = {".html", ".htm", ".css", ".scss", ".sass", ".less", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".vue", ".svelte", ".astro", ".php", ".erb", ".njk", ".liquid"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "__pycache__"}
MAX_BYTES = 400_000


def external_refs(root: Path, limit: int = 50) -> list[dict]:
    """-> [{file, kind, host, advice}], first `limit`, one row per (file, host kind)."""
    out: list[dict] = []
    root = Path(root)
    files = [root] if root.is_file() else (p for p in sorted(root.rglob("*")) if p.is_file() and not (SKIP_DIRS & set(p.relative_to(root).parts)))
    for p in files:
        if p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for rx, kind, advice in _COMPILED:
            m = rx.search(text)
            if m:
                out.append({"file": str(p.relative_to(root)) if root.is_dir() else p.name, "kind": kind, "host": m.group(0).lower(), "advice": advice})
                if len(out) >= limit:
                    return out
    return out
