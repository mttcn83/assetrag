import json
import subprocess
import sys

import pytest

from assetrag import repomap

PY_CORE = '''
class Engine:
    """core"""
    def start(self, speed: int = 3) -> bool:
        return True

    @property
    def state(self):
        return 1

    @property
    def mode(self):
        return 2

    @property
    def level(self):
        return 3


def make_engine(kind: str) -> Engine:
    return Engine()


_private = 1
LIMITS = {"a": 1}
'''
PY_APP = '''
from core import make_engine, Engine

def run_app():
    e = make_engine("x")
    return e.start()
'''
PY_TEST = '''
from core import make_engine

def test_make_engine():
    assert make_engine("a")
'''
TS = '''
export class Router { handle(req: string): string { return req } }
export interface Opts { a: number }
export type Id = string
export const double = (n: number): number => n * 2
export function route(path: string): Router { return new Router() }
'''
GO = '''package m

type Server struct{ Addr string }

func (s *Server) Listen(port int) error { return nil }

func New() *Server { return &Server{} }
'''
RUST = '''pub struct Parser { pub depth: u32 }
impl Parser { pub fn new() -> Self { Parser { depth: 0 } } }
pub trait Visit { fn visit(&self); }
mod tests { fn hidden() {} }
mod linked;
'''


def write(root, files):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def have(lang):
    return repomap._parser(lang) is not None


@pytest.fixture
def py_repo(tmp_path):
    return write(tmp_path, {"core.py": PY_CORE, "app.py": PY_APP, "tests/test_core.py": PY_TEST, "README.md": "# x", "pyproject.toml": ""})


def test_python_symbols_signatures_and_lines(py_repo):
    m = repomap.build(py_repo, 1500)
    t = m.text
    assert "class Engine" in t and "def make_engine(kind: str) -> Engine" in t and "def start(self, speed: int=3) -> bool" in t
    assert "props: state, mode, level" in t                     # accessors collapse into one line
    assert "_private" not in t and "LIMITS" not in t            # private names and unused constants are noise
    assert "# also at root: pyproject.toml README.md" in t
    line = next(l for l in t.splitlines() if "def make_engine" in l)
    assert line.split()[0] == str(PY_CORE.split("\n").index("def make_engine(kind: str) -> Engine:") + 1)                              # real line number, so Claude can Read an exact range


def test_most_depended_on_file_ranks_first_and_tests_last(py_repo):
    m = repomap.build(py_repo, 1500)
    order = [p for p, _, _ in m.ranked]
    assert order[0] == "core.py" and order[-1] == "tests/test_core.py"


def test_budget_is_respected_and_degrades_gracefully(py_repo):
    big = repomap.build(py_repo, 3000)
    tiny = repomap.build(py_repo, 120)
    assert tiny.tokens < big.tokens and tiny.tokens <= 170
    assert "core.py" in tiny.text                                # still names the files it could not describe


def test_budget_on_a_larger_tree(tmp_path):
    files = {f"pkg/mod{i}.py": "\n".join(f"def func_{i}_{j}(a, b, c):\n    return {j}\n" for j in range(30)) + (f"\nfrom pkg.mod{i-1} import func_{i-1}_1\n" if i else "") for i in range(60)}
    m = repomap.build(write(tmp_path, files), 800)
    assert m.tokens <= 900 and m.files_total == 60


def test_focus_file_is_shown_in_full_and_pulls_in_its_dependencies(py_repo):
    m = repomap.build(py_repo, 400, focus=["tests/test_core.py"])
    assert "def test_make_engine" in m.text                       # shown in full even though tests normally rank last
    assert "def make_engine" in m.text                            # ...and so is what it depends on, despite the tiny budget


def test_focus_on_a_symbol_name(py_repo):
    m = repomap.build(py_repo, 1500, focus=["make_engine"])
    assert m.ranked[0][0] == "core.py"


