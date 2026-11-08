"""Round-4 recipes, part A (Python, `unittest`): Linux sysadmin readers and security/crypto building blocks."""
from .recipes import RC, UT

R4A = [
RC("py-proc-reader-processes-mem-cpu-ports", "Linux /proc readers: process list, memory, CPU %, listening TCP ports (IPv4+IPv6) with socket owners", "python",
   "Build a ps/free/top/ss-style tool or a monitoring check without psutil, and without parsing the decorative output of other commands.",
   "Read /proc directly with an injectable root so tests run on a fake tree: parse /proc/PID/stat splitting on the LAST ')' (process names may contain spaces and parentheses), cmdline NUL-split with a [comm] fallback for kernel threads, Uid from status, tolerate processes that vanish mid-scan and count permission-denied ones instead of hiding them, MemAvailable-based memory usage, CPU percent from two /proc/stat samples, and /proc/net/tcp{,6} LISTEN sockets with little-endian hex address decoding plus inode->pid owner lookup.",
   {"procinfo.py": r'''"""Process, memory, CPU and listening-port readers built on /proc. `proc` is injectable for tests."""
import os
import socket

PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8", "replace")


def parse_stat(text):
    """/proc/PID/stat. The command name sits in parentheses and may itself contain spaces or ')', so split on the LAST ')'.
    After it, field N of proc(5) is f[N-3]."""
    lp, rp = text.index("("), text.rindex(")")
    f = text[rp + 2:].split()
    return dict(pid=int(text[:lp]), comm=text[lp + 1:rp], state=f[0], ppid=int(f[1]),
                utime=int(f[11]), stime=int(f[12]), rss_pages=int(f[21]))


def list_processes(proc="/proc"):
    """-> (processes sorted by pid, number of processes we were not allowed to read). Processes that exit mid-scan are skipped."""
    out, denied = [], 0
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        d = os.path.join(proc, name)
        try:
            st = parse_stat(_read(d + "/stat"))
            cmd = [c for c in _read(d + "/cmdline").split("\0") if c]
            uid = None
            for line in _read(d + "/status").splitlines():
                if line.startswith("Uid:"):
                    uid = int(line.split()[1])
                    break
        except (FileNotFoundError, ProcessLookupError):
            continue                                   # exited while we were looking: not an error
        except PermissionError:
            denied += 1                                # say so; "nothing there" would be a lie
            continue
        st.update(cmdline=cmd or ["[%s]" % st["comm"]], uid=uid, rss_bytes=st["rss_pages"] * PAGE)
        out.append(st)
    return sorted(out, key=lambda p: p["pid"]), denied


def meminfo(proc="/proc"):
    m = {}
    for line in _read(proc + "/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            m[k] = int(v.split()[0]) * 1024
    total = m["MemTotal"]
    avail = m.get("MemAvailable", m["MemFree"] + m.get("Buffers", 0) + m.get("Cached", 0))   # MemAvailable exists on kernels >= 3.14
    return dict(total=total, available=avail, used=total - avail, swap_used=m.get("SwapTotal", 0) - m.get("SwapFree", 0),
                percent=round(100 * (total - avail) / total, 1))


def cpu_times(proc="/proc"):
    """(busy+idle total, idle) jiffies from the aggregate cpu line. guest columns are already inside user/nice, so they are not added again."""
    v = [int(x) for x in _read(proc + "/stat").splitlines()[0].split()[1:]]
    return sum(v[:8]), v[3] + (v[4] if len(v) > 4 else 0)


def cpu_percent(a, b):
    """Utilisation between two cpu_times() samples; one sample alone says nothing."""
    dt, di = b[0] - a[0], b[1] - a[1]
    return 0.0 if dt <= 0 else round(100 * (dt - di) / dt, 1)


def _addr(hexaddr):
    ip, port = hexaddr.split(":")
    raw = bytes.fromhex(ip)
    if len(raw) == 4:                                  # kernel prints the in-memory (little-endian) word
        return socket.inet_ntop(socket.AF_INET, raw[::-1]), int(port, 16)
    return socket.inet_ntop(socket.AF_INET6, b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))), int(port, 16)


def listening(proc="/proc"):
    """LISTEN-state TCP sockets (state 0A) from tcp and tcp6, sorted by port."""
    out = []
    for fam in ("tcp", "tcp6"):
        try:
            lines = _read("%s/net/%s" % (proc, fam)).splitlines()[1:]
        except FileNotFoundError:
            continue
        for ln in lines:
            p = ln.split()
            if len(p) > 9 and p[3] == "0A":
                ip, port = _addr(p[1])
                out.append(dict(proto=fam, addr=ip, port=port, uid=int(p[7]), inode=int(p[9])))
    return sorted(out, key=lambda r: (r["port"], r["proto"]))


def socket_owners(proc="/proc"):
    """inode -> pid for every socket we may inspect. Other users' processes need root: their sockets simply stay unmapped (None), not missing."""
    owners = {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        fd = os.path.join(proc, name, "fd")
        try:
            for e in os.listdir(fd):
                try:
                    t = os.readlink(os.path.join(fd, e))
                except OSError:
                    continue
                if t.startswith("socket:["):
                    owners[int(t[8:-1])] = int(name)
        except OSError:
            continue
    return owners
''', "test_procinfo.py": r'''import os, tempfile, unittest
from unittest import mock
import procinfo as P


def stat_line(pid, comm, state="S", ppid=1, utime=0, stime=0, rss=0):
    f = ["0"] * 49                                     # fields 3..51
    f[0], f[1], f[11], f[12], f[21] = state, str(ppid), str(utime), str(stime), str(rss)
    return "%d (%s) %s" % (pid, comm, " ".join(f))


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh: fh.write(text)


def fake_proc():
    root = tempfile.mkdtemp()
    put(root + "/42/stat", stat_line(42, "my (weird) app", "R", 1, 150, 50, 256))
    put(root + "/42/cmdline", "/usr/bin/app\0--flag\0value with space\0")
    put(root + "/42/status", "Name:\tapp\nUid:\t1000\t1000\t1000\t1000\n")
    put(root + "/2/stat", stat_line(2, "kthreadd", "S", 0)); put(root + "/2/cmdline", ""); put(root + "/2/status", "Uid:\t0\t0\t0\t0\n")
    put(root + "/99/cmdline", "x")                      # no stat: exited while we were scanning
    put(root + "/self/stat", "ignored"); put(root + "/cpuinfo", "x")
    return root


class Procs(unittest.TestCase):
    def test_comm_with_spaces_and_parens(self):
        s = P.parse_stat(stat_line(7, "a) b (c", "Z", 3, 11, 22, 5))
        self.assertEqual((s["comm"], s["state"], s["ppid"], s["utime"], s["stime"], s["rss_pages"]), ("a) b (c", "Z", 3, 11, 22, 5))

    def test_list_processes(self):
        procs, denied = P.list_processes(fake_proc())
        self.assertEqual([p["pid"] for p in procs], [2, 42]); self.assertEqual(denied, 0)
        app = procs[1]
        self.assertEqual(app["cmdline"], ["/usr/bin/app", "--flag", "value with space"]); self.assertEqual((app["uid"], app["comm"]), (1000, "my (weird) app"))
        self.assertEqual(app["rss_bytes"], 256 * P.PAGE); self.assertEqual(procs[0]["cmdline"], ["[kthreadd]"])    # kernel thread fallback

    def test_permission_denied_is_counted_not_hidden(self):
        root = fake_proc(); real = P._read
        def deny(path):
            if "/42/" in path: raise PermissionError(path)
            return real(path)
        with mock.patch.object(P, "_read", deny):
            procs, denied = P.list_processes(root)
        self.assertEqual(([p["pid"] for p in procs], denied), ([2], 1))

    def test_meminfo_uses_available(self):
        root = tempfile.mkdtemp()
        put(root + "/meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nSwapTotal: 50 kB\nSwapFree: 20 kB\n")
        m = P.meminfo(root); self.assertEqual((m["total"], m["available"], m["used"], m["swap_used"], m["percent"]), (1024000, 409600, 614400, 30720, 60.0))

    def test_meminfo_old_kernel_fallback(self):
        root = tempfile.mkdtemp(); put(root + "/meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 100 kB\nCached: 300 kB\n")
        self.assertEqual(P.meminfo(root)["percent"], 50.0)

    def test_cpu_percent(self):
        root = tempfile.mkdtemp()
        put(root + "/stat", "cpu 100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\n"); a = P.cpu_times(root)
        put(root + "/stat", "cpu 200 0 200 750 150 0 0 0 0 0\n"); b = P.cpu_times(root)
        self.assertEqual(a, (1000, 800)); self.assertEqual(P.cpu_percent(a, b), 66.7); self.assertEqual(P.cpu_percent(a, a), 0.0)

    def test_listening_v4_v6_and_owner(self):
        root = tempfile.mkdtemp(); hdr = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        put(root + "/net/tcp", hdr + "   0: 0100007F:0BB8 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 5551 1 x\n"
                                   "   1: 0100007F:9C40 0100007F:0050 01 00000000:00000000 00:00000000 00000000  1000        0 5552 1 x\n")
        put(root + "/net/tcp6", hdr + "   0: 00000000000000000000000001000000:1538 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 5553 1 x\n")
        socks = P.listening(root)
        self.assertEqual([(s["proto"], s["addr"], s["port"]) for s in socks], [("tcp", "127.0.0.1", 3000), ("tcp6", "::1", 5432)])   # ESTABLISHED row excluded
        os.makedirs(root + "/42/fd"); os.symlink("socket:[5551]", root + "/42/fd/3"); os.symlink("/dev/null", root + "/42/fd/0")
        owners = P.socket_owners(root); self.assertEqual(owners.get(5551), 42); self.assertIsNone(owners.get(5553))

    @unittest.skipUnless(os.path.isdir("/proc/self"), "needs a real /proc")
    def test_real_proc_smoke(self):
        procs, _ = P.list_processes(); self.assertIn(os.getpid(), [p["pid"] for p in procs])
        self.assertTrue(0 < P.meminfo()["percent"] <= 100)


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Linux /proc layout (kernel >= 3.14 for MemAvailable); udp/unix sockets and per-process CPU% over time are left out", "Seeing other users' socket owners needs root; unreadable ones are reported as unmapped", "psutil is the portable, battle-tested alternative when you do not mind a dependency"], compat={"python": ">=3.9"},
   tags=["linux", "proc", "ps", "process-list", "memory", "free", "cpu", "top", "ports", "ss", "listening", "sysadmin", "monitoring", "sockets", "ipv6"],
   avoid="Splitting /proc/PID/stat on whitespace (breaks on names with spaces); reading a pid's files twice (it can exit between reads); treating PermissionError as 'no such process'; summing guest CPU columns twice."),

RC("py-sysadmin-passwd-journal-rotate-watch", "Sysadmin toolkit: /etc/passwd+group parsing, journalctl JSON, disk alerts, log rotation with gzip, poll-based directory watching", "python",
   "The small chores every ops script repeats: who are the users, what did a unit log, is a disk filling up, rotate this log, what changed in this folder.",
   "Colon-file parsers that report malformed lines instead of crashing, `journalctl -o json` consumed through an injected runner (argument list, never a shell) with MESSAGE byte-array and priority handling, disk-usage thresholds with an injectable usage function, shift-and-gzip log rotation that keeps N generations and the file mode, and a portable snapshot/diff directory watcher (inotify-free, works anywhere, testable).",
   {"admin.py": r'''"""Sysadmin helpers. Pure functions plus thin wrappers with injectable I/O so they can be tested without root."""
import datetime, gzip, json, os, shutil, subprocess

PRIORITY = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]


def _rows(text, nfields):
    rows, bad = [], []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        p = line.split(":")
        if len(p) != nfields:
            bad.append((n, line))
        else:
            rows.append(p)
    return rows, bad


def parse_passwd(text):
    """-> (users, malformed lines). name:x:uid:gid:gecos:home:shell"""
    rows, bad = _rows(text, 7)
    return [dict(name=r[0], uid=int(r[2]), gid=int(r[3]), gecos=r[4], home=r[5], shell=r[6]) for r in rows], bad


def parse_group(text):
    rows, bad = _rows(text, 4)
    return [dict(name=r[0], gid=int(r[2]), members=[m for m in r[3].split(",") if m]) for r in rows], bad


def users_in_group(users, groups, group):
    """Supplementary members plus users whose PRIMARY gid is the group (those are not listed in /etc/group)."""
    g = next((g for g in groups if g["name"] == group), None)
    if g is None:
        return None
    names = set(g["members"]) | {u["name"] for u in users if u["gid"] == g["gid"]}
    return sorted(names)


def login_users(users):
    return [u["name"] for u in users if u["uid"] >= 1000 and not u["shell"].endswith(("nologin", "false"))]


def parse_journal_json(lines):
    """Entries from `journalctl -o json`. MESSAGE can be a list of byte values for binary output. -> (entries, number of unparsable lines)."""
    out, bad = [], 0
    for ln in lines:
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            bad += 1
            continue
        msg = e.get("MESSAGE", "")
        if isinstance(msg, list):
            msg = bytes(msg).decode("utf-8", "replace")
        ts = datetime.datetime.fromtimestamp(int(e.get("__REALTIME_TIMESTAMP", 0)) / 1e6, datetime.timezone.utc)
        pri = int(e.get("PRIORITY", 6))
        out.append(dict(time=ts, priority=pri, level=PRIORITY[pri] if 0 <= pri < 8 else "?", unit=e.get("_SYSTEMD_UNIT") or e.get("SYSLOG_IDENTIFIER", ""), message=msg))
    return out, bad


def read_journal(unit=None, since=None, priority=None, lines=None, run=subprocess.run):
    cmd = ["journalctl", "-o", "json", "--no-pager"]
    if unit: cmd += ["-u", unit]
    if since: cmd += ["--since", since]
    if priority is not None: cmd += ["-p", str(priority)]
    if lines: cmd += ["-n", str(lines)]
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("journalctl failed (%d): %s - are you in the systemd-journal group?" % (r.returncode, r.stderr.strip()))
    return parse_journal_json(r.stdout.splitlines())[0]


def disk_alerts(paths, threshold=90.0, usage=shutil.disk_usage):
    """Paths at or over `threshold` percent used. Unreadable paths are reported, never skipped silently."""
    out = []
    for p in paths:
        try:
            u = usage(p)
        except OSError as e:
            out.append(dict(path=p, error=str(e)))
            continue
        pct = round(100 * u.used / u.total, 1) if u.total else 0.0
        if pct >= threshold:
            out.append(dict(path=p, percent=pct, free=u.free))
    return out


def rotate(path, keep=5, compress=True):
    """app.log -> app.log.1[.gz] -> .2 ... ; generation `keep` is deleted. A fresh empty file keeps the old mode.
    Writers must reopen the file afterwards (SIGHUP / logging.WatchedFileHandler), exactly like logrotate's default."""
    suffix = ".gz" if compress else ""
    mode = os.stat(path).st_mode & 0o7777
    try:
        os.unlink("%s.%d%s" % (path, keep, suffix))
    except FileNotFoundError:
        pass
    for i in range(keep - 1, 0, -1):
        src = "%s.%d%s" % (path, i, suffix)
        if os.path.exists(src):
            os.replace(src, "%s.%d%s" % (path, i + 1, suffix))
    first = "%s.1" % path
    os.replace(path, first)
    if compress:
        with open(first, "rb") as s, gzip.open(first + ".gz.tmp", "wb") as d:
            shutil.copyfileobj(s, d)
        os.replace(first + ".gz.tmp", first + ".gz")
        os.unlink(first)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    os.close(fd)
    os.chmod(path, mode)


def snapshot(root):
    snap = {}
    for dp, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dp, f)
            try:
                st = os.stat(full)
            except FileNotFoundError:
                continue                               # deleted between listing and stat
            snap[os.path.relpath(full, root)] = (st.st_mtime_ns, st.st_size)
    return snap


def diff(old, new):
    return dict(created=sorted(set(new) - set(old)), deleted=sorted(set(old) - set(new)),
                modified=sorted(p for p in new if p in old and new[p] != old[p]))


def watch(root, on_change, interval=1.0, sleep=None, stop=lambda: False):
    import time
    sleep = sleep or time.sleep
    prev = snapshot(root)
    while not stop():
        sleep(interval)
        cur = snapshot(root)
        d = diff(prev, cur)
        if any(d.values()):
            on_change(d)
        prev = cur
''', "test_admin.py": r'''import gzip, os, stat, subprocess, tempfile, unittest, collections
import admin as A

PASSWD = "# comment\nroot:x:0:0:root:/root:/bin/bash\nalice:x:1000:1000:Alice,,,:/home/alice:/bin/fish\nbot:x:1001:1001::/var/bot:/usr/bin/nologin\nbroken line\n\n"
GROUP = "wheel:x:998:alice\ndev:x:1001:\nbad\n"


class Accounts(unittest.TestCase):
    def test_passwd_and_malformed(self):
        users, bad = A.parse_passwd(PASSWD)
        self.assertEqual([u["name"] for u in users], ["root", "alice", "bot"]); self.assertEqual(bad, [(5, "broken line")])
        self.assertEqual(A.login_users(users), ["alice"])

    def test_group_members_include_primary_gid(self):
        users, _ = A.parse_passwd(PASSWD); groups, bad = A.parse_group(GROUP)
        self.assertEqual(len(bad), 1)
        self.assertEqual(A.users_in_group(users, groups, "wheel"), ["alice"]); self.assertEqual(A.users_in_group(users, groups, "dev"), ["bot"])   # bot is only in via primary gid
        self.assertIsNone(A.users_in_group(users, groups, "nope"))


class Journal(unittest.TestCase):
    LINES = ['{"__REALTIME_TIMESTAMP":"1700000000000000","PRIORITY":"3","_SYSTEMD_UNIT":"nginx.service","MESSAGE":"boom"}',
             'not json', '{"__REALTIME_TIMESTAMP":"1700000001500000","SYSLOG_IDENTIFIER":"sshd","MESSAGE":[104,105]}']

    def test_parse(self):
        e, bad = A.parse_journal_json(self.LINES)
        self.assertEqual(bad, 1); self.assertEqual((e[0]["level"], e[0]["unit"], e[0]["message"]), ("err", "nginx.service", "boom"))
        self.assertEqual((e[1]["message"], e[1]["level"], e[1]["unit"]), ("hi", "info", "sshd")); self.assertEqual(e[0]["time"].isoformat(), "2023-11-14T22:13:20+00:00")

    def test_runner_gets_argument_list_not_shell(self):
        seen = []
        def run(cmd, **kw): seen.append(cmd); return subprocess.CompletedProcess(cmd, 0, "\n".join(self.LINES), "")
        A.read_journal(unit="a; rm -rf /", priority=3, lines=5, run=run)
        self.assertEqual(seen[0], ["journalctl", "-o", "json", "--no-pager", "-u", "a; rm -rf /", "-p", "3", "-n", "5"])

    def test_failure_is_explained(self):
        with self.assertRaisesRegex(RuntimeError, "systemd-journal group"):
            A.read_journal(run=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "no access"))


