"""Run recipe tests in the sandbox without touching the database:  check_recipes.py <module> [key ...]
e.g. check_recipes.py recipes_go go-pidfd-terminate"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from assetrag.config import load  # noqa: E402
from assetrag.seeds.load import verify_recipe  # noqa: E402

mod = importlib.import_module("assetrag.seeds." + sys.argv[1])
recipes = [r for v in vars(mod).values() if isinstance(v, list) for r in v if isinstance(r, dict) and "files" in r and "cmd" in r]
want = set(sys.argv[2:])
cfg, bad = load(), 0
for r in recipes:
    if want and r["key"] not in want:
        continue
    try:
        ev = verify_recipe(cfg, r)
        print(f"  ok   {r['key']:<34} {ev['result']}")
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  FAIL {r['key']}\n{str(e)[-1400:]}")
print(f"{len(recipes)} recipes, {bad} failed")
sys.exit(1 if bad else 0)
