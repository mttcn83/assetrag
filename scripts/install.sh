#!/bin/sh
# AssetRAG installer. No sudo, no system changes: installs into your user account with pipx or uv, then runs `assetrag setup`.
#   curl -fsSL https://raw.githubusercontent.com/mttcn83/assetrag/main/scripts/install.sh | sh
# Options (env): ASSETRAG_REF=<tag|branch> (default main), ASSETRAG_SPEC=<pip spec> (e.g. a local path), ASSETRAG_NO_SETUP=1, ASSETRAG_REPOMAP=1,
#   ASSETRAG_SETUP_ARGS='--provider openrouter --embed-model google/gemini-embedding-2' (skip the questions), ASSETRAG_NO_PROMPT=1
set -eu

REPO="https://github.com/mttcn83/assetrag"
REF="${ASSETRAG_REF:-main}"
SPEC="${ASSETRAG_SPEC:-git+$REPO@$REF}"
[ "${ASSETRAG_REPOMAP:-0}" = 1 ] && SPEC="assetrag[repomap] @ $SPEC"

banner() {
  # wordmark only on a wide UTF-8 colour terminal; honours NO_COLOR; one plain line otherwise
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ] && [ "$(tput cols 2>/dev/null || echo 80)" -ge 72 ] && locale charmap 2>/dev/null | grep -qi utf; then
    printf '\n'
    printf '  \033[38;5;51m%s\033[0m\n' ' █████╗ ███████╗███████╗███████╗████████╗██████╗  █████╗  ██████╗ '
    printf '  \033[38;5;45m%s\033[0m\n' '██╔══██╗██╔════╝██╔════╝██╔════╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝ '
    printf '  \033[38;5;39m%s\033[0m\n' '███████║███████╗███████╗█████╗     ██║   ██████╔╝███████║██║  ███╗'
    printf '  \033[38;5;33m%s\033[0m\n' '██╔══██║╚════██║╚════██║██╔══╝     ██║   ██╔══██╗██╔══██║██║   ██║'
    printf '  \033[38;5;27m%s\033[0m\n' '██║  ██║███████║███████║███████╗   ██║   ██║  ██║██║  ██║╚██████╔╝'
    printf '  \033[38;5;21m%s\033[0m\n' '╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝ '
    printf '  \033[2m%s\033[0m\n\n' 'licensed assets · verified fixes · honest memory for Claude Code'
    export ASSETRAG_BANNER_SHOWN=1
  else
    printf '\nASSETRAG installer\n\n'
  fi
}

say() { printf '  %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "AssetRAG supports Linux only (found $(uname -s))."
command -v git >/dev/null 2>&1 || die "git is required. Install it with your package manager (pacman -S git / apt install git / dnf install git / apk add git)."

PY=""
for p in python3.13 python3.12 python3.11 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then PY="$(command -v "$p")"; break; fi
done

banner
say "Installing AssetRAG (user-level, no sudo). Source: $SPEC"
if command -v pipx >/dev/null 2>&1 && [ -n "$PY" ]; then
  pipx install --force --python "$PY" "$SPEC"
elif command -v uv >/dev/null 2>&1; then
  uv tool install --force --python ">=3.11" "$SPEC"
elif [ -n "$PY" ]; then
  say "pipx/uv not found; using a private venv in ~/.local/share/assetrag-venv"
  "$PY" -m venv "${XDG_DATA_HOME:-$HOME/.local/share}/assetrag-venv" || die "python venv module missing (Debian/Ubuntu: apt install python3-venv)."
  "${XDG_DATA_HOME:-$HOME/.local/share}/assetrag-venv/bin/pip" install --quiet "$SPEC"
  mkdir -p "$HOME/.local/bin"
  for b in assetrag assetrag-mcp; do ln -sf "${XDG_DATA_HOME:-$HOME/.local/share}/assetrag-venv/bin/$b" "$HOME/.local/bin/$b"; done
else
  die "need Python 3.11+ (and ideally pipx or uv). Arch: pacman -S python-pipx; Debian/Ubuntu: apt install pipx; Fedora: dnf install pipx; Alpine: apk add pipx."
fi

export PATH="$HOME/.local/bin:$PATH"
command -v assetrag >/dev/null 2>&1 || die "installed, but 'assetrag' is not on PATH. Add \$HOME/.local/bin to PATH and re-run 'assetrag setup'."
assetrag --version

# `curl ... | sh` has no stdin, so give setup the real terminal: it asks which embedding provider, API key and model to use.
# No terminal (CI, containers)? It silently picks a running local server that has EmbeddingGemma, else keyword-only.
if [ "${ASSETRAG_NO_SETUP:-0}" = 1 ]; then say "skipping setup; run 'assetrag setup' when ready."
elif [ -t 0 ]; then assetrag setup ${ASSETRAG_SETUP_ARGS:-}
elif [ -z "${ASSETRAG_NO_PROMPT:-}" ] && { : </dev/tty; } 2>/dev/null; then assetrag setup ${ASSETRAG_SETUP_ARGS:-} </dev/tty
else assetrag setup ${ASSETRAG_SETUP_ARGS:-}; fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "note: add \$HOME/.local/bin to your PATH.";; esac
say "uninstall: assetrag uninstall --yes && pipx uninstall assetrag   (or: uv tool uninstall assetrag)"
