"""Stable duplicate ranking and bounded payload creation agree with an oracle."""
import itertools
import json
import os
import random
import subprocess
import sys

import pytest

from crapkit.dup import find_duplicates, find_twins
from crapkit.snapshot import InventoryRow


def row(path, text, name="f", start=1, end=None):
    end = len(text.splitlines()) if end is None else end
    return InventoryRow("src", path, name, start, end, 3, 3, 3, end - start + 1, 0, 0)


def tie_case():
    first = [f"    x{i} = first({i})" for i in range(20)]
    second = [f"    y{i} = second({i})" for i in range(20)]
    sources = {"a.py": "\n".join(["def a():", *first, *second]),
               "b.py": "\n".join(["def b():", *first, "z=1", "q=2", "p=3"]),
               "c.py": "\n".join(["def c():", *second, "z=4", "q=5", "p=6"])}
    return [row(path, text) for path, text in sources.items()], sources


def function_order(function):
    return (function["path"], function["start"], function["end"], function["long_name"], function["nloc"])


def pair_order(pair):
    return -pair["similarity"], tuple(map(function_order, pair["functions"]))


def oracle(rows, sources, min_lines, threshold, top):
    indexed = []
    for function in rows:
        if function.path not in sources:
            continue
        lines = sources[function.path].splitlines()[function.start - 1:function.end]
        lines = ["".join(line.split()) for line in lines if line.strip()
                 and not line.strip().startswith(("#", "//", "/*", "*", '"""', "'''"))]
        if len(lines) >= min_lines:
            indexed.append((function, {tuple(lines[i:i + 4]) for i in range(len(lines) - 3)}))
    pairs = []
    for (a, left), (b, right) in itertools.combinations(indexed, 2):
        count = len(left & right)
        nested = a.path == b.path and ((a.start <= b.start and b.end <= a.end)
                                        or (b.start <= a.start and a.end <= b.end))
        if not count or nested:
            continue
        score = count / min(len(left), len(right))
        if score >= threshold:
            functions = [{key: getattr(r, key) for key in ("path", "long_name", "start", "end", "nloc")}
                         for r in (a, b)]
            pairs.append({"functions": sorted(functions, key=function_order),
                          "similarity": round(score, 4), "contained": False})
    return sorted(pairs, key=pair_order)[:top]


def test_equal_pairs_keep_one_total_order_across_input_permutations():
    rows, sources = tie_case()
    expected = oracle(rows, sources, 8, 0.8, 1)
    assert expected[0]["functions"][1]["path"] == "b.py"
    for shuffled in itertools.permutations(rows):
        assert find_duplicates(list(shuffled), lambda: sources, top=1) == expected


def test_process_hash_seeds_cannot_select_different_top_pairs(tmp_path):
    rows, sources = tie_case()
    fixture = tmp_path / "input.json"
    fixture.write_text(json.dumps({"rows": rows, "sources": sources}), encoding="utf-8")
    code = ("import json, sys; from crapkit.dup import find_duplicates; "
            "from crapkit.snapshot import InventoryRow; "
            "data=json.load(open(sys.argv[1], encoding='utf-8')); "
            "print(json.dumps(find_duplicates([InventoryRow(*r) for r in data['rows']], "
            "lambda:data['sources'], top=1)))")
    expected = oracle(rows, sources, 8, 0.8, 1)
    for seed in range(1, 7):
        process = subprocess.run([sys.executable, "-c", code, str(fixture)],
                                 env={**os.environ, "PYTHONHASHSEED": str(seed)},
                                 capture_output=True, text=True, check=True)
        assert json.loads(process.stdout) == expected, f"seed {seed}"


@pytest.mark.parametrize("top", [0, 1, 3, 1000, -1])
@pytest.mark.parametrize("threshold", [0.0, 0.5, 0.8, 1.0, float("nan"), float("inf"), float("-inf")])
def test_small_corpora_match_exhaustive_string_shingle_oracle(top, threshold):
    rng = random.Random(21)
    sources = {f"{i}.py": "\n".join(f"    step{x} = call({x})" for x in
               list(range(8)) + [rng.randrange(12) for _ in range(7)]) for i in range(8)}
    rows = [row(path, text) for path, text in sources.items()]
    rows.extend([row("0.py", sources["0.py"], "nested", 3, 12),
                 row("missing.py", "", "missing", 1, 9)])
    expected = oracle(rows, sources, 8, threshold, top)
    assert find_duplicates(rows, lambda: sources, similarity=threshold, top=top) == expected


def test_only_returned_pairs_allocate_payloads(monkeypatch):
    import crapkit.dup as dup

    sources = {f"{i}.py": "\n".join(f"step{n} = call({n})" for n in range(12)) for i in range(40)}
    rows = [row(path, text) for path, text in sources.items()]
    built = []
    original = dup._pair_payload

    def counted(*args):
        built.append(1)
        return original(*args)

    monkeypatch.setattr(dup, "_pair_payload", counted)
    assert len(find_duplicates(rows, lambda: sources, top=3)) == 3
    assert len(built) == 3


def test_twin_ties_include_the_full_visible_function_identity():
    text = "\n".join(f"step{n} = call({n})" for n in range(12))
    sources = {"a.py": text, "b.py": text}
    target = row("a.py", text)
    alpha, beta = row("b.py", text, "alpha"), row("b.py", text, "beta")
    for twins in itertools.permutations([alpha, beta]):
        found = find_twins(target, [target, *twins], sources, top=1)
        assert found[0]["long_name"] == "alpha"
