"""Near-duplicate functions: `duplication --json` pairs and brief's `duplication_twins`.

Definitions (README `duplication` row; docs/agent-json.md:1175 and :490):
containment is shared shingles over the smaller function's shingles; the
defaults are `--min-lines 8` and `--similarity 0.8`; a pair whose spans nest
in one file is dropped; ties keep one order across hash seeds. The docs do not
say what a shingle is. The hand values below use crapkit's stated scheme
(src/crapkit/dup.py module docstring: a window of 4 consecutive lines, each
line stripped of all whitespace, blank and comment lines left out) and every
function's whole span, def line included.

Hand fixture (`hand` below):
- copy_a and copy_b: the same 10 body lines, copy_b with comment lines, blank
  lines and wider spacing. 11 normalized lines each, so 8 shingles; the def
  lines differ, so 7 are shared: 7/8 = 0.875.
- factory holds closure, whose 10 body lines are copy_a's: closure pairs with
  copy_a and copy_b at 0.875, and factory never pairs with closure (R68).
- other: 11 lines unlike the rest, no pair.
- outer (h.py) holds a nested copy of copy_a, def line and all, so every one
  of copy_a's 8 shingles is in outer: containment 1.0 across two files.
- big_e has 4006 body lines (4004 shingles). big_f shares its first 3206 body
  lines (3203 shingles): 3203/4004 = 0.799950... under 0.8, though it rounds
  to 0.8000 at 4 places. big_g shares 3207 (3204 shingles): 0.800200, a pair.

jscpd 5.3.2 (weak mode, which drops comments) is the outside oracle on the
small functions: the functions that hold each clone it reports, joined into
clone classes, are the pairs crapkit reports.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive, oracles, rulings

pytestmark = pytest.mark.process

BODY = ["total = 0", "for item in items:", "    if item > limit:", "        total += item * 2",
        "    elif item < 0:", "        total -= item", "    else:", "        total += 1",
        "count = len(items)", "return total / max(count, 1)"]
BIG = [f"e_{i} = {i}" for i in range(4006)]
SMALL_FILES = ("a.py", "b.py", "c.py", "d.py")


def _function(name: str, body: list[str], indent: str = "    ") -> str:
    return f"def {name}(items, limit):\n" + "".join(f"{indent}{line}\n" for line in body)


def _noisy(name: str) -> str:
    lines = []
    for line in BODY:
        lines += ["    # a comment line", "", "    " + line.replace(" = ", "  =  ")]
    return f"def {name}(items, limit):\n" + "\n".join(lines) + "\n"


def _nested(outer: str, inner: str) -> str:
    """`outer` holding `inner`, whose body is BODY and which `outer` returns."""
    head = f"def {outer}(limit):\n    def {inner}(items, limit):\n"
    return head + "".join(f"        {line}\n" for line in BODY) + f"    return {inner}\n"


def _big(name: str, shared: int, tag: str) -> str:
    return _function(name, BIG[:shared] + [f"{tag}_{i} = {i}" for i in range(900)])


def hand_files() -> dict:
    return {
        "a.py": _function("copy_a", BODY),
        "b.py": _noisy("copy_b"),
        "c.py": _nested("factory", "closure"),
        "d.py": _function("other", [f"value_{i} = compute({i}, limit)" for i in range(10)]),
        "h.py": _nested("outer", "copy_a"),
        "e.py": _function("big_e", BIG),
        "f.py": _big("big_f", 3206, "f"),
        "g.py": _big("big_g", 3207, "g"),
    }


def _bare(long_name: str) -> str:
    return long_name.split("(")[0].strip()


def pair_names(payload: dict) -> dict:
    """{frozenset of the two bare names: similarity} from `duplication --json`."""
    return {frozenset(_bare(f["long_name"]) for f in pair["functions"]): pair["similarity"]
            for pair in payload["pairs"]}


@pytest.fixture(scope="module")
def hand(tmp_path_factory):
    files = hand_files()
    tree = {"crapkit.toml": analysis_inventory.config(analysis_inventory.languages_of(files)),
            **files}
    root = analysis_inventory.build(tree, tmp_path_factory.mktemp("dup") / "repo")
    driver = drive.Driver(root, spawn=True)
    done = driver.run("coverage")
    assert done.code == 0, done.stderr
    return driver


@pytest.fixture(scope="module")
def pairs(hand):
    return pair_names(hand.json("duplication", "--json"))


def _twins(driver, path: str, name: str) -> dict:
    packet = driver.json("brief", path, name, "--json")
    return {_bare(twin["long_name"]): twin for twin in packet["duplication_twins"]}


def test_copies_with_comments_blanks_and_spacing_pair_at_seven_of_eight(pairs):
    assert pairs[frozenset({"copy_a", "copy_b"})] == 0.875
    assert pairs[frozenset({"copy_a", "factory.closure"})] == 0.875
    assert pairs[frozenset({"copy_b", "factory.closure"})] == 0.875


def test_factory_and_closure_are_not_a_pair(pairs):
    """R68: README 'A function and its nested closure never pair'."""
    assert frozenset({"factory", "factory.closure"}) not in pairs


def test_unrelated_function_pairs_with_nothing(pairs):
    assert not [names for names in pairs if "other" in names]


def test_threshold_boundary_matches_exact_containment(hand, pairs):
    """R55: 3203/4004 rounds to 0.8000 but is under 0.8, in duplication and brief alike."""
    assert frozenset({"big_e", "big_f"}) not in pairs
    assert pairs[frozenset({"big_e", "big_g"})] == 0.8002
    twins = _twins(hand, "e.py", "big_e")
    assert "big_f" not in twins
    assert twins["big_g"]["similarity"] == 0.8002


@rulings.applies("AO-DUP-CONTAINED")
def test_a_twin_marks_contained_when_the_spans_nest(hand):
    """docs/agent-json.md:1175 reads `contained` as nesting: nested pairs are dropped,
    so `contained` is 'therefore' false on every pair. :490 defines it as every
    shingle of the smaller in the larger, which outer meets for copy_a at 1.0 from
    another file. crapkit follows :1175; the row records :490's wording."""
    closure = _twins(hand, "c.py", "closure")
    assert (closure["factory"]["similarity"], closure["factory"]["contained"]) == (1.0, True)
    outer = _twins(hand, "a.py", "copy_a")["outer"]
    assert outer["similarity"] == 1.0
    rulings.pin_ruling("AO-DUP-CONTAINED", crapkit=str(outer["contained"]).lower(),
                       oracle="true")


