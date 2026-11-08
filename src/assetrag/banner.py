"""The ASSETRAG wordmark (ANSI Shadow style) for setup and the installer. Shown only on a wide UTF-8 terminal, otherwise one plain line."""
from __future__ import annotations

import os
import shutil
import sys

_GLYPHS = {
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "G": [" ██████╗ ", "██╔════╝ ", "██║  ███╗", "██║   ██║", "╚██████╔╝", " ╚═════╝ "],
}
# one accent, cyan fading to blue down the letters; 256-colour codes
_ROW_COLOURS = (51, 45, 39, 33, 27, 21)
TAGLINE = "licensed assets · verified fixes · honest memory for Claude Code"


def art_lines(word: str = "ASSETRAG") -> list[str]:
    return ["".join(_GLYPHS[ch][r] for ch in word) for r in range(6)]


def _utf8() -> bool:
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


def render(color: bool | None = None, width: int | None = None) -> str:
    """Return the banner text for the current terminal: full art if it fits, else one line. No trailing newline."""
    tty = sys.stdout.isatty()
    if color is None:
        color = tty and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns
    lines = art_lines()
    if not _utf8() or width < len(lines[0]) + 4 or os.environ.get("TERM") == "dumb":
        return "ASSETRAG  " + TAGLINE if width >= 70 else "ASSETRAG"
    out = []
    for i, ln in enumerate(lines):
        out.append("  " + (f"\033[38;5;{_ROW_COLOURS[i]}m{ln}\033[0m" if color else ln))
    tag = "  " + TAGLINE
    out.append(f"\033[2m{tag}\033[0m" if color else tag)
    return "\n".join(out)


def show() -> None:
    if os.environ.get("ASSETRAG_BANNER_SHOWN"):          # the installer already drew it
        return
    print("\n" + render() + "\n")
