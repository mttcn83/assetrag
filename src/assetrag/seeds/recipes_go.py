"""Go building blocks for Linux CLI/TUI tools (stdlib only). Every recipe's tests run in the sandbox at seed time."""
from .recipes import GOT, RC

GO_SYSTEMS = [
RC("go-atomic-write", "Atomic file write with fsync and rename (Go)", "go",
   "Replacing a config/state file without ever leaving a half-written or empty file after a crash or power loss.",
   "Write to a temp file in the same directory, chmod, fsync the file, rename over the target, fsync the directory so the rename is durable; clean up the temp file on any error.",
   {"atomicfile.go": '''// Package atomicfile replaces files atomically: readers see the old content or the new content, never a partial file.
package atomicfile

import (
	"os"
	"path/filepath"
)

// WriteFile atomically replaces path with data. The temp file lives in the same directory so rename(2) never crosses
// filesystems. If path is a symlink the link itself is replaced, not its target.
func WriteFile(path string, data []byte, perm os.FileMode) (err error) {
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, "."+filepath.Base(path)+".tmp-*")
	if err != nil {
		return err
	}
	name := tmp.Name()
	defer func() {
		if err != nil {
			tmp.Close()
			os.Remove(name)
		}
	}()
	if _, err = tmp.Write(data); err != nil {
		return err
	}
	if err = tmp.Chmod(perm); err != nil {
		return err
	}
	if err = tmp.Sync(); err != nil {
		return err
	}
	if err = tmp.Close(); err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	return syncDir(dir)
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
''', "atomicfile_test.go": '''package atomicfile

import (
	"os"
	"path/filepath"
	"testing"
)

func leftovers(t *testing.T, dir string) []string {
	t.Helper()
	m, _ := filepath.Glob(filepath.Join(dir, ".*.tmp-*"))
	return m
}

func TestCreateAndReplace(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "cfg.json")
	if err := WriteFile(p, []byte("one"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := WriteFile(p, []byte("two"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(p)
	if string(got) != "two" {
		t.Fatalf("got %q", got)
	}
	st, _ := os.Stat(p)
	if st.Mode().Perm() != 0o600 {
		t.Fatalf("perm %v", st.Mode().Perm())
	}
	if l := leftovers(t, dir); len(l) != 0 {
		t.Fatalf("temp files left: %v", l)
	}
}

func TestMissingDirectoryFails(t *testing.T) {
	if err := WriteFile(filepath.Join(t.TempDir(), "nope", "x"), []byte("x"), 0o644); err == nil {
		t.Fatal("expected error")
	}
}

func TestFailedRenameKeepsOriginalAndCleansUp(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "target")
	if err := os.Mkdir(target, 0o755); err != nil { // renaming a file over a directory fails
		t.Fatal(err)
	}
	if err := WriteFile(target, []byte("x"), 0o644); err == nil {
		t.Fatal("expected rename error")
	}
	if st, err := os.Stat(target); err != nil || !st.IsDir() {
		t.Fatal("original was damaged")
	}
	if l := leftovers(t, dir); len(l) != 0 {
		t.Fatalf("temp files left: %v", l)
	}
}
'''}, GOT, assumptions=["Go >= 1.16 (os.CreateTemp)", "Linux/POSIX rename semantics; same filesystem for temp and target"],
   compat={"go": ">=1.16"}, tags=["atomic", "fsync", "rename", "file", "config", "durability", "linux"],
   avoid="Writing the target in place (truncate + write) or renaming without fsync: a crash can leave an empty or partial file."),

RC("go-xdg-dirs", "XDG base directories (config, cache, state, data) in Go", "go",
   "Finding per-user config/cache/state/data directories the way Linux tools are expected to, without a dependency.",
   "Honor XDG_*_HOME only when set to an absolute path (the spec says relative values must be ignored), else fall back to ~/.config, ~/.cache, ~/.local/state, ~/.local/share; getenv and home are injectable.",
   {"xdg.go": '''// Package xdg resolves XDG base directories for an application.
package xdg

import (
	"fmt"
	"os"
	"path/filepath"
)

type Paths struct {
	App    string
	Getenv func(string) string
	Home   string
}

// New uses the real environment and home directory.
func New(app string) Paths {
	home, _ := os.UserHomeDir()
	return Paths{App: app, Getenv: os.Getenv, Home: home}
}

func (p Paths) base(env, fallback string) string {
	if v := p.Getenv(env); v != "" && filepath.IsAbs(v) { // relative values are invalid per the spec
		return v
	}
	return filepath.Join(p.Home, fallback)
}

func (p Paths) Config() string { return filepath.Join(p.base("XDG_CONFIG_HOME", ".config"), p.App) }
func (p Paths) Cache() string  { return filepath.Join(p.base("XDG_CACHE_HOME", ".cache"), p.App) }
func (p Paths) State() string  { return filepath.Join(p.base("XDG_STATE_HOME", ".local/state"), p.App) }
func (p Paths) Data() string   { return filepath.Join(p.base("XDG_DATA_HOME", ".local/share"), p.App) }

// Runtime has no spec default: use XDG_RUNTIME_DIR when absolute, else a per-user directory under the temp dir.
func (p Paths) Runtime() string {
	if v := p.Getenv("XDG_RUNTIME_DIR"); v != "" && filepath.IsAbs(v) {
		return filepath.Join(v, p.App)
	}
	return filepath.Join(os.TempDir(), fmt.Sprintf("%s-%d", p.App, os.Getuid()))
}

// Ensure creates dir with 0700 (private by default) and returns it.
func Ensure(dir string) (string, error) { return dir, os.MkdirAll(dir, 0o700) }
''', "xdg_test.go": '''package xdg

import (
	"os"
	"path/filepath"
	"testing"
)

func env(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func TestDefaults(t *testing.T) {
	p := Paths{App: "tool", Getenv: env(nil), Home: "/home/u"}
	want := map[string]string{p.Config(): "/home/u/.config/tool", p.Cache(): "/home/u/.cache/tool",
		p.State(): "/home/u/.local/state/tool", p.Data(): "/home/u/.local/share/tool"}
	for got, w := range want {
		if got != w {
			t.Errorf("got %s want %s", got, w)
		}
	}
}

func TestEnvOverridesAndRelativeIgnored(t *testing.T) {
	p := Paths{App: "tool", Home: "/home/u", Getenv: env(map[string]string{"XDG_CONFIG_HOME": "/etc/x", "XDG_CACHE_HOME": "relative/dir"})}
	if p.Config() != "/etc/x/tool" {
		t.Error(p.Config())
	}
	if p.Cache() != "/home/u/.cache/tool" {
		t.Errorf("relative XDG_CACHE_HOME must be ignored, got %s", p.Cache())
	}
}

func TestRuntimeFallbackAndEnsure(t *testing.T) {
	p := Paths{App: "tool", Home: "/h", Getenv: env(map[string]string{"XDG_RUNTIME_DIR": "/run/user/1000"})}
	if p.Runtime() != "/run/user/1000/tool" {
		t.Error(p.Runtime())
	}
	q := Paths{App: "tool", Home: "/h", Getenv: env(nil)}
	if filepath.Dir(q.Runtime()) != os.TempDir() {
		t.Error(q.Runtime())
	}
	d, err := Ensure(filepath.Join(t.TempDir(), "a", "b"))
	if err != nil {
		t.Fatal(err)
	}
	st, _ := os.Stat(d)
	if st.Mode().Perm() != 0o700 {
		t.Errorf("perm %v", st.Mode().Perm())
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux/XDG Base Directory spec 0.8"], compat={"go": ">=1.16"},
   tags=["xdg", "config", "cache", "state", "directories", "linux", "cli"],
   avoid="Hard-coding ~/.config or accepting a relative XDG_CONFIG_HOME; writing state into the config directory."),

RC("go-proc-net-sockets", "Parse /proc/net/tcp(6) into listening sockets and map inodes to PIDs (Go)", "go",
   "Listing which ports are open and which process owns them without shelling out to ss/lsof.",
   "Decode the little-endian hex addresses (IPv4 and IPv6, 4-byte words reversed), keep state names, filter LISTEN, then walk /proc/<pid>/fd for socket:[inode] links; unreadable PIDs are counted, not treated as 'no owner'.",
   {"procnet.go": '''// Package procnet reads /proc/net/tcp and /proc/net/tcp6.
package procnet

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

type Socket struct {
	Local, Remote netip.AddrPort
	State         string
	UID           int
	Inode         uint64
}

var states = map[string]string{"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2",
	"06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}

// parseAddr decodes "0100007F:1F90". The kernel prints each 32-bit word in host (little-endian) order.
func parseAddr(s string) (netip.AddrPort, error) {
	h, p, ok := strings.Cut(s, ":")
	if !ok {
		return netip.AddrPort{}, fmt.Errorf("bad address %q", s)
	}
	raw, err := hex.DecodeString(h)
	if err != nil || (len(raw) != 4 && len(raw) != 16) {
		return netip.AddrPort{}, fmt.Errorf("bad host %q", h)
	}
	for i := 0; i+4 <= len(raw); i += 4 {
		raw[i], raw[i+1], raw[i+2], raw[i+3] = raw[i+3], raw[i+2], raw[i+1], raw[i]
	}
	addr, _ := netip.AddrFromSlice(raw)
	port, err := strconv.ParseUint(p, 16, 16)
	if err != nil {
		return netip.AddrPort{}, err
	}
	return netip.AddrPortFrom(addr.Unmap(), uint16(port)), nil
}

// ParseTCP parses the content of /proc/net/tcp or /proc/net/tcp6 (header line included).
func ParseTCP(r io.Reader) ([]Socket, error) {
	var out []Socket
	sc := bufio.NewScanner(r)
	sc.Scan() // header
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 10 {
			continue
		}
		l, err := parseAddr(f[1])
		if err != nil {
			return nil, err
		}
		rm, err := parseAddr(f[2])
		if err != nil {
			return nil, err
		}
		uid, _ := strconv.Atoi(f[7])
		ino, _ := strconv.ParseUint(f[9], 10, 64)
		st := states[strings.ToUpper(f[3])]
		if st == "" {
			st = f[3]
		}
		out = append(out, Socket{Local: l, Remote: rm, State: st, UID: uid, Inode: ino})
	}
	return out, sc.Err()
}

func Listening(all []Socket) []Socket {
	var out []Socket
	for _, s := range all {
		if s.State == "LISTEN" {
			out = append(out, s)
		}
	}
	return out
}

// Exposed reports whether the socket accepts connections from other machines (wildcard bind) rather than only this one.
func (s Socket) Exposed() bool { return s.Local.Addr().IsUnspecified() }

// OwnersByInode maps socket inodes to PIDs by scanning procRoot/<pid>/fd. unreadable counts PIDs whose fd directory
// could not be read (other users' processes without root): their sockets have an *unknown* owner, not no owner.
func OwnersByInode(procRoot string) (owners map[uint64]int, unreadable int, err error) {
	ents, err := os.ReadDir(procRoot)
	if err != nil {
		return nil, 0, err
	}
	owners = map[uint64]int{}
	for _, e := range ents {
		pid, perr := strconv.Atoi(e.Name())
		if perr != nil {
			continue
		}
		fdDir := filepath.Join(procRoot, e.Name(), "fd")
		fds, rerr := os.ReadDir(fdDir)
		if rerr != nil {
			if !os.IsNotExist(rerr) { // a process that exited meanwhile is not "unreadable"
				unreadable++
			}
			continue
		}
		for _, fd := range fds {
			link, lerr := os.Readlink(filepath.Join(fdDir, fd.Name()))
			if lerr != nil || !strings.HasPrefix(link, "socket:[") {
				continue
			}
			if ino, perr := strconv.ParseUint(strings.TrimSuffix(strings.TrimPrefix(link, "socket:["), "]"), 10, 64); perr == nil {
				owners[ino] = pid
			}
		}
	}
	return owners, unreadable, nil
}
''', "procnet_test.go": '''package procnet

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const tcp4 = `  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12345 1 0000000000000000 100 0 0 10 0
   1: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 222 1 0000000000000000 100 0 0 10 0
   2: 0100007F:D2A0 0100007F:1F90 01 00000000:00000000 00:00000000 00000000  1000        0 333 1 0000000000000000 20 4 30 10 -1
`

const tcp6 = `  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000001000000:0277 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 4444 1 0000000000000000 100 0 0 10 0
   1: 00000000000000000000000000000000:01BB 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000     0        0 5555 1 0000000000000000 100 0 0 10 0
`

func TestParseIPv4(t *testing.T) {
	all, err := ParseTCP(strings.NewReader(tcp4))
	if err != nil || len(all) != 3 {
		t.Fatal(err, len(all))
	}
	l := Listening(all)
	if len(l) != 2 || l[0].Local.String() != "127.0.0.1:8080" || l[0].UID != 1000 || l[0].Inode != 12345 {
		t.Fatalf("%+v", l)
	}
	if l[0].Exposed() || !l[1].Exposed() || l[1].Local.String() != "0.0.0.0:22" {
		t.Fatalf("exposure wrong: %+v", l)
	}
	if all[2].State != "ESTABLISHED" {
		t.Fatal(all[2].State)
	}
}

func TestParseIPv6(t *testing.T) {
	all, err := ParseTCP(strings.NewReader(tcp6))
	if err != nil || len(all) != 2 {
		t.Fatal(err, len(all))
	}
	if all[0].Local.String() != "[::1]:631" || all[0].Exposed() {
		t.Fatalf("%v", all[0].Local)
	}
	if all[1].Local.String() != "[::]:443" || !all[1].Exposed() {
		t.Fatalf("%v", all[1].Local)
	}
}

func TestBadAddressIsAnError(t *testing.T) {
	if _, err := ParseTCP(strings.NewReader("h\\n 0: ZZZZ:1F90 00000000:0000 0A 0:0 0:0 0 0 0 1 1\\n")); err == nil {
		t.Fatal("expected error")
	}
}

func TestOwnersByInode(t *testing.T) {
	root := t.TempDir()
	mk := func(pid, fd, target string) {
		d := filepath.Join(root, pid, "fd")
		os.MkdirAll(d, 0o755)
		os.Symlink(target, filepath.Join(d, fd))
	}
	mk("100", "3", "socket:[12345]")
	mk("100", "4", "/dev/null")
	mk("200", "5", "socket:[222]")
	os.MkdirAll(filepath.Join(root, "self"), 0o755) // non-numeric entries are skipped
	os.MkdirAll(filepath.Join(root, "300", "fd"), 0o000)
	owners, unreadable, err := OwnersByInode(root)
	if err != nil || owners[12345] != 100 || owners[222] != 200 || len(owners) != 2 {
		t.Fatal(err, owners)
	}
	if os.Geteuid() != 0 && unreadable != 1 {
		t.Fatalf("unreadable = %d, want 1", unreadable)
	}
}
'''}, GOT, assumptions=["Go >= 1.18 (net/netip)", "Linux /proc format; mapping other users' sockets needs root"], compat={"go": ">=1.18"},
   tags=["proc", "/proc/net/tcp", "sockets", "listening", "ports", "lsof", "ss", "linux", "procfs", "inode", "ipv6"],
   avoid="Parsing `ss`/`netstat` text output, or reporting a socket as ownerless when its PID was merely unreadable."),

RC("go-proc-stats", "Parse /proc/meminfo, /proc/stat CPU usage and /proc/loadavg (Go)", "go",
   "System monitoring/tuning tools need memory, CPU utilisation and load without cgo or external commands.",
   "Pure parsers over io.Reader (testable with fixtures): meminfo to a byte map, aggregate and per-core CPU jiffies, usage as the delta of two samples, loadavg fields.",
   {"procstat.go": '''// Package procstat parses a few /proc files.
package procstat

import (
	"bufio"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// ParseMeminfo returns bytes per key (the kernel reports kB; the key suffix ':' is removed).
func ParseMeminfo(r io.Reader) (map[string]uint64, error) {
	out := map[string]uint64{}
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		k, rest, ok := strings.Cut(sc.Text(), ":")
		if !ok {
			continue
		}
		f := strings.Fields(rest)
		if len(f) == 0 {
			continue
		}
		v, err := strconv.ParseUint(f[0], 10, 64)
		if err != nil {
			return nil, fmt.Errorf("meminfo %s: %w", k, err)
		}
		if len(f) > 1 && f[1] == "kB" {
			v *= 1024
		}
		out[k] = v
	}
	return out, sc.Err()
}

type CPU struct{ User, Nice, System, Idle, IOWait, IRQ, SoftIRQ, Steal uint64 }

func (c CPU) Total() uint64 { return c.User + c.Nice + c.System + c.Idle + c.IOWait + c.IRQ + c.SoftIRQ + c.Steal }
func (c CPU) Busy() uint64  { return c.Total() - c.Idle - c.IOWait }

// ParseCPU returns the aggregate "cpu" line and the per-core "cpuN" lines of /proc/stat.
func ParseCPU(r io.Reader) (total CPU, cores []CPU, err error) {
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 8 || !strings.HasPrefix(f[0], "cpu") {
			continue
		}
		var v [8]uint64
		for i := range v {
			if v[i], err = strconv.ParseUint(f[i+1], 10, 64); err != nil {
				return total, nil, err
			}
		}
		c := CPU{v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]}
		if f[0] == "cpu" {
			total = c
		} else {
			cores = append(cores, c)
		}
	}
	return total, cores, sc.Err()
}

// Usage is the busy fraction (0..1) between two samples; 0 when no time passed or the counters went backwards.
func Usage(prev, cur CPU) float64 {
	dt := cur.Total() - prev.Total()
	if cur.Total() < prev.Total() || dt == 0 {
		return 0
	}
	db := cur.Busy() - prev.Busy()
	if cur.Busy() < prev.Busy() {
		return 0
	}
	return float64(db) / float64(dt)
}

type Load struct {
	One, Five, Fifteen float64
	Running, Total     int
}

func ParseLoadavg(s string) (Load, error) {
	f := strings.Fields(s)
	if len(f) < 4 {
		return Load{}, fmt.Errorf("loadavg: %q", s)
	}
	var l Load
	var err error
	for i, p := range []*float64{&l.One, &l.Five, &l.Fifteen} {
		if *p, err = strconv.ParseFloat(f[i], 64); err != nil {
			return Load{}, err
		}
	}
	run, tot, ok := strings.Cut(f[3], "/")
	if !ok {
		return Load{}, fmt.Errorf("loadavg: %q", f[3])
	}
	if l.Running, err = strconv.Atoi(run); err != nil {
		return Load{}, err
	}
	l.Total, err = strconv.Atoi(tot)
	return l, err
}
''', "procstat_test.go": '''package procstat

import (
	"math"
	"strings"
	"testing"
)

func TestMeminfo(t *testing.T) {
	m, err := ParseMeminfo(strings.NewReader("MemTotal:       16384 kB\\nMemAvailable:    8192 kB\\nHugePages_Total:       0\\nbroken line\\n"))
	if err != nil || m["MemTotal"] != 16384*1024 || m["MemAvailable"] != 8192*1024 || m["HugePages_Total"] != 0 {
		t.Fatal(err, m)
	}
	if _, err := ParseMeminfo(strings.NewReader("MemTotal: abc kB\\n")); err == nil {
		t.Fatal("expected error")
	}
}

const stat1 = "cpu  100 0 50 800 50 0 0 0 0 0\\ncpu0 60 0 30 400 10 0 0 0 0 0\\ncpu1 40 0 20 400 40 0 0 0 0 0\\nintr 1 2 3\\n"
const stat2 = "cpu  200 0 100 900 50 0 0 0 0 0\\ncpu0 0 0 0 0 0 0 0 0 0 0\\n"

func TestCPUUsage(t *testing.T) {
	a, cores, err := ParseCPU(strings.NewReader(stat1))
	if err != nil || len(cores) != 2 || a.Total() != 1000 || a.Busy() != 150 {
		t.Fatal(err, a, cores)
	}
	b, _, _ := ParseCPU(strings.NewReader(stat2))
	// delta: busy +150 of total +250
	if u := Usage(a, b); math.Abs(u-0.6) > 1e-9 {
		t.Fatalf("usage %v", u)
	}
	if Usage(a, a) != 0 || Usage(b, a) != 0 {
		t.Fatal("zero or backwards delta must be 0")
	}
}

func TestLoadavg(t *testing.T) {
	l, err := ParseLoadavg("0.52 0.58 0.59 2/1234 98765\\n")
	if err != nil || l.One != 0.52 || l.Fifteen != 0.59 || l.Running != 2 || l.Total != 1234 {
		t.Fatal(err, l)
	}
	if _, err := ParseLoadavg("garbage"); err == nil {
		t.Fatal("expected error")
	}
}
'''}, GOT, assumptions=["Go >= 1.18", "Linux /proc formats"], compat={"go": ">=1.18"},
   tags=["proc", "meminfo", "cpu", "/proc/stat", "loadavg", "procfs", "monitoring", "linux"],
   avoid="Reading cpu counters once (they are cumulative since boot) or comparing samples without checking for counter resets."),

RC("go-pidfd-terminate", "Terminate a process safely with pidfd (SIGTERM, grace, SIGKILL) in Go", "go",
   "Killing a process the user picked without hitting an unrelated process that reused the PID.",
   "pidfd_open pins the process; an identity check runs AFTER the pidfd exists; pidfd_send_signal sends SIGTERM, select() on the pidfd waits for exit within a grace period, then SIGKILL only if still alive.",
   {"pidkill.go": '''//go:build linux && (amd64 || arm64)

// Package pidkill terminates a process through a pidfd so a recycled PID can never be signalled by mistake.
package pidkill

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	sysPidfdOpen       = 434 // same number on amd64 and arm64 (kernel >= 5.3)
	sysPidfdSendSignal = 424 // kernel >= 5.1
)

