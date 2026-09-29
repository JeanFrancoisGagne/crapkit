"""Near-duplicate functions: `duplication --json` pairs and brief's `duplication_twins`.

Definitions (README `duplication` row; docs/agent-json.md:1175 and :490):
containment is shared shingles over the smaller function's shingles; the
defaults are `--min-lines 8` and `--similarity 0.8`; a pair whose spans nest
in one file is dropped; ties keep one order across hash seeds. The docs do not
say what a shingle is. The hand values below use crapkit's stated scheme
(src/crapkit/dup.py module docstring: a window of 4 consecutive lines, each
line stripped of all whitespace, blank and comment lines left out) and every
function's own lines (README `duplication` row): its span less every line past
the first of a function nested in it, which are that function's.

Hand fixture (`hand` below):
- copy_a and copy_b: the same 10 body lines, copy_b with comment lines, blank
  lines and wider spacing. 11 normalized lines each, so 8 shingles; the def
  lines differ, so 7 are shared: 7/8 = 0.875.
- factory holds closure, whose 10 body lines are copy_a's: closure pairs with
  copy_a and copy_b at 0.875. factory keeps 3 lines of its own, under
  --min-lines 8, so it pairs with nothing (R68).
- other: 11 lines unlike the rest, no pair.
- outer (h.py) holds a nested copy of copy_a, def line and all. outer keeps 3
  lines of its own and pairs with nothing; outer.copy_a holds every one of
  copy_a's 8 shingles: containment 1.0 across two files.
- big_e has 4006 body lines (4004 shingles). big_f shares its first 3206 body
  lines (3203 shingles): 3203/4004 = 0.799950... under 0.8, though it rounds
  to 0.8000 at 4 places. big_g shares 3207 (3204 shingles): 0.800200, a pair.
- hub (t.py) holds four 12-line blocks; tie_1..tie_4 (u1.py..u4.py) each hold
  one of them under their own def line: 13 lines, 10 shingles, 9 inside the
  block, so each pairs with hub at 9/10 = 0.9, a four-way tie under the one
  pair at 1.0 (copy_a with outer.copy_a).
- star_a and star_b (s1.py, s2.py): 12 body lines, the 6th a `**first(items),`
  or `**second(items),` argument line, which is code. 13 lines, 10 shingles,
  5 outside the differing line: 0.5, no pair. crapkit left the line out as a
  comment and read 8/9 = 0.8889 until analysis-oracles-140 was fixed.
- doc_a and doc_b (k1.py, k2.py): one 10-line body under two def lines and two
  one-line docstrings. crapkit leaves a line starting with three quotes out:
  11 lines, 7/8 = 0.875. Keeping the docstring, a string literal: 7/9 = 0.7778.

jscpd 5.3.2 (weak mode, which drops comments) is the outside oracle on the
small functions: the functions that hold each clone it reports, joined into
clone classes, are the pairs crapkit reports. A brute force over every pair
(oracles/symilar_adapter.py) is the self-diff for the whole set, and pylint's
symilar the nightly oracle.
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
import re
import subprocess
from typing import NamedTuple

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_inventory
from accuracy.analysis_oracles.oracles import symilar_adapter
from accuracy.kit import drive, oracles, rulings, runlog

pytestmark = pytest.mark.process

BODY = ["total = 0", "for item in items:", "    if item > limit:", "        total += item * 2",
        "    elif item < 0:", "        total -= item", "    else:", "        total += 1",
        "count = len(items)", "return total / max(count, 1)"]
BIG = [f"e_{i} = {i}" for i in range(4006)]
SMALL_FILES = ("a.py", "b.py", "c.py", "d.py")
TIE_BLOCKS = [[f"part_{block}_{line} = shift(items, {line}) + {block}" for line in range(12)]
              for block in range(1, 5)]
DOC_BODY = [f"step_{line} = scale(items, {line}) - limit" for line in range(10)]


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


def _star(name: str, spread: str) -> str:
    return _function(name, ["acc = 0", "for entry in items:", "    acc += entry", "merged = dict(",
                            "    base=acc,", f"    **{spread}(items),", "    limit=limit,", ")",
                            "size = len(items)", "if size > limit:", "    return merged",
                            "return None"])


def _documented(name: str, docstring: str) -> str:
    return _function(name, [f'"""{docstring}"""', *DOC_BODY])


def _ties() -> dict:
    ties = {f"u{block}.py": _function(f"tie_{block}", TIE_BLOCKS[block - 1])
            for block in range(1, 5)}
    return {"t.py": _function("hub", [line for block in TIE_BLOCKS for line in block]), **ties}


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
        **_ties(),
        "s1.py": _star("star_a", "first"),
        "s2.py": _star("star_b", "second"),
        "k1.py": _documented("doc_a", "Sum the items above the limit."),
        "k2.py": _documented("doc_b", "Add up what passes the limit."),
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
    """docs/agent-json.md:490 and :1175 read `contained` as nesting. Each function is
    shingled from its own lines, so factory, 3 lines of its own, is no twin of the
    closure inside it, and outer.copy_a, holding every shingle of copy_a from
    another file, is a twin at 1.0 that is not contained."""
    assert "factory" not in _twins(hand, "c.py", "closure")
    twin = _twins(hand, "a.py", "copy_a")["outer.copy_a"]
    assert twin["similarity"] == 1.0
    rulings.pin_ruling("AO-DUP-CONTAINED", crapkit=str(twin["contained"]).lower(),
                       oracle="false")


@rulings.applies("AO-DUP-ENCLOSING-UNDER-MIN")
def test_an_enclosing_function_under_min_lines_never_pairs(pairs):
    """factory's own lines are 3 (its nloc), under --min-lines 8; only the lines of
    the closure nested in it reach 8, and the closure's own pairs are listed."""
    through_closure = [names for names in pairs if "factory" in names]
    rulings.pin_ruling("AO-DUP-ENCLOSING-UNDER-MIN", crapkit=str(len(through_closure)),
                       oracle="0")


