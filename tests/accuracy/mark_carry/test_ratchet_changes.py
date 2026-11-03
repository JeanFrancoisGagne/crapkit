"""ratchet_changes counts: a passing verify re-keys a carried mark and counts
it, and the ratchet check reads the function that carries a mark.

Three checks, each with its expected value worked without crapkit:

1. hand_changes.tsv, a file's functions and fresh CRAP, its marks before and
   after the tighten, the counts (dropped, tightened, rekeyed) and the ratchet
   check's verdict, worked by hand from docs/ratchet.md. `update_ratchet`,
   `ratchet_delta` and verify's ratchet check give each row.
2. carry_oracle.py pairs marks to functions by the same rule over lizard's
   long names and diffs the two marks files' text line by line; it agrees
   with every hand row.
3. Properties: every prior mark is exactly one of dropped, kept, tightened or
   rekeyed, tightened and rekeyed allowed together; no mark rises; and the
   ratchet check's verdict on a carried function equals its verdict with the
   mark written under the function's own key.

The probe row fails against 0.8.1's counts, which read the re-key as a drop:
the retro check. crapkit is loaded by name when a test runs.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure
from accuracy.mark_carry import carry_oracle as oracle

HERE = Path(__file__).resolve().parent
PATH = "calc/grade.py"
CEILING = 6
OLD, SIG = "classify( a , b )", "classify( a , b , c = None )"


def crapkit(name: str):
    return importlib.import_module(f"crapkit.{name}")


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_changes.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


HAND = hand_rows()


def pairs(cell: str) -> list[tuple[str, float]]:
    return [] if cell == "-" else [(name, float(value)) for name, value in
                                   (part.rsplit("=", 1) for part in cell.split(" | "))]


def functions_of(row: dict) -> list[tuple[str, float]]:
    return pairs(row["functions"])


def fresh_rows(functions: list[tuple[str, float]]) -> list:
    score = crapkit("score")
    return [score.ScoredRow("calc", PATH, name, 1 + 10 * n, 9 + 10 * n, 7, 7, 7, 9, 2, 1, 0.0, "cc_only",
                            crap, "decompose") for n, (name, crap) in enumerate(functions)]


def entries(marks: list[tuple[str, float]]) -> list:
    entry = crapkit("ratchet").RatchetEntry
    return [entry(PATH, name, value) for name, value in marks]


def marks_text(marks: list) -> str:
    ratchet = crapkit("ratchet")
    return ratchet.dump_ratchet(marks, stamp=ratchet.metric_version(), key_version=1)


def crapkit_answer(functions: list[tuple[str, float]], prior: list[tuple[str, float]]) -> tuple:
    """What a passing verify writes and counts, and what its ratchet check finds."""
    ratchet, verify = crapkit("ratchet"), crapkit("verify")
    fresh, marks = fresh_rows(functions), entries(prior)
    updated = ratchet.update_ratchet(marks, fresh, target=CEILING)
    found = verify.evaluate(fresh=fresh, changed_ranges={}, ratchet=marks, baseline_failures=set(),
                            fresh_failures=set(), target=CEILING).ratchet_regressions
    return updated, tuple(ratchet.ratchet_delta(marks, updated, fresh)), found


def check_of(found) -> str:
    if not found:
        return "ok"
    (rise,) = found
    return f"rise {rise.long_name}" + (f" from {rise.carried_from}" if rise.carried_from else "")


def counts_of(row: dict) -> tuple | None:
    return None if row["counts"] == "-" else tuple(int(n) for n in row["counts"].split(","))


# --- check 1: the hand table ---------------------------------------------------------------------

@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_every_hand_row_gets_its_counts_from_the_update(row):
    updated, counts, found = crapkit_answer(functions_of(row), pairs(row["prior"]))

    assert check_of(found) == row["check"]
    if row["check"] == "ok":
        assert [(e.long_name, e.crap) for e in updated] == pairs(row["updated"])
        assert counts == counts_of(row)


def test_the_hand_table_holds_every_count_and_both_verdicts():
    counted = [counts_of(row) for row in HAND if row["check"] == "ok"]
    assert all(any(c[n] for c in counted) for n in range(3))
    assert {row["check"].split()[0] for row in HAND} == {"ok", "rise"}


# --- check 2: the oracle over lizard's names and the files' text ---------------------------------

def oracle_answer(row: dict) -> tuple:
    functions, prior = functions_of(row), dict(pairs(row["prior"]))
    keys = oracle.keyed([name for name, _ in functions])
    craps = dict(zip(keys, (crap for _, crap in functions)))
    after = oracle.updated(keys, craps, prior, CEILING)
    found = oracle.rises(keys, craps, prior)
    text = marks_text(entries(sorted(after.items())))
    return after, oracle.counts(marks_text(entries(prior.items())), text, keys), found


@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_the_oracle_agrees_with_every_hand_row(row):
    after, counts, found = oracle_answer(row)

    check = "ok" if not found else " ".join(["rise", found[0][0], *(["from", found[0][1]] if found[0][1] else [])])
    assert check == row["check"]
    if row["check"] == "ok":
        assert sorted(after.items()) == sorted(pairs(row["updated"]))
        assert counts == counts_of(row)


def test_lizard_names_the_probe_as_the_hand_table_does():
    body = "".join(f"    if a == {n}:\n        return {n}\n" for n in range(6)) + "    return b\n"

    assert oracle.long_names("def classify(a, b, c=None):\n" + body) == [SIG]


def test_the_probe_row_fails_against_the_exact_key_counts():
    """Retro detection: 0.8.1's counts read the mark missing from the update
    as dropped."""
    (probe,) = [row for row in HAND if row["case"].startswith("the probe:")]
    marks, updated = entries(pairs(probe["prior"])), entries(pairs(probe["updated"]))

    assert tuple(crapkit("ratchet").ratchet_delta(marks, updated)) == (1, 0, 0) != counts_of(probe)


# --- check 3: properties -------------------------------------------------------------------------

NAMES = [OLD, SIG, "classify( x )", "grade( a )", "grade( a , b )"]
CRAP = st.floats(min_value=1.0, max_value=40.0, allow_nan=False).map(lambda value: round(value, 4))
MARK = st.integers(10_000, 400_000).map(lambda value: value / 10_000)


@given(functions=st.dictionaries(st.sampled_from(NAMES), CRAP, max_size=4),
       prior=st.dictionaries(st.sampled_from(NAMES), MARK, max_size=4))
@pure
def test_every_mark_moves_one_way_and_none_rises(functions, prior):
    ratchet = crapkit("ratchet")
    fresh, marks = fresh_rows(sorted(functions.items())), entries(sorted(prior.items()))
    updated = ratchet.update_ratchet(marks, fresh, target=CEILING)
    delta = ratchet.ratchet_delta(marks, updated, fresh)

    after = {e.long_name: e.crap for e in updated}
    found = fates(prior, after, sorted(functions))

    assert all(fate in FATES for fate, _ in found)
    assert all(now is None or now <= was for (_, now), was in zip(found, prior.values()))
    assert tuple(delta) == tuple(sum(name in fate for fate, _ in found)
                                 for name in ("dropped", "tightened", "rekeyed"))
    assert len(updated) == len(prior) - delta.dropped


FATES = ({"dropped"}, {"kept"}, {"tightened"}, {"rekeyed"}, {"rekeyed", "tightened"})


def fates(prior: dict[str, float], after: dict[str, float], keys: list[str]) -> list[tuple[set, float | None]]:
    """Each prior mark's moves and its value after, read from the two mark
    sets by the oracle's pairing."""
    found = []
    for name, value in prior.items():
        key = name if name in after else oracle.carrier(keys, prior, name)
        now = None if key is None else after.get(key)
        fate = set() if now is None or key == name else {"rekeyed"}
        fate |= {"dropped"} if now is None else {"tightened"} if now < value else set()
        found.append((fate or {"kept"}, now))
    return found


@given(crap=st.floats(min_value=6.5, max_value=80.0, allow_nan=False).map(lambda value: round(value, 5)),
       mark=st.integers(65_000, 800_000).map(lambda value: value / 10_000))
@pure
def test_a_carried_check_equals_the_check_under_the_function_s_own_key(crap, mark):
    carried = crapkit_answer([(SIG, crap)], [(OLD, mark)])[2]
    own = crapkit_answer([(SIG, crap)], [(SIG, mark)])[2]

    assert [r._replace(carried_from=None) for r in carried] == own
    assert bool(own) == (round(crap, 4) > mark)