def test_strings_and_comments_do_not_create_dependencies(tmp_path):
    write(tmp_path, {"lib.py": "def widget_factory(): pass\n", "docs.py": '"""widget_factory widget_factory"""\nTEXT = "call widget_factory() often"\n# widget_factory\n',
                     "user.py": "from lib import widget_factory\nwidget_factory()\n"})
    users = repomap.rank_files(files := {p: repomap.parse_file(p, (tmp_path / p).read_text())[0] for p in ("lib.py", "docs.py", "user.py")}, set(), set())
    assert users["widget_factory"] == ["user.py"]


def test_junk_is_skipped_and_reported(tmp_path):
    write(tmp_path, {"ok.py": "def fine(): pass\n", "broken.py": "def (:\n", "node_modules/x/index.js": "function nope(){}", "build/gen.py": "def gen(): pass\n",
                     "app.min.js": "var a=1;" * 600, "big.min.js": "x"})
    m = repomap.build(tmp_path, 800)
    assert "fine" in m.text and "nope" not in m.text and "def gen" not in m.text
    assert m.files_total == 1


def test_missing_grammar_is_reported_not_silent(tmp_path, monkeypatch):
    write(tmp_path, {"a.go": GO, "b.py": "def f(): pass\n", "c.rb": "def x; end\n"})
    monkeypatch.setitem(repomap._parsers, "go", None)
    m = repomap.build(tmp_path, 800)
    assert "no parser for" in m.text and ".go" in m.text and ".rb" in m.text
    assert "def f" in m.text


@pytest.mark.skipif(not have("typescript"), reason="tree-sitter typescript grammar not installed")
def test_typescript(tmp_path):
    t = repomap.build(write(tmp_path, {"r.ts": TS}), 800).text
    for want in ("class Router", "interface Opts", "type Id", "function route(path: string): Router", "const double = (n: number): number", "handle(req: string): string"):
        assert want in t, t


@pytest.mark.skipif(not have("go"), reason="tree-sitter go grammar not installed")
def test_go(tmp_path):
    t = repomap.build(write(tmp_path, {"s.go": GO}), 800).text
    assert "type Server struct" in t and "func (s *Server) Listen(port int) error" in t and "func New() *Server" in t


@pytest.mark.skipif(not have("rust"), reason="tree-sitter rust grammar not installed")
def test_rust(tmp_path):
    t = repomap.build(write(tmp_path, {"p.rs": RUST}), 800).text
    assert "pub struct Parser" in t and "impl Parser" in t and "pub fn new() -> Self" in t and "pub trait Visit" in t
    assert "hidden" not in t and "mod linked" not in t           # test modules and `mod x;` file links are noise


def test_git_repo_honours_gitignore(py_repo):
    subprocess.run(["git", "init", "-q"], cwd=py_repo, check=True)
    (py_repo / ".gitignore").write_text("ignored.py\n")
    write(py_repo, {"ignored.py": "def secret_thing(): pass\n"})
    assert "secret_thing" not in repomap.build(py_repo, 1500).text


def test_changed_flag_focuses_on_uncommitted_files(py_repo):
    subprocess.run(["git", "init", "-q"], cwd=py_repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=py_repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"], cwd=py_repo, check=True)
    (py_repo / "app.py").write_text(PY_APP + "\ndef newly_added(): pass\n")
    assert "def newly_added" in repomap.build(py_repo, 150, changed=True).text


def test_not_a_directory_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        repomap.build(tmp_path / "nope")


def test_cli_text_and_json_and_errors(py_repo):
    base = [sys.executable, "-m", "assetrag.cli", "repomap"]
    out = subprocess.run(base + [str(py_repo), "-b", "800"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.startswith("# repo map:") and "\x1b" not in out.stdout
    js = json.loads(subprocess.run(base + [str(py_repo), "--json"], capture_output=True, text=True).stdout)
    assert js["files"] == 3 and js["ranked"][0][0] == "core.py"
    bad = subprocess.run(base + [str(py_repo / "nope")], capture_output=True, text=True)
    assert bad.returncode == 1 and "not a directory" in bad.stderr