class Disk(unittest.TestCase):
    def test_threshold_and_errors(self):
        U = collections.namedtuple("U", "total used free")
        def usage(p):
            if p == "/gone": raise FileNotFoundError("/gone")
            return {"/": U(100, 95, 5), "/home": U(100, 50, 50)}[p]
        r = A.disk_alerts(["/", "/home", "/gone"], 90, usage)
        self.assertEqual(r[0], dict(path="/", percent=95.0, free=5)); self.assertEqual(len(r), 2); self.assertIn("error", r[1])


class Rotate(unittest.TestCase):
    def test_keep_n_generations_newest_first_and_mode(self):
        d = tempfile.mkdtemp(); p = d + "/app.log"
        for gen in range(1, 6):
            with open(p, "w") as f: f.write("gen%d\n" % gen)
            os.chmod(p, 0o640); A.rotate(p, keep=3)
        self.assertEqual(sorted(os.listdir(d)), ["app.log", "app.log.1.gz", "app.log.2.gz", "app.log.3.gz"])
        self.assertEqual(gzip.open(p + ".1.gz", "rt").read(), "gen5\n"); self.assertEqual(gzip.open(p + ".3.gz", "rt").read(), "gen3\n")
        self.assertEqual((os.path.getsize(p), stat.S_IMODE(os.stat(p).st_mode)), (0, 0o640))

    def test_uncompressed(self):
        d = tempfile.mkdtemp(); p = d + "/x.log"; open(p, "w").write("a"); A.rotate(p, keep=2, compress=False); open(p, "w").write("b"); A.rotate(p, keep=2, compress=False)
        self.assertEqual((open(p + ".1").read(), open(p + ".2").read()), ("b", "a"))


