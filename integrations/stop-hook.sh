#!/usr/bin/env bash
# Optional Claude Code Stop hook: nudges (does not block) toward harvesting/lessons when a project has code changes.
# Add to .claude/settings.json: {"hooks":{"Stop":[{"hooks":[{"type":"command","command":"/path/to/stop-hook.sh"}]}]}}
set -u
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
n=$(git status --porcelain 2>/dev/null | grep -Ec '\.(py|go|rs|ts|js)$' || true)
[ "${n:-0}" -gt 0 ] && echo "AssetRAG: ${n} changed source files - consider harvest_code / save_lesson if the task is complete." >&2
exit 0
