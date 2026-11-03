"""Mark value kept across a re-key: `rekey_marks` moves a mark's whole key and
writes the value back as the same string.

Three checks, each with its expected value worked without crapkit:

1. hand_rekey.tsv, a marks file's bytes before and after each move, worked
   from docs/ratchet.md's file format.
2. A property: no move set changes the multiset of value strings, and the
   count is the number of moves whose old key held a mark.
3. A round trip: a move set and its inverse give back the file, sha256 equal.

crapkit is loaded by name when a test runs (kit/closure.py follows import
statements only), so the expected side never borrows the code it checks.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure

HERE = Path(__file__).resolve().parent
HEADER = "path\tlong_name\tcrap\n"


def ratchet():
    return importlib.import_module("crapkit.ratchet")


def unescape(cell: str) -> str:
    return cell.replace("\\t", "\t").replace("\\n", "\n")


def parsed_moves(cell: str) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """`old path, old name, new path, new name` per line, tab-separated."""
    fields = [line.split("\t") for line in unescape(cell).split("\n") if line]
    return [((f[0], f[1]), (f[2], f[3])) for f in fields]


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_rekey.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


HAND = hand_rows()


def rekeyed(text: str, moves) -> tuple[str, int]:
    """crapkit's marks file after MOVES, written under the file's own stamps."""
    module = ratchet()
    entries, moved = module.rekey_marks(module.load_ratchet(text), moves)
    return module.dump_ratchet(entries, stamp=module.read_stamp(text),
                               key_version=module.read_key_version(text)), moved


# --- check 1: the hand table ---------------------------------------------------------------------

@pytest.mark.parametrize("row", [r for r in HAND if r["after"] != "refused"], ids=lambda r: r["case"])
def test_every_hand_move_writes_the_hand_bytes(row):
    after, moved = rekeyed(unescape(row["before"]), parsed_moves(row["moves"]))

    assert after.encode("utf-8") == unescape(row["after"]).encode("utf-8")
    assert moved == int(row["moved"])


@pytest.mark.parametrize("row", [r for r in HAND if r["after"] == "refused"], ids=lambda r: r["case"])
def test_every_hand_move_onto_a_marked_key_is_refused_naming_both(row):
    (old, new), = parsed_moves(row["moves"])

    with pytest.raises(ValueError) as refused:
        rekeyed(unescape(row["before"]), [(old, new)])

    assert f"{old[0]} {old[1]}" in str(refused.value)
    assert f"{new[0]} {new[1]}" in str(refused.value)


# --- checks 2 and 3: generated marks and moves ---------------------------------------------------

PATHS = ["calc/a.py", "calc/b.py", "lib/route.rs"]
NAMES = ["classify( a )", "classify( a )#2", "classify( a , b )", "grade( x )",
         "route cmd : & Cmd", "route cmd : & Cmd , x : u8", "total( n )"]
KEYS = [(path, name) for path in PATHS for name in NAMES]
VALUE = st.builds(lambda whole, part: f"{whole}.{part:04d}", st.integers(0, 99_999), st.integers(0, 9_999))


def marks_text(marks: dict) -> str:
    """docs/ratchet.md's form: the header, then one row per mark sorted by key."""
    return HEADER + "".join(f"{path}\t{name}\t{value}\n" for (path, name), value in sorted(marks.items()))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def values(text: str) -> Counter:
    return Counter(line.split("\t")[2] for line in text.splitlines()[1:])


@st.composite
def marks_and_moves(draw):
    """Marks on some keys, and moves from distinct old keys (marked or not) to
    distinct keys no mark holds, so no move is refused."""
    keys = draw(st.permutations(KEYS))
    marked = draw(st.integers(0, len(keys) // 3))
    marks = {key: draw(VALUE) for key in keys[:marked]}
    olds = draw(st.lists(st.sampled_from(keys), unique=True, max_size=6))
    free = [key for key in keys if key not in marks and key not in olds]
    news = draw(st.lists(st.sampled_from(free), unique=True, min_size=len(olds), max_size=len(olds)))
    return marks, list(zip(olds, news))


@given(case=marks_and_moves())
@pure
def test_no_move_set_changes_the_value_strings(case):
    marks, moves = case
    before = marks_text(marks)

    after, moved = rekeyed(before, moves)

    assert values(after) == values(before)
    assert moved == sum(old in marks for old, _ in moves)


@given(case=marks_and_moves())
@pure
def test_a_move_set_and_its_inverse_give_back_the_file(case):
    marks, moves = case
    before = marks_text(marks)

    there, _ = rekeyed(before, moves)
    back, _ = rekeyed(there, [(new, old) for old, new in moves])

    assert digest(back) == digest(before)