# --- hash seeds and input order (R29) -------------------------------------------------

# The renamed layout: the small files, outer's file and the tie set under new
# paths that reverse the ties' order; hub still sorts before every tie.
RENAMES = {"a.py": "lib/z_a.py", "b.py": "lib/y_b.py", "c.py": "lib/x_c.py", "d.py": "lib/w_d.py",
           "h.py": "lib/v_h.py", "t.py": "ties/hub.py", "u1.py": "ties/t4.py",
           "u2.py": "ties/t3.py", "u3.py": "ties/t2.py", "u4.py": "ties/t1.py"}
RENAMED_NAMES = {"copy_a", "copy_b", "factory", "factory.closure", "other", "outer",
                 "outer.copy_a", "hub", "tie_1", "tie_2", "tie_3", "tie_4"}
# --top 4 keeps the one pair at 1.0 and cuts the 0.9 tie after three of its four
# pairs. README: "Ties have a stable order across hash seeds"; the order kept
# is by location (test_ties_are_ordered_by_location_not_by_arrival), so the
# renamed layout keeps the ties that now sort first.
TOP_FOUR = {
    "given": [{"copy_a", "outer.copy_a"}, {"hub", "tie_1"}, {"hub", "tie_2"},
              {"hub", "tie_3"}],
    "renamed": [{"copy_a", "outer.copy_a"}, {"hub", "tie_4"}, {"hub", "tie_3"},
                {"hub", "tie_2"}],
}


@pytest.fixture(scope="module")
def renamed(tmp_path_factory):
    files = hand_files()
    moved = {new: files[old] for old, new in RENAMES.items()}
    tree = {"crapkit.toml": analysis_inventory.config(analysis_inventory.languages_of(moved)),
            **moved}
    driver = drive.Driver(analysis_inventory.build(tree, tmp_path_factory.mktemp("dup-renamed")
                                                   / "repo"))
    done = driver.run("coverage")
    assert done.code == 0, done.stderr
    return driver


