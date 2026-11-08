"""ClamAV release gate -> security-report.md. Never deletes files."""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import Config


def update_signatures(cfg: Config) -> tuple[bool, str, Path | None]:
    """Update signatures into a user-owned dir (no sudo). Falls back to the system DB."""
    if not shutil.which("freshclam"):
        return False, "freshclam not installed", None
    d = cfg.home / "clamdb"
    d.mkdir(exist_ok=True)
    conf = cfg.home / "freshclam.conf"
    conf.write_text(f"DatabaseDirectory {d}\nDatabaseMirror database.clamav.net\nDNSDatabaseInfo current.cvd.clamav.net\n"
                    f"Checks 1\nLogTime yes\n")
    r = subprocess.run(["freshclam", f"--config-file={conf}", f"--datadir={d}"], capture_output=True, text=True, timeout=900)
    ok = r.returncode == 0 and any(d.glob("*.c[lv]d"))
    return ok, (r.stdout + r.stderr)[-600:], d if ok else None


def security_report(cfg: Config, target: Path, report: Path) -> int:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not shutil.which("clamscan"):
        report.write_text(f"# Security report\n\n{ts}\n\n**BLOCKED:** clamscan is not installed (`sudo pacman -S clamav`).\n")
        print("ClamAV missing"); return 2
    ver = subprocess.run(["clamscan", "--version"], capture_output=True, text=True).stdout.strip()
    ok, fmsg, db = update_signatures(cfg)
    log = report.with_name("clamav-report.log")
    cmd = ["clamscan", "-r", "-i", f"--log={log}"]
    if db:
        cmd += ["-d", str(db)]
    cmd.append(str(target))
    print("scanning", target, "...")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=14400)
    out = r.stdout + r.stderr
    found = [l for l in out.splitlines() if l.endswith(" FOUND")]
    summ = {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"^([A-Za-z ]+):\s+(.+)$", out.split("SCAN SUMMARY")[-1], re.M)}
    status = {0: "CLEAN", 1: "DETECTIONS - RELEASE BLOCKED", 2: "ERROR - RELEASE BLOCKED"}.get(r.returncode, "ERROR")
    lines = ["# Security report", "", f"- Generated: {ts}", f"- Scanner: `{ver}`",
             f"- Signature update: {'updated via freshclam into ' + str(db) if ok else 'FAILED, used system signatures: ' + fmsg.strip()[:200]}",
             f"- Scope: `{target}` (recursive, includes downloaded assets, templates, stored dependencies)",
             f"- Command: `{' '.join(cmd)}`", f"- Result: **{status}**", "", "## Summary", "", "```"]
    lines += [f"{k}: {v}" for k, v in summ.items()] + ["```", ""]
    lines += ["## Detections", ""] + (["- " + l for l in found] if found else ["None."]) + [""]
    lines += ["## Notes", "", "ClamAV is one layer of defence, not proof an asset is safe. Per-download scans run during ingestion "
              "(`data/clamav-ingest.log`); untrusted code is only executed inside a bubblewrap sandbox without network.", "",
              "Suspicious files are never auto-deleted; investigate with `clamscan -i <path>` and quarantine manually."]
    report.write_text("\n".join(lines) + "\n")
    print(status, f"({len(found)} detections) ->", report)
    return r.returncode