var (
	ErrGone     = errors.New("process does not exist")
	ErrMismatch = errors.New("process identity changed; not signalled")
	ErrSurvived = errors.New("process survived SIGKILL (uninterruptible state?)")
)

type Result string

const (
	Terminated Result = "terminated" // exited after SIGTERM
	Killed     Result = "killed"     // needed SIGKILL
)

func pidfdOpen(pid int) (int, error) {
	fd, _, e := syscall.Syscall(sysPidfdOpen, uintptr(pid), 0, 0)
	if e != 0 {
		if e == syscall.ESRCH {
			return -1, ErrGone
		}
		return -1, e
	}
	return int(fd), nil
}

func send(fd int, sig syscall.Signal) error {
	if _, _, e := syscall.Syscall6(sysPidfdSendSignal, uintptr(fd), uintptr(sig), 0, 0, 0, 0); e != 0 {
		if e == syscall.ESRCH {
			return ErrGone
		}
		return e
	}
	return nil
}

// exited waits until the pidfd becomes readable, which happens when the process terminates (before it is reaped).
func exited(fd int, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for {
		left := time.Until(deadline)
		if left < 0 {
			left = 0
		}
		var set syscall.FdSet
		set.Bits[fd/64] |= 1 << (uint(fd) % 64)
		tv := syscall.NsecToTimeval(left.Nanoseconds())
		n, err := syscall.Select(fd+1, &set, nil, nil, &tv)
		if err == syscall.EINTR {
			continue
		}
		return err == nil && n > 0
	}
}

