"""The setup progress bar redraws one line per stage; it never prints a line per update."""
import io

from assetrag import setup_cmd


class Tty(io.StringIO):
    def isatty(self):
        return True


def _run(monkeypatch, tty: bool):
    out = Tty() if tty else io.StringIO()
    monkeypatch.setattr(setup_cmd.sys, "stdout", out)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "1")
    p = setup_cmd.Progress()
    for stage, total in (("verifying", 250), ("embedding", 700), ("saving", 327)):
        for d in range(total + 1):
            p(stage, d, total)
    p.close()
    return out.getvalue()


def test_terminal_bar_uses_one_line_per_stage(monkeypatch):
    out = _run(monkeypatch, tty=True)
    assert out.count("\n") == 3                      # one finished line per stage, however many updates
    assert out.count("\r") >= 3 and "250/250" in out and "700/700" in out and "327/327" in out


def test_pipe_prints_a_line_every_ten_percent(monkeypatch):
    out = _run(monkeypatch, tty=False)
    assert "\r" not in out and "\x1b" not in out
    assert 3 * 8 <= out.count("\n") <= 3 * 12        # ~10 lines per stage, not one per update