class Watch(unittest.TestCase):
    def test_diff_created_modified_deleted(self):
        d = tempfile.mkdtemp(); os.makedirs(d + "/sub")
        for n in ("a.txt", "sub/b.txt", "gone.txt"): open(os.path.join(d, n), "w").write("1")
        before = A.snapshot(d)
        open(d + "/new.txt", "w").write("x"); open(d + "/a.txt", "w").write("22"); os.unlink(d + "/gone.txt")
        self.assertEqual(A.diff(before, A.snapshot(d)), dict(created=["new.txt"], deleted=["gone.txt"], modified=["a.txt"]))

    def test_watch_loop_reports_only_changes(self):
        d = tempfile.mkdtemp(); events = []; ticks = {"n": 0}
        def sleep(_):
            ticks["n"] += 1
            if ticks["n"] == 2: open(d + "/f", "w").write("hello")
        A.watch(d, events.append, sleep=sleep, stop=lambda: ticks["n"] >= 3)
        self.assertEqual(events, [dict(created=["f"], deleted=[], modified=[])])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Polling watcher: fine for dozens-to-thousands of files and a 1s interval; use inotify (watchdog/inotify_simple) for instant events on huge trees", "journalctl needs membership of systemd-journal (or root) to see other users' units", "rotate() is rename-based like logrotate's default: the writer must reopen the file"], compat={"python": ">=3.9"},
   tags=["linux", "sysadmin", "passwd", "group", "users", "journalctl", "systemd", "journal", "logs", "disk-usage", "df", "alert", "log-rotation", "logrotate", "gzip", "watch", "inotify", "file-watcher", "directory"],
   avoid="Shelling out with an f-string for journalctl; copy-truncate rotation that loses lines written during the copy; assuming primary-group members are listed in /etc/group; ignoring files that vanish between listdir and stat."),

