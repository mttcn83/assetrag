"""Clone the source repos in sources.py (parallel, hooks off) and print licence + file counts."""
import sys
from concurrent.futures import ThreadPoolExecutor
from assetrag.config import load
from assetrag.ingest import clone

REPOS = sys.argv[1:]
cfg = load()
def go(u):
    try:
        c = clone(cfg, u)
        n = sum(1 for _ in c.path.rglob("*") if _.is_file() and ".git" not in _.parts)
        return f"{u.split('github.com/')[1]:45} {str(c.spdx):14} files={n}"
    except Exception as e:
        return f"{u.split('github.com/')[1]:45} FAIL {str(e)[:90]}"
with ThreadPoolExecutor(6) as ex:
    for line in ex.map(go, [f"https://github.com/{r}" for r in REPOS]):
        print(line, flush=True)
