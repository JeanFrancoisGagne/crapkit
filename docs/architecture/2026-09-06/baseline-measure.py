"""Compare the new picker with the prior implementation on generated histories."""
import ast
import itertools
import json
import random
import statistics
import subprocess
import time
from pathlib import Path
from crapkit.store import BaselinePick, pick_baseline

prior = subprocess.check_output(["git", "show", "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6:src/crapkit/store.py"], cwd=Path(__file__).resolve().parents[3], text=True, encoding="utf-8")
names = {"pick_baseline", "is_trusted", "_verdict_verifies", "_blocking_verify",
         "_is_refused", "_baseline_candidates"}
selected = [n for n in ast.parse(prior).body if isinstance(n, ast.FunctionDef) and n.name in names]
scope = {"BaselinePick": BaselinePick, "takewhile": itertools.takewhile}
exec(compile(ast.Module(body=selected, type_ignores=[]), "prior_store.py", "exec"), scope)
old = scope["pick_baseline"]
variants = [("coverage", None, {}), ("verify", False, {}), ("verify", True, {}),
            ("verify", None, {}), ("hook", None, {}), ("partial", None, {}),
            ("legacy", None, {}), ("legacy", None, {"unit": {}})]


def history(items):
    return [{"id": n, "kind": kind, "verdict_ok": ok, "lanes": lanes}
            for n, (kind, ok, lanes) in enumerate(items, 1)]


checked = 0
for size in range(5):
    for items in itertools.product(variants, repeat=size):
        rows = history(items)
        assert pick_baseline(rows) == old(rows), rows
        checked += 1
rng = random.Random(6731)
for _ in range(5000):
    rows = history(rng.choices(variants, k=rng.randrange(5, 150)))
    assert pick_baseline(rows) == old(rows), rows
    checked += 1


def median_ms(fn, rows):
    elapsed = []
    for _ in range(5):
        start = time.perf_counter()
        fn(rows)
        elapsed.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(elapsed), 4)


timings = []
for size in (500, 1000, 2000, 4000):
    rows = history([variants[0], variants[1]] + [variants[0]] * (size - 2))
    timings.append({"runs": size, "before_ms": median_ms(old, rows),
                    "after_ms": median_ms(pick_baseline, rows)})
print(json.dumps({"equivalent_histories": checked, "timings": timings}, indent=2))