# hub's 0.9 ties in location order for each layout.
TIES_BY_LOCATION = {"given": ["tie_1", "tie_2", "tie_3", "tie_4"],
                    "renamed": ["tie_4", "tie_3", "tie_2", "tie_1"]}


@pytest.mark.parametrize("seed", ["0", "1", "4242"])
@pytest.mark.parametrize("layout", ["given", "renamed"])
def test_top_pairs_ignore_hash_seed_and_input_order(hand, renamed, layout, seed):
    """R29: however many of hub's ties --top 4 has room for, it keeps the ones that
    sort first by location, under three hash seeds and two layouts of the same
    bytes. How many pairs rank above the tie is the next test's; this check reads
    the tie alone, so it replays on commits that ranked those pairs otherwise."""
    kept = _top_four(hand, renamed, layout, seed)
    ties = [min(names - {"hub"}) for names in kept if "hub" in names]
    assert ties and ties == TIES_BY_LOCATION[layout][:len(ties)]


@pytest.mark.parametrize("seed", ["0", "1", "4242"])
@pytest.mark.parametrize("layout", ["given", "renamed"])
def test_top_four_is_the_pair_at_1_and_the_first_three_ties(hand, renamed, layout, seed):
    """The pairs --top 4 keeps, and their order, under the same seeds and layouts:
    outer.copy_a with copy_a at 1.0, then the three ties that sort first."""
    assert _top_four(hand, renamed, layout, seed) == TOP_FOUR[layout]


def _top_four(hand, renamed, layout: str, seed: str) -> list[set[str]]:
    """The bare names of each pair `duplication --top 4` lists under PYTHONHASHSEED=seed."""
    root = {"given": hand.root, "renamed": renamed.root}[layout]
    return [set(names) for names in _top_four_pairs(root, seed)]


@functools.cache
def _top_four_pairs(root: Path, seed: str) -> tuple[frozenset[str], ...]:
    """One `duplication --top 4` run in its own process per root and seed; both
    tests above read it."""
    seeded = drive.Driver(root, spawn=True, env={"PYTHONHASHSEED": seed})
    done = seeded.run("duplication", "--json", "--top", "4")
    assert done.code == 0, done.stderr
    return tuple(frozenset(_bare(f["long_name"]) for f in pair["functions"])
                 for pair in done.json()["pairs"])


def test_renamed_paths_give_the_same_pairs(pairs, renamed):
    """The same bytes under other paths pair the same functions at the same
    similarities: a path only orders ties."""
    moved = pair_names(renamed.json("duplication", "--json"))
    assert moved == {names: value for names, value in pairs.items() if names <= RENAMED_NAMES}
    assert len(moved) == 10


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


# --- line scheme rulings and the brute-force self-diff ------------------------------------

@pytest.fixture(scope="module")
def hand_rows(hand, tmp_path_factory):
    """crapkit's inventory rows for the hand set: the functions and their spans."""
    out = tmp_path_factory.mktemp("dup-rows") / "inventory.tsv"
    return list(analysis_inventory.run_inventory(hand.root, out).rows)


def _texts(files: dict) -> dict[str, list[str]]:
    return {path: text.splitlines() for path, text in files.items()}


def _file_windows(path: str, keep_docstrings: bool = False) -> set:
    """The windows of the one function a hand file holds, by the scheme or,
    with keep_docstrings, with its docstring line kept."""
    lines = _texts(hand_files())[path]
    if keep_docstrings:
        return symilar_adapter.windows(["".join(line.split()) for line in lines if line.strip()])
    return symilar_adapter.windows(symilar_adapter.normalized(lines, 1, len(lines)))