RC("py-security-hash-token-hmac-totp", "Stdlib security primitives: scrypt password hashes, constant-time compare, secure tokens, signed webhooks, TOTP (RFC 6238), safe paths and SQL", "python",
   "Do the everyday security chores correctly without rolling your own crypto: store passwords, compare secrets, issue tokens, verify webhook signatures, add 2FA, and refuse path traversal and SQL injection.",
   "hashlib.scrypt with parameters embedded in the stored string (so cost can be raised later and old hashes still verify) plus needs_rehash; hmac.compare_digest for every secret comparison; secrets-based URL-safe tokens stored only as SHA-256 hashes; timestamped HMAC webhook signatures with replay tolerance and multi-signature support for key rotation; RFC 4226/6238 HOTP/TOTP checked against the published test vectors with window and replay protection; safe_join against `../` and symlink escapes; and parameterised SQL shown with a classic injection string.",
   {"secure.py": r'''"""Security building blocks using only the standard library."""
import base64, hashlib, hmac, os, secrets, struct, sys, time, urllib.parse

# ---- passwords -------------------------------------------------------------------------------
N, R, P = 2 ** 15, 8, 1


def _scrypt(pw, salt, n, r, p):
    return hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * n * r)


def hash_password(pw, n=N, r=R, p=P):
    salt = os.urandom(16)
    b = lambda x: base64.b64encode(x).decode()
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, b(salt), b(_scrypt(pw, salt, n, r, p)))


def verify_password(pw, stored):
    """False for a wrong password AND for a malformed record - never raises on bad stored data."""
    try:
        alg, n, r, p, salt, want = stored.split("$")
        if alg != "scrypt":
            return False
        got = _scrypt(pw, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError, AttributeError):
        return False


def needs_rehash(stored, n=N, r=R, p=P):
    try:
        _, sn, sr, sp, _, _ = stored.split("$")
        return (int(sn), int(sr), int(sp)) != (n, r, p)
    except ValueError:
        return True


# ---- comparison and tokens ------------------------------------------------------------------
def same_secret(a, b):
    """Constant-time equality for str or bytes (compare_digest rejects non-ASCII str, so encode first)."""
    a = a.encode() if isinstance(a, str) else a
    b = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(a, b)


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


def token_digest(token):
    """Store this, not the token. High-entropy tokens do not need a slow hash; the DB leak is then useless to an attacker."""
    return hashlib.sha256(token.encode()).hexdigest()


# ---- signed webhooks ----------------------------------------------------------------------------
class SignatureError(Exception):
    pass


def sign(secret, body, t=None):
    t = int(time.time()) if t is None else int(t)
    mac = hmac.new(secret.encode(), b"%d." % t + body, hashlib.sha256).hexdigest()
    return "t=%d,v1=%s" % (t, mac)


def verify(secret, body, header, tolerance=300, now=None):
    """Raises SignatureError with a reason; any one of several v1= signatures may match (secret rotation)."""
    try:
        parts = [kv.split("=", 1) for kv in header.split(",")]
        t = int(next(v for k, v in parts if k == "t"))
    except (StopIteration, ValueError):
        raise SignatureError("malformed header")
    sigs = [v for k, v in parts if k == "v1"]
    if abs((time.time() if now is None else now) - t) > tolerance:
        raise SignatureError("timestamp outside tolerance (replay?)")
    want = hmac.new(secret.encode(), b"%d." % t + body, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(want, s) for s in sigs):
        raise SignatureError("bad signature")
    return True


# ---- TOTP (RFC 4226 / 6238) ---------------------------------------------------------------------
def hotp(secret, counter, digits=6, algo="sha1"):
    h = hmac.new(secret, struct.pack(">Q", counter), algo).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def totp(secret, t=None, step=30, digits=6, algo="sha1"):
    return hotp(secret, int((time.time() if t is None else t) // step), digits, algo)


def new_totp_secret():
    return base64.b32encode(os.urandom(20)).decode()


def decode_secret(b32):
    s = b32.replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def verify_totp(b32, code, t=None, window=1, step=30, last_counter=-1):
    """-> matching counter (store it as last_counter to stop code replay) or None. Accepts +-window steps for clock skew."""
    key, now = decode_secret(b32), int((time.time() if t is None else t) // step)
    code = code.strip()
    for c in range(now - window, now + window + 1):
        if c > last_counter and hmac.compare_digest(hotp(key, c), code):
            return c
    return None


def provisioning_uri(b32, account, issuer):
    return "otpauth://totp/%s:%s?secret=%s&issuer=%s" % (urllib.parse.quote(issuer), urllib.parse.quote(account), b32, urllib.parse.quote(issuer))


# ---- untrusted input ------------------------------------------------------------------------------
def safe_join(base, *parts):
    """Join untrusted path parts under base. Rejects NUL, '..' escapes and symlinks that leave base (resolved before comparing)."""
    if any("\0" in p for p in parts):
        raise ValueError("NUL in path")
    root = os.path.realpath(base)
    full = os.path.realpath(os.path.join(root, *parts))
    if os.path.commonpath([root, full]) != root:
        raise ValueError("path escapes base directory")
    return full
''', "test_secure.py": r'''import hashlib, os, sqlite3, tempfile, unittest
import secure as S


class Passwords(unittest.TestCase):
    def test_round_trip_and_unique_salts(self):
        h1, h2 = S.hash_password("hunter2", n=2 ** 10), S.hash_password("hunter2", n=2 ** 10)
        self.assertNotEqual(h1, h2); self.assertTrue(S.verify_password("hunter2", h1)); self.assertFalse(S.verify_password("hunter3", h1))

    def test_default_cost_works(self):
        self.assertTrue(S.verify_password("pw", S.hash_password("pw")))

    def test_malformed_records_are_just_false(self):
        for bad in ("", "x", "scrypt$1$2", "bcrypt$1$1$1$AA$AA", "scrypt$a$b$c$d$e", None): self.assertFalse(S.verify_password("pw", bad))

    def test_needs_rehash_after_raising_cost(self):
        old = S.hash_password("pw", n=2 ** 10); self.assertTrue(S.needs_rehash(old)); self.assertFalse(S.needs_rehash(S.hash_password("pw", n=S.N)))
        self.assertTrue(S.verify_password("pw", old))          # old hashes keep verifying
        self.assertTrue(S.needs_rehash("garbage"))

    def test_unicode_password(self):
        h = S.hash_password("pässwörd✓", n=2 ** 10); self.assertTrue(S.verify_password("pässwörd✓", h))


class Tokens(unittest.TestCase):
    def test_same_secret(self):
        self.assertTrue(S.same_secret("abc", b"abc")); self.assertFalse(S.same_secret("abc", "abd")); self.assertTrue(S.same_secret("é", "é"))

    def test_tokens_unique_and_digest_stable(self):
        a, b = S.new_token(), S.new_token(); self.assertNotEqual(a, b); self.assertGreaterEqual(len(a), 40)
        self.assertEqual(S.token_digest(a), hashlib.sha256(a.encode()).hexdigest()); self.assertNotIn(a, S.token_digest(a))


class Webhooks(unittest.TestCase):
    body = b'{"id":1}'

    def test_valid(self):
        h = S.sign("whsec", self.body, t=1000); self.assertTrue(S.verify("whsec", self.body, h, now=1100))

    def test_tampered_body_wrong_secret_and_replay(self):
        h = S.sign("whsec", self.body, t=1000)
        for args, msg in (((("whsec", b'{"id":2}', h)), "bad signature"), (("other", self.body, h), "bad signature")):
            with self.assertRaisesRegex(S.SignatureError, msg): S.verify(*args, now=1000)
        with self.assertRaisesRegex(S.SignatureError, "tolerance"): S.verify("whsec", self.body, h, now=1000 + 301)
        with self.assertRaisesRegex(S.SignatureError, "tolerance"): S.verify("whsec", self.body, h, now=1000 - 301)

    def test_rotation_second_signature_matches(self):
        h = S.sign("old", self.body, t=1000) + "," + S.sign("new", self.body, t=1000).split(",")[1]
        self.assertTrue(S.verify("new", self.body, h, now=1000)); self.assertTrue(S.verify("old", self.body, h, now=1000))

    def test_garbage_header(self):
        for bad in ("", "nonsense", "t=abc,v1=ff", "v1=ff"):
            with self.assertRaisesRegex(S.SignatureError, "malformed"): S.verify("s", b"", bad)


class Totp(unittest.TestCase):
    K20 = b"12345678901234567890"

    def test_rfc4226_vectors(self):
        self.assertEqual([S.hotp(self.K20, i) for i in range(5)], ["755224", "287082", "359152", "969429", "338314"])

    def test_rfc6238_vectors(self):
        for t, want in ((59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"), (1234567890, "89005924"), (2000000000, "69279037")):
            self.assertEqual(S.totp(self.K20, t, digits=8), want)
        self.assertEqual(S.totp(b"12345678901234567890123456789012", 59, digits=8, algo="sha256"), "46119246")

    def test_verify_window_and_replay(self):
        sec = S.new_totp_secret(); key = S.decode_secret(sec); t = 1_700_000_000
        code = S.totp(key, t); c = S.verify_totp(sec, code, t=t); self.assertEqual(c, t // 30)
        self.assertIsNone(S.verify_totp(sec, code, t=t, last_counter=c))         # replay of the same code
        self.assertEqual(S.verify_totp(sec, code, t=t + 30), t // 30)             # one step of clock skew is tolerated
        self.assertIsNone(S.verify_totp(sec, code, t=t + 90)); self.assertIsNone(S.verify_totp(sec, "000000", t=t) if code != "000000" else None)

    def test_secret_formats(self):
        sec = S.new_totp_secret(); self.assertEqual(len(sec), 32)
        spaced = " ".join(sec[i:i + 4] for i in range(0, 32, 4)).lower(); self.assertEqual(S.decode_secret(spaced), S.decode_secret(sec))
        self.assertTrue(S.provisioning_uri(sec, "a@b.c", "My App").startswith("otpauth://totp/My%20App:a%40b.c?secret="))


class Untrusted(unittest.TestCase):
    def test_safe_join(self):
        base = tempfile.mkdtemp(); os.makedirs(base + "/sub")
        self.assertEqual(S.safe_join(base, "sub", "f.txt"), os.path.realpath(base) + "/sub/f.txt")
        for bad in (("..", "etc", "passwd"), ("/etc/passwd",), ("sub", "..", "..", "x"), ("a\0b",)):
            with self.assertRaises(ValueError): S.safe_join(base, *bad)

    def test_symlink_escape_is_blocked(self):
        base = tempfile.mkdtemp(); outside = tempfile.mkdtemp(); os.symlink(outside, base + "/link")
        with self.assertRaises(ValueError): S.safe_join(base, "link", "secret.txt")

    def test_parameterised_sql_stores_injection_as_data(self):
        db = sqlite3.connect(":memory:"); db.execute("create table u(name text)")
        evil = "x'); drop table u; --"
        db.execute("insert into u values (?)", (evil,))
        self.assertEqual(db.execute("select name from u").fetchall(), [(evil,)])


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["scrypt N=2**15 r=8 is a reasonable interactive default (~50-100 ms, 32 MiB); tune upward as hardware allows and let needs_rehash migrate users on login", "Webhook scheme is Stripe-style (timestamp + HMAC-SHA256); follow your provider's exact signing string if it differs", "argon2id (argon2-cffi) is the OWASP first choice when a dependency is acceptable; scrypt is the strongest option in the stdlib"], compat={"python": ">=3.9"},
   tags=["security", "password", "hashing", "scrypt", "argon2", "bcrypt", "constant-time", "timing-attack", "compare-digest", "token", "secrets", "random", "hmac", "webhook", "signature", "totp", "2fa", "otp", "path-traversal", "sql-injection", "sanitize", "injection", "validate"],
   avoid="Comparing secrets with ==; md5/sha256 for passwords; random.random() for tokens; storing raw API tokens; verifying a webhook without a timestamp; building SQL or file paths from user strings."),

RC("py-crypto-aesgcm-fernet-encrypt", "Encrypt and decrypt data with a passphrase or key: AES-256-GCM with scrypt, tamper detection, Fernet and key rotation", "python",
   "Encrypt a file, config value or database field at rest and get a clear failure (not garbage) when the key is wrong or the data was altered.",
   "Use the `cryptography` package's AEAD, never a home-made cipher: AES-256-GCM with a fresh random 96-bit nonce per message, a self-describing header (magic, scrypt cost, salt, nonce) so the format can evolve, associated data binding the ciphertext to its context, one DecryptError for wrong key / tampering / truncation, plus Fernet with MultiFernet for painless key rotation.",
   {"vault.py": r'''"""Authenticated encryption with the `cryptography` package (pip install cryptography)."""
import os
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"AG1"
SALT, NONCE = 16, 12


class DecryptError(Exception):
    """Wrong passphrase/key, modified data, wrong context, or not our format. Deliberately one error: do not tell an attacker which."""


def _key(passphrase, salt, log2n):
    return Scrypt(salt=salt, length=32, n=2 ** log2n, r=8, p=1).derive(passphrase.encode())


def encrypt(passphrase, data, aad=b"", log2n=15):
    salt, nonce = os.urandom(SALT), os.urandom(NONCE)
    header = MAGIC + bytes([log2n]) + salt + nonce
    return header + AESGCM(_key(passphrase, salt, log2n)).encrypt(nonce, data, MAGIC + aad)


def decrypt(passphrase, blob, aad=b"", max_log2n=20):
    hl = len(MAGIC) + 1 + SALT + NONCE
    if len(blob) < hl + 16 or not blob.startswith(MAGIC):
        raise DecryptError("not an encrypted blob")
    log2n = blob[3]
    if not 10 <= log2n <= max_log2n:                           # an attacker-chosen cost must not become a memory DoS
        raise DecryptError("unsupported cost parameter")
    salt, nonce = blob[4:4 + SALT], blob[4 + SALT:hl]
    try:
        return AESGCM(_key(passphrase, salt, log2n)).decrypt(nonce, blob[hl:], MAGIC + aad)
    except InvalidTag:
        raise DecryptError("wrong passphrase, wrong context, or data was modified") from None


def encrypt_with_key(key32, data, aad=b""):
    """Raw 32-byte key (from a KMS / env secret). Random 96-bit nonces are safe up to ~2**32 messages per key; rotate before that."""
    nonce = os.urandom(NONCE)
    return nonce + AESGCM(key32).encrypt(nonce, data, aad)


def decrypt_with_key(key32, blob, aad=b""):
    if len(blob) < NONCE + 16:
        raise DecryptError("truncated")
    try:
        return AESGCM(key32).decrypt(blob[:NONCE], blob[NONCE:], aad)
    except InvalidTag:
        raise DecryptError("wrong key or data was modified") from None


def new_key():
    return AESGCM.generate_key(bit_length=256)


# Fernet = AES-128-CBC + HMAC with timestamp, URL-safe base64: simplest choice for small values (cookies, DB fields).
def fernet_encrypt(keys, text):
    """`keys` newest first. Encrypts with keys[0]."""
    return MultiFernet([Fernet(k) for k in keys]).encrypt(text.encode())


def fernet_decrypt(keys, token, ttl=None):
    try:
        return MultiFernet([Fernet(k) for k in keys]).decrypt(token, ttl=ttl).decode()
    except InvalidToken:
        raise DecryptError("invalid or expired token") from None


def fernet_rotate(keys, token):
    """Re-encrypt under keys[0] without exposing plaintext to the caller. Old keys can be dropped once every token is rotated."""
    try:
        return MultiFernet([Fernet(k) for k in keys]).rotate(token)
    except InvalidToken:
        raise DecryptError("invalid token") from None
''', "test_vault.py": r'''import unittest
import vault as V

FAST = 12


class Passphrase(unittest.TestCase):
    def test_round_trip_binary_and_empty(self):
        for data in (b"hello", b"", bytes(range(256)) * 50):
            self.assertEqual(V.decrypt("pw", V.encrypt("pw", data, log2n=FAST)), data)

    def test_nonce_and_salt_are_fresh(self):
        a, b = V.encrypt("pw", b"same", log2n=FAST), V.encrypt("pw", b"same", log2n=FAST); self.assertNotEqual(a, b)

    def test_wrong_passphrase_tamper_truncation_and_context(self):
        blob = V.encrypt("pw", b"secret", aad=b"user:7", log2n=FAST)
        with self.assertRaises(V.DecryptError): V.decrypt("nope", blob, aad=b"user:7")
        with self.assertRaises(V.DecryptError): V.decrypt("pw", blob, aad=b"user:8")             # ciphertext moved to another record
        flipped = bytearray(blob); flipped[-5] ^= 1
        with self.assertRaises(V.DecryptError): V.decrypt("pw", bytes(flipped), aad=b"user:7")
        for cut in (0, 3, 20, len(blob) - 1):
            with self.assertRaises(V.DecryptError): V.decrypt("pw", blob[:cut], aad=b"user:7")
        self.assertEqual(V.decrypt("pw", blob, aad=b"user:7"), b"secret")

    def test_header_tamper_changes_key_and_fails(self):
        blob = bytearray(V.encrypt("pw", b"x", log2n=FAST)); blob[5] ^= 1                         # salt byte
        with self.assertRaises(V.DecryptError): V.decrypt("pw", bytes(blob))

    def test_hostile_cost_parameter_rejected_before_deriving(self):
        blob = bytearray(V.encrypt("pw", b"x", log2n=FAST)); blob[3] = 30
        with self.assertRaisesRegex(V.DecryptError, "cost"): V.decrypt("pw", bytes(blob))

    def test_not_our_format(self):
        with self.assertRaises(V.DecryptError): V.decrypt("pw", b"x" * 100)


class RawKey(unittest.TestCase):
    def test_round_trip_and_failures(self):
        k = V.new_key(); blob = V.encrypt_with_key(k, b"data", b"ctx"); self.assertEqual(V.decrypt_with_key(k, blob, b"ctx"), b"data")
        with self.assertRaises(V.DecryptError): V.decrypt_with_key(V.new_key(), blob, b"ctx")
        with self.assertRaises(V.DecryptError): V.decrypt_with_key(k, blob[:10])


class FernetRotation(unittest.TestCase):
    def test_rotation_keeps_data_readable_then_retires_old_key(self):
        from cryptography.fernet import Fernet
        old, new = Fernet.generate_key(), Fernet.generate_key()
        token = V.fernet_encrypt([old], "card on file")
        self.assertEqual(V.fernet_decrypt([new, old], token), "card on file")        # new key first, old still accepted
        moved = V.fernet_rotate([new, old], token)
        self.assertEqual(V.fernet_decrypt([new], moved), "card on file")             # old key no longer needed
        with self.assertRaises(V.DecryptError): V.fernet_decrypt([new], token)       # un-rotated token needs the old key

    def test_garbage_token(self):
        from cryptography.fernet import Fernet
        with self.assertRaises(V.DecryptError): V.fernet_decrypt([Fernet.generate_key()], b"nonsense")


if __name__ == "__main__": unittest.main()
'''}, UT, assumptions=["Requires the `cryptography` package (present in the verification sandbox)", "Whole-message AEAD: for files larger than memory (GBs) use a chunked STREAM construction - tink, age or libsodium secretstream - rather than one giant GCM message", "Key management (where the key lives, who can read it) is the hard part; this only covers the cipher layer"], compat={"python": ">=3.9"},
   tags=["encryption", "decrypt", "aes", "aes-gcm", "aead", "fernet", "cryptography", "secrets", "vault", "key-rotation", "scrypt", "passphrase", "tamper", "at-rest", "crypto"],
   avoid="ECB or unauthenticated CBC; reusing a nonce with the same key; deriving keys with a plain hash of the passphrase; telling callers whether the key or the data was wrong; rolling your own cipher out of hmac."),
]