@rulings.applies("AO-DUP-ENCLOSING-UNDER-MIN")
def test_an_enclosing_function_under_min_lines_never_pairs(pairs):
    """factory's own lines are 3 (its nloc), under --min-lines 8; only the lines of
    the closure nested in it reach 8, and the closure's own pairs are listed."""
    through_closure = [names for names in pairs if "factory" in names]
    rulings.pin_ruling("AO-DUP-ENCLOSING-UNDER-MIN", crapkit=str(len(through_closure)),
                       oracle="0")


# --- hash seeds and input order (R29) -------------------------------------------------

@pytest.mark.parametrize("seed", ["0", "1", "4242"])
def test_top_pairs_ignore_hash_seed_and_input_order(hand, seed):
    base = hand.run("duplication", "--json", "--top", "3")
    seeded = drive.Driver(hand.root, spawn=True, env={"PYTHONHASHSEED": seed})
    again = seeded.run("duplication", "--json", "--top", "3")
    assert (again.code, again.json()["pairs"]) == (0, base.json()["pairs"])


def _both_small(places: tuple) -> bool:
    return all(path in SMALL_FILES for path, _ in places)


def test_ties_are_ordered_by_location_not_by_arrival(hand):
    """The small files' pairs all tie at 0.875. README says only that ties keep one
    order across hash seeds; the order crapkit keeps is by (path, start)."""
    ranked = [tuple((f["path"], f["start"]) for f in pair["functions"])
              for pair in hand.json("duplication", "--json")["pairs"]]
    ties = [paths for paths in ranked if _both_small(paths)]
    assert len(ties) >= 3
    assert ties == sorted(ties)


# --- jscpd, the outside oracle ------------------------------------------------------------

def _holder(rows: list[dict], path: str, first: int, last: int) -> str:
    """The innermost function whose span holds lines first..last of path."""
    spans = [row for row in rows if row["path"] == path and row["start"] <= first
             and last <= row["end"]]
    return _bare(min(spans, key=lambda row: row["end"] - row["start"])["long_name"])


def _classes(links: list[tuple[str, str]]) -> dict[str, set]:
    """Each name's clone class: the names the links join it to, directly or not."""
    group: dict[str, set] = {}
    for left, right in links:
        merged = group.get(left, {left}) | group.get(right, {right})
        for name in merged:
            group[name] = merged
    return group


def _class_pairs(group: dict[str, set]) -> set:
    return {frozenset({a, b}) for members in group.values() for a in members for b in members
            if a < b}


def _jscpd_report(root: Path, out: Path) -> dict:
    script = oracles.node_modules("push") / "jscpd" / "run-jscpd.js"
    done = subprocess.run(["node", str(script), "--mode", "weak", "--min-lines", "5",
                           "--min-tokens", "20", "--reporters", "json", "--output", str(out),
                           *SMALL_FILES], cwd=root, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads((out / "jscpd-report.json").read_text(encoding="utf-8"))


def _link(rows: list[dict], clone: dict) -> tuple[str, str]:
    first, second = clone["firstFile"], clone["secondFile"]
    return (_holder(rows, first["name"], first["start"], first["end"]),
            _holder(rows, second["name"], second["start"], second["end"]))


def jscpd_pairs(root: Path, rows: list[dict], out: Path) -> set:
    """The function pairs jscpd's clones imply: each clone links the innermost
    functions holding its two fragments, and every two functions in one clone
    class pair."""
    links = [_link(rows, clone) for clone in _jscpd_report(root, out)["duplicates"]]
    return _class_pairs(_classes(links))


def test_jscpd_clone_classes_are_the_pairs_crapkit_reports(oracle, hand, pairs, tmp_path):
    oracle("jscpd")
    inventory = analysis_inventory.run_inventory(hand.root, tmp_path / "inventory.tsv")
    found = jscpd_pairs(hand.root, list(inventory.rows), tmp_path / "jscpd")
    small = {"copy_a", "copy_b", "factory", "factory.closure", "other"}
    mine = {names for names in pairs if names <= small and "factory" not in names}
    assert found == mine == {frozenset({"copy_a", "copy_b"}),
                             frozenset({"copy_a", "factory.closure"}),
                             frozenset({"copy_b", "factory.closure"})}