@rulings.applies("AO-DUP-STAR-LINE")
def test_a_code_line_starting_with_a_star_is_shingled(pairs):
    """A `**spread(items),` argument line is code; only comment lines are left out."""
    hand = symilar_adapter.containment(_file_windows("s1.py"), _file_windows("s2.py"))
    assert hand == 0.5
    rulings.pin_ruling("AO-DUP-STAR-LINE", oracle="none",
                       crapkit=pairs.get(frozenset({"star_a", "star_b"}), "none"))


@rulings.applies("AO-DUP-DOCSTRING-LINES")
def test_a_docstring_line_is_left_out_like_a_comment(pairs):
    """A docstring is a string literal (Python reference, "Docstrings"), yet a line
    starting with three quotes is left out, and the scheme the brute force and
    symilar read takes the same transform: raw 7/9, transformed 7/8."""
    raw = symilar_adapter.containment(_file_windows("k1.py", keep_docstrings=True),
                                      _file_windows("k2.py", keep_docstrings=True))
    transformed = symilar_adapter.containment(_file_windows("k1.py"), _file_windows("k2.py"))
    assert (round(raw, 4), transformed) == (0.7778, 0.875)
    rulings.pin_ruling("AO-DUP-DOCSTRING-LINES", crapkit=pairs[frozenset({"doc_a", "doc_b"})],
                       oracle=round(raw, 4))


def _named(found: dict) -> dict:
    return {frozenset(_bare(name) for _, _, name in key): value for key, value in found.items()}


def _without(found: dict, names: set) -> dict:
    return {key: value for key, value in found.items() if not key & names}


def test_brute_force_over_every_pair_gives_crapkit_s_pairs(hand_rows, pairs):
    """Self-diff: containment over every pair of shingled functions, with plain
    sets of line windows and no index, is crapkit's pair list. star_a and star_b
    hold an analysis-oracles-140 line and are set aside."""
    texts = _texts(hand_files())
    aside = {_bare(name) for _, _, name in symilar_adapter.set_aside(hand_rows, texts)}
    found = _named(symilar_adapter.brute_force(hand_rows, texts, 0.8))
    assert aside == {"star_a", "star_b"}
    assert len(pairs) < 50, "the default --top 50 cut the list"
    assert _without(pairs, aside) == _without(found, aside)


def _pairs_by_key(payload: dict) -> dict:
    return {tuple(sorted((f["path"], f["start"], f["long_name"]) for f in pair["functions"])):
            pair["similarity"] for pair in payload["pairs"]}


def _decoded(files: dict) -> dict[str, list[str]]:
    return {path: (text.decode("utf-8") if isinstance(text, bytes) else text).splitlines()
            for path, text in files.items()}


def _outside(found: dict, aside: set) -> dict:
    return {key: value for key, value in found.items() if not set(key) & aside}


def _python_corpus(request, name: str) -> tuple[dict, Path, list[dict]]:
    """(files, repo root, inventory rows) for the push hand set, crapkit's src,
    the standard library or a full-corpus Python member."""
    if name == "hand":
        return (hand_files(), request.getfixturevalue("hand").root,
                request.getfixturevalue("hand_rows"))
    if name in ("src", "stdlib"):
        measured = request.getfixturevalue(f"{name}_inventory")
        files = request.getfixturevalue(f"{name}_corpus").files
    else:
        files = analysis_corpora.member_files(request.getfixturevalue("full_corpus"), name,
                                              (".py",))
        measured = request.getfixturevalue("measure_set")(files)
    return files, Path(measured.root), list(measured.rows)


def _crapkit_pairs(root: Path, similarity: float) -> dict:
    return _pairs_by_key(drive.Driver(root).json(
        "duplication", "--similarity", str(similarity), "--top", "10000000"))


@pytest.mark.nightly
@pytest.mark.parametrize("corpus, similarity, floor", [("src", 0.01, 5), ("stdlib", 0.01, 150),
                                                       ("click", 0.01, 30)])