// StartTime returns field 22 of /proc/<pid>/stat (clock ticks since boot): (pid, starttime) identifies a process uniquely.
func StartTime(pid int) (uint64, error) {
	b, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/stat")
	if err != nil {
		return 0, ErrGone
	}
	s := string(b)
	i := strings.LastIndexByte(s, ')') // the comm field may contain spaces and parentheses
	if i < 0 {
		return 0, fmt.Errorf("unexpected /proc stat format")
	}
	f := strings.Fields(s[i+1:])
	if len(f) < 20 {
		return 0, fmt.Errorf("short /proc stat")
	}
	return strconv.ParseUint(f[19], 10, 64)
}

// Terminate signals pid. verify (optional) runs after the pidfd is open, so it checks the very process that will be
// signalled; return false to abort. grace is how long SIGTERM gets before SIGKILL.
func Terminate(pid int, grace time.Duration, verify func() bool) (Result, error) {
	fd, err := pidfdOpen(pid)
	if err != nil {
		return "", err
	}
	defer syscall.Close(fd)
	if verify != nil && !verify() {
		return "", ErrMismatch
	}
	if err := send(fd, syscall.SIGTERM); err != nil {
		return "", err
	}
	if exited(fd, grace) {
		return Terminated, nil
	}
	if err := send(fd, syscall.SIGKILL); err != nil {
		return "", err
	}
	if exited(fd, 2*time.Second) {
		return Killed, nil
	}
	return "", ErrSurvived
}
''', "pidkill_test.go": '''//go:build linux && (amd64 || arm64)

package pidkill

import (
	"errors"
	"os/exec"
	"syscall"
	"testing"
	"time"
)

func start(t *testing.T, script string) (*exec.Cmd, chan struct{}) {
	t.Helper()
	cmd := exec.Command("sh", "-c", script)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() { cmd.Wait(); close(done) }()
	t.Cleanup(func() { cmd.Process.Kill() })
	return cmd, done
}

func TestTerminateWellBehaved(t *testing.T) {
	cmd, done := start(t, "sleep 30")
	res, err := Terminate(cmd.Process.Pid, 2*time.Second, nil)
	if err != nil || res != Terminated {
		t.Fatal(res, err)
	}
	<-done
	if ws := cmd.ProcessState.Sys().(syscall.WaitStatus); !ws.Signaled() || ws.Signal() != syscall.SIGTERM {
		t.Fatalf("expected SIGTERM, got %v", ws)
	}
}

func TestEscalatesToKill(t *testing.T) {
	cmd, done := start(t, `trap "" TERM; while :; do sleep 1; done`)
	time.Sleep(300 * time.Millisecond) // let the shell install its trap
	res, err := Terminate(cmd.Process.Pid, 300*time.Millisecond, nil)
	if err != nil || res != Killed {
		t.Fatal(res, err)
	}
	<-done
}

func TestVerifyMismatchSignalsNothing(t *testing.T) {
	cmd, _ := start(t, "sleep 30")
	st, err := StartTime(cmd.Process.Pid)
	if err != nil || st == 0 {
		t.Fatal(st, err)
	}
	if _, err := Terminate(cmd.Process.Pid, time.Second, func() bool { return false }); !errors.Is(err, ErrMismatch) {
		t.Fatal(err)
	}
	if err := cmd.Process.Signal(syscall.Signal(0)); err != nil {
		t.Fatalf("process must still be alive: %v", err)
	}
	if st2, _ := StartTime(cmd.Process.Pid); st2 != st {
		t.Fatal("start time must be stable")
	}
}

