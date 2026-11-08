"""Round-2 recipes, part A: shell scripting for sysadmins/power users (bash, tested with real bash, coreutils, flock, rsync and
jq inside the sandbox). Every recipe has a lib.sh to source and a test.sh that exits non-zero on the first failed assertion."""
from .recipes import RC

BT = ["bash", "test.sh"]
BASH = ["bash 4.4+ (uses arrays, [[ ]], mapfile/printf -v)", "coreutils/find/flock/rsync/jq from the sandbox image; GNU userland assumed (Linux)"]

HELPERS = r'''
pass=0
ok()   { pass=$((pass+1)); }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
eq()   { [[ "$1" == "$2" ]] && ok || fail "expected [$2] got [$1] ${3:-}"; }
true_(){ "$@" && ok || fail "expected success: $*"; }
false_(){ if "$@"; then fail "expected failure: $*"; else ok; fi; }
done_() { printf 'OK %d assertions\n' "$pass"; }
'''

R2A = [
RC("sh-strict-mode-template", "Bash script skeleton: strict mode, ERR trap with line number, die(), dependency checks", "bash",
   "Shell scripts that keep running after a failure, hide errors in pipelines or expand unset variables into `rm -rf /`.",
   "`set -Eeuo pipefail`, safe IFS, an ERR trap that reports file:line and the failing command, die() helper with exit codes, `require cmd...` dependency check with a helpful message, readonly script dir resolved with `${BASH_SOURCE[0]}`, main() pattern so functions are testable by sourcing.",
   {"lib.sh": r'''#!/usr/bin/env bash
# Skeleton: source-able (functions only) and runnable (main at the bottom).
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
readonly SCRIPT_DIR

die() { local code=$1; shift; printf 'error: %s\n' "$*" >&2; exit "$code"; }

on_err() {
  local code=$? line=$1 cmd=$2
  printf 'error: command failed (exit %d) at line %s: %s\n' "$code" "$line" "$cmd" >&2
}
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR

require() {
  local missing=() c
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  ((${#missing[@]} == 0)) || { local IFS=' '; die 127 "missing required command(s): ${missing[*]}"; }   # "${a[*]}" joins with the FIRST char of IFS (a newline here!)
}

main() {
  require printf cat
  local name=${1:?usage: script NAME}      # unset/empty argument is an error, not an empty string
  printf 'hello %s\n' "$name"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
''', "test.sh": HELPERS + r'''
cd "$(dirname "$0")"
out=$(bash lib.sh world 2>&1); eq "$out" "hello world"
out=$(bash lib.sh 2>&1) && fail "missing arg must fail" || true
[[ "$out" == *"usage: script NAME"* ]] && ok || fail "usage message missing: $out"

# pipefail: a failure at the start of a pipeline is not hidden
set +e; out=$(bash -c 'source ./lib.sh; false | cat; echo unreachable' 2>&1); rc=$?; set -e
[[ $rc -ne 0 && "$out" != *unreachable* ]] && ok || fail "pipefail not effective: rc=$rc out=$out"
# unset variables are errors
set +e; bash -c 'source ./lib.sh; echo "$undefined_var"' >/dev/null 2>&1; rc=$?; set -e
[[ $rc -ne 0 ]] && ok || fail "nounset not effective"
# ERR trap names the line and command
out=$(bash -c 'source ./lib.sh
ls /definitely/not/here' 2>&1 || true)
[[ "$out" == *"command failed"* && "$out" == *"ls /definitely/not/here"* ]] && ok || fail "ERR trap output: $out"
# require() reports every missing command, exit 127
set +e; out=$(bash -c 'source ./lib.sh; require nosuchcmd1 ls nosuchcmd2' 2>&1); rc=$?; set -e
eq "$rc" 127; [[ "$out" == *"nosuchcmd1 nosuchcmd2"* ]] && ok || fail "require output: $out"
# sourcing does not run main
out=$(bash -c 'source ./lib.sh; echo sourced'); eq "$out" sourced
done_
'''}, BT, assumptions=BASH, compat={"bash": ">=4.4"},
   tags=["bash", "shell", "script", "strict-mode", "pipefail", "trap", "template", "set-euo-pipefail", "sysadmin"],
   avoid="Bare `set -e` alone (misses pipelines and unset vars); scripts that cannot be sourced for testing; `cd` without checking."),

RC("sh-getopts-args", "Bash argument parsing: short options with getopts, long options, `--`, validation and usage", "bash",
   "A shell tool needs flags like `-n 5 -v --dry-run FILE...` with proper errors instead of positional-argument guessing.",
   "getopts for short options (with `:` handling for missing values), a small pre-pass that converts long options to short ones, `--` and remaining positionals via `shift $((OPTIND-1))`, integer validation, usage() to stdout for --help (exit 0) and stderr for errors (exit 2).",
   {"lib.sh": r'''usage() { cat <<'EOF'
Usage: tool [-n COUNT] [-v] [-d] [--] FILE...
  -n COUNT   how many (default 1)
  -v         verbose (repeatable)
  -d         dry run
  -h         this help
Long forms: --count N, --verbose, --dry-run, --help
EOF
}

parse_args() {
  COUNT=1 VERBOSE=0 DRY=0 FILES=()
  local args=() a
  for a in "$@"; do                         # translate long options, but stop at "--"
    if [[ ${seen_dd:-0} == 1 ]]; then args+=("$a"); continue; fi
    case $a in
      --) seen_dd=1; args+=("$a") ;;
      --count=*) args+=(-n "${a#*=}") ;;
      --count) args+=(-n) ;;
      --verbose) args+=(-v) ;;
      --dry-run) args+=(-d) ;;
      --help) args+=(-h) ;;
      --*) printf 'tool: unknown option %s\n' "$a" >&2; usage >&2; return 2 ;;
      *) args+=("$a") ;;
    esac
  done
  unset seen_dd
  local OPTIND=1 opt
  while getopts ':n:vdh' opt "${args[@]}"; do
    case $opt in
      n) [[ $OPTARG =~ ^[0-9]+$ ]] || { printf 'tool: -n needs a number, got "%s"\n' "$OPTARG" >&2; return 2; }; COUNT=$OPTARG ;;
      v) VERBOSE=$((VERBOSE+1)) ;;
      d) DRY=1 ;;
      h) usage; return 100 ;;                # 100 = "help shown, caller should exit 0"
      :) printf 'tool: option -%s needs a value\n' "$OPTARG" >&2; return 2 ;;
      \?) printf 'tool: unknown option -%s\n' "$OPTARG" >&2; usage >&2; return 2 ;;
    esac
  done
  shift $((OPTIND-1)) 2>/dev/null || true
  set -- "${args[@]:$((OPTIND-1))}"
  FILES=("$@")
}
''', "test.sh": HELPERS + r'''
source "$(dirname "$0")/lib.sh"
parse_args -n 5 -vv a.txt "b c.txt"; eq "$COUNT" 5; eq "$VERBOSE" 2; eq "${#FILES[@]}" 2; eq "${FILES[1]}" "b c.txt"
parse_args --count=7 --dry-run x; eq "$COUNT" 7; eq "$DRY" 1; eq "${FILES[0]}" x
parse_args --count 9 y; eq "$COUNT" 9; eq "${FILES[0]}" y
parse_args -- -v --count; eq "$VERBOSE" 0; eq "${FILES[0]}" -v; eq "${FILES[1]}" --count
parse_args; eq "$COUNT" 1; eq "${#FILES[@]}" 0
set +e
parse_args -n abc 2>/dev/null; eq $? 2
parse_args -n 2>/dev/null; eq $? 2
parse_args -z 2>/dev/null; eq $? 2
parse_args --bogus 2>/dev/null; eq $? 2
out=$(parse_args -h); rc=$?; eq "$rc" 100; [[ "$out" == Usage:* ]] && ok || fail "help text"
set -e
parse_args -v -- -d; eq "$DRY" 0; eq "${FILES[0]}" -d
done_
'''}, BT, assumptions=BASH + ["GNU getopt(1) is deliberately not used: it is not portable to macOS/busybox"], compat={"bash": ">=4.4"},
   tags=["bash", "shell", "getopts", "arguments", "cli", "flags", "usage", "parsing", "sysadmin"],
   avoid="Positional `$1 $2` parsing for options; eval-ing user arguments; printing usage errors to stdout."),

RC("sh-flock-single-instance", "Prevent concurrent runs of a script with flock (cron-safe, auto-released)", "bash",
   "A cron job or timer overlaps with its previous run and corrupts data or doubles work.",
   "Take an exclusive non-blocking `flock` on a lock file via a file descriptor opened for the script's lifetime; the kernel releases it when the process dies (no stale PID files), optionally wait with a timeout, and report who holds it. Lock file lives in $XDG_RUNTIME_DIR or /tmp. Children must not inherit the lock fd (`cmd {LOCK_FD}>&-`), otherwise an orphaned child keeps the lock after the parent dies.",
   {"lib.sh": r'''# usage: acquire_lock NAME [WAIT_SECONDS]
# Holds the lock until the shell exits. Returns 0 if acquired, 1 if another instance is running.
acquire_lock() {
  local name=$1 wait=${2:-0}
  local dir=${XDG_RUNTIME_DIR:-/tmp}
  LOCK_FILE="$dir/$name.lock"
  exec {LOCK_FD}>>"$LOCK_FILE" || return 2            # append mode: opening must not truncate the holder's PID; fd stays open for the process lifetime
  if (( wait > 0 )); then flock -w "$wait" "$LOCK_FD"; else flock -n "$LOCK_FD"; fi || { exec {LOCK_FD}>&-; return 1; }
  printf '%d\n' "$$" >"$LOCK_FILE"                    # informational only; the flock is the truth
}
''', "test.sh": HELPERS + r'''
export XDG_RUNTIME_DIR=$(mktemp -d); trap 'rm -rf "$XDG_RUNTIME_DIR"' EXIT
lib="$(cd "$(dirname "$0")" && pwd)/lib.sh"

# 1. first instance holds the lock; a second one is refused while the first is alive
bash -c "source '$lib'; acquire_lock job && sleep 2 {LOCK_FD}>&-" & holder=$!      # child must not inherit the lock fd
for _ in $(seq 50); do [[ -s "$XDG_RUNTIME_DIR/job.lock" ]] && break; sleep 0.05; done
set +e; bash -c "source '$lib'; acquire_lock job"; rc=$?; set -e
eq "$rc" 1 "second instance must be refused"
eq "$(cat "$XDG_RUNTIME_DIR/job.lock")" "$holder" "lock file names the holder"

# 2. waiting variant times out while held
set +e; bash -c "source '$lib'; acquire_lock job 1"; rc=$?; set -e; eq "$rc" 1

# 3. killing the holder releases the lock immediately (no stale lock file problem)
kill -9 "$holder"; wait "$holder" 2>/dev/null || true
bash -c "source '$lib'; acquire_lock job" && ok || fail "lock should be free after the holder died"

# 4. different names do not interfere
bash -c "source '$lib'; acquire_lock a && bash -c \"source '$lib'; acquire_lock b\"" && ok || fail "independent locks"
done_
'''}, BT, assumptions=BASH + ["Local filesystems only: flock on NFS is unreliable"], compat={"util-linux": "flock"},
   tags=["bash", "shell", "flock", "lock", "cron", "systemd-timer", "concurrency", "single-instance", "sysadmin", "backup"],
   avoid="PID files and `[ -e lockfile ]` checks (race + stale locks after a crash); letting long-running children inherit the lock descriptor; opening the lock file with `>` in the second instance (truncates the holder's PID)."),

RC("sh-trap-tmp-cleanup", "Temporary files and directories that are always cleaned up (trap EXIT/INT/TERM)", "bash",
   "Scripts leave temp files behind on errors or Ctrl-C, or delete the wrong directory when a variable is empty.",
   "mktemp -d under a per-run directory, one cleanup function registered for EXIT (and INT/TERM converted into a normal exit with the right status), guard so it only removes the directory it created, stack of extra cleanup commands, and no `rm -rf \"$VAR/\"` with possibly-empty VAR.",
   {"lib.sh": r'''CLEANUPS=()
TMP_ROOT=
__OWN_ROOT=      # private copy: a buggy script reassigning TMP_ROOT can never make cleanup delete something else

cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  local i
  for ((i=${#CLEANUPS[@]}-1; i>=0; i--)); do eval "${CLEANUPS[i]}" || true; done   # reverse order, commands registered by this script only
  # only ever remove the directory WE created (remembered privately), never whatever TMP_ROOT points at now
  if [[ -n $__OWN_ROOT && -d $__OWN_ROOT ]]; then rm -rf -- "$__OWN_ROOT"; fi
  exit "$rc"
}

init_tmp() {
  TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/tmp.XXXXXXXX") || return 1
  __OWN_ROOT=$TMP_ROOT
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

on_cleanup() { CLEANUPS+=("$1"); }
tmpfile() { mktemp "$TMP_ROOT/f.XXXXXX"; }
''', "test.sh": HELPERS + r'''
lib="$(cd "$(dirname "$0")" && pwd)/lib.sh"
base=$(mktemp -d); export TMPDIR=$base; trap 'rm -rf "$base"' EXIT
count() { find "$base" -mindepth 1 -maxdepth 1 -name 'tmp.*' | wc -l; }

bash -c "source '$lib'; init_tmp; echo data >\"\$(tmpfile)\"; true"; eq "$(count)" 0 "normal exit cleans up"
set +e; bash -c "source '$lib'; init_tmp; tmpfile >/dev/null; exit 7"; rc=$?; set -e; eq "$rc" 7; eq "$(count)" 0 "exit status preserved and dir removed"
set +e; bash -c "source '$lib'; set -e; init_tmp; tmpfile >/dev/null; false"; rc=$?; set -e; eq "$rc" 1; eq "$(count)" 0 "set -e failure cleans up"

# interrupted run: signal arrives while the script is working
bash -c "source '$lib'; init_tmp; tmpfile >/dev/null; echo ready >'$base/ready'; sleep 30" & pid=$!
for _ in $(seq 100); do [[ -e "$base/ready" ]] && break; sleep 0.05; done
[[ $(count) == 1 ]] && ok || fail "temp dir should exist while running"
kill -TERM "$pid"; set +e; wait "$pid"; rc=$?; set -e
eq "$(count)" 0 "SIGTERM cleans up"; ok

# extra cleanup commands run in reverse order
out=$(bash -c "source '$lib'; init_tmp; on_cleanup 'echo first'; on_cleanup 'echo second'; true")
eq "$out" $'second\nfirst'
# a poisoned TMP_ROOT is never deleted
mkdir "$base/precious"; touch "$base/precious/keep"
bash -c "source '$lib'; init_tmp; TMP_ROOT='$base/precious'; true"; [[ -e "$base/precious/keep" ]] && ok || fail "cleanup removed a directory it did not create"
bash -c "source '$lib'; init_tmp; TMP_ROOT=''; true"; [[ -d "$base" ]] && ok
done_
'''}, BT, assumptions=BASH + ["`kill -9` cannot be trapped: stale temp dirs are still possible; use systemd PrivateTmp for hard guarantees"], compat={"bash": ">=4.4"},
   tags=["bash", "shell", "trap", "mktemp", "cleanup", "temp-files", "signals", "sigint", "sysadmin"],
   avoid="Fixed /tmp/myscript.tmp names; cleanup only at the end of the script; `rm -rf $DIR/*` with an unchecked variable."),

RC("sh-find-delete-old-safely", "Delete old files safely: dry run by default, null-delimited, guarded paths, count and size report", "bash",
   "Housekeeping scripts (logs, caches, old backups) that must never delete the wrong thing or choke on spaces and newlines in names.",
   "`find -print0` with `-mtime`/`-type f`, dry-run unless `--delete` is explicit, refuse dangerous roots (/, $HOME, empty, relative `..`), mount boundary with `-xdev`, never follow symlinks, summary of how many files and bytes, exits non-zero if anything failed to delete.",
   {"lib.sh": r'''# usage: purge_old DIR DAYS [--delete]
purge_old() {
  local dir=$1 days=$2 mode=${3:-}
  [[ -d $dir ]] || { echo "purge: $dir is not a directory" >&2; return 2; }
  dir=$(cd -- "$dir" && pwd -P)
  case $dir in
    /|/home|/usr|/etc|/var|/boot|/root|"$HOME") echo "purge: refusing to work on $dir" >&2; return 2 ;;
  esac
  [[ $days =~ ^[0-9]+$ ]] || { echo "purge: DAYS must be a number" >&2; return 2; }

  local n=0 bytes=0 failed=0 f sz
  while IFS= read -r -d '' f; do
    sz=$(stat -c %s -- "$f" 2>/dev/null || echo 0)
    if [[ $mode == --delete ]]; then
      if rm -f -- "$f"; then n=$((n+1)); bytes=$((bytes+sz)); else failed=$((failed+1)); fi
    else
      printf 'would delete: %s\n' "$f"; n=$((n+1)); bytes=$((bytes+sz))
    fi
  done < <(find "$dir" -xdev -type f -mtime +"$days" -print0)      # -type f: symlinks and dirs are never removed

  local verb="would delete"; [[ $mode == --delete ]] && verb=deleted
  printf '%s %d file(s), %d byte(s)\n' "$verb" "$n" "$bytes"
  ((failed == 0))
}
''', "test.sh": HELPERS + r'''
source "$(dirname "$0")/lib.sh"
d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
mkdir -p "$d/sub dir"
printf 'aaaa' >"$d/old.log"; printf 'bb' >"$d/sub dir/old two.log"; printf 'c' >"$d/new.log"
printf 'x' >"$d/line"$'\n'"break.log"
touch -d '10 days ago' "$d/old.log" "$d/sub dir/old two.log" "$d/line"$'\n'"break.log"
echo target >"$d/target.txt"; ln -s "$d/target.txt" "$d/link.log"; touch -h -d '10 days ago' "$d/link.log"

out=$(purge_old "$d" 5); [[ "$out" == *"would delete 3 file(s), 7 byte(s)"* ]] && ok || fail "dry run summary: $out"
[[ -e "$d/old.log" ]] && ok || fail "dry run deleted something"
out=$(purge_old "$d" 5 --delete); [[ "$out" == *"deleted 3 file(s), 7 byte(s)"* ]] && ok || fail "delete summary: $out"
[[ ! -e "$d/old.log" && ! -e "$d/sub dir/old two.log" && -e "$d/new.log" ]] && ok || fail "wrong files removed"
[[ -L "$d/link.log" && -e "$d/target.txt" ]] && ok || fail "symlink or its target was touched"

set +e
purge_old / 1 --delete 2>/dev/null; eq $? 2
purge_old "$HOME" 1 --delete 2>/dev/null; eq $? 2
purge_old "" 1 2>/dev/null; eq $? 2
purge_old "$d" "1; rm -rf /" 2>/dev/null; eq $? 2
purge_old /no/such/dir 1 2>/dev/null; eq $? 2
set -e
done_
'''}, BT, assumptions=BASH + ["GNU find/stat flags (-print0, -mtime, stat -c)"], compat={"findutils": "GNU"},
   tags=["bash", "shell", "find", "delete", "cleanup", "logs", "cron", "retention", "sysadmin", "safety"],
   avoid="`find ... -exec rm {} \\;` without a dry run; `rm -rf $DIR/*` with an unchecked variable; parsing `ls` output."),

RC("sh-rsync-snapshot-backup", "rsync snapshot backups with hardlinks (--link-dest) and retention pruning", "bash",
   "Incremental, space-efficient, browsable backups without a backup suite: each run looks like a full copy but only changed files take space.",
   "rsync -a --delete into a dated directory using the previous snapshot as --link-dest, write to `.partial` then rename so a snapshot is atomic, a `latest` symlink, keep the newest N and prune the rest, exclude file support, and refuse to run when the source is empty or missing (so a failed mount cannot wipe the history).",
   {"lib.sh": r'''# usage: snapshot SRC DEST [KEEP]
snapshot() {
  local src=${1%/} dest=$2 keep=${3:-7}
  [[ -d $src ]] || { echo "backup: source $src missing" >&2; return 2; }
  [[ -n $(ls -A -- "$src" 2>/dev/null) ]] || { echo "backup: source $src is empty (unmounted?); refusing" >&2; return 2; }
  mkdir -p -- "$dest"
  local name; name=$(date +%Y-%m-%dT%H%M%S)${SNAP_SUFFIX:-}
  local prev; prev=$(readlink -f -- "$dest/latest" 2>/dev/null || true)
  local opts=(-a --delete --numeric-ids)
  [[ -n $prev && -d $prev ]] && opts+=(--link-dest="$prev")
  rm -rf -- "$dest/.partial"
  rsync "${opts[@]}" -- "$src/" "$dest/.partial/" || { echo "backup: rsync failed; previous snapshots untouched" >&2; return 1; }
  mv -- "$dest/.partial" "$dest/$name"                 # atomic: a snapshot either exists completely or not at all
  ln -sfn -- "$name" "$dest/latest"
  prune "$dest" "$keep"
}

prune() {
  local dest=$1 keep=$2 s i=0
  while IFS= read -r s; do
    i=$((i+1)); ((i > keep)) && rm -rf -- "$dest/$s"
  done < <(ls -1 -- "$dest" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' | sort -r)      # only our own snapshot names are ever pruned
}
''', "test.sh": HELPERS + r'''
source "$(dirname "$0")/lib.sh"
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT; src=$t/src; dst=$t/dst; mkdir -p "$src"
echo one >"$src/a"; head -c 20000 /dev/zero >"$src/big"
SNAP_SUFFIX=-1 snapshot "$src" "$dst" 3
s1=$(readlink "$dst/latest"); [[ -f "$dst/$s1/a" ]] && ok || fail "first snapshot content"
echo two >"$src/b"; SNAP_SUFFIX=-2 snapshot "$src" "$dst" 3
s2=$(readlink "$dst/latest"); [[ "$s1" != "$s2" && -f "$dst/$s2/b" && ! -e "$dst/$s1/b" ]] && ok || fail "second snapshot"
# unchanged file is a hardlink: same inode, no extra space
eq "$(stat -c %i "$dst/$s1/big")" "$(stat -c %i "$dst/$s2/big")" "unchanged files share an inode"
# changed file breaks the link and old snapshots keep old content
echo changed >"$src/a"; SNAP_SUFFIX=-3 snapshot "$src" "$dst" 3
eq "$(cat "$dst/$s1/a")" one; eq "$(cat "$dst/$(readlink "$dst/latest")/a")" changed
# retention keeps only the newest 3
SNAP_SUFFIX=-4 snapshot "$src" "$dst" 3; eq "$(ls -1 "$dst" | grep -c '^20')" 3; [[ ! -e "$dst/$s1" ]] && ok || fail "oldest not pruned"
# deletions propagate to the newest snapshot only
rm "$src/b"; SNAP_SUFFIX=-5 snapshot "$src" "$dst" 3; [[ ! -e "$dst/latest/b" ]] && ok || fail "deleted file still in latest"
# an empty / missing source never touches the history
before=$(ls -1 "$dst" | wc -l); empty=$t/empty; mkdir "$empty"
set +e; snapshot "$empty" "$dst" 3 2>/dev/null; eq $? 2; snapshot "$t/nope" "$dst" 3 2>/dev/null; eq $? 2; set -e
eq "$(ls -1 "$dst" | wc -l)" "$before"
done_
'''}, BT, assumptions=BASH + ["Local destination; add `-e ssh` and a remote DEST for off-site copies", "Snapshots on the same disk are not a backup of the disk: copy them elsewhere too"], compat={"rsync": ">=3.1"},
   tags=["bash", "rsync", "backup", "snapshot", "hardlink", "retention", "sysadmin", "cron", "link-dest", "restic"],
   avoid="`rsync --delete` straight onto the only copy; backing up an unmounted (empty) source over good data; non-atomic snapshot directories."),

RC("sh-color-log-functions", "Shell logging helpers: levels, colors only on a TTY, NO_COLOR, stderr, timestamps", "bash",
   "Scripts print everything to stdout, with raw ANSI codes in log files and no way to silence them.",
   "log_debug/info/warn/error functions writing to stderr, colors enabled only when stderr is a TTY and NO_COLOR is unset (and TERM isn't dumb), LOG_LEVEL filtering, optional ISO timestamps, `die` helper, and output that is clean plain text when redirected.",
   {"lib.sh": r'''LOG_LEVEL=${LOG_LEVEL:-info}

_use_color() { [[ -t 2 && -z ${NO_COLOR:-} && ${TERM:-dumb} != dumb ]]; }

_level_num() { case $1 in debug) echo 10;; info) echo 20;; warn) echo 30;; error) echo 40;; *) echo 20;; esac; }

_log() {
  local level=$1; shift
  (( $(_level_num "$level") >= $(_level_num "$LOG_LEVEL") )) || return 0
  local tag color reset=''
  case $level in debug) tag=DEBUG color='\033[2m';; info) tag=INFO color='\033[32m';; warn) tag=WARN color='\033[33m';; error) tag=ERROR color='\033[31m';; esac
  _use_color && reset='\033[0m' || color=''
  local ts=''; [[ -n ${LOG_TIMESTAMP:-} ]] && ts="$(date -u +%Y-%m-%dT%H:%M:%SZ) "
  printf "${ts}${color}%-5s${reset} %s\n" "$tag" "$*" >&2
}

log_debug() { _log debug "$@"; }
log_info()  { _log info "$@"; }
log_warn()  { _log warn "$@"; }
log_error() { _log error "$@"; }
die()       { log_error "$@"; exit 1; }
''', "test.sh": HELPERS + r'''
lib="$(cd "$(dirname "$0")" && pwd)/lib.sh"
run() { bash -c "source '$lib'; $1" 2>&1 >/dev/null; }     # capture stderr only

out=$(run 'log_info hello'); eq "$out" "INFO  hello"
[[ -z $(run 'log_debug hidden') ]] && ok || fail "debug should be filtered by default"
out=$(LOG_LEVEL=debug run 'log_debug shown'); eq "$out" "DEBUG shown"
out=$(LOG_LEVEL=error run 'log_warn nope; log_error yes'); eq "$out" "ERROR yes"
# nothing on stdout: logs never pollute data pipelines
eq "$(bash -c "source '$lib'; log_info x; echo data" 2>/dev/null)" data
# redirected stderr = plain text (no escape codes)
out=$(run 'log_error boom'); [[ $out != *$'\033'* ]] && ok || fail "ANSI codes leaked into a non-tty"
# on a pseudo-terminal colors appear, unless NO_COLOR is set
if command -v script >/dev/null; then
  export TERM=xterm; c=$(script -qec "bash -c \"source '$lib'; log_error boom\"" /dev/null | cat -v); [[ $c == *"^[[31m"* ]] && ok || fail "expected color on tty: $c"
  n=$(NO_COLOR=1 script -qec "bash -c \"source '$lib'; log_error boom\"" /dev/null | cat -v); [[ $n != *"^[["* ]] && ok || fail "NO_COLOR ignored: $n"
else ok; ok; fi
out=$(LOG_TIMESTAMP=1 run 'log_info t'); [[ $out =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z\ INFO ]] && ok || fail "timestamp: $out"
set +e; bash -c "source '$lib'; die fatal" 2>/dev/null; eq $? 1; set -e
done_
'''}, BT, assumptions=BASH + ["The pseudo-tty check is skipped when `script` is unavailable"], compat={"bash": ">=4.4"},
   tags=["bash", "shell", "logging", "color", "ansi", "no_color", "stderr", "tty", "sysadmin", "script"],
   avoid="echo with raw escape codes; logging to stdout; colors when output is piped."),

RC("sh-retry-backoff", "Retry a flaky command with exponential backoff, jitter and a total timeout (bash)", "bash",
   "curl, git fetch, package installs or API calls fail transiently in scripts and CI; a bare loop either gives up too soon or hammers the server.",
   "`retry` function: attempts, base delay doubling to a cap with jitter, only retries on chosen exit codes (not on usage errors), per-attempt `timeout`, returns the last exit status, sleep injectable for tests via $RETRY_SLEEP, messages on stderr.",
   {"lib.sh": r'''# usage: retry [-n ATTEMPTS] [-d BASE_DELAY] [-m MAX_DELAY] [-t PER_ATTEMPT_TIMEOUT] [-x "CODES NOT TO RETRY"] -- cmd args...
retry() {
  local attempts=5 delay=1 max=30 tmo=0 noretry="2 126 127" opt
  local OPTIND=1
  while getopts 'n:d:m:t:x:' opt; do
    case $opt in n) attempts=$OPTARG;; d) delay=$OPTARG;; m) max=$OPTARG;; t) tmo=$OPTARG;; x) noretry=$OPTARG;; *) return 2;; esac
  done
  shift $((OPTIND-1)); [[ ${1:-} == -- ]] && shift
  (( $# > 0 )) || { echo "retry: no command" >&2; return 2; }
  local n=1 rc=0 sleep_cmd=${RETRY_SLEEP:-sleep} wait
  while :; do
    if (( tmo > 0 )); then timeout "$tmo" "$@"; else "$@"; fi && return 0
    rc=$?
    [[ " $noretry " == *" $rc "* ]] && { echo "retry: exit $rc is not retryable" >&2; return "$rc"; }
    (( n >= attempts )) && { echo "retry: giving up after $n attempt(s), last exit $rc" >&2; return "$rc"; }
    wait=$(( delay * (1 << (n-1)) )); (( wait > max )) && wait=$max
    wait=$(( wait / 2 + RANDOM % (wait / 2 + 1) ))                # jitter in [wait/2, wait]
    echo "retry: attempt $n failed (exit $rc); retrying in ${wait}s" >&2
    "$sleep_cmd" "$wait"; n=$((n+1))
  done
}
''', "test.sh": HELPERS + r'''
source "$(dirname "$0")/lib.sh"
export RETRY_SLEEP=true      # `true` ignores its argument: no real sleeping in tests
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT; echo 0 >"$tmp/n"
flaky() { local n; n=$(<"$tmp/n"); echo $((n+1)) >"$tmp/n"; (( n + 1 >= $1 )); }   # succeeds on the Nth call

echo 0 >"$tmp/n"; retry -n 5 -- flaky 3 2>/dev/null && ok || fail "should succeed on 3rd try"; eq "$(<"$tmp/n")" 3
echo 0 >"$tmp/n"; set +e; retry -n 3 -- flaky 10 2>/dev/null; rc=$?; set -e; eq "$rc" 1; eq "$(<"$tmp/n")" 3 "stops after N attempts"
# usage errors / command not found are not retried
echo 0 >"$tmp/n"; set +e; retry -n 5 -- bash -c 'echo x >>"'"$tmp"'/c"; exit 2' 2>/dev/null; rc=$?; set -e; eq "$rc" 2; eq "$(wc -l <"$tmp/c")" 1
set +e; retry -n 5 -- nosuchcommand_zz 2>/dev/null; eq $? 127; set -e
# last exit code is propagated
set +e; retry -n 2 -- bash -c 'exit 42' 2>/dev/null; eq $? 42; set -e
# backoff delays double (captured via a recording sleep) and respect the cap
sleeps=(); rec() { sleeps+=("$1"); }; RETRY_SLEEP=rec
set +e; retry -n 6 -d 2 -m 10 -- false 2>/dev/null; set -e
(( ${#sleeps[@]} == 5 )) && ok || fail "expected 5 sleeps got ${#sleeps[@]}"
(( sleeps[0] <= 2 && sleeps[1] <= 4 && sleeps[2] <= 8 && sleeps[3] <= 10 && sleeps[4] <= 10 )) && ok || fail "delays exceed schedule: ${sleeps[*]}"
(( sleeps[3] >= 5 )) && ok || fail "jitter should keep at least half of the delay: ${sleeps[*]}"
# per-attempt timeout kills hung commands
RETRY_SLEEP=true; set +e; retry -n 2 -t 1 -- sleep 5 2>/dev/null; rc=$?; set -e; eq "$rc" 124
done_
'''}, BT, assumptions=BASH, compat={"bash": ">=4.4"},
   tags=["bash", "shell", "retry", "backoff", "jitter", "curl", "ci", "flaky", "timeout", "sysadmin"],
   avoid="`until cmd; do sleep 1; done` (infinite, no backoff); retrying on every exit code including usage errors."),

RC("sh-jq-json-recipes", "jq patterns for shell scripts: safe extraction, filtering, grouping, injection-safe variables", "bash",
   "Scripts that parse JSON with grep/sed/awk, or build JSON by string concatenation and get quoting and injection bugs.",
   "jq -e for exit-status checks, `// empty` defaults, --arg/--argjson instead of interpolating shell variables, -r with @tsv/@csv/@sh for safe output, group_by/map/select for reports, building JSON with --null-input, reading NDJSON with -c, and handling missing keys without crashing.",
   {"lib.sh": r'''# Each function reads JSON on stdin (or builds it) and demonstrates one jq idiom that is safe to put in scripts.

names_of_active()   { jq -r '.users[] | select(.active) | .name'; }
get_or_default()    { jq -r --arg d "$1" '.config.region // $d'; }                 # missing key -> default, no "null" string
has_key()           { jq -e --arg k "$1" 'has($k)' >/dev/null; }                  # exit status 0/1 for use in `if`
count_by_role()     { jq -c 'group_by(.role) | map({role: .[0].role, n: length}) | sort_by(-.n, .role)'; }
tsv_report()        { jq -r '.users[] | [.name, .role, (.age // "-")] | @tsv'; }
build_payload()     { jq -n --arg name "$1" --argjson age "$2" '{name: $name, age: $age, tags: ["a","b"]}'; }   # quoting handled by jq
update_field()      { jq --arg v "$1" '.version = $v'; }
shell_quote_names() { jq -r '.users[].name | @sh'; }                              # safe to eval into an array
ndjson_errors()     { jq -c 'select(.level == "error") | {ts, msg}'; }            # one JSON object per line in, per line out
'''
    , "test.sh": HELPERS + r'''
source "$(dirname "$0")/lib.sh"
J='{"users":[{"name":"ann","role":"dev","active":true,"age":30},{"name":"bob \"the builder\"","role":"ops","active":false},{"name":"cy; rm -rf /","role":"dev","active":true}],"config":{}}'

eq "$(names_of_active <<<"$J" | paste -sd,)" 'ann,cy; rm -rf /'
eq "$(get_or_default eu-west <<<"$J")" eu-west
eq "$(get_or_default eu-west <<<'{"config":{"region":"us-east"}}')" us-east
has_key users <<<"$J" && ok || fail "has_key true"; if has_key nope <<<"$J"; then fail "has_key false"; else ok; fi
eq "$(count_by_role <<<"$(jq -c .users <<<"$J")")" '[{"role":"dev","n":2},{"role":"ops","n":1}]'
eq "$(tsv_report <<<"$J" | sed -n 2p)" $'bob "the builder"\tops\t-'
# injection-safe payload: quotes and JSON-looking text stay data
p=$(build_payload 'x", "admin": true, "y":"' 7); eq "$(jq -r '.name' <<<"$p")" 'x", "admin": true, "y":"'; eq "$(jq 'has("admin")' <<<"$p")" false; eq "$(jq .age <<<"$p")" 7
if build_payload bob notanumber 2>/dev/null; then fail "--argjson must reject non-JSON"; else ok; fi
eq "$(update_field 2.0 <<<'{"version":"1.0","x":1}' | jq -c .)" '{"version":"2.0","x":1}'
# @sh output is safe to eval into an array even with hostile names
eval "arr=($(shell_quote_names <<<"$J"))"; eq "${#arr[@]}" 3; eq "${arr[2]}" 'cy; rm -rf /'
printf '%s\n' '{"level":"info","ts":1,"msg":"a"}' '{"level":"error","ts":2,"msg":"b"}' | ndjson_errors | { read -r l; eq "$l" '{"ts":2,"msg":"b"}'; }
# invalid JSON is an error exit, never silent
if jq -e . <<<'{nope' 2>/dev/null; then fail "invalid json must fail"; else ok; fi
done_
'''}, BT, assumptions=BASH + ["jq 1.6+"], compat={"jq": ">=1.6"},
   tags=["bash", "shell", "jq", "json", "parsing", "cli", "scripting", "ndjson", "sysadmin", "api"],
   avoid="grep/sed on JSON; interpolating shell variables into jq programs or JSON strings; printing `null` strings into configs."),

RC("sh-sigint-restore-terminal", "Handle Ctrl-C cleanly and restore the terminal (cursor, stty, alt screen) in shell scripts", "bash",
   "A script that hides the cursor, turns off echo or uses the alternate screen leaves the user's terminal broken after Ctrl-C or an error.",
   "Save `stty -g`, register ONE restore function on EXIT, convert INT/TERM into normal exits with 128+signal statuses, emit show-cursor / leave-alt-screen / reset sequences only when stdout is a TTY, and restore in reverse order of setup. The same pattern applies in Go/Python/Rust TUIs.",
   {"lib.sh": r'''TERM_STATE=
HIDDEN=0
ALT=0

term_setup() {
  [[ -t 1 ]] || return 0                                    # never emit escape codes into pipes/files
  [[ -t 0 ]] && TERM_STATE=$(stty -g 2>/dev/null || true)
  trap term_restore EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

hide_cursor()   { [[ -t 1 ]] && { printf '\033[?25l'; HIDDEN=1; } || true; }
enter_alt()     { [[ -t 1 ]] && { printf '\033[?1049h'; ALT=1; } || true; }
raw_input()     { [[ -t 0 ]] && stty -echo -icanon min 1 time 0 || true; }

term_restore() {
  local rc=$?
  trap - EXIT INT TERM
  [[ -n $TERM_STATE ]] && stty "$TERM_STATE" 2>/dev/null || true          # input modes back first
  (( ALT )) && printf '\033[?1049l'                                      # leave alt screen
  (( HIDDEN )) && printf '\033[?25h'                                     # show cursor
  printf '\033[0m'
  exit "$rc"
}
''', "test.sh": HELPERS + r'''
lib="$(cd "$(dirname "$0")" && pwd)/lib.sh"
command -v script >/dev/null || { echo "OK (skipped: no script(1) for a pty)"; exit 0; }
run_tty() { script -qec "bash -c \"$1\"" /dev/null | cat -v; }

out=$(run_tty "source '$lib'; term_setup; hide_cursor; enter_alt; true")
[[ $out == *"^[[?25l"*"^[[?1049h"*"^[[?1049l"*"^[[?25h"* ]] && ok || fail "setup/restore sequence order: $out"
# restore also runs on failure and keeps the exit status
out=$(script -qec "bash -c \"source '$lib'; term_setup; hide_cursor; exit 3\"" /dev/null; echo "rc=\$?" ) ; true
set +e; script -qec "bash -c \"source '$lib'; term_setup; hide_cursor; exit 3\"" /dev/null >/dev/null; rc=$?; set -e; eq "$rc" 3
# Ctrl-C (SIGINT) mid-run still restores the cursor
script -qec "bash -c \"source '$lib'; term_setup; hide_cursor; kill -INT \\\$\\\$; sleep 5\"" /dev/null 2>&1 | cat -v >"${TMPDIR:-/tmp}/sig.$$" || true
[[ $(<"${TMPDIR:-/tmp}/sig.$$") == *"^[[?25h"* ]] && ok || fail "cursor not restored after SIGINT"; rm -f "${TMPDIR:-/tmp}/sig.$$"
# without a tty nothing is emitted at all
out=$(bash -c "source '$lib'; term_setup; hide_cursor; enter_alt; echo plain" | cat -v); eq "$out" plain
done_
'''}, BT, assumptions=BASH + ["Uses script(1) to get a pseudo-terminal; the test is skipped (not failed) when it is missing"], compat={"bash": ">=4.4"},
   tags=["bash", "shell", "sigint", "ctrl-c", "terminal", "stty", "cursor", "tui", "trap", "cli", "restore"],
   avoid="Hiding the cursor or disabling echo without a trap; trapping only INT (errors still leave the terminal broken); escape codes into pipes."),
]