def test_brute_force_over_a_corpus_gives_crapkit_s_pairs(request, corpus, similarity, floor):
    """The self-diff over crapkit's own source, CPython's Lib at the tag the
    corpus pins for the running Python and click, at --similarity 0.01, so every
    pair that shares one window is compared (the pinned Lib: 103 files, 2,916
    functions and 252 pairs on 3.12)."""
    files, root, rows = _python_corpus(request, corpus)
    texts = _decoded(files)
    aside = symilar_adapter.set_aside(rows, texts)
    runlog.note("skipped_files", oracle=f"brute force {corpus}: functions set aside (ao-140)",
                count=len(aside))
    found = _outside(symilar_adapter.brute_force(rows, texts, similarity), aside)
    assert len(found) >= floor
    assert _outside(_crapkit_pairs(root, similarity), aside) == found


# --- pylint's symilar, the nightly oracle ---------------------------------------------------

@pytest.mark.nightly
@pytest.mark.parametrize("corpus, floor", [("hand", 12), ("src", 5), ("click", 30),
                                           ("requests", 5)])
def test_symilar_runs_give_crapkit_s_pairs(request, oracle, corpus, floor, tmp_path):
    """Every pair that shares one window (--similarity 0.01), with its
    containment worked from the runs symilar reports over one file per function."""
    oracle("pylint")
    files, root, rows = _python_corpus(request, corpus)
    texts = _decoded(files)
    aside = symilar_adapter.set_aside(rows, texts)
    runlog.note("skipped_files", oracle=f"symilar {corpus}: functions set aside (ao-140)",
                count=len(aside))
    found = _outside(symilar_adapter.pairs(rows, texts, tmp_path / "functions", 0.01), aside)
    assert len(found) >= floor
    assert _outside(_crapkit_pairs(root, 0.01), aside) == found


ONE_BODY = [f"value_{line} = combine(items, {line}) or limit" for line in range(10)]
EVERY_BODY = [f"entry_{line} = pick(items, limit, {line})" for line in range(10)]
GRID = ["grid = [", "    [", "    ],", "]"]  # one line of the four holds a word character
WINDOW = ["first = items[0]", "second = items[-1]", "middle = first + second",
          "span = middle - limit"]


def _one_window(name: str, shared: list[str]) -> str:
    """A 12-line function sharing only `shared`, 4 lines, with its twin: 1/9."""
    own = [f"{name}_{line} = {line} + limit" for line in range(7)]
    return _function(name, [*own[:3], *shared, *own[3:]])


def symilar_hand_files() -> dict:
    return {
        "scheme_a.py": _function("scheme_a", BODY), "scheme_b.py": _noisy("scheme_b"),
        "one.py": _function("one_a", ONE_BODY) + _function("one_b", ONE_BODY),
        **{f"every_{tag}.py": _function(f"every_{tag}", EVERY_BODY) for tag in "abc"},
        "content_a.py": _one_window("content_a", GRID),
        "content_b.py": _one_window("content_b", GRID),
        "window_a.py": _one_window("window_a", WINDOW),
        "window_b.py": _one_window("window_b", WINDOW),
    }


class SymilarHand(NamedTuple):
    root: Path  # the repo the hand cases are written in
    rows: list  # crapkit's inventory rows for them
    found: dict  # crapkit's pairs at --similarity 0.01, by bare names
    written: dict  # the adapter's function files: {file: (row, lines)}
    functions: Path  # the directory holding those files


@pytest.fixture(scope="module")
def symilar_hand(tmp_path_factory):
    files = symilar_hand_files()
    tree = {"crapkit.toml": analysis_inventory.config(analysis_inventory.languages_of(files)),
            **files}
    work = tmp_path_factory.mktemp("symilar-hand")
    driver = drive.Driver(analysis_inventory.build(tree, work / "repo"))
    assert driver.run("coverage").code == 0
    found = pair_names(driver.json("duplication", "--similarity", "0.01", "--top", "1000"))
    rows = list(analysis_inventory.run_inventory(driver.root, work / "rows.tsv").rows)
    written = symilar_adapter.function_files(rows, _texts(files), work / "functions")
    return SymilarHand(driver.root, rows, found, written, work / "functions")