func TestGone(t *testing.T) {
	cmd, done := start(t, "exit 0")
	<-done
	if _, err := Terminate(cmd.Process.Pid, time.Second, nil); !errors.Is(err, ErrGone) {
		t.Fatal(err)
	}
}
'''}, GOT, assumptions=["Linux >= 5.3 on amd64/arm64", "Go >= 1.18", "pidfd_open may be blocked by a seccomp profile (containers)"],
   compat={"go": ">=1.18", "linux": ">=5.3"},
   tags=["pidfd", "kill", "sigterm", "sigkill", "process", "terminate", "pid-reuse", "linux", "signal"],
   avoid="kill(pid) after the user saw a listing: the PID may belong to a different process by then. Never SIGKILL first."),

RC("go-flock-single-instance", "Single instance lock with flock (Go)", "go",
   "Preventing two copies of a tool (or a helper and a TUI) from modifying the same state at once.",
   "Open a lock file and take a non-blocking exclusive flock; the kernel drops the lock when the process dies, so there are no stale locks; keep the file (deleting it races).",
   {"singleinstance.go": '''// Package singleinstance guards against running two copies at once using flock(2).
package singleinstance

import (
	"errors"
	"os"
	"syscall"
)

var ErrLocked = errors.New("another instance holds the lock")

// Acquire takes an exclusive, non-blocking lock on path (created 0600). Call release to drop it early; otherwise it
// is dropped automatically when the process exits, even after a crash or SIGKILL.
func Acquire(path string) (release func() error, err error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		f.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrLocked
		}
		return nil, err
	}
	return func() error {
		defer f.Close() // closing also releases the lock; the file is intentionally not removed
		return syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	}, nil
}
''', "singleinstance_test.go": '''package singleinstance

import (
	"errors"
	"path/filepath"
	"testing"
)

func TestSecondAcquireFailsUntilReleased(t *testing.T) {
	p := filepath.Join(t.TempDir(), "app.lock")
	rel, err := Acquire(p)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := Acquire(p); !errors.Is(err, ErrLocked) {
		t.Fatalf("want ErrLocked, got %v", err)
	}
	if err := rel(); err != nil {
		t.Fatal(err)
	}
	rel2, err := Acquire(p)
	if err != nil {
		t.Fatalf("lock must be free after release: %v", err)
	}
	rel2()
}

