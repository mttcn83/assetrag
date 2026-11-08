#!/bin/sh
# Run the AssetRAG feature checks inside throwaway containers on several distros.
#   scripts/container-test.sh                  # arch, debian, ubuntu, fedora, alpine
#   scripts/container-test.sh debian:12        # one image
#   SANDBOX=1 scripts/container-test.sh        # also let bubblewrap create namespaces (seccomp=unconfined) so recipe tests really execute
# Tests the committed tree (git archive HEAD), never your working directory, data dir or home.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${OUT:-${TMPDIR:-/tmp}/assetrag-container-test}"
IMAGES="${*:-archlinux:latest debian:12 ubuntu:24.04 fedora:latest alpine:latest}"
rm -rf "$OUT" && mkdir -p "$OUT/src"
git -C "$ROOT" archive HEAD | tar -x -C "$OUT/src"
cp "$ROOT/scripts/container-inner.sh" "$OUT/inner.sh"
OPTS=""
[ "${SANDBOX:-0}" = 1 ] && OPTS="--security-opt seccomp=unconfined --security-opt apparmor=unconfined --security-opt systempaths=unconfined --cap-add SYS_ADMIN"
for img in $IMAGES; do
  name="$(echo "$img" | tr ':/' '__')"
  printf '== %s\n' "$img"
  # shellcheck disable=SC2086
  if docker run --rm $OPTS -v "$OUT/src:/src:ro" -v "$OUT/inner.sh:/inner.sh:ro" "$img" sh /inner.sh root >"$OUT/$name.log" 2>&1; then
    echo "   PASS  ($(grep -c '^PASS' "$OUT/$name.log") checks)"
  else
    echo "   FAIL  see $OUT/$name.log"; grep '^FAIL' "$OUT/$name.log" | head -8
  fi
done
echo "logs: $OUT"
