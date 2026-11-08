import subprocess

from assetrag import fixmine as F


def test_failure_signature_accepts_assertions_and_rejects_build_errors():
    py = "E   AssertionError: assert 3 == 4\nFAILED tests/test_x.py::t - AssertionError: assert 3 == 4\n1 failed in 0.1s"
    assert F.failure_signature(py, "python") == "AssertionError: assert 3 == 4"
    assert F.failure_signature("ERROR collecting tests/test_x.py\nImportError: cannot import name foo\n1 error during collection", "python") is None
    go = "--- FAIL: TestX (0.00s)\n    x_test.go:12: got 3, want 4\nFAIL"
    assert F.failure_signature(go, "go") == "got 3, want 4"
    assert F.failure_signature("./x_test.go:5:2: undefined: NewThing\nFAIL x [build failed]", "go") is None


def test_candidate_selection_needs_fixish_subject_and_both_source_and_test(tmp_path):
    def git(*a): subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    git("init", "-q")
    def commit(msg, files):
        for n, t in files.items():
            p = tmp_path / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(t)
        git("add", "-A"); git("commit", "-q", "-m", msg)
    commit("initial", {"a.py": "x=1\n"})
    commit("Fix off-by-one in a", {"a.py": "x=2\n", "tests/test_a.py": "def test(): pass\n"})   # candidate
    commit("Fix typo in docs", {"a.py": "x=3\n"})                                                # no test file
    commit("Add feature", {"a.py": "x=4\n", "tests/test_a.py": "def test(): assert 1\n"})        # not a fix
    got = list(F.candidates(tmp_path, "python"))
    assert [c[2] for c in got] == ["Fix off-by-one in a"] and got[0][3] == ["tests/test_a.py"] and got[0][4] == ["a.py"]
