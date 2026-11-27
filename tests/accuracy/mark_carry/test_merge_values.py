"""Merged mark value: `merge_ratchets` merges three marks files one mark at a
time, and a mark one side re-keyed or moved to another path is still one mark.

Three checks, each with its expected value worked without crapkit:

1. hand_merge.tsv, the merged file worked by hand from (base, ours, theirs)
   for the probe, its mirror and every case the ticket names.
2. A brute-force oracle: each side is an edit script over the base marks
   (keep, drop, tighten, re-key, move to another path). The oracle knows which
   base mark each line came from and applies the 3-way rule per base mark.
3. Hypothesis properties: merge(b, x, x) == x, merge(b, b, x) == x, ours and
   theirs swap freely, and no merged mark is above the lowest value any side
   holds for that mark.

crapkit is loaded by name when a test runs (kit/closure.py follows import
statements only), so the expected side never borrows the code it checks.
"""
from __future__ import annotations

import importlib
import itertools
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure

HERE = Path(__file__).resolve().parent


def ratchet():
    return importlib.import_module("crapkit.ratchet")


def unescape(cell: str) -> str:
    return cell.replace("\\t", "\t").replace("\\n", "\n")


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_merge.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def merged_text(base: str, ours: str, theirs: str) -> str:
    """crapkit's merged marks file, written under the base file's own stamps."""
    module = ratchet()
    merged = module.merge_ratchets(*(module.load_ratchet(text) for text in (base, ours, theirs)))
    return module.dump_ratchet(merged, stamp=module.read_stamp(base), key_version=module.read_key_version(base))


# --- check 1: the hand table ---------------------------------------------------------------------

@pytest.mark.parametrize("row", hand_rows(), ids=lambda r: r["case"])
def test_every_hand_merge_writes_the_hand_marks(row):
    sides = [unescape(row[name]) for name in ("base", "ours", "theirs")]

    assert merged_text(*sides).encode("utf-8") == unescape(row["merged"]).encode("utf-8")


# --- check 2: a brute-force oracle over edit scripts ---------------------------------------------

# Three base marks, each with its own bare name, so every edit below names one
# base mark unambiguously.
BASE = {("calc/a.py", "f( a )"): 7.0, ("calc/a.py", "g( b )"): 8.0, ("calc/b.py", "h( c )"): 9.0}
EDITS = ("keep", "drop", "tighten", "re-key c", "re-key d", "re-key c and tighten", "move")


def rekeyed(name: str, param: str) -> str:
    return f"{name[:-2]} , {param} )"


def edited(key: tuple[str, str], value: float, edit: str) -> tuple[tuple[str, str], float] | None:
    """The line a side holds for one base mark after one edit, or None when it dropped it."""
    path, name = key
    lines = {"keep": (key, value), "drop": None, "tighten": (key, value - 1.5),
             "re-key c": ((path, rekeyed(name, "c = None")), value),
             "re-key d": ((path, rekeyed(name, "d = 1")), value),
             "re-key c and tighten": ((path, rekeyed(name, "c = None")), value - 0.5),
             "move": ((f"moved/{path}", name), value)}
    return lines[edit]


def side(script: dict) -> dict:
    """A side's marks: each base mark's line after its edit."""
    lines = [edited(key, value, script[key]) for key, value in BASE.items()]
    return dict(line for line in lines if line is not None)


def three_way(b, o, t):
    """git's rule for one mark: the changed side wins; both changed keeps the lower."""
    if o == t:
        return o
    if o == b:
        return t
    if t == b:
        return o
    return min(v for v in (o, t) if v is not None)


def oracle(ours: dict, theirs: dict) -> dict:
    """The merge, worked per base mark from the two edit scripts."""
    merged = {}
    for key, value in BASE.items():
        o, t = edited(key, value, ours[key]), edited(key, value, theirs[key])
        crap = three_way(value, o and o[1], t and t[1])
        moved = sorted({line[0] for line in (o, t) if line is not None and line[0] != key})
        merged.update(dict.fromkeys(moved or [key], crap) if crap is not None else {})
    return merged


def crapkit_merge(base: dict, ours: dict, theirs: dict) -> dict:
    module = ratchet()
    entries = [[module.RatchetEntry(path, name, crap) for (path, name), crap in marks.items()]
               for marks in (base, ours, theirs)]
    return {(e.path, e.long_name): e.crap for e in module.merge_ratchets(*entries)}


def test_every_pair_of_edits_to_one_mark_merges_as_the_oracle_says():
    """Each (ours, theirs) edit pair on f, with g and h kept on one side and
    tightened on the other."""
    for o, t in itertools.product(EDITS, repeat=2):
        ours = {**dict.fromkeys(BASE, "keep"), ("calc/a.py", "f( a )"): o}
        theirs = {**dict.fromkeys(BASE, "tighten"), ("calc/a.py", "f( a )"): t}

        assert crapkit_merge(BASE, side(ours), side(theirs)) == oracle(ours, theirs), (o, t)


SCRIPT = st.fixed_dictionaries({key: st.sampled_from(EDITS) for key in BASE})


@given(ours=SCRIPT, theirs=SCRIPT)
@pure
def test_any_two_edit_scripts_merge_as_the_oracle_says(ours, theirs):
    assert crapkit_merge(BASE, side(ours), side(theirs)) == oracle(ours, theirs)


# --- check 3: properties at merge_ratchets -------------------------------------------------------

@given(script=SCRIPT)
@pure
def test_a_side_merged_with_itself_or_with_base_is_that_side(script):
    edited_side = side(script)

    assert crapkit_merge(BASE, edited_side, edited_side) == edited_side
    assert crapkit_merge(BASE, BASE, edited_side) == edited_side
    assert crapkit_merge(BASE, edited_side, BASE) == edited_side


@given(ours=SCRIPT, theirs=SCRIPT)
@pure
def test_the_merge_is_symmetric_and_never_above_the_lowest_value_a_side_holds(ours, theirs):
    merged = crapkit_merge(BASE, side(ours), side(theirs))

    assert merged == crapkit_merge(BASE, side(theirs), side(ours))
    for key, value in BASE.items():
        lines = [edited(key, value, script[key]) for script in (ours, theirs)]
        lowest = min([value] + [line[1] for line in lines if line is not None])
        landed = {line[0] for line in lines if line is not None} | {key}
        assert all(merged[at] <= lowest for at in landed if at in merged), (key, merged)
