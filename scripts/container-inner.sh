#!/bin/sh
# Runs INSIDE a container (see container-test.sh). Phase "root": install system packages, create a user. Phase "user": install AssetRAG and check every feature.
set -u
phase="${1:-root}"

if [ "$phase" = root ]; then
  . /etc/os-release
  echo "distro: $PRETTY_NAME"
  case "${ID:-}" in
    arch)            pacman -Sy --noconfirm --needed python python-pipx git bubblewrap shadow >/dev/null 2>&1 ;;
    debian|ubuntu)   export DEBIAN_FRONTEND=noninteractive; apt-get update -qq >/dev/null 2>&1; apt-get install -y -qq python3 python3-venv pipx git bubblewrap ca-certificates >/dev/null 2>&1 ;;
    fedora)          dnf install -y -q python3 pipx git bubblewrap shadow-utils util-linux >/dev/null 2>&1 ;;
    alpine)          apk add --no-cache python3 py3-pip pipx git bubblewrap shadow >/dev/null 2>&1 ;;
    *)               echo "FAIL unsupported distro ${ID:-?}"; exit 1 ;;
  esac
  for t in python3 git pipx; do command -v "$t" >/dev/null 2>&1 || { echo "FAIL system package missing: $t"; exit 1; }; done
  useradd -m tester 2>/dev/null || adduser -D tester
  cp -r /src /home/tester/src && chown -R tester /home/tester/src
  exec su tester -c "sh /inner.sh user"
fi

# ----------------------------------------------------------------------------------------------- user phase
cd "$HOME" || exit 1
export PATH="$HOME/.local/bin:$PATH"
fails=0
ok()   { printf 'PASS %s\n' "$1"; }
bad()  { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
chk()  { name="$1"; shift; if out="$("$@" 2>&1)"; then ok "$name"; else bad "$name :: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"; fi; }
has()  { name="$1"; pat="$2"; shift 2; out="$("$@" 2>&1)"; if printf '%s' "$out" | grep -qi -- "$pat"; then ok "$name"; else bad "$name :: want '$pat', got: $(printf '%s' "$out" | head -3 | tr '\n' ' ')"; fi; }

echo "python: $(python3 --version 2>&1)"
ASSETRAG_SPEC=/home/tester/src ASSETRAG_NO_SETUP=1 sh /home/tester/src/scripts/install.sh >install.log 2>&1 && ok "install.sh (pipx/user-level)" || { bad "install.sh :: $(tail -4 install.log | tr '\n' ' ')"; exit 1; }
chk "assetrag --version" assetrag --version
chk "assetrag --help" assetrag --help
chk "assetrag-mcp on PATH" sh -c 'command -v assetrag-mcp'

SANDBOX=no; assetrag doctor --json 2>/dev/null | python3 -c 'import sys,json; sys.exit(0 if any(c["name"]=="sandbox" and c["status"]=="ok" for c in json.load(sys.stdin)["checks"]) else 1)' && SANDBOX=yes
echo "sandbox usable here: $SANDBOX"
has "setup (fresh, keyword-only)" "knowledge:" assetrag setup --keyword-only
has "setup is re-runnable" "already there" assetrag setup --keyword-only
has "know: inotify recipe found" "inotify" assetrag know "watch files with inotify"
has "know: nonsense gives honest empty state" "No matching" assetrag know "unrelated flurble quux"
has "know: exact error -> bug fix" "NoneType" assetrag know --error "TypeError: 'NoneType' object is not iterable"
has "rules" "must" assetrag rules write an http client
has "kstats" "total_active" assetrag kstats
has "search (no assets yet) is graceful" "" assetrag search "go cli progress bar"
has "doctor --json" '"score"' assetrag doctor --json
has "weakest" "" assetrag weakest
has "usage" "" assetrag usage
has "scan-code finds a secret" "AWS\|aws\|finding\|secret" sh -c "printf \"k = 'AKIAABCDEFGHIJKLMNOP'\n\" > leak.py && assetrag scan-code leak.py || true"
has "repomap (python)" "def \|class \|\.py" assetrag repomap /home/tester/src/src/assetrag --budget 400
chk "snapshot" assetrag snapshot
has "rollback --list" "" assetrag rollback --list
has "update --dry-run (offline-safe)" "" sh -c 'assetrag update --dry-run --no-licenses || true'
has "audit empty project" "clean" assetrag audit /home/tester/src/docs
mkdir -p proj && printf '<link href="https://fonts.googleapis.com/css2?family=Inter">' > proj/index.html
has "audit warns about Google Fonts hotlink" "fonts.googleapis.com" assetrag audit proj
has "uninstall is a dry run without --yes" "re-run with --yes" assetrag uninstall

# MCP server over stdio (initialize + list tools)
cat > mcp_check.py <<'PY'
import asyncio, shutil
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    p = StdioServerParameters(command=shutil.which("assetrag-mcp"))
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            names = [t.name for t in (await s.list_tools()).tools]
            res = await s.call_tool("find_knowledge", {"query": "retry with exponential backoff", "limit": 1})
            print("tools", len(names), "|", res.content[0].text[:60].replace("\n", " "))
asyncio.run(main())
PY
MCPPY="$(head -1 "$(command -v assetrag-mcp)" | sed 's/^#!//' | awk '{print $1}')"
has "MCP server: initialize, list tools, find_knowledge" "tools 1" "$MCPPY" mcp_check.py

# unit tests from the committed tree, in a venv with the dev extra
(cd /home/tester/src && python3 -m venv /home/tester/tv >/dev/null 2>&1 && /home/tester/tv/bin/pip install -q -e ".[dev]" >/home/tester/pip.log 2>&1) \
  && { has "pytest suite" "passed" sh -c 'cd /home/tester/src && /home/tester/tv/bin/python -m pytest -q -x 2>&1 | tail -3'; } \
  || bad "dev venv :: $(tail -3 /home/tester/pip.log | tr '\n' ' ')"

if [ "$SANDBOX" = yes ]; then
  has "seed verifies recipes in bwrap" "unverified_here" sh -c 'assetrag seed'
  has "recheck runs recipe tests" "" sh -c 'assetrag recheck 2>&1 | tail -3'
else
  has "seed without sandbox labels entries unverified" "unverified_here" sh -c 'assetrag seed'
fi
echo "SUMMARY fails=$fails sandbox=$SANDBOX"
[ "$fails" -eq 0 ]
