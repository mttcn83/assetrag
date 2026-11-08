"""Config / devops recipes. Tests are *structural* (parse the file and assert the properties that matter: hardening
keys, pinned versions, non-root user, contrast, ...) plus real tools where the sandbox has them (bash, systemd-analyze).
They do not build images, publish releases or talk to a database - evidence says so."""
from .recipes import RC, UT

PYT = ["python3", "-m", "unittest", "-q"]
STRUCT = ["structural tests: the file is parsed and its properties asserted; it is not deployed or built in the sandbox",
          "tests import PyYAML where YAML is parsed (present in this environment)"]

CFG = [
RC("cfg-systemd-hardened-service", "Hardened systemd service unit for a daemon", "systemd",
   "A long-running tool needs a systemd unit that restarts sanely and runs with the least privilege it can.",
   "Dedicated DynamicUser, StateDirectory for writable state, ProtectSystem=strict, ProtectHome, PrivateTmp/Devices, NoNewPrivileges, empty CapabilityBoundingSet, restricted address families and syscalls, Restart=on-failure with RestartSec and a start-rate limit; ExecStart uses an absolute path.",
   {"mytool.service": r'''[Unit]
Description=mytool daemon
Documentation=https://example.com/mytool
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/local/bin/mytool serve --config /etc/mytool/config.toml
Restart=on-failure
RestartSec=2s
TimeoutStopSec=20s

# identity and state
DynamicUser=yes
StateDirectory=mytool
CacheDirectory=mytool
RuntimeDirectory=mytool
ConfigurationDirectory=mytool

# sandboxing
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
UMask=0077

[Install]
WantedBy=multi-user.target
''', "test_unit.py": r'''import configparser, pathlib, re, shutil, subprocess, tempfile, unittest

TEXT = pathlib.Path("mytool.service").read_text()
cp = configparser.ConfigParser(strict=False, interpolation=None, allow_no_value=True)
cp.optionxform = str
cp.read_string(TEXT)
S = cp["Service"]


class UnitTests(unittest.TestCase):
    def test_sections_and_exec(self):
        self.assertEqual(set(cp.sections()), {"Unit", "Service", "Install"})
        self.assertTrue(S["ExecStart"].startswith("/"), "ExecStart must be an absolute path")

    def test_restart_policy_is_bounded(self):
        self.assertEqual(S["Restart"], "on-failure")
        self.assertIn("RestartSec", S)
        self.assertIn("StartLimitBurst", cp["Unit"])

    def test_hardening_keys(self):
        want = {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
                "MemoryDenyWriteExecute": "yes", "RestrictSUIDSGID": "yes", "LockPersonality": "yes", "DynamicUser": "yes"}
        for k, v in want.items():
            self.assertEqual(S.get(k), v, k)
        self.assertEqual(S.get("CapabilityBoundingSet"), "", "drop all capabilities unless needed")
        self.assertNotIn("AF_PACKET", S["RestrictAddressFamilies"])

    def test_writable_paths_are_explicit(self):
        self.assertIn("StateDirectory", S)                                   # ProtectSystem=strict needs somewhere to write
        self.assertIsNone(re.search(r"^ReadWritePaths=/($|\s)", TEXT, re.M))   # never make / writable

    def test_systemd_analyze_accepts_it(self):
        exe = shutil.which("systemd-analyze")
        if not exe:
            self.skipTest("systemd-analyze not available")
        d = tempfile.mkdtemp()
        unit = pathlib.Path(d, "mytool.service")
        unit.write_text(TEXT.replace("/usr/local/bin/mytool", "/usr/bin/true").replace("--config /etc/mytool/config.toml", ""))
        r = subprocess.run([exe, "verify", "--man=no", str(unit)], capture_output=True, text=True)
        bad = [l for l in (r.stdout + r.stderr).splitlines() if re.search(r"Unknown (key|section)|Invalid|bad setting|Failed to parse", l, re.I)]
        self.assertEqual(bad, [], bad)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["systemd-analyze verify runs when present (its lines about missing optional files are ignored); behaviour at runtime depends on your binary needing none of the denied syscalls"],
   compat={"systemd": ">=250"},
   tags=["systemd", "service", "unit", "hardening", "daemon", "sandboxing", "restart", "linux", "dynamicuser", "protectsystem", "config"],
   avoid="Running as root; User=nobody; Restart=always without a start limit (crash loops); ExecStart relying on PATH or shell syntax."),

RC("cfg-dockerfile-multistage", "Multi-stage minimal Dockerfile (Go binary on distroless, non-root)", "dockerfile",
   "Ship a small, reproducible, non-root container image for a compiled service.",
   "Build stage pins the toolchain and uses BuildKit cache mounts; static binary with -trimpath and stripped symbols; final stage is distroless static nonroot, copies only the binary, runs as USER nonroot with exec-form ENTRYPOINT; base images pinned by version; .dockerignore keeps the context small.",
   {"Dockerfile": r'''# syntax=docker/dockerfile:1.7
FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
ARG VERSION=dev
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w -X main.version=${VERSION}" -o /out/app ./cmd/app

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/app /app
USER nonroot:nonroot
EXPOSE 8080
ENTRYPOINT ["/app"]
''', ".dockerignore": r'''.git
.github
node_modules
*.md
*.test
dist
coverage
.env
.env.*
''', "test_dockerfile.py": r'''import pathlib, re, unittest

SRC = pathlib.Path("Dockerfile").read_text()
LINES = [l for l in SRC.replace("\\\n", " ").splitlines() if l.strip() and not l.strip().startswith("#")]
INS = [(l.split(None, 1)[0].upper(), l.split(None, 1)[1] if len(l.split(None, 1)) > 1 else "") for l in LINES]


class DockerfileTests(unittest.TestCase):
    def froms(self):
        return [a for i, a in INS if i == "FROM"]

    def test_multi_stage_and_final_stage_copies_from_build(self):
        self.assertGreaterEqual(len(self.froms()), 2)
        self.assertTrue(any(i == "COPY" and "--from=build" in a for i, a in INS))

    def test_base_images_are_pinned(self):
        for f in self.froms():
            image = f.split()[0]
            self.assertNotRegex(image, r":latest$"); self.assertIn(":", image, f"{image} has no tag")

    def test_runs_as_non_root(self):
        users = [a for i, a in INS if i == "USER"]
        self.assertTrue(users, "no USER instruction")
        self.assertNotIn(users[-1].split(":")[0], ("root", "0"))

    def test_exec_form_entrypoint(self):
        ep = [a for i, a in INS if i == "ENTRYPOINT"]
        self.assertTrue(ep and ep[-1].strip().startswith("["), "use exec form so signals reach the process")

    def test_static_trimmed_binary_and_cache_friendly_order(self):
        build = " ".join(a for i, a in INS if i == "RUN")
        for flag in ("CGO_ENABLED=0", "-trimpath", "-s -w"):
            self.assertIn(flag, build)
        copies = [a.strip() for i, a in INS if i == "COPY"]
        self.assertLess(copies.index("go.mod go.sum ./"), copies.index(". ."), "copy dependency manifests before sources for layer caching")

    def test_no_secrets_or_add_from_urls(self):
        self.assertIsNone(re.search(r"(?i)\b(password|secret|token)\s*=", SRC)); self.assertFalse(any(i == "ADD" for i, a in INS))

    def test_dockerignore_excludes_vcs_and_env(self):
        ig = pathlib.Path(".dockerignore").read_text().split()
        for e in (".git", ".env"):
            self.assertIn(e, ig)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["image is not built here (no Docker in the sandbox); pin by digest in production for full reproducibility"],
   compat={"docker": ">=24 with BuildKit"},
   tags=["docker", "dockerfile", "multi-stage", "distroless", "container", "image", "nonroot", "buildkit", "minimal", "go", "config"],
   avoid="FROM x:latest; running as root; apt-get in the final image; COPY . . before dependency manifests (busts the cache on every edit); shell-form ENTRYPOINT (signals never reach the app)."),

RC("cfg-github-actions-test-release", "GitHub Actions workflow: test matrix on push/PR, release on version tags", "yaml",
   "CI should test on every push and pull request and publish a release when a version tag is pushed, with minimal token permissions.",
   "Workflow-level permissions: contents: read; a test matrix across OS and language versions with dependency caching; a release job that runs only for v* tags, needs the tests, and gets contents: write only for itself; concurrency cancels superseded runs; third-party actions pinned to a version.",
   {".github/workflows/ci.yml": r'''name: ci

on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref_type != 'tag' }}

jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        go: ["1.22", "1.23"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with:
          go-version: ${{ matrix.go }}
          cache: true
      - run: go vet ./...
      - run: go test -race -count=1 ./...

  release:
    if: startsWith(github.ref, 'refs/tags/v')
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-go@v5
        with:
          go-version: "1.23"
      - uses: goreleaser/goreleaser-action@v6
        with:
          version: "~> v2"
          args: release --clean
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
''', "test_workflow.py": r'''import pathlib, re, unittest
import yaml

SRC = pathlib.Path(".github/workflows/ci.yml").read_text()
W = yaml.safe_load(SRC)
ON = W.get("on", W.get(True))        # PyYAML parses the bare key `on` as boolean True


class WorkflowTests(unittest.TestCase):
    def test_triggers(self):
        self.assertIn("pull_request", ON); self.assertIn("v*", ON["push"]["tags"])

    def test_least_privilege_token(self):
        self.assertEqual(W["permissions"], {"contents": "read"})
        self.assertEqual(W["jobs"]["release"]["permissions"], {"contents": "write"})
        self.assertNotIn("permissions", W["jobs"]["test"])

    def test_test_matrix_and_no_fail_fast(self):
        s = W["jobs"]["test"]["strategy"]
        self.assertFalse(s["fail-fast"]); self.assertGreaterEqual(len(s["matrix"]["os"]), 2); self.assertGreaterEqual(len(s["matrix"]["go"]), 2)

    def test_release_only_on_tags_after_tests(self):
        r = W["jobs"]["release"]
        self.assertIn("refs/tags/v", r["if"]); self.assertEqual(r["needs"], "test")

    def test_actions_are_pinned_to_a_version(self):
        uses = re.findall(r"uses:\s*(\S+)", SRC)
        self.assertTrue(uses)
        for u in uses:
            self.assertRegex(u, r"@(v?\d[\w.]*|[0-9a-f]{40})$", u)

    def test_concurrency_and_checkout_depth_for_changelog(self):
        self.assertIn("cancel-in-progress", W["concurrency"])
        steps = W["jobs"]["release"]["steps"]
        self.assertEqual(steps[0]["with"]["fetch-depth"], 0)             # goreleaser needs tags and history

    def test_secrets_only_via_context(self):
        self.assertIsNone(re.search(r"(?i)(token|password|key):\s*['\"]?[A-Za-z0-9]{20,}", SRC))


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["not run on GitHub; action versions reflect those current when written - bump them with Dependabot"],
   compat={"github-actions": "any"},
   tags=["github-actions", "workflow", "ci", "release", "test", "matrix", "tags", "goreleaser", "yaml", "permissions", "config", "cd"],
   avoid="permissions: write-all; pull_request_target with checkout of PR code; unpinned @main actions; secrets echoed into logs."),

RC("cfg-pkgbuild-go-cli", "PKGBUILD for packaging a Go CLI tool for the AUR / Arch", "pkgbuild",
   "A tool needs an Arch Linux package that follows the packaging guidelines for Go programs and installs license, completions and the binary correctly.",
   "Source tarball from the tagged release with sha256sums; makedepends=go; Go build flags from the Arch Go package guidelines (-buildmode=pie, -trimpath, -mod=readonly, -modcacherw, external linkmode); check() runs the tests; package() uses install -Dm755 and installs the LICENSE into /usr/share/licenses/$pkgname.",
   {"PKGBUILD": r'''# Maintainer: Your Name <you@example.com>
pkgname=mytool
pkgver=1.2.3
pkgrel=1
pkgdesc='Small command-line tool that does one thing well'
arch=('x86_64' 'aarch64')
url="https://github.com/example/${pkgname}"
license=('MIT')
depends=('glibc')
makedepends=('go')
source=("${pkgname}-${pkgver}.tar.gz::${url}/archive/v${pkgver}.tar.gz")
sha256sums=('0000000000000000000000000000000000000000000000000000000000000000')

prepare() {
  cd "${pkgname}-${pkgver}"
  mkdir -p build/
}

build() {
  cd "${pkgname}-${pkgver}"
  export CGO_CPPFLAGS="${CPPFLAGS}"
  export CGO_CFLAGS="${CFLAGS}"
  export CGO_CXXFLAGS="${CXXFLAGS}"
  export CGO_LDFLAGS="${LDFLAGS}"
  export GOFLAGS="-buildmode=pie -trimpath -mod=readonly -modcacherw"
  go build -ldflags "-linkmode external -extldflags \"${LDFLAGS}\" -X main.version=${pkgver}" -o build/${pkgname} ./cmd/${pkgname}
}

check() {
  cd "${pkgname}-${pkgver}"
  go test ./...
}

package() {
  cd "${pkgname}-${pkgver}"
  install -Dm755 build/${pkgname} "${pkgdir}/usr/bin/${pkgname}"
  install -Dm644 LICENSE "${pkgdir}/usr/share/licenses/${pkgname}/LICENSE"
  install -Dm644 README.md "${pkgdir}/usr/share/doc/${pkgname}/README.md"
}
''', "test_pkgbuild.py": r'''import pathlib, re, subprocess, unittest

SRC = pathlib.Path("PKGBUILD").read_text()


def sh(script):
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


class PkgbuildTests(unittest.TestCase):
    def test_bash_syntax(self):
        rc, _, err = sh("bash -n PKGBUILD"); self.assertEqual(rc, 0, err)

    def test_required_fields_after_sourcing(self):
        for var in ("pkgname", "pkgver", "pkgrel", "pkgdesc", "url", "license", "source", "sha256sums"):
            rc, out, _ = sh(f'source ./PKGBUILD; test -n "${{{var}}}" && echo set')
            self.assertEqual(out, "set", var)
        rc, out, _ = sh("source ./PKGBUILD; echo ${#arch[@]}"); self.assertGreaterEqual(int(out), 1)

    def test_functions_exist(self):
        for fn in ("build", "check", "package"):
            rc, out, _ = sh(f"source ./PKGBUILD; declare -F {fn}"); self.assertEqual(out, fn)

    def test_checksum_count_matches_sources_and_is_sha256(self):
        rc, out, _ = sh('source ./PKGBUILD; echo ${#source[@]} ${#sha256sums[@]}'); a, b = out.split(); self.assertEqual(a, b)
        rc, out, _ = sh("source ./PKGBUILD; printf '%s\\n' \"${sha256sums[@]}\""); [self.assertRegex(x, r"^([0-9a-f]{64}|SKIP)$") for x in out.split()]

    def test_arch_go_guidelines(self):
        for needle in ("-buildmode=pie", "-trimpath", "-mod=readonly", "-modcacherw", "-linkmode external", "CGO_LDFLAGS"):
            self.assertIn(needle, SRC)

    def test_package_installs_binary_and_license_to_pkgdir(self):
        pkg = SRC[SRC.index("package()"):]
        self.assertRegex(pkg, r'install -Dm755 .*"\$\{pkgdir\}/usr/bin/'); self.assertRegex(pkg, r'/usr/share/licenses/\$\{pkgname\}/')
        self.assertNotRegex(pkg, r'(^|\s)/usr/bin/\S+\s*$'.replace("$", "") + r'(?<!pkgdir\})', msg="never write outside $pkgdir") if False else None
        self.assertNotRegex(pkg, r'(?m)^\s*(cp|install|mv)\b[^\n]*\s/usr/')

    def test_no_network_in_build_or_package(self):
        self.assertNotRegex(SRC, r"\b(curl|wget|git clone|go get)\b")


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["makepkg is not run here; the PKGBUILD is sourced in bash and checked, then build it in a clean chroot (extra-x86_64-build) before submitting"],
   compat={"pacman": "any"},
   tags=["pkgbuild", "aur", "arch", "archlinux", "makepkg", "packaging", "package", "go", "install", "license", "bash", "config"],
   avoid="Building from the network in build(); omitting check(); installing without -D/-m modes; forgetting the license file; sha256sums=('SKIP') for release tarballs."),

RC("cfg-goreleaser-config", "GoReleaser config for cross-platform releases", "yaml",
   "Release a Go CLI for Linux/macOS/Windows on amd64/arm64 with archives, checksums and a changelog from tags.",
   "Static builds (CGO_ENABLED=0, -trimpath, stripped, version injected via ldflags), tar.gz archives with zip for Windows, README/LICENSE bundled, SHA-256 checksums, changelog grouped by conventional-commit prefixes with docs/test noise filtered.",
   {".goreleaser.yaml": r'''# yaml-language-server: $schema=https://goreleaser.com/static/schema.json
version: 2

before:
  hooks:
    - go mod tidy

builds:
  - id: mytool
    main: ./cmd/mytool
    binary: mytool
    env:
      - CGO_ENABLED=0
    flags:
      - -trimpath
    ldflags:
      - -s -w -X main.version={{.Version}} -X main.commit={{.Commit}} -X main.date={{.Date}}
    goos: [linux, darwin, windows]
    goarch: [amd64, arm64]
    ignore:
      - goos: windows
        goarch: arm64

archives:
  - id: default
    formats: [tar.gz]
    name_template: "{{ .ProjectName }}_{{ .Version }}_{{ .Os }}_{{ .Arch }}"
    format_overrides:
      - goos: windows
        formats: [zip]
    files:
      - LICENSE*
      - README*

checksum:
  name_template: checksums.txt
  algorithm: sha256

changelog:
  sort: asc
  groups:
    - title: Features
      regexp: '^feat(\(.+\))?:'
      order: 0
    - title: Fixes
      regexp: '^fix(\(.+\))?:'
      order: 1
    - title: Other
      order: 999
  filters:
    exclude:
      - '^docs:'
      - '^test:'
      - '^chore:'

release:
  draft: false
  prerelease: auto
''', "test_goreleaser.py": r'''import pathlib, unittest
import yaml

C = yaml.safe_load(pathlib.Path(".goreleaser.yaml").read_text())
B = C["builds"][0]


class GoreleaserTests(unittest.TestCase):
    def test_version_2_schema(self):
        self.assertEqual(C["version"], 2)

    def test_static_reproducible_builds(self):
        self.assertIn("CGO_ENABLED=0", B["env"]); self.assertIn("-trimpath", B["flags"])
        ld = " ".join(B["ldflags"]); self.assertIn("-s -w", ld); self.assertIn("main.version={{.Version}}", ld)

    def test_cross_platform_matrix(self):
        self.assertEqual(set(B["goos"]), {"linux", "darwin", "windows"}); self.assertEqual(set(B["goarch"]), {"amd64", "arm64"})
        self.assertIn({"goos": "windows", "goarch": "arm64"}, B["ignore"])

    def test_archives_zip_on_windows_and_bundle_license(self):
        a = C["archives"][0]
        self.assertEqual(a["formats"], ["tar.gz"]); self.assertEqual(a["format_overrides"][0], {"goos": "windows", "formats": ["zip"]})
        self.assertTrue(any(f.startswith("LICENSE") for f in a["files"]))

    def test_checksums_and_changelog(self):
        self.assertEqual(C["checksum"]["algorithm"], "sha256")
        self.assertTrue(C["changelog"]["filters"]["exclude"]); self.assertTrue(any(g["title"] == "Fixes" for g in C["changelog"]["groups"]))

    def test_archive_names_are_unique_per_platform(self):
        t = C["archives"][0]["name_template"]
        for part in ("Version", "Os", "Arch"):
            self.assertIn(part, t)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["goreleaser itself is not run in the sandbox; validate with `goreleaser check` locally"], compat={"goreleaser": ">=2"},
   tags=["goreleaser", "release", "go", "cross-platform", "archives", "checksums", "changelog", "ldflags", "yaml", "ci", "config"],
   avoid="Forgetting CGO_ENABLED=0 (breaks static binaries); same archive name for every OS; no checksums; hand-written release notes."),

RC("cfg-cloudflare-pages-headers", "Cloudflare Pages _headers with a strict Content-Security-Policy", "headers",
   "A static site on Cloudflare Pages needs security headers and long-lived caching for hashed assets without breaking the page.",
   "A _headers file: per-path blocks with indented Name: value lines; a strict CSP (default-src 'self', no unsafe-eval, frame-ancestors 'none', base-uri/form-action 'self'), HSTS, nosniff, referrer and permissions policies; immutable one-year caching for hashed /assets/*, no-cache for HTML.",
   {"_headers": r'''/*
  Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; upgrade-insecure-requests
  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
  Cross-Origin-Opener-Policy: same-origin
  X-Frame-Options: DENY

/assets/*
  Cache-Control: public, max-age=31536000, immutable

/*.html
  Cache-Control: public, max-age=0, must-revalidate

/
  Cache-Control: public, max-age=0, must-revalidate
''', "test_headers.py": r'''import pathlib, re, unittest


def parse(text):
    rules, cur = {}, None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw[0].isspace():
            cur = rules.setdefault(raw.strip(), {})
        else:
            name, _, value = raw.strip().partition(":")
            if not value:
                raise ValueError(f"header line without value: {raw!r}")
            cur[name.strip()] = value.strip()
    return rules


R = parse(pathlib.Path("_headers").read_text())
CSP = {d.split()[0]: d.split()[1:] for d in R["/*"]["Content-Security-Policy"].split(";") if d.strip()}


class HeadersTests(unittest.TestCase):
    def test_format(self):
        self.assertIn("/*", R)
        for path in R:
            self.assertTrue(path.startswith("/"), path)

    def test_csp_is_strict(self):
        self.assertEqual(CSP["default-src"], ["'self'"])
        for d in ("script-src", "style-src"):
            self.assertNotIn("'unsafe-eval'", CSP[d]); self.assertNotIn("*", CSP[d]); self.assertNotIn("'unsafe-inline'", CSP[d])
        self.assertEqual(CSP["frame-ancestors"], ["'none'"]); self.assertEqual(CSP["object-src"], ["'none'"])
        self.assertEqual(CSP["base-uri"], ["'self'"]); self.assertEqual(CSP["form-action"], ["'self'"])

    def test_transport_and_sniffing_headers(self):
        h = R["/*"]
        self.assertGreaterEqual(int(re.search(r"max-age=(\d+)", h["Strict-Transport-Security"]).group(1)), 15552000)
        self.assertEqual(h["X-Content-Type-Options"], "nosniff"); self.assertIn("Referrer-Policy", h); self.assertIn("Permissions-Policy", h)

    def test_caching_policy(self):
        self.assertIn("immutable", R["/assets/*"]["Cache-Control"]); self.assertIn("max-age=31536000", R["/assets/*"]["Cache-Control"])
        self.assertIn("must-revalidate", R["/*.html"]["Cache-Control"])

    def test_cloudflare_limits(self):
        self.assertLessEqual(len(R), 100)
        for rules in R.values():
            for k, v in rules.items():
                self.assertLessEqual(len(k) + len(v), 2000)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["inline scripts/styles need nonces or hashes under this CSP; Pages does not generate nonces for static files"],
   compat={"cloudflare-pages": "any"},
   tags=["cloudflare", "pages", "headers", "_headers", "csp", "content-security-policy", "hsts", "security-headers", "cache", "static-site", "config"],
   avoid="script-src 'unsafe-inline' 'unsafe-eval' to 'make it work'; caching HTML for a year; HSTS preload without knowing every subdomain serves HTTPS."),

RC("cfg-vite-pwa-config", "vite-plugin-pwa configuration for an installable offline app", "typescript",
   "A Vite app should be installable and work offline with auto-updating service worker and a valid web manifest.",
   "VitePWA with registerType autoUpdate, a complete manifest (name, short_name, start_url, display standalone, theme/background colors, 192/512 icons incl. a maskable one), Workbox precache of the built assets, navigateFallback to index.html (denylisting /api), cleanupOutdatedCaches and a NetworkFirst runtime cache for API GETs.",
   {"vite.config.ts": r'''import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: 'My App',
        short_name: 'MyApp',
        description: 'An installable app that works offline',
        start_url: '/',
        scope: '/',
        display: 'standalone',
        theme_color: '#0b1020',
        background_color: '#0b1020',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          { src: 'pwa-maskable-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//],
        cleanupOutdatedCaches: true,
        runtimeCaching: [
          {
            urlPattern: ({ url, request }: { url: URL; request: Request }) => request.method === 'GET' && url.pathname.startsWith('/api/'),
            handler: 'NetworkFirst',
            options: { cacheName: 'api', networkTimeoutSeconds: 3, expiration: { maxEntries: 50, maxAgeSeconds: 86400 } },
          },
        ],
      },
    }),
  ],
})
''', "test_pwa_config.py": r'''import pathlib, re, unittest

SRC = pathlib.Path("vite.config.ts").read_text()


class PwaConfigTests(unittest.TestCase):
    def test_plugin_registered_with_auto_update(self):
        self.assertIn("from 'vite-plugin-pwa'", SRC); self.assertRegex(SRC, r"registerType:\s*'autoUpdate'")

    def test_manifest_has_installability_fields(self):
        for field in ("name", "short_name", "start_url", "display", "theme_color", "background_color", "icons"):
            self.assertRegex(SRC, rf"\b{field}:", field)
        self.assertRegex(SRC, r"display:\s*'(standalone|fullscreen|minimal-ui)'")

    def test_icons_cover_192_512_and_maskable(self):
        sizes = set(re.findall(r"sizes:\s*'(\d+x\d+)'", SRC))
        self.assertTrue({"192x192", "512x512"} <= sizes); self.assertRegex(SRC, r"purpose:\s*'maskable'")

    def test_theme_colors_are_hex(self):
        for k in ("theme_color", "background_color"):
            self.assertRegex(SRC, rf"{k}:\s*'#[0-9a-fA-F]{{6}}'")

    def test_offline_navigation_and_api_not_swallowed(self):
        self.assertRegex(SRC, r"navigateFallback:\s*'/index\.html'"); self.assertIn("navigateFallbackDenylist", SRC)
        self.assertIn("cleanupOutdatedCaches: true", SRC); self.assertRegex(SRC, r"handler:\s*'NetworkFirst'")

    def test_only_get_requests_are_runtime_cached(self):
        self.assertIn("request.method === 'GET'", SRC)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["vite and vite-plugin-pwa are not installed in the sandbox, so the build is not run; add the icon files and run `vite build` + Lighthouse locally"],
   compat={"vite": ">=5", "vite-plugin-pwa": ">=0.20"},
   tags=["vite", "pwa", "vite-plugin-pwa", "manifest", "service-worker", "workbox", "installable", "offline", "icons", "maskable", "typescript", "config"],
   avoid="Registering a hand-written service worker next to the plugin's; caching /api navigations as the app shell; missing maskable icon (Android shows a white square)."),

RC("cfg-supabase-rls-policies", "Postgres row level security policies for per-user data (Supabase)", "sql",
   "A multi-user app on Postgres/Supabase must guarantee users only read and write their own rows, even if the API layer has a bug.",
   "Owner column defaulting to auth.uid(); RLS enabled (and forced); one policy per command for the authenticated role using (select auth.uid()) so the planner caches it; WITH CHECK on insert/update; an index on the owner column; no `using (true)` write policies and no anon access.",
   {"schema.sql": r'''create table public.notes (
  id          uuid primary key default gen_random_uuid(),
  owner_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  title       text not null check (char_length(title) between 1 and 200),
  body        text not null default '',
  created_at  timestamptz not null default now()
);

create index notes_owner_id_idx on public.notes (owner_id);

alter table public.notes enable row level security;
alter table public.notes force row level security;

create policy "notes: read own" on public.notes
  for select to authenticated
  using ((select auth.uid()) = owner_id);

create policy "notes: insert own" on public.notes
  for insert to authenticated
  with check ((select auth.uid()) = owner_id);

create policy "notes: update own" on public.notes
  for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

create policy "notes: delete own" on public.notes
  for delete to authenticated
  using ((select auth.uid()) = owner_id);

revoke all on public.notes from anon;
grant select, insert, update, delete on public.notes to authenticated;
''', "test_rls.py": r'''import pathlib, re, unittest

SQL = pathlib.Path("schema.sql").read_text()
POLICIES = re.findall(r"create policy\s+\"[^\"]+\"\s+on\s+public\.(\w+)\s+for\s+(\w+)\s+to\s+(\w+)(.*?);", SQL, re.S | re.I)


class RlsTests(unittest.TestCase):
    def test_rls_enabled_and_forced(self):
        self.assertRegex(SQL, r"alter table public\.notes enable row level security;")
        self.assertRegex(SQL, r"alter table public\.notes force row level security;")

    def test_one_policy_per_command(self):
        self.assertEqual(sorted(c.lower() for _, c, _, _ in POLICIES), ["delete", "insert", "select", "update"])

    def test_policies_target_authenticated_only(self):
        for _, _, role, _ in POLICIES:
            self.assertEqual(role.lower(), "authenticated")
        self.assertIn("revoke all on public.notes from anon", SQL)

    def test_uid_is_wrapped_in_select_for_plan_caching(self):
        self.assertNotRegex(SQL, r"(?<!select )auth\.uid\(\)\s*=\s*owner_id")
        for _, _, _, body in POLICIES:
            self.assertIn("(select auth.uid())", body)

    def test_writes_have_with_check_and_no_open_policies(self):
        for _, cmd, _, body in POLICIES:
            if cmd.lower() in ("insert", "update"):
                self.assertIn("with check", body.lower(), cmd)
            self.assertNotRegex(body, r"(using|check)\s*\(\s*true\s*\)")

    def test_owner_column_defaults_and_is_indexed(self):
        self.assertRegex(SQL, r"owner_id\s+uuid not null default auth\.uid\(\)")
        self.assertRegex(SQL, r"create index \w+ on public\.notes \(owner_id\)")

    def test_cascade_on_user_delete(self):
        self.assertIn("on delete cascade", SQL)


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["not executed against Postgres (none in the sandbox); test with two real users via supabase test helpers (pgTAP) before relying on it", "service_role keys bypass RLS by design - never ship them to clients"],
   compat={"postgres": ">=14", "supabase": "any"},
   tags=["rls", "row-level-security", "postgres", "supabase", "policy", "auth.uid", "multi-tenant", "sql", "authorization", "data", "config"],
   avoid="Disabling RLS 'temporarily'; policies with using (true); calling auth.uid() bare in policies (re-evaluated per row); trusting a client-supplied user_id column."),

RC("cfg-dark-mode-tokens", "Dark-mode design tokens with a contrast-checked palette (CSS custom properties)", "css",
   "A site needs light and dark themes that follow the OS, can be overridden by the user, and keep readable contrast in both.",
   "Tokens as CSS custom properties on :root; the dark set is applied by @media (prefers-color-scheme: dark) unless the user forced light, and by [data-theme=dark]; color-scheme set so form controls follow; every token exists in all three blocks; text/background pairs are verified against WCAG 2.x contrast (>= 4.5:1 body, >= 3:1 UI).",
   {"tokens.css": r''':root {
  color-scheme: light dark;
  --bg: #ffffff;
  --surface: #f4f5f7;
  --fg: #14161a;
  --muted: #4b5563;
  --accent: #1d4ed8;
  --accent-fg: #ffffff;
  --border: #8a93a3;
  --danger: #b42318;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0e1116;
    --surface: #171b22;
    --fg: #e8eaee;
    --muted: #a3acb9;
    --accent: #7aa2ff;
    --accent-fg: #0e1116;
    --border: #6b7686;
    --danger: #ff8a80;
  }
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0e1116;
  --surface: #171b22;
  --fg: #e8eaee;
  --muted: #a3acb9;
  --accent: #7aa2ff;
  --accent-fg: #0e1116;
  --border: #6b7686;
  --danger: #ff8a80;
}

:root[data-theme="light"] { color-scheme: light; }

body { background: var(--bg); color: var(--fg); }
a { color: var(--accent); }
button.primary { background: var(--accent); color: var(--accent-fg); border: 1px solid var(--border); }
''', "test_tokens.py": r'''import pathlib, re, unittest

CSS = pathlib.Path("tokens.css").read_text()


def block(selector_start):
    i = CSS.index(selector_start); j = CSS.index("{", i); depth, k = 1, j + 1
    while depth:
        depth += {"{": 1, "}": -1}.get(CSS[k], 0); k += 1
    return CSS[j + 1:k - 1]


def tokens(body):
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", body))


def lum(hex_):
    c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


LIGHT = tokens(block(":root {"))
DARK_MEDIA = tokens(block("@media (prefers-color-scheme: dark)"))
DARK_ATTR = tokens(block(':root[data-theme="dark"]'))


class TokenTests(unittest.TestCase):
    def test_same_tokens_in_every_theme(self):
        self.assertEqual(set(LIGHT), set(DARK_MEDIA)); self.assertEqual(set(LIGHT), set(DARK_ATTR))

    def test_media_and_attribute_dark_themes_agree(self):
        self.assertEqual(DARK_MEDIA, DARK_ATTR)

    def test_body_text_contrast_aa(self):
        for name, t in (("light", LIGHT), ("dark", DARK_ATTR)):
            self.assertGreaterEqual(ratio(t["--fg"], t["--bg"]), 4.5, name)
            self.assertGreaterEqual(ratio(t["--fg"], t["--surface"]), 4.5, name)
            self.assertGreaterEqual(ratio(t["--muted"], t["--bg"]), 4.5, name)
            self.assertGreaterEqual(ratio(t["--accent-fg"], t["--accent"]), 4.5, name)
            self.assertGreaterEqual(ratio(t["--accent"], t["--bg"]), 4.5, name + " link text")
            self.assertGreaterEqual(ratio(t["--danger"], t["--bg"]), 4.5, name)

    def test_ui_component_contrast_3_to_1(self):
        for name, t in (("light", LIGHT), ("dark", DARK_ATTR)):
            self.assertGreaterEqual(ratio(t["--border"], t["--bg"]), 3.0, name)

    def test_user_override_and_color_scheme(self):
        self.assertIn(':root:not([data-theme="light"])', CSS); self.assertIn("color-scheme: light dark", CSS)
        self.assertIn(':root[data-theme="light"]', CSS)

    def test_components_use_tokens_not_literals(self):
        rest = CSS[CSS.index("body {"):]
        self.assertNotRegex(rest, r"#[0-9a-fA-F]{3,6}")


if __name__ == "__main__":
    unittest.main()
'''}, PYT, assumptions=STRUCT + ["contrast is computed from the token hex values with the WCAG 2.x formula; gradients, images and opacity are not covered"],
   compat={"browsers": "all with CSS custom properties"},
   tags=["dark-mode", "theme", "tokens", "css", "custom-properties", "prefers-color-scheme", "contrast", "wcag", "design-tokens", "accessibility", "color-scheme", "config"],
   avoid="Inverting colors with filter; pure #000 on #fff; separate hand-copied dark stylesheet that drifts; theme flash because the choice is read after first paint."),
]
