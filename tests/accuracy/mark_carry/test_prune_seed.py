"""prune and seed counts: prune re-keys a carried mark and keeps an ambiguous
one, and seed counts a carried function as marked.

Three checks, each with its expected value worked without crapkit:

1. hand_prune_seed.tsv, a file's functions and CRAP, its marks before and
   after prune or seed, the counts (pruned, followed, re-keyed, added,
   tightened) and the marks kept because no single function carries them,
   worked by hand from docs/ratchet.md. `follow_renames`, `prune_ratchet`,
   `seed_ratchet` and `ambiguous_marks` give each row.
2. carry_oracle.py applies the same rule over lizard's long names; it agrees
   with every hand row.
3. Properties: prune and seed raise no mark and keep each value's bytes, and
   the marks prune keeps, re-keys and drops are the ones `keys.carriers`
   answers for. The scope flip that lets seed raise a mark is left out.

The probe row fails against 0.8.1's exact-key prune, which drops the mark:
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


def crapkit(name: str):
    return importlib.import_module(f"crapkit.{name}")


def hand_rows() -> list[dict]:
    lines = (HERE / "hand_prune_seed.tsv").read_bytes().decode("utf-8").splitlines()
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


HAND = hand_rows()


def pairs(cell: str) -> list[tuple[str, float]]:
    return [] if cell == "-" else [(name, float(value)) for name, value in
                                   (part.rsplit("=", 1) for part in cell.split(" | "))]


def names(cell: str) -> list[str]:
    return [] if cell == "-" else cell.split(" | ")


def fresh_rows(functions: list[tuple[str, float]], path: str = PATH) -> list:
    score = crapkit("score")
    return [score.ScoredRow("calc", path, name, 1 + 10 * n, 9 + 10 * n, 7, 7, 7, 9, 2, 1, 0.0, "cc_only",
                            crap, "decompose") for n, (name, crap) in enumerate(functions)]


def entries(marks: list[tuple[str, float]], path: str = PATH) -> list:
    entry = crapkit("ratchet").RatchetEntry
    return [entry(path, name, value) for name, value in marks]


def counts_of(row: dict) -> tuple[int, ...]:
    return tuple(int(n) for n in row["counts"].split(","))


def crapkit_answer(row: dict) -> tuple[list, tuple, list[str]]:
    """The marks after, the counts and the kept marks, as `ratchet prune` and
    `ratchet seed` reach them: renames first, then prune; or seed."""
    ratchet = crapkit("ratchet")
    fresh, prior = fresh_rows(pairs(row["functions"]), row["at"]), entries(pairs(row["prior"]))
    if row["action"] == "seed":
        after, added, tightened = ratchet.seed_ratchet(prior, fresh, target=CEILING)
        counts = (0, 0, ratchet.ratchet_delta(prior, after, fresh).rekeyed, added, tightened)
    else:
        followed, moved = ratchet.follow_renames(prior, fresh, {PATH: row["at"]} if row["at"] != PATH else {})
        after, dropped = ratchet.prune_ratchet(followed, fresh)
        counts = (dropped, moved, ratchet.ratchet_delta(followed, after, fresh).rekeyed, 0, 0)
    kept = ratchet.ambiguous_marks(after, fresh)
    return after, counts, [name for path in kept for name in kept[path]]


# --- check 1: the hand table ---------------------------------------------------------------------

@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_every_hand_row_gets_its_counts_from_prune_and_seed(row):
    after, counts, kept = crapkit_answer(row)

    assert [(e.path, e.long_name, f"{e.crap:.4f}") for e in after] == [
        (row["at"], name, f"{value:.4f}") for name, value in pairs(row["after"])]
    assert counts == counts_of(row)
    assert kept == names(row["kept"])


def test_the_hand_table_holds_every_count_both_actions_and_a_kept_mark():
    assert {row["action"] for row in HAND} == {"prune", "seed"}
    assert all(any(counts_of(row)[n] for row in HAND) for n in range(5))
    assert {row["action"] for row in HAND if row["kept"] != "-"} == {"prune", "seed"}


# --- check 2: the oracle over lizard's names -----------------------------------------------------

def oracle_answer(row: dict) -> tuple[dict, tuple, list[str]]:
    functions, prior = pairs(row["functions"]), dict(pairs(row["prior"]))
    keys = oracle.keyed([name for name, _ in functions])
    if row["action"] == "seed":
        after, added, tightened, rekeyed = oracle.seeded(keys, dict(zip(keys, (c for _, c in functions))),
                                                         prior, CEILING)
        return after, (0, 0, rekeyed, added, tightened), []
    after, dropped, rekeyed, kept = oracle.pruned(keys, prior)
    return after, (dropped, int(row["at"] != PATH), rekeyed, 0, 0), kept


@pytest.mark.parametrize("row", HAND, ids=lambda r: r["case"])
def test_the_oracle_agrees_with_every_hand_row(row):
    after, counts, kept = oracle_answer(row)

    assert sorted(after.items()) == sorted(pairs(row["after"]))
    assert counts == counts_of(row)
    if row["action"] == "prune":
        assert kept == names(row["kept"])


def test_lizard_keys_the_twin_rows_as_the_hand_table_does():
    body = "".join(f"    if a == {n}:\n        return {n}\n" for n in range(6)) + "    return b\n"
    source = "def classify(a, b, c=None):\n" + body
    sig = "classify( a , b , c = None )"

    assert oracle.keyed(oracle.long_names(source + "\n\n" + source)) == [sig, f"{sig}#2"]


def test_the_probe_row_fails_against_the_exact_key_prune():
    """Retro detection: 0.8.1's prune kept only the marks whose own key a row
    held, so it dropped the carried mark."""
    (probe,) = [row for row in HAND if row["case"].startswith("the probe: prune")]
    prior = entries(pairs(probe["prior"]))

    exact = crapkit("ratchet").exact_marks(prior, fresh_rows(pairs(probe["functions"])))

    assert (exact, len(prior) - len(exact)) == ([], 1) != ([], counts_of(probe)[0])


# --- check 3: properties -------------------------------------------------------------------------

NAMES = ["classify( a , b )", "classify( a , b , c = None )", "classify( x )", "grade( a )",
         "grade( a , b )"]
CRAP = st.floats(min_value=1.0, max_value=40.0, allow_nan=False).map(lambda value: round(value, 4))
MARK = st.integers(10_000, 400_000).map(lambda value: value / 10_000)


def expected_prune(functions: set, prior: dict, answers: dict) -> set:
    """(key name, value as written) for each mark prune keeps: under its
    carrier's key when it carried, under its own when a function holds it or
    the carry refused, none when nothing answered."""
    carry = crapkit("keys").Carry
    kept = set()
    for name, value in prior.items():
        answer = answers.get((PATH, name))
        if isinstance(answer, carry):
            kept.add((answer.new_key[1], f"{value:.4f}"))
        elif (PATH, name) in functions or answer is not None:
            kept.add((name, f"{value:.4f}"))
    return kept


@given(functions=st.dictionaries(st.sampled_from(NAMES), CRAP, max_size=4),
       prior=st.dictionaries(st.sampled_from(NAMES), MARK, max_size=4))
@pure
def test_prune_moves_marks_as_carriers_answers_and_keeps_their_bytes(functions, prior):
    ratchet, keys = crapkit("ratchet"), crapkit("keys")
    fresh, marks = fresh_rows(sorted(functions.items())), entries(sorted(prior.items()))
    held = set(keys.rows_by_key(fresh))

    pruned, dropped = ratchet.prune_ratchet(marks, fresh)

    answers = keys.carriers(held, keys.MarkIndex(marks).keys())
    assert {(e.long_name, f"{e.crap:.4f}") for e in pruned} == expected_prune(held, prior, answers)
    assert dropped == len(marks) - len(pruned)


@given(functions=st.dictionaries(st.sampled_from(NAMES), CRAP, max_size=4),
       prior=st.dictionaries(st.sampled_from(NAMES), MARK, max_size=4))
@pure
def test_seed_raises_no_mark_and_drops_none(functions, prior):
    ratchet, keys = crapkit("ratchet"), crapkit("keys")
    fresh, marks = fresh_rows(sorted(functions.items())), entries(sorted(prior.items()))
    carried = keys.carried_keys(set(keys.rows_by_key(fresh)), keys.MarkIndex(marks).keys())

    seeded, _, _ = ratchet.seed_ratchet(marks, fresh, target=CEILING)

    after = {e.long_name: e.crap for e in seeded}
    for name, value in prior.items():
        now = after.get(name, after.get(carried.get((PATH, name), (PATH, ""))[1]))
        assert now is not None and now <= value
        assert name in after or now < value
