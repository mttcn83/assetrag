"""Round-5 gap fillers named in the roadmap: Rust file-system tooling, Linux clipboard (Wayland/X11), firewall inspection, file watching + systemd timers.
Every recipe's tests run in the bubblewrap sandbox before it is admitted."""
from .recipes import RC, UT
from .recipes_r1e import RS, RSA

R5A = [
RC("rust-fs-walk-hardlinks-human-sizes", "Rust (std only): parallel directory walk, hard-link-aware disk usage, symlink safety, human sizes and error collection", "rust",
   "Write a `du`/`ncdu`-style tool or any recursive scanner in Rust that is fast, counts hard-linked files once, never follows symlinks out of the tree, and keeps going past permission errors.",
   "Scoped worker threads share a work queue (Mutex<Vec> + Condvar with an in-flight counter so idle workers know when the walk is truly finished). Each directory is read with `read_dir`, entries are classified with `symlink_metadata` (never `metadata`, which follows links), regular files are keyed by (dev, ino) so hard links count once, apparent size and allocated size (st_blocks*512) are reported separately, I/O errors are collected as (path, error) instead of aborting, and sizes are formatted in IEC (KiB) or SI (kB) units with correct rounding at unit boundaries. In a real project prefer the `ignore` or `walkdir` crates (and `rayon`), which add .gitignore handling, sorting and platform quirks; this shows what they do underneath.",
   {"lib.rs": r'''//! std-only parallel walker with hard-link dedupe. Unix only (uses MetadataExt for dev/ino/blocks).
use std::collections::HashSet;
use std::fs;
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::{Condvar, Mutex};

#[derive(Debug, Default, Clone, PartialEq)]
pub struct Usage {
    pub files: u64,
    pub dirs: u64,
    pub symlinks: u64,
    pub apparent: u64,  // sum of file lengths
    pub allocated: u64, // sum of st_blocks * 512 (what the disk actually holds)
    pub hardlinks_skipped: u64,
    pub errors: Vec<(PathBuf, String)>,
}

struct Shared {
    queue: Mutex<(Vec<PathBuf>, usize)>, // (pending dirs, workers currently busy)
    cv: Condvar,
    seen: Mutex<HashSet<(u64, u64)>>,
    total: Mutex<Usage>,
}

/// Walk `root` with `threads` workers. Symlinks are counted but never followed; files with nlink > 1 are counted once.
pub fn scan(root: &Path, threads: usize) -> io::Result<Usage> {
    let meta = fs::symlink_metadata(root)?;
    if !meta.is_dir() {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "not a directory"));
    }
    let sh = Shared { queue: Mutex::new((vec![root.to_path_buf()], 0)), cv: Condvar::new(), seen: Mutex::new(HashSet::new()), total: Mutex::new(Usage::default()) };
    std::thread::scope(|s| {
        for _ in 0..threads.max(1) {
            s.spawn(|| worker(&sh));
        }
    });
    let mut u = sh.total.into_inner().unwrap();
    u.dirs += 1; // the root itself
    Ok(u)
}

fn worker(sh: &Shared) {
    loop {
        let dir = {
            let mut q = sh.queue.lock().unwrap();
            loop {
                if let Some(d) = q.0.pop() {
                    q.1 += 1;
                    break d;
                }
                if q.1 == 0 {
                    sh.cv.notify_all(); // nothing queued and nobody can add more: the walk is over
                    return;
                }
                q = sh.cv.wait(q).unwrap();
            }
        };
        let mut local = Usage::default();
        let mut subdirs = Vec::new();
        match fs::read_dir(&dir) {
            Err(e) => local.errors.push((dir.clone(), e.to_string())),
            Ok(rd) => {
                for ent in rd {
                    let ent = match ent {
                        Ok(e) => e,
                        Err(e) => {
                            local.errors.push((dir.clone(), e.to_string()));
                            continue;
                        }
                    };
                    let path = ent.path();
                    let md = match fs::symlink_metadata(&path) {
                        Ok(m) => m,
                        Err(e) => {
                            local.errors.push((path, e.to_string()));
                            continue;
                        }
                    };
                    let ft = md.file_type();
                    if ft.is_symlink() {
                        local.symlinks += 1;
                    } else if ft.is_dir() {
                        local.dirs += 1;
                        subdirs.push(path);
                    } else if ft.is_file() {
                        if md.nlink() > 1 && !sh.seen.lock().unwrap().insert((md.dev(), md.ino())) {
                            local.hardlinks_skipped += 1;
                            continue;
                        }
                        local.files += 1;
                        local.apparent += md.len();
                        local.allocated += md.blocks() * 512;
                    }
                }
            }
        }
        {
            let mut t = sh.total.lock().unwrap();
            t.files += local.files;
            t.dirs += local.dirs;
            t.symlinks += local.symlinks;
            t.apparent += local.apparent;
            t.allocated += local.allocated;
            t.hardlinks_skipped += local.hardlinks_skipped;
            t.errors.extend(local.errors);
        }
        let mut q = sh.queue.lock().unwrap();
        q.0.extend(subdirs);
        q.1 -= 1;
        sh.cv.notify_all();
    }
}

/// Human-readable size. `si = false` -> 1024-based (KiB, MiB); `si = true` -> 1000-based (kB, MB). Never prints "1024.0 KiB".
pub fn human(bytes: u64, si: bool) -> String {
    let (base, units): (f64, &[&str]) = if si { (1000.0, &["B", "kB", "MB", "GB", "TB", "PB"]) } else { (1024.0, &["B", "KiB", "MiB", "GiB", "TiB", "PiB"]) };
    if bytes < base as u64 {
        return format!("{} B", bytes);
    }
    let mut v = bytes as f64;
    let mut i = 0;
    while v >= base && i < units.len() - 1 {
        v /= base;
        i += 1;
    }
    let s = format!("{:.1}", v);
    if s.parse::<f64>().unwrap() >= base && i < units.len() - 1 {
        return format!("{:.1} {}", v / base, units[i + 1]); // 1023.96 KiB rounds to 1024.0: promote the unit instead
    }
    format!("{} {}", s, units[i])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::os::unix::fs::symlink;

    fn tmp(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("walk-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }
    fn put(p: &Path, n: usize) {
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::File::create(p).unwrap().write_all(&vec![b'x'; n]).unwrap();
    }

    #[test]
    fn counts_files_dirs_and_bytes_across_threads() {
        let d = tmp("count");
        for i in 0..40 {
            put(&d.join(format!("a{}/b{}/f.txt", i % 5, i)), 100 + i);
        }
        let want: u64 = (0..40).map(|i| 100 + i as u64).sum();
        for t in [1, 4, 16] {
            let u = scan(&d, t).unwrap();
            assert_eq!((u.files, u.apparent), (40, want), "threads={}", t);
            assert_eq!(u.dirs, 1 + 5 + 40);
            assert!(u.errors.is_empty());
            assert!(u.allocated >= u.apparent || u.apparent < 4096);
        }
    }

    #[test]
    fn hard_links_count_once_and_are_reported() {
        let d = tmp("hard");
        put(&d.join("orig"), 5000);
        fs::hard_link(d.join("orig"), d.join("link1")).unwrap();
        fs::hard_link(d.join("orig"), d.join("sub_link")).unwrap_or(());
        fs::create_dir_all(d.join("sub")).unwrap();
        fs::hard_link(d.join("orig"), d.join("sub/link2")).unwrap();
        let u = scan(&d, 4).unwrap();
        assert_eq!((u.files, u.apparent, u.hardlinks_skipped), (1, 5000, 3));
    }

    #[test]
    fn symlinks_are_counted_never_followed() {
        let d = tmp("sym");
        put(&d.join("real/big.bin"), 10_000);
        symlink(d.join("real"), d.join("loop_to_real")).unwrap();
        symlink(&d, d.join("real/back_to_root")).unwrap(); // a cycle if followed
        symlink("/definitely/missing", d.join("dangling")).unwrap();
        let u = scan(&d, 4).unwrap();
        assert_eq!((u.files, u.apparent, u.symlinks), (1, 10_000, 3));
        assert!(u.errors.is_empty());
    }

    #[test]
    fn unreadable_directories_are_reported_and_the_walk_continues() {
        use std::os::unix::fs::PermissionsExt;
        let d = tmp("perm");
        put(&d.join("ok/f"), 10);
        put(&d.join("locked/secret"), 99);
        fs::set_permissions(d.join("locked"), fs::Permissions::from_mode(0o000)).unwrap();
        let u = scan(&d, 2).unwrap();
        fs::set_permissions(d.join("locked"), fs::Permissions::from_mode(0o755)).unwrap();
        let running_as_root = unsafe_uid_is_zero();
        if !running_as_root {
            assert_eq!(u.errors.len(), 1);
            assert!(u.errors[0].0.ends_with("locked"));
            assert_eq!(u.files, 1);
        }
    }
    fn unsafe_uid_is_zero() -> bool {
        fs::metadata("/proc/self").map(|m| m.uid() == 0).unwrap_or(false)
    }

    #[test]
    fn root_must_be_a_directory() {
        let d = tmp("file");
        put(&d.join("f"), 1);
        assert!(scan(&d.join("f"), 2).is_err());
        assert!(scan(&d.join("missing"), 2).is_err());
    }

    #[test]
    fn empty_tree_terminates() {
        let d = tmp("empty");
        let u = scan(&d, 8).unwrap();
        assert_eq!((u.files, u.dirs), (0, 1));
    }

    #[test]
    fn human_sizes() {
        assert_eq!(human(0, false), "0 B");
        assert_eq!(human(1023, false), "1023 B");
        assert_eq!(human(1024, false), "1.0 KiB");
        assert_eq!(human(1536, false), "1.5 KiB");
        assert_eq!(human(1000, true), "1.0 kB");
        assert_eq!(human(1_048_576, false), "1.0 MiB");
        assert_eq!(human(1024 * 1024 - 1, false), "1.0 MiB"); // 1023.999 KiB must not print as "1024.0 KiB"
        assert_eq!(human(u64::MAX, false), "16.0 EiB".replace("EiB", "PiB").replace("16.0", "16384.0"));
    }
}
'''}, RS, assumptions=RSA + ["Unix only (MetadataExt)", "allocated size uses st_blocks*512 and so reflects the file system's block accounting (sparse files, compression, reflinks are not special-cased)"],
   compat={"rust": ">=1.63"}, tags=["rust", "filesystem", "directory", "walk", "du", "disk-usage", "hard-links", "inode", "symlink", "parallel", "threads", "human-readable", "sizes", "ncdu", "walkdir", "ignore", "recursive", "scanner", "cli"],
   avoid="Following symlinks with fs::metadata; counting hard links twice; aborting the whole walk on one EACCES; one thread per directory; formatting 1023.99 KiB as 1024.0 KiB; stopping workers on an empty queue while others may still add work."),

RC("py-linux-clipboard-wayland-x11", "Linux clipboard from a CLI or daemon: Wayland (wl-clipboard), X11 (xclip/xsel), macOS, OSC 52 over SSH, without hangs", "python",
   "Copy to or paste from the system clipboard from a Python tool on Linux, working on Wayland and X11, and degrade sensibly over SSH or in a container.",
   "Detect the session (WAYLAND_DISPLAY first, then DISPLAY) and pick wl-copy/wl-paste, xclip or xsel (pbcopy/pbpaste on macOS). Pass text on stdin with argument arrays (no shell). The two classic traps are handled: `wl-copy` and `xclip` fork a background process that keeps inherited pipes open, so stdout/stderr must go to DEVNULL or `subprocess.run(capture_output=True)` hangs forever; and a clipboard that nobody reads is lost when the owner exits on X11, so xclip is left running. Also: primary selection, explicit MIME types, timeouts on paste, a clear error naming the package to install, and an OSC 52 fallback that works through SSH and tmux in terminals that allow it.",
   {"clip.py": r'''"""Clipboard helper for Linux (Wayland/X11) with macOS and OSC 52 fallbacks. Standard library only."""
import base64, os, shutil, subprocess, sys


class ClipboardError(RuntimeError):
    pass


def session(env=None):
    env = os.environ if env is None else env
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "none"


def _tool(env, path=None):
    which = lambda n: shutil.which(n, path=path)          # noqa: E731
    s = session(env)
    if sys.platform == "darwin" and which("pbcopy"):
        return "macos", ["pbcopy"], ["pbpaste"]
    if s == "wayland" and which("wl-copy") and which("wl-paste"):
        return "wl-clipboard", ["wl-copy"], ["wl-paste", "--no-newline"]
    if s in ("x11", "wayland") and which("xclip"):        # XWayland also offers an X11 clipboard
        return "xclip", ["xclip", "-selection", "clipboard", "-in"], ["xclip", "-selection", "clipboard", "-out"]
    if s in ("x11", "wayland") and which("xsel"):
        return "xsel", ["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]
    return None


INSTALL_HINT = {"wayland": "install wl-clipboard (pacman -S wl-clipboard / apt install wl-clipboard)", "x11": "install xclip (pacman -S xclip / apt install xclip)",
                "none": "no display session: set WAYLAND_DISPLAY/DISPLAY, or use osc52() in a terminal that supports it"}


def copy(text, primary=False, mime=None, env=None, path=None):
    """Put text (str or bytes) on the clipboard; returns the backend name."""
    data = text.encode() if isinstance(text, str) else text
    t = _tool(env, path)
    if not t:
        raise ClipboardError("no clipboard tool found: " + INSTALL_HINT[session(env)])
    name, cmd = t[0], list(t[1])
    if primary:
        if name == "wl-clipboard":
            cmd.append("--primary")
        elif name == "xclip":
            cmd[cmd.index("clipboard")] = "primary"
        elif name == "xsel":
            cmd[1] = "--primary"
        else:
            raise ClipboardError("%s has no primary selection" % name)
    if mime and name == "wl-clipboard":
        cmd += ["--type", mime]
    elif mime and name == "xclip":
        cmd += ["-target", mime]
    try:
        # DEVNULL for stdout/stderr is the point: these tools fork a daemon that inherits the pipes; capturing them would block until that daemon exits
        p = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, env=env)
    except subprocess.TimeoutExpired:
        raise ClipboardError("%s did not return within 5s" % name) from None
    if p.returncode != 0:
        raise ClipboardError("%s failed with exit code %d" % (name, p.returncode))
    return name


def paste(primary=False, mime=None, env=None, path=None, timeout=3):
    """Read the clipboard as bytes. An empty clipboard returns b'' (wl-paste exits 1 for 'nothing to paste')."""
    t = _tool(env, path)
    if not t:
        raise ClipboardError("no clipboard tool found: " + INSTALL_HINT[session(env)])
    name, cmd = t[0], list(t[2])
    if primary and name == "wl-clipboard":
        cmd.append("--primary")
    if mime and name == "wl-clipboard":
        cmd += ["--type", mime]
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise ClipboardError("%s timed out reading the clipboard (the owner of the selection is not answering)" % name) from None
    if p.returncode != 0:
        if name == "wl-clipboard" and b"nothing is copied" in p.stderr.lower() or (name == "wl-clipboard" and p.returncode == 1 and not p.stdout):
            return b""
        raise ClipboardError("%s failed (%d): %s" % (name, p.returncode, p.stderr.decode(errors="replace").strip()[:120]))
    return p.stdout


def osc52(text, tmux=False, limit=74994):
    """Escape sequence that asks the TERMINAL to set the clipboard: works over SSH with no tools on the remote host. Many terminals cap the payload (~75 KB)."""
    b = base64.b64encode(text.encode() if isinstance(text, str) else text)
    if len(b) > limit:
        raise ClipboardError("text too large for OSC 52 (%d > %d base64 bytes)" % (len(b), limit))
    seq = "\033]52;c;%s\a" % b.decode()
    return "\033Ptmux;\033" + seq.replace("\033", "\033\033") + "\033\\" if tmux else seq
''', "test_clip.py": r'''import os, stat, tempfile, unittest
import clip


def make_bin(d, name, body):
    p = os.path.join(d, name)
    open(p, "w").write("#!/bin/sh\n" + body)
    os.chmod(p, 0o755)


class Fake(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.store = os.path.join(self.d, "clipboard"); self.path = self.d + ":/usr/bin:/bin"
        # a wl-copy that, like the real one, forks a background child holding stdout/stderr open for a while
        make_bin(self.d, "wl-copy", 'cat > "%s"; (sleep 5 &) ; echo "$@" > "%s.args"\n' % (self.store, self.store))
        make_bin(self.d, "wl-paste", '[ -f "%s" ] || { echo "Nothing is copied" >&2; exit 1; }; cat "%s"\n' % (self.store, self.store))
        make_bin(self.d, "xclip", 'case "$*" in *-in*) cat > "%s"; echo "$@" > "%s.args";; *) cat "%s";; esac\n' % (self.store, self.store, self.store))
        self.wl = {"WAYLAND_DISPLAY": "wayland-0", "PATH": self.path}
        self.x11 = {"DISPLAY": ":0", "PATH": self.path}

    def test_session_detection_prefers_wayland(self):
        self.assertEqual(clip.session({"WAYLAND_DISPLAY": "w", "DISPLAY": ":0"}), "wayland")
        self.assertEqual(clip.session({"DISPLAY": ":0"}), "x11"); self.assertEqual(clip.session({}), "none")

    def test_wayland_copy_and_paste_round_trip_and_does_not_hang(self):
        import time
        t = time.time(); name = clip.copy("héllo\nworld", env=self.wl, path=self.path); dt = time.time() - t
        self.assertEqual(name, "wl-clipboard"); self.assertLess(dt, 3)                                  # the forked 'sleep 5' did not block us
        self.assertEqual(clip.paste(env=self.wl, path=self.path), "héllo\nworld".encode())

    def test_x11_uses_xclip_with_argument_arrays(self):
        self.assertEqual(clip.copy(b"\x00\x01binary", env=self.x11, path=self.path), "xclip")
        self.assertEqual(open(self.store, "rb").read(), b"\x00\x01binary")
        self.assertIn("-selection clipboard -in", open(self.store + ".args").read())

    def test_shell_metacharacters_in_text_are_just_data(self):
        evil = "$(touch %s/pwned); `id`; \"quoted\" ; rm -rf /" % self.d
        clip.copy(evil, env=self.wl, path=self.path); self.assertEqual(clip.paste(env=self.wl, path=self.path).decode(), evil); self.assertFalse(os.path.exists(self.d + "/pwned"))

    def test_primary_selection_and_mime_flags(self):
        clip.copy("x", primary=True, mime="text/plain", env=self.wl, path=self.path); self.assertEqual(open(self.store + ".args").read().split(), ["--primary", "--type", "text/plain"])
        clip.copy("y", primary=True, env=self.x11, path=self.path); self.assertIn("-selection primary -in", open(self.store + ".args").read())

    def test_empty_clipboard_is_empty_bytes_not_an_exception(self):
        self.assertEqual(clip.paste(env=self.wl, path=self.path), b"")

    def test_missing_tool_says_what_to_install(self):
        empty = tempfile.mkdtemp()
        with self.assertRaisesRegex(clip.ClipboardError, "wl-clipboard"): clip.copy("x", env={"WAYLAND_DISPLAY": "w", "PATH": empty}, path=empty)
        with self.assertRaisesRegex(clip.ClipboardError, "xclip"): clip.copy("x", env={"DISPLAY": ":0", "PATH": empty}, path=empty)
        with self.assertRaisesRegex(clip.ClipboardError, "no display session"): clip.copy("x", env={"PATH": empty}, path=empty)

    def test_failing_tool_is_reported(self):
        make_bin(self.d, "wl-copy", "exit 3\n")
        with self.assertRaisesRegex(clip.ClipboardError, "exit code 3"): clip.copy("x", env=self.wl, path=self.path)

    def test_hung_paste_times_out(self):
        make_bin(self.d, "wl-paste", "sleep 30\n")
        with self.assertRaisesRegex(clip.ClipboardError, "timed out"): clip.paste(env=self.wl, path=self.path, timeout=0.3)

    def test_osc52(self):
        self.assertEqual(clip.osc52("hi"), "\033]52;c;aGk=\a")
        self.assertTrue(clip.osc52("hi", tmux=True).startswith("\033Ptmux;\033\033\033]52;c;aGk=\a"))
        with self.assertRaises(clip.ClipboardError): clip.osc52("x" * 100000)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["wl-clipboard/xclip/xsel must be installed; tests use fake scripts and need /bin/sh", "Clipboard managers, sandboxes (Flatpak) and Wayland compositors without wlr-data-control may refuse background clipboard access", "OSC 52 support and size caps vary by terminal; many require enabling it"], compat={"python": ">=3.9"},
   tags=["clipboard", "wayland", "x11", "wl-copy", "wl-paste", "wl-clipboard", "xclip", "xsel", "pbcopy", "osc52", "ssh", "linux", "cli", "copy-to-clipboard", "paste", "primary-selection", "desktop", "tmux"],
   avoid="subprocess.run(['wl-copy'], capture_output=True) (hangs on the forked daemon); shell=True with the text interpolated; assuming X11; silently failing when no tool is installed; pyperclip when a 60-line helper is enough."),

RC("py-firewall-inspect-nft-iptables", "Inspect a Linux firewall without guessing: parse nft JSON and iptables-save, normalise rules, answer 'is port N reachable?', honest about root", "python",
   "Show which ports a Linux host's firewall allows or drops (nftables, iptables, ufw/firewalld sit on top) and cross-check it with listening sockets, from an unprivileged diagnostics tool.",
   "Parse `nft -j list ruleset` (stable JSON) and `iptables-save` text into one normalised model (family, table, chain, hook, policy, rules with verdict, protocol, dport ranges, saddr, iifname, counters). A reachability function walks the input-hook chains in priority order with the first terminal verdict winning and falls back to the base-chain policy, handling `jump` to user chains and `return`. Reading rules needs root (CAP_NET_ADMIN): the collector distinguishes 'not permitted' and 'tool missing' from 'no rules' so the tool never claims a host is open when it simply could not look.",
   {"fw.py": r'''"""Normalise nftables / iptables rules and answer reachability questions. Parsing is pure (testable with fixtures); collection is separate."""
import json, re, shutil, subprocess
from dataclasses import dataclass, field


@dataclass
class Rule:
    chain: str
    verdict: str                        # accept | drop | reject | jump:<chain> | return | other
    proto: str = ""                     # tcp | udp | "" (any)
    dports: list = field(default_factory=list)   # [(lo, hi)]
    saddr: str = ""
    iif: str = ""
    text: str = ""
    skip: bool = False                  # only matches packets of established connections: irrelevant to "can a NEW connection reach port N"
    unknown: bool = False               # has a condition this module does not evaluate (marks, rate limits, sets by name...)


@dataclass
class Chain:
    family: str
    table: str
    name: str
    hook: str = ""                      # input | forward | output | ""(user chain)
    prio: int = 0
    policy: str = "accept"
    rules: list = field(default_factory=list)


class Unavailable(Exception):
    """The firewall could not be read (tool missing / not permitted). Different from 'no rules'."""


# ------------------------------------------------------------------ nftables JSON
def _ports(v):
    if isinstance(v, int): return [(v, v)]
    if isinstance(v, str) and v.isdigit(): return [(int(v), int(v))]
    if isinstance(v, dict):
        if "range" in v: return [(int(v["range"][0]), int(v["range"][1]))]
        if "set" in v: return [p for x in v["set"] for p in _ports(x)]
    if isinstance(v, list): return [p for x in v for p in _ports(x)]
    return []


def parse_nft(doc):
    chains, rules = {}, []
    for item in doc.get("nftables", []):
        if "chain" in item:
            c = item["chain"]
            chains[(c["family"], c["table"], c["name"])] = Chain(c["family"], c["table"], c["name"], c.get("hook", ""), c.get("prio", 0), c.get("policy", "accept" if c.get("hook") else ""))
        elif "rule" in item:
            rules.append(item["rule"])
    for r in rules:
        ch = chains.get((r["family"], r["table"], r["chain"]))
        if ch is None: continue
        rule = Rule(ch.name, "other", text=json.dumps(r["expr"])[:200])
        for e in r["expr"]:
            m = e.get("match")
            if m:
                left, right = m["left"], m["right"]
                pl = left.get("payload", {})
                if pl.get("field") == "dport" and m.get("op", "==") in ("==", "in"):
                    rule.proto = pl.get("protocol", rule.proto); rule.dports += _ports(right)
                elif pl.get("field") == "saddr": rule.saddr = str(right)
                elif left.get("meta", {}).get("key") == "iifname": rule.iif = str(right)
                elif left.get("meta", {}).get("key") == "l4proto": rule.proto = str(right)
                elif left.get("ct", {}).get("key") == "state" and set(right if isinstance(right, list) else [right]) <= {"established", "related"}: rule.skip = True
                else: rule.unknown = True
            for k in ("accept", "drop", "reject", "return"):
                if k in e: rule.verdict = k
            for k in ("jump", "goto"):
                if k in e: rule.verdict = "jump:" + e[k]["target"]
        ch.rules.append(rule)
    return list(chains.values())


# ------------------------------------------------------------------ iptables-save
def parse_iptables_save(text, family="ip"):
    chains, table = {}, ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("*"): table = line[1:]
        elif line.startswith(":"):
            name, pol = line[1:].split()[:2]
            chains[(table, name)] = Chain(family, table, name, name.lower() if table == "filter" and name in ("INPUT", "FORWARD", "OUTPUT") else "", 0, pol.lower() if pol != "-" else "")
        elif line.startswith("-A "):
            toks = line.split()
            ch = chains.get((table, toks[1]))
            if ch is None: continue
            opt = lambda f: toks[toks.index(f) + 1] if f in toks else ""           # noqa: E731
            j = opt("-j")
            verdict = {"ACCEPT": "accept", "DROP": "drop", "REJECT": "reject", "RETURN": "return"}.get(j, "jump:" + j if j else "other")
            dp = opt("--dport") or opt("--dports")
            ports = []
            for part in dp.split(",") if dp else []:
                lo, _, hi = part.replace(":", "-").partition("-")
                ports.append((int(lo), int(hi or lo)))
            r = Rule(ch.name, verdict, opt("-p"), ports, opt("-s"), opt("-i"), line[:200])
            mods = [toks[i + 1] for i, t in enumerate(toks) if t == "-m" and i + 1 < len(toks)]
            for mod in mods:
                if mod == "conntrack" and set(opt("--ctstate").split(",")) <= {"ESTABLISHED", "RELATED"}: r.skip = True
                elif mod not in ("tcp", "udp", "multiport", "comment"): r.unknown = True
            ch.rules.append(r)
    return list(chains.values())


# ------------------------------------------------------------------ analysis
def _matches(r, proto, port, iface, src):
    if r.proto and r.proto != proto: return False
    if r.dports and not any(lo <= port <= hi for lo, hi in r.dports): return False
    if r.iif and r.iif != iface: return False                # interface-restricted (lo, docker0...): not what an external client hits unless asked for
    if r.saddr and r.saddr != src: return False             # source-restricted rule: not open to "anyone"
    return True


def _walk(chains_by_name, ch, proto, port, iface, src, notes, depth=0):
    for r in ch.rules:
        if r.skip or not _matches(r, proto, port, iface, src): continue
        if r.unknown:
            notes.append(r.text[:60]); continue               # cannot evaluate its condition: ignore it, but say so
        if r.verdict in ("accept", "drop", "reject"): return r.verdict, r
        if r.verdict == "return": return None, r
        if r.verdict.startswith("jump:") and depth < 8:
            tgt = chains_by_name.get((ch.family, ch.table, r.verdict[5:])) or chains_by_name.get(("", ch.table, r.verdict[5:]))
            if tgt:
                v, why = _walk(chains_by_name, tgt, proto, port, iface, src, notes, depth + 1)
                if v: return v, why
    return None, None


def reachable(chains, port, proto="tcp", iface="", src=""):
    """-> (verdict, reason). Evaluates every input-hook base chain (lowest priority first); the first terminal verdict wins; an accept in one chain can still be
    dropped by a later chain with a higher priority number, so all are consulted. src='' means 'any external address' (source-restricted rules do not count)."""
    by = {(c.family, c.table, c.name): c for c in chains}
    by.update({("", c.table, c.name): c for c in chains})
    base = sorted((c for c in chains if c.hook == "input"), key=lambda c: c.prio)
    if not base:
        return "accept", "no input chain: nothing filters incoming traffic"
    verdict, reason, notes = "accept", "default", []
    suffix = lambda: ("; ignored %d rule(s) with conditions this tool does not evaluate" % len(notes)) if notes else ""      # noqa: E731
    for c in base:
        v, r = _walk(by, c, proto, port, iface, src, notes)
        if v in ("drop", "reject"):
            return v, "%s/%s: %s%s" % (c.table, c.name, r.text[:80], suffix())
        if v == "accept":
            verdict, reason = "accept", "%s/%s: %s" % (c.table, c.name, r.text[:80])
        elif c.policy in ("drop", "reject"):
            return "drop", "%s/%s: policy %s (no rule accepts %s/%d)%s" % (c.table, c.name, c.policy, proto, port, suffix())
    return verdict, reason + suffix()


def open_ports(chains, proto="tcp"):
    """Ports explicitly accepted by input chains (a hint list, not a proof of reachability)."""
    out = set()
    for c in chains:
        if c.hook == "input":
            for r in c.rules:
                if r.verdict == "accept" and r.dports and (not r.proto or r.proto == proto):
                    out.update(r.dports)
    return sorted(out)


# ------------------------------------------------------------------ collection
def _run(cmd):
    if not shutil.which(cmd[0]): raise Unavailable("%s is not installed" % cmd[0])
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        err = p.stderr.strip()
        if re.search(r"permission denied|operation not permitted|must be root|you must be root", err, re.I): raise Unavailable("%s: not permitted (needs root / CAP_NET_ADMIN)" % cmd[0])
        raise Unavailable("%s failed: %s" % (cmd[0], err[:120]))
    return p.stdout


def collect():
    """Try nftables, then iptables. Raises Unavailable with the reason if neither can be read."""
    errs = []
    try: return "nftables", parse_nft(json.loads(_run(["nft", "-j", "list", "ruleset"])))
    except (Unavailable, ValueError) as e: errs.append(str(e))
    try: return "iptables", parse_iptables_save(_run(["iptables-save"]))
    except Unavailable as e: errs.append(str(e))
    raise Unavailable("; ".join(errs))
''', "test_fw.py": r'''import json, os, stat, tempfile, unittest
import fw

NFT = {"nftables": [
    {"metainfo": {"version": "1.0"}},
    {"chain": {"family": "inet", "table": "filter", "name": "input", "handle": 1, "type": "filter", "hook": "input", "prio": 0, "policy": "drop"}},
    {"chain": {"family": "inet", "table": "filter", "name": "services", "handle": 2}},
    {"rule": {"family": "inet", "table": "filter", "chain": "input", "expr": [{"match": {"op": "==", "left": {"meta": {"key": "iifname"}}, "right": "lo"}}, {"accept": None}]}},
    {"rule": {"family": "inet", "table": "filter", "chain": "input", "expr": [{"match": {"op": "in", "left": {"ct": {"key": "state"}}, "right": ["established", "related"]}}, {"accept": None}]}},
    {"rule": {"family": "inet", "table": "filter", "chain": "input", "expr": [{"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 22}}, {"accept": None}]}},
    {"rule": {"family": "inet", "table": "filter", "chain": "input", "expr": [{"jump": {"target": "services"}}]}},
    {"rule": {"family": "inet", "table": "filter", "chain": "services", "expr": [{"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": {"set": [80, 443, {"range": [8000, 8010]}]}}}, {"accept": None}]}},
    {"rule": {"family": "inet", "table": "filter", "chain": "services", "expr": [{"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "saddr"}}, "right": "10.0.0.5"}}, {"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 5432}}, {"accept": None}]}},
]}
IPT = """*filter
:INPUT DROP [0:0]
:FORWARD DROP [0:0]
:OUTPUT ACCEPT [0:0]
:DOCKER-USER - [0:0]
-A INPUT -i lo -j ACCEPT
-A INPUT -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A INPUT -p tcp -m multiport --dports 22 -j ACCEPT
-A INPUT -p tcp --dport 8080:8090 -j ACCEPT
-A INPUT -p udp --dport 53 -j DROP
-A INPUT -j DOCKER-USER
COMMIT
"""


class Nft(unittest.TestCase):
    def setUp(self): self.c = fw.parse_nft(NFT)

    def test_parse_chains_policy_and_rules(self):
        inp = next(c for c in self.c if c.name == "input")
        self.assertEqual((inp.hook, inp.policy, len(inp.rules)), ("input", "drop", 4)); self.assertEqual(inp.rules[2].dports, [(22, 22)]); self.assertEqual(inp.rules[0].iif, "lo")

    def test_reachability(self):
        for port, want in ((22, "accept"), (80, "accept"), (443, "accept"), (8005, "accept"), (3306, "drop")):
            self.assertEqual(fw.reachable(self.c, port)[0], want, port)
        self.assertEqual(fw.reachable(self.c, 53, "udp")[0], "drop")

    def test_reason_names_the_decisive_rule(self):
        v, why = fw.reachable(self.c, 3306); self.assertIn("policy drop", why); self.assertIn("filter/input", why)

    def test_source_restricted_rules_do_not_open_the_port_to_the_world(self):
        self.assertEqual(fw.reachable(self.c, 5432)[0], "drop"); self.assertEqual(fw.reachable(self.c, 5432, src="10.0.0.5")[0], "accept"); self.assertEqual(fw.reachable(self.c, 5432, src="10.0.0.6")[0], "drop")

    def test_open_ports_listing(self):
        self.assertEqual(fw.open_ports(self.c), [(22, 22)])                      # services chain is not an input base chain; reachable() follows the jump

    def test_loopback_and_established_rules_do_not_make_everything_reachable(self):
        self.assertEqual(fw.reachable(self.c, 9999)[0], "drop"); self.assertEqual(fw.reachable(self.c, 9999, iface="lo")[0], "accept")

    def test_unevaluable_conditions_are_ignored_and_reported(self):
        doc = {"nftables": [{"chain": {"family": "inet", "table": "f", "name": "input", "type": "filter", "hook": "input", "prio": 0, "policy": "drop"}},
                            {"rule": {"family": "inet", "table": "f", "chain": "input", "expr": [{"match": {"op": "==", "left": {"meta": {"key": "mark"}}, "right": 7}}, {"accept": None}]}}]}
        v, why = fw.reachable(fw.parse_nft(doc), 80); self.assertEqual(v, "drop"); self.assertIn("ignored 1 rule", why)

    def test_no_input_chain_means_unfiltered(self):
        v, why = fw.reachable([fw.Chain("ip", "nat", "prerouting", "prerouting")], 22); self.assertEqual(v, "accept"); self.assertIn("nothing filters", why)

    def test_later_priority_chain_can_still_drop(self):
        doc = {"nftables": NFT["nftables"] + [
            {"chain": {"family": "inet", "table": "ban", "name": "input", "type": "filter", "hook": "input", "prio": 10, "policy": "accept"}},
            {"rule": {"family": "inet", "table": "ban", "chain": "input", "expr": [{"match": {"op": "==", "left": {"payload": {"protocol": "tcp", "field": "dport"}}, "right": 22}}, {"drop": None}]}}]}
        self.assertEqual(fw.reachable(fw.parse_nft(doc), 22)[0], "drop")


class Ipt(unittest.TestCase):
    def setUp(self): self.c = fw.parse_iptables_save(IPT)

    def test_parse(self):
        inp = next(c for c in self.c if c.name == "INPUT"); self.assertEqual((inp.hook, inp.policy), ("input", "drop")); self.assertEqual(inp.rules[3].dports, [(8080, 8090)])
        self.assertEqual(next(c for c in self.c if c.name == "DOCKER-USER").hook, "")

    def test_reachability_with_ranges_and_explicit_drop(self):
        self.assertEqual(fw.reachable(self.c, 22)[0], "accept"); self.assertEqual(fw.reachable(self.c, 8085)[0], "accept"); self.assertEqual(fw.reachable(self.c, 8091)[0], "drop")
        self.assertEqual(fw.reachable(self.c, 53, "udp")[0], "drop")


class Collect(unittest.TestCase):
    def fake(self, body):
        d = tempfile.mkdtemp(); p = os.path.join(d, "nft"); open(p, "w").write("#!/bin/sh\n" + body); os.chmod(p, 0o755)
        old = os.environ["PATH"]; os.environ["PATH"] = d + ":/usr/bin:/bin"; self.addCleanup(lambda: os.environ.__setitem__("PATH", old)); return d

    def test_permission_denied_is_unavailable_not_empty(self):
        self.fake('echo "Operation not permitted (you must be root)" >&2; exit 1')
        with self.assertRaisesRegex(fw.Unavailable, "needs root"): fw.collect()

    def test_missing_tools(self):
        d = tempfile.mkdtemp(); old = os.environ["PATH"]; os.environ["PATH"] = d
        try:
            with self.assertRaisesRegex(fw.Unavailable, "not installed"): fw.collect()
        finally: os.environ["PATH"] = old

    def test_collect_parses_json_from_the_tool(self):
        self.fake("cat <<'EOF'\n" + json.dumps(NFT) + "\nEOF\n")
        name, chains = fw.collect(); self.assertEqual(name, "nftables"); self.assertEqual(fw.reachable(chains, 443)[0], "accept")


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Covers the common nftables/iptables shapes (port matches, sets, ranges, jump, accept/drop/reject, source restriction); NAT, marks, ct state, rate limits and sets-by-name are not evaluated", "Rules are read with root; docker, ufw and firewalld add their own chains: this reads what the kernel has, not what those front-ends were asked for", "A 'drop'/'accept' answer describes the host's input filtering only, not upstream firewalls, security groups or NAT"], compat={"python": ">=3.9"},
   tags=["firewall", "nftables", "nft", "iptables", "ufw", "firewalld", "ports", "reachable", "open-ports", "security", "linux", "network", "diagnostics", "root", "sysadmin", "portpeek", "cli"],
   avoid="Parsing `nft list ruleset` human output with regexes (use -j); concluding 'no firewall' when the tool needed root; ignoring chain priorities and policy; treating a source-restricted accept as open to the world."),

RC("py-file-watch-inotify-systemd-timer", "Watch files on Linux with inotify (ctypes, no dependency) with debounce and overflow handling, plus systemd timer/service units that actually validate", "python",
   "React to file changes (rebuild, sync, reload) in a daemon or CLI, and schedule periodic jobs with systemd timers instead of cron.",
   "A small inotify wrapper over libc via ctypes: recursive watches (new directories are added as they appear), decoded event masks, rename pairing by cookie, IN_Q_OVERFLOW treated as 'rescan everything', a debouncer that coalesces bursts (editors write temp files + rename) into one callback, and a polling fallback for file systems where inotify is blind (NFS, some FUSE, /proc). Then a generator for systemd .service/.timer pairs (OnCalendar, Persistent=true, RandomizedDelaySec, user-unit paths) with a validator that rejects the mistakes that make timers silently never fire.",
   {"watch.py": r'''"""inotify via ctypes + debounce + polling fallback, and systemd timer unit generation. Linux only, standard library only."""
import ctypes, ctypes.util, os, select, struct, time

IN_MODIFY, IN_ATTRIB, IN_CLOSE_WRITE = 0x2, 0x4, 0x8
IN_MOVED_FROM, IN_MOVED_TO, IN_CREATE, IN_DELETE = 0x40, 0x80, 0x100, 0x200
IN_Q_OVERFLOW, IN_IGNORED, IN_ISDIR = 0x4000, 0x8000, 0x40000000
IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
WATCH_MASK = IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE
NAMES = {IN_MODIFY: "modify", IN_ATTRIB: "attrib", IN_CLOSE_WRITE: "close_write", IN_MOVED_FROM: "moved_from", IN_MOVED_TO: "moved_to", IN_CREATE: "create", IN_DELETE: "delete"}
_libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
_HDR = struct.Struct("iIII")


class WatchError(OSError):
    pass


class Watcher:
    """Recursive directory watcher. Events are (kind, path) tuples; ('overflow', root) means the kernel queue overflowed - rescan."""
    def __init__(self, root, recursive=True, mask=WATCH_MASK):
        self.root, self.recursive, self.mask = os.fspath(root), recursive, mask
        self.fd = _libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if self.fd < 0:
            raise WatchError(ctypes.get_errno(), "inotify_init1 failed (limit of instances reached?)")
        self.wd = {}
        self._add_tree(self.root)

    def _add(self, path):
        w = _libc.inotify_add_watch(self.fd, os.fsencode(path), self.mask)
        if w < 0:
            err = ctypes.get_errno()
            if err == 28:                                   # ENOSPC: the *watch* limit, not disk space
                raise WatchError(err, "inotify watch limit reached: raise fs.inotify.max_user_watches")
            if err in (2, 13, 20):                          # vanished, unreadable, not a directory: skip quietly
                return
            raise WatchError(err, os.strerror(err))
        self.wd[w] = path

    def _add_tree(self, top):
        self._add(top)
        if self.recursive:
            for dp, dns, _ in os.walk(top):
                for d in dns:
                    self._add(os.path.join(dp, d))

    def read(self, timeout=0.2):
        if not select.select([self.fd], [], [], timeout)[0]:
            return []
        try:
            buf = os.read(self.fd, 65536)
        except BlockingIOError:
            return []
        out, i = [], 0
        while i < len(buf):
            wd, mask, cookie, ln = _HDR.unpack_from(buf, i)
            name = buf[i + _HDR.size:i + _HDR.size + ln].rstrip(b"\0").decode(errors="surrogateescape")
            i += _HDR.size + ln
            if mask & IN_Q_OVERFLOW:
                out.append(("overflow", self.root)); continue
            if mask & IN_IGNORED:
                self.wd.pop(wd, None); continue
            base = self.wd.get(wd)
            if base is None:
                continue
            path = os.path.join(base, name) if name else base
            if mask & IN_ISDIR and mask & (IN_CREATE | IN_MOVED_TO) and self.recursive:
                self._add_tree(path)                       # a new directory: watch it (and anything already inside it)
            for bit, kind in NAMES.items():
                if mask & bit:
                    out.append((kind + ("_dir" if mask & IN_ISDIR else ""), path)); break
        return out

    def close(self):
        os.close(self.fd)


class Debouncer:
    """Coalesce bursts: editors create temp files, write, rename; builds touch hundreds of files. Fire once, `quiet` seconds after the last event."""
    def __init__(self, quiet=0.3, ignore=lambda p: p.endswith(("~", ".swp", ".tmp")) or "/.git/" in p):
        self.quiet, self.ignore, self.pending, self.last = quiet, ignore, set(), 0.0

    def feed(self, events, now=None):
        now = time.monotonic() if now is None else now
        for kind, path in events:
            if kind == "overflow" or not self.ignore(path):
                self.pending.add((kind, path)); self.last = now

    def ready(self, now=None):
        now = time.monotonic() if now is None else now
        if self.pending and now - self.last >= self.quiet:
            batch, self.pending = self.pending, set()
            return sorted(batch)
        return None


def snapshot(root):
    """Polling fallback: {path: (mtime_ns, size)}. Use where inotify cannot see changes (NFS, some FUSE mounts)."""
    out = {}
    for dp, _, fns in os.walk(root):
        for f in fns:
            p = os.path.join(dp, f)
            try:
                st = os.stat(p); out[p] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
    return out


def diff(old, new):
    return sorted([("create", p) for p in new.keys() - old.keys()] + [("delete", p) for p in old.keys() - new.keys()] + [("modify", p) for p in new.keys() & old.keys() if new[p] != old[p]], key=lambda e: e[1])


# ---------------------------------------------------------------------------------- systemd timers
class UnitError(ValueError):
    pass


def validate_calendar(spec):
    """Cheap structural check of OnCalendar (use `systemd-analyze calendar` for the authoritative answer)."""
    shortcuts = {"minutely", "hourly", "daily", "weekly", "monthly", "quarterly", "semiannually", "yearly", "annually"}
    s = spec.strip()
    if s in shortcuts:
        return s
    import re
    wd = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
    lst = r"(?:(?:\.\.|,)[\d*/]+)*"
    wdx, date = wd + r"(?:(?:\.\.|,)" + wd + r")*", r"[\d*]+(?:/\d+)?" + lst + r"-[\d*]+(?:/\d+)?" + lst + r"-[\d*]+(?:/\d+)?" + lst
    time_ = r"[\d*]+(?:/\d+)?" + lst + r":[\d*]+(?:/\d+)?" + lst + r"(?::[\d*]+(?:\.\d+)?)?"
    if not s or not re.fullmatch(r"(?:%s\s+)?(?:%s\s+)?(?:%s)?\s*(?:UTC)?" % (wdx, date, time_), s) or s in ("UTC",):
        raise UnitError("OnCalendar=%r is not a recognised calendar expression (check with: systemd-analyze calendar '%s')" % (spec, spec))
    return s


def make_units(name, command, on_calendar, description="", user=True, persistent=True, randomized_delay="5min", working_dir=None, env=None):
    """-> {filename: text} for <name>.service + <name>.timer. The timer is the thing you `enable --now`; the service is activated by it."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9:_.\\-]+", name):
        raise UnitError("unit name may only contain letters, digits and : _ . -")
    if not command or not isinstance(command, (list, tuple)) or not os.path.isabs(command[0]):
        raise UnitError("command must be an argv list whose first element is an ABSOLUTE path (systemd does not search $PATH the way a shell does)")
    if any("\n" in a or "\0" in a for a in command):
        raise UnitError("newline or NUL in command")
    cal = validate_calendar(on_calendar)
    esc = lambda a: '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"' if re.search(r'[\s"\'\\$%;]', a) else a      # noqa: E731
    svc = ["[Unit]", "Description=%s" % (description or name), "", "[Service]", "Type=oneshot", "ExecStart=" + " ".join(esc(a) for a in command)]
    if working_dir: svc.append("WorkingDirectory=%s" % working_dir)
    for k, v in sorted((env or {}).items()): svc.append("Environment=%s" % esc("%s=%s" % (k, v)))
    tim = ["[Unit]", "Description=Schedule for %s" % (description or name), "", "[Timer]", "OnCalendar=%s" % cal]
    if persistent: tim.append("Persistent=true")                         # run once at next boot if the machine was off at the scheduled time
    if randomized_delay: tim.append("RandomizedDelaySec=%s" % randomized_delay)
    tim += ["", "[Install]", "WantedBy=timers.target"]                  # NOT multi-user.target: timers are pulled in by timers.target
    return {name + ".service": "\n".join(svc) + "\n", name + ".timer": "\n".join(tim) + "\n"}


def unit_dir(user=True):
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "systemd", "user") if user else "/etc/systemd/system"
''', "test_watch.py": r'''import os, tempfile, threading, time, unittest
import watch as W


def drain(w, secs=0.6):
    out, end = [], time.time() + secs
    while time.time() < end:
        out += w.read(0.1)
    return out


class Inotify(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.w = W.Watcher(self.d); self.addCleanup(self.w.close)

    def kinds(self, ev): return {(k, os.path.relpath(p, self.d)) for k, p in ev}

    def test_create_modify_delete(self):
        p = os.path.join(self.d, "a.txt"); open(p, "w").write("x"); open(p, "a").write("y"); os.remove(p)
        k = self.kinds(drain(self.w)); self.assertIn(("create", "a.txt"), k); self.assertIn(("modify", "a.txt"), k); self.assertIn(("delete", "a.txt"), k)

    def test_new_subdirectories_are_watched_recursively(self):
        os.makedirs(os.path.join(self.d, "x", "y")); drain(self.w, 0.3)
        open(os.path.join(self.d, "x", "y", "deep.txt"), "w").write("1"); self.assertIn(("create", "x/y/deep.txt"), self.kinds(drain(self.w)))

    def test_directories_that_existed_at_start_are_watched(self):
        d2 = tempfile.mkdtemp(); os.makedirs(os.path.join(d2, "pre", "sub")); w = W.Watcher(d2); self.addCleanup(w.close)
        open(os.path.join(d2, "pre", "sub", "f"), "w").write("1"); self.assertIn(("create", "pre/sub/f"), {(k, os.path.relpath(p, d2)) for k, p in drain(w)})

    def test_rename_into_the_tree_is_seen(self):
        other = tempfile.mkdtemp(); src = os.path.join(other, "m.txt"); open(src, "w").write("1"); os.rename(src, os.path.join(self.d, "moved.txt"))
        self.assertIn(("moved_to", "moved.txt"), self.kinds(drain(self.w)))

    def test_editor_save_pattern_is_one_debounced_batch(self):
        deb = W.Debouncer(quiet=0.25); p = os.path.join(self.d, "doc.txt")
        open(p, "w").write("v1"); drain(self.w, 0.2)
        tmp = p + ".swp"; open(tmp, "w").write("v2"); os.rename(tmp, p)                      # vim-style: temp file then rename over the original
        deb.feed(drain(self.w, 0.4), now=100.0)
        self.assertIsNone(deb.ready(now=100.1)); batch = deb.ready(now=100.5)
        self.assertTrue(batch and all(not path.endswith(".swp") for _, path in batch) and any(path == p for _, path in batch)); self.assertIsNone(deb.ready(now=101))

    def test_watch_limit_error_message(self):
        class W2(W.Watcher):
            pass
        err = W.WatchError(28, "inotify watch limit reached: raise fs.inotify.max_user_watches"); self.assertIn("max_user_watches", str(err))

    def test_missing_root_is_skipped_not_fatal(self):
        w = W.Watcher(os.path.join(self.d, "nope")); self.addCleanup(w.close); self.assertEqual(w.read(0.05), [])

    def test_polling_fallback_diff(self):
        a = W.snapshot(self.d); open(os.path.join(self.d, "n"), "w").write("1"); b = W.snapshot(self.d)
        self.assertEqual(W.diff(a, b), [("create", os.path.join(self.d, "n"))]); open(os.path.join(self.d, "n"), "w").write("22"); c = W.snapshot(self.d)
        self.assertEqual([k for k, _ in W.diff(b, c)], ["modify"]); os.remove(os.path.join(self.d, "n")); self.assertEqual([k for k, _ in W.diff(c, W.snapshot(self.d))], ["delete"])


class Debounce(unittest.TestCase):
    def test_overflow_always_passes_the_ignore_filter(self):
        d = W.Debouncer(quiet=0); d.feed([("overflow", "/r"), ("modify", "/r/.git/index")], now=1); self.assertEqual(d.ready(now=2), [("overflow", "/r")])


class Units(unittest.TestCase):
    def test_generates_a_valid_pair(self):
        u = W.make_units("backup", ["/usr/bin/rsync", "-a", "/home/me/My Docs", "/mnt/b"], "daily", "Nightly backup")
        self.assertEqual(sorted(u), ["backup.service", "backup.timer"])
        self.assertIn('ExecStart=/usr/bin/rsync -a "/home/me/My Docs" /mnt/b', u["backup.service"]); self.assertIn("Type=oneshot", u["backup.service"])
        t = u["backup.timer"]; self.assertIn("OnCalendar=daily", t); self.assertIn("Persistent=true", t); self.assertIn("WantedBy=timers.target", t); self.assertNotIn("multi-user", t)

    def test_calendar_expressions(self):
        for ok in ("daily", "Mon..Fri 09:00", "*-*-* 03:30:00", "Sat,Sun 12:00", "*-*-01 00:00", "hourly", "*:0/15"):
            try: W.validate_calendar(ok)
            except W.UnitError: self.fail(ok)
        for bad in ("every day", "tomorrow at 5", "", "25:00 pm", "daily!"):
            with self.assertRaises(W.UnitError, msg=bad): W.validate_calendar(bad)

    def test_rejects_the_mistakes_that_make_timers_never_fire(self):
        with self.assertRaisesRegex(W.UnitError, "ABSOLUTE"): W.make_units("x", ["rsync", "-a"], "daily")
        with self.assertRaises(W.UnitError): W.make_units("bad name", ["/bin/true"], "daily")
        with self.assertRaises(W.UnitError): W.make_units("x", ["/bin/true", "a\nExecStartPost=/bin/evil"], "daily")

    def test_percent_and_dollar_are_escaped_for_systemd(self):
        s = W.make_units("x", ["/bin/date", "+%Y-%m-%d", "$HOME"], "daily")["x.service"]
        self.assertIn('"+%%Y-%%m-%%d"', s); self.assertIn('"$$HOME"', s)

    def test_user_unit_directory(self):
        os.environ["XDG_CONFIG_HOME"] = "/tmp/xdgtest"; self.assertEqual(W.unit_dir(True), "/tmp/xdgtest/systemd/user"); self.assertEqual(W.unit_dir(False), "/etc/systemd/system")


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Linux only (inotify, systemd). The sandbox test exercises real inotify on a tmpfs", "inotify does not report changes made by other hosts on NFS and misses some FUSE mounts: use the polling fallback there", "Calendar validation is structural; `systemd-analyze calendar` is authoritative"], compat={"python": ">=3.9"},
   tags=["inotify", "file-watch", "watcher", "fsnotify", "watchdog", "file-changes", "debounce", "systemd", "timer", "systemd-timer", "cron", "oncalendar", "persistent", "service", "unit", "scheduler", "linux", "daemon", "ctypes", "recursive"],
   avoid="Polling os.stat in a loop on local disks; one callback per write event; forgetting to watch newly created directories; ignoring IN_Q_OVERFLOW; WantedBy=multi-user.target on a timer; relative ExecStart paths; cron for jobs that must run after a missed boot."),
]