func TestUnwritableLockPath(t *testing.T) {
	if _, err := Acquire(filepath.Join(t.TempDir(), "missing", "x.lock")); err == nil {
		t.Fatal("expected error")
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux/BSD flock; not reliable on some network filesystems (NFS)"], compat={"go": ">=1.16"},
   tags=["flock", "lock", "lockfile", "single-instance", "file lock", "linux", "concurrency"],
   avoid="PID files (stale after a crash) or deleting the lock file on exit."),

RC("go-termcolor", "Color/TTY detection honoring NO_COLOR, CLICOLOR_FORCE and TERM=dumb (Go)", "go",
   "CLI tools that colour output must not emit escapes into pipes, files, JSON modes or dumb terminals.",
   "Decide once from the environment and a real isatty check (TCGETS ioctl; /dev/null is a character device but not a terminal), expose a Style that returns plain text when disabled, plus Strip and visible-rune counting.",
   {"termcolor.go": '''// Package termcolor decides whether to emit ANSI colour and provides tiny styling helpers.
package termcolor

import (
	"io"
	"os"
	"regexp"
	"syscall"
	"unsafe"
)

// IsTerminal asks the kernel (TCGETS). Do not use Stat().Mode()&ModeCharDevice: /dev/null passes that test.
func IsTerminal(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	var t syscall.Termios
	_, _, e := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), uintptr(syscall.TCGETS), uintptr(unsafe.Pointer(&t)))
	return e == 0
}

// Enabled follows https://no-color.org (NO_COLOR non-empty disables) and CLICOLOR_FORCE; otherwise colour only on a tty.
func Enabled(w io.Writer, getenv func(string) string) bool {
	if getenv("NO_COLOR") != "" {
		return false
	}
	if v := getenv("CLICOLOR_FORCE"); v != "" && v != "0" {
		return true
	}
	if getenv("TERM") == "dumb" {
		return false
	}
	return IsTerminal(w)
}

type Style struct{ On bool }

func (s Style) wrap(code, t string) string {
	if !s.On {
		return t
	}
	return "\\x1b[" + code + "m" + t + "\\x1b[0m"
}

func (s Style) Bold(t string) string   { return s.wrap("1", t) }
func (s Style) Dim(t string) string    { return s.wrap("2", t) }
func (s Style) Red(t string) string    { return s.wrap("31", t) }
func (s Style) Green(t string) string  { return s.wrap("32", t) }
func (s Style) Yellow(t string) string { return s.wrap("33", t) }
func (s Style) Cyan(t string) string   { return s.wrap("36", t) }

var ansi = regexp.MustCompile("\\x1b\\\\[[0-9;?]*[ -/]*[@-~]")

// Strip removes ANSI escape sequences.
func Strip(s string) string { return ansi.ReplaceAllString(s, "") }

// Runes counts visible runes (escapes ignored). For East Asian wide characters use a width-aware helper instead.
func Runes(s string) int { return len([]rune(Strip(s))) }
''', "termcolor_test.go": '''package termcolor

import (
	"bytes"
	"os"
	"testing"
)

func env(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func TestNotATerminal(t *testing.T) {
	if Enabled(&bytes.Buffer{}, env(nil)) {
		t.Fatal("a buffer is not a tty")
	}
	f, err := os.Open(os.DevNull)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if IsTerminal(f) {
		t.Fatal("/dev/null is not a terminal")
	}
}

func TestEnvPrecedence(t *testing.T) {
	var b bytes.Buffer
	if !Enabled(&b, env(map[string]string{"CLICOLOR_FORCE": "1"})) {
		t.Error("CLICOLOR_FORCE should force colour")
	}
	if Enabled(&b, env(map[string]string{"CLICOLOR_FORCE": "1", "NO_COLOR": "1"})) {
		t.Error("NO_COLOR wins over CLICOLOR_FORCE")
	}
	if !Enabled(&b, env(map[string]string{"CLICOLOR_FORCE": "1", "NO_COLOR": ""})) {
		t.Error("empty NO_COLOR must be ignored")
	}
	if Enabled(&b, env(map[string]string{"CLICOLOR_FORCE": "0"})) {
		t.Error("CLICOLOR_FORCE=0 does not force")
	}
	if Enabled(&b, env(map[string]string{"TERM": "dumb"})) {
		t.Error("TERM=dumb disables")
	}
}

func TestStyleAndStrip(t *testing.T) {
	on, off := Style{On: true}, Style{}
	if off.Red("x") != "x" {
		t.Fatal("disabled style must be plain")
	}
	s := on.Bold(on.Green("ok"))
	if s == "ok" || Strip(s) != "ok" || Runes(s) != 2 {
		t.Fatalf("%q", s)
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux (TCGETS ioctl)"], compat={"go": ">=1.16"},
   tags=["no_color", "tty", "isatty", "color", "ansi", "cli", "terminal", "linux"],
   avoid="Checking ModeCharDevice for tty detection (true for /dev/null) or colouring --json output."),

RC("go-sysctl-sysfs", "Read/write sysctl and sysfs tunables with key validation and path confinement (Go)", "go",
   "Tuning tools read and write kernel parameters (vm.swappiness, CPU governors, I/O schedulers) and must never be steerable to other files.",
   "Map sysctl keys to /proc/sys paths with a strict segment whitelist; confine sysfs paths to the /sys root after symlink evaluation; write to existing files only (no create); roots are injectable for tests.",
   {"kernelfs.go": '''// Package kernelfs reads and writes kernel tunables under /proc/sys and /sys.
package kernelfs

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

type FS struct{ ProcSys, Sys string }

func Default() FS { return FS{ProcSys: "/proc/sys", Sys: "/sys"} }

var seg = regexp.MustCompile(`^[a-z0-9_][a-z0-9_-]*$`)

// SysctlPath maps "vm.swappiness" to /proc/sys/vm/swappiness. Every segment must match [a-z0-9_-]; no dots-as-paths tricks.
func (f FS) SysctlPath(key string) (string, error) {
	parts := strings.Split(key, ".")
	for _, p := range parts {
		if !seg.MatchString(p) {
			return "", fmt.Errorf("invalid sysctl key %q", key)
		}
	}
	return filepath.Join(append([]string{f.ProcSys}, parts...)...), nil
}

func (f FS) ReadSysctl(key string) (string, error) {
	p, err := f.SysctlPath(key)
	if err != nil {
		return "", err
	}
	b, err := os.ReadFile(p)
	return strings.TrimSpace(string(b)), err
}

func (f FS) WriteSysctl(key, value string) error {
	p, err := f.SysctlPath(key)
	if err != nil {
		return err
	}
	return writeExisting(p, value)
}

// SysPath confines rel to the sys root: after resolving symlinks (sysfs is full of them) the result must stay inside.
func (f FS) SysPath(rel string) (string, error) {
	if rel == "" || filepath.IsAbs(rel) || strings.Contains(rel, "\\x00") {
		return "", fmt.Errorf("invalid sysfs path %q", rel)
	}
	root, err := filepath.EvalSymlinks(f.Sys)
	if err != nil {
		return "", err
	}
	real, err := filepath.EvalSymlinks(filepath.Join(root, rel))
	if err != nil {
		return "", err
	}
	if real != root && !strings.HasPrefix(real, root+string(filepath.Separator)) {
		return "", errors.New("sysfs path escapes the sys root")
	}
	return real, nil
}

func (f FS) ReadSys(rel string) (string, error) {
	p, err := f.SysPath(rel)
	if err != nil {
		return "", err
	}
	b, err := os.ReadFile(p)
	return strings.TrimSpace(string(b)), err
}

func (f FS) WriteSys(rel, value string) error {
	p, err := f.SysPath(rel)
	if err != nil {
		return err
	}
	return writeExisting(p, value)
}

// writeExisting never creates files: a typo must fail, not create a stray file. One write call, as the kernel expects.
func writeExisting(path, value string) error {
	if strings.ContainsAny(value, "\\n\\x00") {
		return errors.New("value must be a single line")
	}
	fh, err := os.OpenFile(path, os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	_, werr := fh.WriteString(value + "\\n")
	if cerr := fh.Close(); werr == nil {
		werr = cerr
	}
	return werr
}
''', "kernelfs_test.go": '''package kernelfs

import (
	"os"
	"path/filepath"
	"testing"
)

func fake(t *testing.T) FS {
	t.Helper()
	root := t.TempDir()
	f := FS{ProcSys: filepath.Join(root, "proc", "sys"), Sys: filepath.Join(root, "sys")}
	os.MkdirAll(filepath.Join(f.ProcSys, "vm"), 0o755)
	os.WriteFile(filepath.Join(f.ProcSys, "vm", "swappiness"), []byte("60\\n"), 0o644)
	os.MkdirAll(filepath.Join(f.Sys, "devices", "cpu0", "cpufreq"), 0o755)
	os.WriteFile(filepath.Join(f.Sys, "devices", "cpu0", "cpufreq", "scaling_governor"), []byte("powersave\\n"), 0o644)
	os.MkdirAll(filepath.Join(f.Sys, "class"), 0o755)
	os.Symlink("../devices/cpu0", filepath.Join(f.Sys, "class", "cpu0")) // in-tree symlink, like real sysfs
	os.WriteFile(filepath.Join(root, "secret"), []byte("x"), 0o644)
	os.Symlink("../secret", filepath.Join(f.Sys, "evil")) // symlink escaping the root
	return f
}

func TestSysctlRoundTrip(t *testing.T) {
	f := fake(t)
	if v, err := f.ReadSysctl("vm.swappiness"); err != nil || v != "60" {
		t.Fatal(v, err)
	}
	if err := f.WriteSysctl("vm.swappiness", "10"); err != nil {
		t.Fatal(err)
	}
	if v, _ := f.ReadSysctl("vm.swappiness"); v != "10" {
		t.Fatal(v)
	}
}

func TestSysctlKeyValidation(t *testing.T) {
	f := fake(t)
	for _, k := range []string{"", "vm..swappiness", "../etc.passwd", "vm.swappiness/../../x", "VM.swappiness", "vm.swap ness", "vm.sw\\x00"} {
		if _, err := f.SysctlPath(k); err == nil {
			t.Errorf("key %q must be rejected", k)
		}
	}
}

func TestWriteDoesNotCreateAndRejectsMultiline(t *testing.T) {
	f := fake(t)
	if err := f.WriteSysctl("vm.nonexistent", "1"); err == nil {
		t.Error("must not create files")
	}
	if _, err := os.Stat(filepath.Join(f.ProcSys, "vm", "nonexistent")); err == nil {
		t.Error("file was created")
	}
	if err := f.WriteSysctl("vm.swappiness", "1\\n2"); err == nil {
		t.Error("multi-line value must be rejected")
	}
}

func TestSysfsConfinement(t *testing.T) {
	f := fake(t)
	if v, err := f.ReadSys("class/cpu0/cpufreq/scaling_governor"); err != nil || v != "powersave" {
		t.Fatalf("in-tree symlink should work: %v %v", v, err)
	}
	if err := f.WriteSys("devices/cpu0/cpufreq/scaling_governor", "performance"); err != nil {
		t.Fatal(err)
	}
	for _, rel := range []string{"../secret", "evil", "/etc/passwd", ""} {
		if _, err := f.ReadSys(rel); err == nil {
			t.Errorf("%q must be rejected", rel)
		}
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux; writing needs root"], compat={"go": ">=1.16"},
   tags=["sysctl", "sysfs", "/proc/sys", "tunables", "kernel", "governor", "path-confinement", "tuning", "linux"],
   avoid="Building paths with Sprintf from user input, or opening with O_CREATE on /proc and /sys."),

RC("go-journaled-apply", "Journal-before-change apply with verification and rollback (Go)", "go",
   "System tweaks that must be undoable: a failure midway, a failed verification or a crash must leave the system as it was.",
   "Capture old values and write the journal atomically BEFORE the first change; record progress before each Set; verify each result with Get; roll back in reverse on failure; Recover() restores after a crash; refuse to start while an unrecovered journal exists.",
   {"txapply.go": '''// Package txapply applies a list of changes with a crash-safe undo journal.
package txapply

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

var ErrPendingJournal = errors.New("an unrecovered journal exists; run Recover first")

type Change struct {
	Name string
	Want string
	Get  func() (string, error)
	Set  func(string) error
}

type journal struct {
	Entries []struct{ Name, Old string } `json:"entries"`
	Started int                          `json:"started"` // entries[:Started] may have been (partly) applied
}

func writeJournal(path string, j journal) error {
	b, _ := json.Marshal(j)
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err = f.Write(b); err == nil {
		err = f.Sync()
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if err = os.Rename(tmp, path); err != nil {
		return err
	}
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

// Apply applies changes in order. On any failure everything already touched is restored (newest first).
func Apply(journalPath string, changes []Change) error {
	if _, err := os.Stat(journalPath); err == nil {
		return ErrPendingJournal
	}
	var j journal
	for _, c := range changes {
		old, err := c.Get()
		if err != nil {
			return fmt.Errorf("read %s: %w", c.Name, err)
		}
		j.Entries = append(j.Entries, struct{ Name, Old string }{c.Name, old})
	}
	if err := writeJournal(journalPath, j); err != nil { // durable BEFORE the first change
		return err
	}
	for i, c := range changes {
		j.Started = i + 1
		if err := writeJournal(journalPath, j); err != nil {
			return rollback(journalPath, changes, j, err)
		}
		if err := c.Set(c.Want); err != nil {
			return rollback(journalPath, changes, j, fmt.Errorf("set %s: %w", c.Name, err))
		}
		if got, err := c.Get(); err != nil || got != c.Want {
			return rollback(journalPath, changes, j, fmt.Errorf("verify %s: got %q want %q (%v)", c.Name, got, c.Want, err))
		}
	}
	return os.Remove(journalPath)
}

func rollback(path string, changes []Change, j journal, cause error) error {
	setters := map[string]func(string) error{}
	for _, c := range changes {
		setters[c.Name] = c.Set
	}
	if err := restore(j, setters); err != nil {
		return fmt.Errorf("%w; ROLLBACK INCOMPLETE (journal kept at %s): %v", cause, path, err)
	}
	os.Remove(path)
	return cause
}

func restore(j journal, setters map[string]func(string) error) error {
	var errs []error
	for i := j.Started - 1; i >= 0; i-- {
		e := j.Entries[i]
		set, ok := setters[e.Name]
		if !ok {
			errs = append(errs, fmt.Errorf("no setter for %s", e.Name))
			continue
		}
		if err := set(e.Old); err != nil {
			errs = append(errs, fmt.Errorf("restore %s: %w", e.Name, err))
		}
	}
	return errors.Join(errs...)
}

// Recover restores state from a journal left by a crash. A missing journal is not an error.
func Recover(journalPath string, setters map[string]func(string) error) error {
	b, err := os.ReadFile(journalPath)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	var j journal
	if err := json.Unmarshal(b, &j); err != nil {
		return fmt.Errorf("corrupt journal %s: %w", journalPath, err)
	}
	if err := restore(j, setters); err != nil {
		return err
	}
	return os.Remove(journalPath)
}
''', "txapply_test.go": '''package txapply

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

type store map[string]string

func (s store) change(name, want string, failSet bool, stuck bool) Change {
	return Change{Name: name, Want: want,
		Get: func() (string, error) { return s[name], nil },
		Set: func(v string) error {
			if failSet && v == want {
				return errors.New("EPERM")
			}
			if stuck && v == want {
				return nil // pretends to succeed but the value does not change
			}
			s[name] = v
			return nil
		}}
}

func setters(s store, names ...string) map[string]func(string) error {
	m := map[string]func(string) error{}
	for _, n := range names {
		n := n
		m[n] = func(v string) error { s[n] = v; return nil }
	}
	return m
}

func TestSuccessRemovesJournal(t *testing.T) {
	j := filepath.Join(t.TempDir(), "j.json")
	s := store{"a": "1", "b": "2"}
	if err := Apply(j, []Change{s.change("a", "10", false, false), s.change("b", "20", false, false)}); err != nil {
		t.Fatal(err)
	}
	if s["a"] != "10" || s["b"] != "20" {
		t.Fatal(s)
	}
	if _, err := os.Stat(j); !os.IsNotExist(err) {
		t.Fatal("journal must be removed on success")
	}
}

func TestFailureRollsBackEverything(t *testing.T) {
	j := filepath.Join(t.TempDir(), "j.json")
	s := store{"a": "1", "b": "2", "c": "3"}
	err := Apply(j, []Change{s.change("a", "10", false, false), s.change("b", "20", true, false), s.change("c", "30", false, false)})
	if err == nil || s["a"] != "1" || s["b"] != "2" || s["c"] != "3" {
		t.Fatal(err, s)
	}
	if _, e := os.Stat(j); !os.IsNotExist(e) {
		t.Fatal("journal must be removed after a clean rollback")
	}
}

func TestVerificationFailureRollsBack(t *testing.T) {
	j := filepath.Join(t.TempDir(), "j.json")
	s := store{"a": "1", "b": "2"}
	err := Apply(j, []Change{s.change("a", "10", false, false), s.change("b", "20", false, true)})
	if err == nil || s["a"] != "1" || s["b"] != "2" {
		t.Fatal(err, s)
	}
}

func TestCrashRecovery(t *testing.T) {
	dir := t.TempDir()
	j := filepath.Join(dir, "j.json")
	// simulate a crash after the 2nd change started: journal exists, the store holds new values
	os.WriteFile(j, []byte(`{"entries":[{"Name":"a","Old":"1"},{"Name":"b","Old":"2"},{"Name":"c","Old":"3"}],"started":2}`), 0o600)
	s := store{"a": "10", "b": "20", "c": "3"}
	if err := Apply(j, []Change{s.change("c", "30", false, false)}); !errors.Is(err, ErrPendingJournal) {
		t.Fatalf("must refuse while a journal is pending, got %v", err)
	}
	if err := Recover(j, setters(s, "a", "b", "c")); err != nil {
		t.Fatal(err)
	}
	if s["a"] != "1" || s["b"] != "2" || s["c"] != "3" {
		t.Fatal(s)
	}
	if err := Recover(j, nil); err != nil {
		t.Fatal("missing journal is not an error")
	}
}

func TestCorruptJournalIsReported(t *testing.T) {
	j := filepath.Join(t.TempDir(), "j.json")
	os.WriteFile(j, []byte("{not json"), 0o600)
	if err := Recover(j, nil); err == nil {
		t.Fatal("expected error")
	}
}
'''}, GOT, assumptions=["Go >= 1.20 (errors.Join)", "single writer (combine with a flock)"], compat={"go": ">=1.20"},
   tags=["journal", "rollback", "snapshot", "undo", "transaction", "verify", "apply", "tuning", "crash-safe", "linux"],
   avoid="Changing the system first and writing the undo information afterwards."),

RC("go-hw-detect", "Detect GPUs and hwmon temperatures from sysfs (Go)", "go",
   "Showing which GPU/driver a machine has and its sensor temperatures without lspci/nvidia-smi.",
   "Read /sys/class/drm/card*/device vendor/device ids and the driver symlink; read hwmon*/name and temp*_input/_label (millidegrees); sysfs root injectable.",
   {"hwdetect.go": '''// Package hwdetect reads GPU and temperature information from sysfs.
package hwdetect

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type GPU struct {
	Card     string // card0
	Vendor   string // NVIDIA | AMD | Intel | 0x....
	VendorID uint16
	DeviceID uint16
	Driver   string // nvidia, amdgpu, i915, xe, nouveau ...
}

var vendors = map[uint16]string{0x10de: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}
var cardRe = regexp.MustCompile(`^card[0-9]+$`) // skips connector entries like card0-DP-1

func readHex(path string) (uint16, bool) {
	b, err := os.ReadFile(path)
	if err != nil {
		return 0, false
	}
	v, err := strconv.ParseUint(strings.TrimPrefix(strings.TrimSpace(string(b)), "0x"), 16, 16)
	return uint16(v), err == nil
}

func GPUs(sys string) []GPU {
	var out []GPU
	cards, _ := filepath.Glob(filepath.Join(sys, "class", "drm", "card*"))
	sort.Strings(cards)
	for _, c := range cards {
		name := filepath.Base(c)
		if !cardRe.MatchString(name) {
			continue
		}
		vid, ok := readHex(filepath.Join(c, "device", "vendor"))
		if !ok {
			continue
		}
		did, _ := readHex(filepath.Join(c, "device", "device"))
		g := GPU{Card: name, VendorID: vid, DeviceID: did, Vendor: vendors[vid]}
		if g.Vendor == "" {
			g.Vendor = "0x" + strconv.FormatUint(uint64(vid), 16)
		}
		if l, err := os.Readlink(filepath.Join(c, "device", "driver")); err == nil {
			g.Driver = filepath.Base(l)
		}
		out = append(out, g)
	}
	return out
}

type Temp struct {
	Chip    string  // k10temp, coretemp, nvme, amdgpu ...
	Label   string  // Tctl, Package id 0, Composite ... (may be empty)
	Celsius float64 // millidegrees / 1000
}

func Temps(sys string) []Temp {
	var out []Temp
	chips, _ := filepath.Glob(filepath.Join(sys, "class", "hwmon", "hwmon*"))
	sort.Strings(chips)
	for _, c := range chips {
		nb, err := os.ReadFile(filepath.Join(c, "name"))
		if err != nil {
			continue
		}
		inputs, _ := filepath.Glob(filepath.Join(c, "temp*_input"))
		sort.Strings(inputs)
		for _, in := range inputs {
			b, err := os.ReadFile(in)
			if err != nil {
				continue // sensors can vanish or error (EIO) at read time
			}
			milli, err := strconv.ParseInt(strings.TrimSpace(string(b)), 10, 64)
			if err != nil {
				continue
			}
			label, _ := os.ReadFile(strings.TrimSuffix(in, "_input") + "_label")
			out = append(out, Temp{Chip: strings.TrimSpace(string(nb)), Label: strings.TrimSpace(string(label)), Celsius: float64(milli) / 1000})
		}
	}
	return out
}
''', "hwdetect_test.go": '''package hwdetect

import (
	"os"
	"path/filepath"
	"testing"
)

func put(t *testing.T, path, content string) {
	t.Helper()
	os.MkdirAll(filepath.Dir(path), 0o755)
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestGPUs(t *testing.T) {
	sys := t.TempDir()
	d0 := filepath.Join(sys, "class", "drm", "card0", "device")
	put(t, filepath.Join(d0, "vendor"), "0x8086\\n")
	put(t, filepath.Join(d0, "device"), "0xa7a1\\n")
	os.Symlink("../../../bus/pci/drivers/i915", filepath.Join(d0, "driver"))
	d1 := filepath.Join(sys, "class", "drm", "card1", "device")
	put(t, filepath.Join(d1, "vendor"), "0x10de\\n")
	put(t, filepath.Join(d1, "device"), "0x2d58\\n")
	os.Symlink("../../../bus/pci/drivers/nvidia", filepath.Join(d1, "driver"))
	put(t, filepath.Join(sys, "class", "drm", "card1-DP-1", "device", "vendor"), "0x10de\\n") // connector: ignored
	put(t, filepath.Join(sys, "class", "drm", "card2", "device", "vendor"), "0xabcd\\n")      // unknown vendor, no driver
	g := GPUs(sys)
	if len(g) != 3 {
		t.Fatalf("%+v", g)
	}
	if g[0].Vendor != "Intel" || g[0].Driver != "i915" || g[0].DeviceID != 0xa7a1 {
		t.Errorf("%+v", g[0])
	}
	if g[1].Vendor != "NVIDIA" || g[1].Driver != "nvidia" {
		t.Errorf("%+v", g[1])
	}
	if g[2].Vendor != "0xabcd" || g[2].Driver != "" {
		t.Errorf("%+v", g[2])
	}
	if len(GPUs(t.TempDir())) != 0 {
		t.Error("empty sysfs must give no GPUs")
	}
}

func TestTemps(t *testing.T) {
	sys := t.TempDir()
	h := filepath.Join(sys, "class", "hwmon", "hwmon0")
	put(t, filepath.Join(h, "name"), "k10temp\\n")
	put(t, filepath.Join(h, "temp1_input"), "45500\\n")
	put(t, filepath.Join(h, "temp1_label"), "Tctl\\n")
	put(t, filepath.Join(h, "temp2_input"), "garbage\\n")
	put(t, filepath.Join(sys, "class", "hwmon", "hwmon1", "temp1_input"), "1000\\n") // no name file: skipped
	ts := Temps(sys)
	if len(ts) != 1 || ts[0].Chip != "k10temp" || ts[0].Label != "Tctl" || ts[0].Celsius != 45.5 {
		t.Fatalf("%+v", ts)
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux sysfs"], compat={"go": ">=1.16"},
   tags=["gpu", "sysfs", "hwmon", "temperature", "hardware", "drm", "nvidia", "amd", "intel", "linux", "detect"],
   avoid="Parsing lspci/nvidia-smi output for basic detection; assuming every card* entry is a GPU."),

RC("go-permutation-test", "Permutation test and bootstrap CI for comparing two benchmark runs (Go)", "go",
   "Deciding whether a tweak really changed performance, not just noise, with a small number of samples and no stats library.",
   "Exact two-sided permutation test on the difference of means when the number of splits is small, seeded Monte Carlo otherwise; bootstrap confidence interval for the mean difference.",
   {"permtest.go": '''// Package permtest compares two sample sets without distribution assumptions.
package permtest

import (
	"math"
	"math/rand"
	"sort"
)

func mean(x []float64) float64 {
	s := 0.0
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}

// MeanDiff is mean(a) - mean(b).
func MeanDiff(a, b []float64) float64 { return mean(a) - mean(b) }

func binom(n, k int) float64 {
	r := 1.0
	for i := 1; i <= k; i++ {
		r = r * float64(n-k+i) / float64(i)
	}
	return math.Round(r)
}

// PValue returns the two-sided p-value for H0 "a and b come from the same distribution", using |mean difference|.
// If C(n,k) <= maxExact it enumerates every split (exact, deterministic); otherwise it samples iters random splits with
// the given seed and applies the +1 correction so p is never 0. exact reports which path ran.
func PValue(a, b []float64, iters int, seed int64) (p float64, exact bool) {
	if len(a) == 0 || len(b) == 0 {
		return math.NaN(), false
	}
	all := append(append([]float64{}, a...), b...)
	n, k := len(all), len(a)
	total := 0.0
	for _, v := range all {
		total += v
	}
	obs := math.Abs(MeanDiff(a, b))
	tol := 1e-12 * (1 + obs)
	const maxExact = 200000
	if binom(n, k) <= maxExact {
		var count, splits float64
		var rec func(start, picked int, sum float64)
		rec = func(start, picked int, sum float64) {
			if picked == k {
				d := math.Abs(sum/float64(k) - (total-sum)/float64(n-k))
				splits++
				if d >= obs-tol {
					count++
				}
				return
			}
			for i := start; i <= n-(k-picked); i++ {
				rec(i+1, picked+1, sum+all[i])
			}
		}
		rec(0, 0, 0)
		return count / splits, true
	}
	rng := rand.New(rand.NewSource(seed))
	idx := rng.Perm(n)
	hits := 0
	for it := 0; it < iters; it++ {
		rng.Shuffle(n, func(i, j int) { idx[i], idx[j] = idx[j], idx[i] })
		s := 0.0
		for _, i := range idx[:k] {
			s += all[i]
		}
		if math.Abs(s/float64(k)-(total-s)/float64(n-k)) >= obs-tol {
			hits++
		}
	}
	return float64(hits+1) / float64(iters+1), false
}

// BootstrapCI is a percentile bootstrap interval for mean(a)-mean(b) at confidence level 1-alpha (alpha=0.05 -> 95%).
func BootstrapCI(a, b []float64, iters int, alpha float64, seed int64) (lo, hi float64) {
	rng := rand.New(rand.NewSource(seed))
	ds := make([]float64, iters)
	ra, rb := make([]float64, len(a)), make([]float64, len(b))
	for it := range ds {
		for i := range ra {
			ra[i] = a[rng.Intn(len(a))]
		}
		for i := range rb {
			rb[i] = b[rng.Intn(len(b))]
		}
		ds[it] = MeanDiff(ra, rb)
	}
	sort.Float64s(ds)
	return ds[int(alpha/2*float64(iters))], ds[int((1-alpha/2)*float64(iters))-1]
}
''', "permtest_test.go": '''package permtest

import (
	"math"
	"testing"
)

func TestExactSeparated(t *testing.T) {
	p, exact := PValue([]float64{1, 2, 3, 4}, []float64{5, 6, 7, 8}, 1000, 1)
	if !exact || math.Abs(p-2.0/70) > 1e-12 { // only the two extreme splits are as extreme as observed
		t.Fatal(p, exact)
	}
}

func TestIdenticalSamplesGiveP1(t *testing.T) {
	p, _ := PValue([]float64{1, 2, 3}, []float64{1, 2, 3}, 1000, 1)
	if p != 1 {
		t.Fatal(p)
	}
}

func TestMonteCarloDeterministicAndClose(t *testing.T) {
	a := []float64{10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21}
	b := []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15}
	p1, e1 := PValue(a, b, 20000, 42)
	p2, _ := PValue(a, b, 20000, 42)
	if e1 || p1 != p2 {
		t.Fatal("expected deterministic Monte Carlo", p1, p2, e1)
	}
	if p1 <= 0 || p1 > 0.01 {
		t.Fatalf("clearly different samples should give a tiny p, got %v", p1)
	}
	q, _ := PValue(a, a[:], 20000, 7)
	if q < 0.9 {
		t.Fatalf("same data should not look different: %v", q)
	}
}

func TestEmptyInput(t *testing.T) {
	if p, _ := PValue(nil, []float64{1}, 10, 1); !math.IsNaN(p) {
		t.Fatal(p)
	}
}

func TestBootstrapCIContainsTrueDifference(t *testing.T) {
	a := []float64{14.8, 15.2, 15.0, 14.9, 15.1, 15.3, 14.7, 15.0}
	b := []float64{10.1, 9.8, 10.0, 10.2, 9.9, 10.3, 9.7, 10.0}
	lo, hi := BootstrapCI(a, b, 5000, 0.05, 3)
	d := MeanDiff(a, b)
	if !(lo < d && d < hi) || lo < 4 || hi > 6 {
		t.Fatal(lo, d, hi)
	}
}
'''}, GOT, assumptions=["Go >= 1.18", "Samples are independent; for paired runs use a paired design instead"], compat={"go": ">=1.18"},
   tags=["permutation", "bootstrap", "statistics", "benchmark", "p-value", "significance", "noise", "tuning"],
   avoid="Reporting a speedup from one run each, or from a t-test on 3 samples with skewed timings."),

RC("go-privileged-helper-allowlist", "Allowlisted root helper for pkexec/polkit actions (Go)", "go",
   "A GUI/TUI must change a few kernel settings as root without becoming a general root shell.",
   "A tiny helper that accepts only fixed actions with strictly validated arguments (key allowlist, value pattern), never runs a shell or user-supplied paths, and maps actions to /proc/sys or /sys writes; ships with a polkit policy file.",
   {"helper.go": '''// Package helper is the logic of a root helper started via pkexec. It accepts a closed set of actions.
package helper

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
)

type Action struct {
	Kind   string // sysctl | governor
	Target string // sysctl key, or cpu number
	Value  string
}

var (
	valueRe = regexp.MustCompile(`^[a-z0-9_.-]{1,32}$`)
	cpuRe   = regexp.MustCompile(`^[0-9]{1,3}$`)
	// closed allowlist: key -> the only values that may be written (nil = any numeric value in range checked by numRe)
	sysctls = map[string]*regexp.Regexp{
		"vm.swappiness":          regexp.MustCompile(`^([0-9]|[1-9][0-9]|1[0-9][0-9]|200)$`),
		"vm.vfs_cache_pressure":  regexp.MustCompile(`^[0-9]{1,4}$`),
		"net.ipv4.tcp_congestion_control": regexp.MustCompile(`^(bbr|cubic|reno)$`),
	}
	governors = map[string]bool{"performance": true, "powersave": true, "schedutil": true, "ondemand": true, "conservative": true}
)

var ErrDenied = errors.New("action not permitted")

// Parse validates argv (without the program name). Anything unknown is denied; there is no escape hatch.
func Parse(args []string) (Action, error) {
	switch {
	case len(args) == 3 && args[0] == "sysctl":
		re, ok := sysctls[args[1]]
		if !ok || !re.MatchString(args[2]) {
			return Action{}, fmt.Errorf("%w: sysctl %q=%q", ErrDenied, args[1], args[2])
		}
		return Action{"sysctl", args[1], args[2]}, nil
	case len(args) == 3 && args[0] == "governor":
		if !cpuRe.MatchString(args[1]) || !governors[args[2]] || !valueRe.MatchString(args[2]) {
			return Action{}, fmt.Errorf("%w: governor %q=%q", ErrDenied, args[1], args[2])
		}
		return Action{"governor", args[1], args[2]}, nil
	}
	return Action{}, fmt.Errorf("%w: unknown or malformed request", ErrDenied)
}

// Path returns the file the action writes, under root ("" in production; a temp dir in tests).
func (a Action) Path(root string) string {
	switch a.Kind {
	case "sysctl":
		parts := splitDots(a.Target)
		return filepath.Join(append([]string{root, "/proc/sys"}, parts...)...)
	default:
		return filepath.Join(root, "/sys/devices/system/cpu", "cpu"+a.Target, "cpufreq", "scaling_governor")
	}
}

func splitDots(s string) []string {
	var out []string
	cur := ""
	for _, r := range s {
		if r == '.' {
			out = append(out, cur)
			cur = ""
			continue
		}
		cur += string(r)
	}
	return append(out, cur)
}

// Run performs the write. It refuses to create files and never invokes a shell.
func Run(a Action, root string) error {
	f, err := os.OpenFile(a.Path(root), os.O_WRONLY, 0)
	if err != nil {
		return err
	}
	_, werr := f.WriteString(a.Value + "\\n")
	if cerr := f.Close(); werr == nil {
		werr = cerr
	}
	return werr
}
''', "org.example.tool.policy": '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1.0/policyconfig.dtd">
<policyconfig>
  <vendor>Example</vendor>
  <action id="org.example.tool.apply">
    <description>Change kernel tuning settings</description>
    <message>Authentication is required to change system tuning settings</message>
    <defaults>
      <allow_any>no</allow_any>
      <allow_inactive>no</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/lib/example-tool/helper</annotate>
  </action>
</policyconfig>
''', "helper_test.go": '''package helper

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAllowedActions(t *testing.T) {
	a, err := Parse([]string{"sysctl", "vm.swappiness", "10"})
	if err != nil || a.Kind != "sysctl" || a.Value != "10" {
		t.Fatal(a, err)
	}
	if _, err := Parse([]string{"governor", "3", "performance"}); err != nil {
		t.Fatal(err)
	}
	if _, err := Parse([]string{"sysctl", "net.ipv4.tcp_congestion_control", "bbr"}); err != nil {
		t.Fatal(err)
	}
}

func TestDeniedInputs(t *testing.T) {
	bad := [][]string{
		nil, {}, {"sysctl"}, {"sysctl", "vm.swappiness"}, {"sysctl", "vm.swappiness", "10", "extra"},
		{"sysctl", "kernel.core_pattern", "|/bin/sh"},           // not on the allowlist
		{"sysctl", "vm.swappiness", "10; rm -rf /"},              // injection attempt
		{"sysctl", "vm.swappiness", "999"},                       // out of range
		{"sysctl", "vm.swappiness", "-1"},
		{"sysctl", "../../etc/passwd", "1"},
		{"sysctl", "vm.swappiness\\x00", "1"},
		{"governor", "../0", "performance"}, {"governor", "0", "turbo"}, {"governor", "", "performance"},
		{"exec", "/bin/sh"}, {"sh", "-c", "id"}, {"SYSCTL", "vm.swappiness", "10"},
	}
	for _, args := range bad {
		if _, err := Parse(args); !errors.Is(err, ErrDenied) {
			t.Errorf("%q should be denied, got %v", args, err)
		}
	}
}

func TestRunWritesOnlyTheMappedFile(t *testing.T) {
	root := t.TempDir()
	a, _ := Parse([]string{"sysctl", "vm.swappiness", "10"})
	p := a.Path(root)
	if !strings.HasPrefix(p, root) || !strings.HasSuffix(p, "/proc/sys/vm/swappiness") {
		t.Fatal(p)
	}
	if err := Run(a, root); err == nil {
		t.Fatal("must not create a missing file")
	}
	os.MkdirAll(filepath.Dir(p), 0o755)
	os.WriteFile(p, []byte("60\\n"), 0o644)
	if err := Run(a, root); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(p); string(b) != "10\\n" {
		t.Fatalf("%q", b)
	}
	g, _ := Parse([]string{"governor", "2", "powersave"})
	if !strings.HasSuffix(g.Path(root), "/sys/devices/system/cpu/cpu2/cpufreq/scaling_governor") {
		t.Fatal(g.Path(root))
	}
}
'''}, GOT, assumptions=["Go >= 1.16", "Linux with polkit; install the helper root-owned and non-writable, path matches the policy annotate"],
   compat={"go": ">=1.16"},
   tags=["pkexec", "polkit", "privilege", "root", "helper", "allowlist", "sudo", "security", "tuning", "linux"],
   avoid="`pkexec sh -c`, passing user-supplied paths or commands to root, or an allowlist built from a denylist."),

RC("go-table-width", "Unicode-width-aware terminal table renderer (Go)", "go",
   "Aligned columns break when cells contain CJK, emoji, combining marks or ANSI colour.",
   "Compute display width from rune ranges (wide=2, combining/zero-width=0) after stripping ANSI, pad by display width, truncate with an ellipsis at a column limit, render a plain aligned table.",
   {"table.go": '''// Package table renders aligned plain-text tables using display width, not byte or rune count.
package table

import (
	"regexp"
	"strings"
)

var ansi = regexp.MustCompile("\\x1b\\\\[[0-9;?]*[ -/]*[@-~]")

type rng struct{ lo, hi rune }

var wide = []rng{{0x1100, 0x115F}, {0x2E80, 0xA4CF}, {0xAC00, 0xD7A3}, {0xF900, 0xFAFF}, {0xFE30, 0xFE6F}, {0xFF00, 0xFF60},
	{0xFFE0, 0xFFE6}, {0x1F300, 0x1F64F}, {0x1F900, 0x1F9FF}, {0x20000, 0x3FFFD}}
var zero = []rng{{0x0300, 0x036F}, {0x200B, 0x200F}, {0x20D0, 0x20FF}, {0xFE00, 0xFE0F}, {0xFE20, 0xFE2F}}

func in(r rune, set []rng) bool {
	for _, s := range set {
		if r >= s.lo && r <= s.hi {
			return true
		}
	}
	return false
}

func runeWidth(r rune) int {
	switch {
	case r == 0 || in(r, zero):
		return 0
	case in(r, wide):
		return 2
	}
	return 1
}

// Width is the number of terminal columns s occupies (ANSI escapes count as 0).
func Width(s string) int {
	w := 0
	for _, r := range ansi.ReplaceAllString(s, "") {
		w += runeWidth(r)
	}
	return w
}

// Truncate shortens s to at most max columns, ending with "…" when it had to cut. Not ANSI-aware: pass plain text.
func Truncate(s string, max int) string {
	if Width(s) <= max {
		return s
	}
	if max <= 0 {
		return ""
	}
	w := 0
	var b strings.Builder
	for _, r := range s {
		rw := runeWidth(r)
		if w+rw > max-1 {
			break
		}
		b.WriteRune(r)
		w += rw
	}
	return b.String() + "…"
}

func pad(s string, w int, right bool) string {
	gap := strings.Repeat(" ", max(0, w-Width(s)))
	if right {
		return gap + s
	}
	return s + gap
}

// Render lays out rows (first row = header) with two spaces between columns. rightAlign marks numeric columns.
// maxCol (0 = unlimited) truncates over-long cells.
func Render(rows [][]string, rightAlign map[int]bool, maxCol int) string {
	if len(rows) == 0 {
		return ""
	}
	cols := 0
	for _, r := range rows {
		cols = max(cols, len(r))
	}
	cells := make([][]string, len(rows))
	widths := make([]int, cols)
	for i, r := range rows {
		cells[i] = make([]string, cols)
		for j := 0; j < cols; j++ {
			if j < len(r) {
				cells[i][j] = r[j]
				if maxCol > 0 {
					cells[i][j] = Truncate(r[j], maxCol)
				}
			}
			widths[j] = max(widths[j], Width(cells[i][j]))
		}
	}
	var b strings.Builder
	for _, r := range cells {
		var line strings.Builder
		for j, c := range r {
			if j > 0 {
				line.WriteString("  ")
			}
			line.WriteString(pad(c, widths[j], rightAlign[j]))
		}
		b.WriteString(strings.TrimRight(line.String(), " ")) // never leave trailing spaces (short rows, last column)
		b.WriteString("\\n")
	}
	return b.String()
}
''', "table_test.go": '''package table

import (
	"strings"
	"testing"
)

func TestWidth(t *testing.T) {
	cases := map[string]int{"abc": 3, "": 0, "日本語": 6, "a日": 3, "e\\u0301": 1, "\\x1b[31mred\\x1b[0m": 3, "😀": 2, "a\\u200bb": 2}
	for s, want := range cases {
		if got := Width(s); got != want {
			t.Errorf("Width(%q) = %d, want %d", s, got, want)
		}
	}
}

func TestTruncate(t *testing.T) {
	if Truncate("hello", 10) != "hello" || Truncate("hello world", 8) != "hello w…" || Truncate("日本語日本語", 5) != "日本…" || Truncate("x", 0) != "" {
		t.Fatal(Truncate("hello world", 8), Truncate("日本語日本語", 5))
	}
	if Width(Truncate("日本語日本語", 5)) > 5 {
		t.Fatal("must never exceed max")
	}
}

func TestRenderAlignsByDisplayWidth(t *testing.T) {
	out := Render([][]string{{"NAME", "PORT"}, {"日本", "80"}, {"\\x1b[32mweb\\x1b[0m", "8080"}}, map[int]bool{1: true}, 0)
	lines := strings.Split(strings.TrimRight(out, "\\n"), "\\n")
	if len(lines) != 3 {
		t.Fatal(out)
	}
	// the PORT column must end at the same display column on every line
	end := Width(lines[0])
	for _, l := range lines {
		if Width(l) != end {
			t.Fatalf("misaligned: %q (width %d, want %d)", l, Width(l), end)
		}
	}
}

func TestEmptyAndRagged(t *testing.T) {
	if Render(nil, nil, 0) != "" {
		t.Fatal()
	}
	out := Render([][]string{{"a", "b", "c"}, {"x"}}, nil, 0)
	if strings.Contains(out, " \\n") {
		t.Fatalf("trailing spaces: %q", out)
	}
}
'''}, GOT, assumptions=["Go >= 1.21 (builtin max)", "Approximate wcwidth table; ambiguous-width and some emoji sequences are not handled"],
   compat={"go": ">=1.21"},
   tags=["table", "terminal", "unicode", "width", "wcwidth", "cjk", "alignment", "tui", "cli", "ansi"],
   avoid="len(s) or utf8.RuneCountInString for padding."),
]