def _files_of(written: dict, names: set) -> list[str]:
    return sorted(file for file, (row, _) in written.items() if _bare(row["long_name"]) in names)


def _raw_scheme(case: SymilarHand, work: Path) -> int:
    return len(symilar_adapter.couples(["scheme_a.py", "scheme_b.py"], case.root))


def _raw_one_file(case: SymilarHand, work: Path) -> int:
    return len(symilar_adapter.couples(["one.py"], case.root))


def _raw_content_lines(case: SymilarHand, work: Path) -> int:
    bare = symilar_adapter.function_files(case.rows, _texts(symilar_hand_files()), work, prefix="")
    return len(symilar_adapter.couples(_files_of(bare, {"content_a", "content_b"}), work))


def _raw_threshold(case: SymilarHand, work: Path) -> int:
    names = _files_of(case.written, {"window_a", "window_b"})
    return len(symilar_adapter.couples(names, case.functions, min_lines=4))


def _raw_every_couple(case: SymilarHand, work: Path) -> int:
    names = _files_of(case.written, {"every_a", "every_b", "every_c"})
    return len(symilar_adapter.report(names, case.functions, min_lines=3))


# ruling id -> (the case's functions, symilar's count of their pairs without the rule)
SYMILAR_RULES = {
    "AO-SYMILAR-SCHEME": ({"scheme_a", "scheme_b"}, _raw_scheme),
    "AO-SYMILAR-ONE-FILE": ({"one_a", "one_b"}, _raw_one_file),
    "AO-SYMILAR-CONTENT-LINES": ({"content_a", "content_b"}, _raw_content_lines),
    "AO-SYMILAR-THRESHOLD": ({"window_a", "window_b"}, _raw_threshold),
    "AO-SYMILAR-EVERY-COUPLE": ({"every_a", "every_b", "every_c"}, _raw_every_couple),
}


@pytest.mark.nightly
@pytest.mark.parametrize("ruling_id", sorted(SYMILAR_RULES))
def test_each_symilar_rule_is_pinned_by_a_hand_case(ruling_id, symilar_hand, oracle, tmp_path):
    """The case's pairs as crapkit lists them, as the adapter works them out,
    and how many symilar finds with the rule left out."""
    oracle("pylint")
    names, raw = SYMILAR_RULES[ruling_id]
    mine = {pair: value for pair, value in symilar_hand.found.items() if pair <= names}
    found = symilar_adapter.couples(sorted(symilar_hand.written), symilar_hand.functions)
    adapter = _named(symilar_adapter.symilar_pairs(symilar_hand.written, found, 0.01))
    assert {pair: value for pair, value in adapter.items() if pair <= names} == mine
    rulings.pin_ruling(ruling_id, crapkit=len(mine), oracle=raw(symilar_hand, tmp_path / "raw"))


def test_every_symilar_rule_has_a_hand_case():
    assert sorted(SYMILAR_RULES) == sorted(set(re.findall(r"AO-SYMILAR-[A-Z-]+[A-Z]",
                                                          symilar_adapter.__doc__)))


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


def test_jscpd_clone_classes_are_the_pairs_crapkit_reports(oracle, hand, hand_rows, pairs,
                                                           tmp_path):
    oracle("jscpd")
    found = jscpd_pairs(hand.root, hand_rows, tmp_path / "jscpd")
    small = {"copy_a", "copy_b", "factory", "factory.closure", "other"}
    mine = {names for names in pairs if names <= small and "factory" not in names}
    assert found == mine == {frozenset({"copy_a", "copy_b"}),
                             frozenset({"copy_a", "factory.closure"}),
                             frozenset({"copy_b", "factory.closure"})}
