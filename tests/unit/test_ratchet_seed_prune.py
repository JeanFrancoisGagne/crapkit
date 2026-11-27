"""Ratchet seed/prune: first-class entry and exit for marks. Seeding never raises one.

A signature edit carries a mark (`keys.carriers`): prune re-keys it at its
recorded value instead of dropping it, seed counts the carried function as
marked, and a mark no single function carries is kept and named.
"""
from fractions import Fraction

import pytest

from accuracy.kit import exact

from crapkit.ratchet import (RatchetEntry, ambiguous_marks, exact_marks, follow_renames,
                             prune_ratchet, raised_marks, ratchet_delta, seed_ratchet)
from crapkit.score import ScoredRow


def scored(path="src/a.ts", name="f( )", ccn=8, cov=0.0, scope="src"):
    c = float(exact.crap(ccn, Fraction(cov)))
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_seed_marks_only_functions_over_their_ceiling():
    rows = [scored(name="hot( )", ccn=8, cov=0.0), scored(name="fine( )", ccn=2, cov=1.0)]
    entries, added, tightened = seed_ratchet([], rows, target=6)
    assert [e.long_name for e in entries] == ["hot( )"]
    assert added == 1 and tightened == 0


def test_seed_never_raises_an_existing_mark():
    prior = [RatchetEntry("src/a.ts", "hot( )", 10.0)]  # audited debt, lower than fresh
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]  # fresh crap = 72
    entries, added, tightened = seed_ratchet(prior, rows, target=6)
    assert entries == prior, "seed must keep the lower audited mark"
    assert added == 0 and tightened == 0


def test_seed_tightens_a_looser_mark_and_is_idempotent():
    prior = [RatchetEntry("src/a.ts", "hot( )", 500.0)]
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]  # 72
    entries, added, tightened = seed_ratchet(prior, rows, target=6)
    assert entries[0].crap == 72.0 and added == 0 and tightened == 1
    again, added2, tightened2 = seed_ratchet(entries, rows, target=6)
    assert again == entries and added2 == 0 and tightened2 == 0


def test_seed_respects_scope_targets():
    rows = [scored(name="hot( )", ccn=8, cov=0.0, scope="legacy")]
    entries, added, _ = seed_ratchet([], rows, target=6, scope_targets={"legacy": 100})
    assert entries == [] and added == 0


def test_prune_drops_only_functions_gone_from_the_run():
    prior = [RatchetEntry("src/a.ts", "gone( )", 30.0),
             RatchetEntry("src/a.ts", "hot( )", 72.0)]
    rows = [scored(name="hot( )", ccn=8, cov=0.0)]
    entries, dropped = prune_ratchet(prior, rows)
    assert [e.long_name for e in entries] == ["hot( )"]
    assert dropped == 1


# --- a mark a signature edit carried -----------------------------------------------

PATH, MOVED = "calc/grade.py", "calc/grading.py"
OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"


def row(name: str, crap: float, start: int = 1, path: str = PATH) -> ScoredRow:
    """A cc-only function at ccn 7 and the given CRAP."""
    return ScoredRow("calc", path, name, start, start + 8, 7, 7, 7, 9, 2, 1, 0.0, "cc_only", crap,
                     "decompose")


def mark(name: str, crap: float, path: str = PATH) -> RatchetEntry:
    return RatchetEntry(path, name, crap)


def pruned(prior: list, fresh: list) -> tuple[list, int, int]:
    """prune's marks, its dropped count and its re-keyed count, as `ratchet prune` prints them."""
    entries, dropped = prune_ratchet(prior, fresh)
    return entries, dropped, ratchet_delta(prior, entries, fresh).rekeyed


def test_prune_re_keys_the_probe_s_mark_at_its_recorded_value():
    entries, dropped, rekeyed = pruned([mark(OLD, 7.0)], [row(NEW, 7.0)])

    assert (entries, dropped, rekeyed) == ([mark(NEW, 7.0)], 0, 1)


@pytest.mark.parametrize("fresh, prior, kept", [
    pytest.param([row(NEW, 9.0), row(NEW, 9.0, start=20)], [mark(OLD, 7.0)], [OLD], id="twins"),
    pytest.param([row(NEW, 9.0), row("classify( q )", 9.0, start=20)], [mark(OLD, 7.0)], [OLD],
                 id="two-candidates"),
    pytest.param([row(NEW, 9.0)], [mark(OLD, 7.0), mark("classify( a , c )", 9.0)],
                 [OLD, "classify( a , c )"], id="two-marks-left"),
])
def test_prune_keeps_a_mark_no_single_function_carries_and_names_it(fresh, prior, kept):
    entries, dropped, rekeyed = pruned(prior, fresh)

    assert (entries, dropped, rekeyed) == (prior, 0, 0)
    assert ambiguous_marks(entries, fresh) == {PATH: kept}


@pytest.mark.parametrize("spelled", ["uvx crapkit", "/usr/bin/python3 -P -m crapkit"])
def test_the_kept_marks_line_names_crapkit_the_way_this_process_runs_it(monkeypatch, capsys,
                                                                          spelled):
    """uvx puts no `crapkit` on PATH and a module-form install runs through its
    interpreter, so the kept line's move names crapkit through invocation._self."""
    from crapkit.cli import ratchet_cmds

    monkeypatch.setattr(ratchet_cmds, "_self", lambda: spelled)

    ratchet_cmds._print_kept("crapkit-ratchet.tsv", [mark(OLD, 7.0)],
                             [row(NEW, 9.0), row(NEW, 9.0, start=20)])

    assert capsys.readouterr().out == (
        f"crapkit-ratchet.tsv: kept 1 mark(s) in {PATH} that no single function carries: {OLD}; "
        f"run {spelled} ratchet move --function {PATH} OLD NEW\n")


def test_prune_drops_a_mark_whose_file_holds_no_unmarked_function_of_its_name():
    entries, dropped, rekeyed = pruned([mark(OLD, 7.0)], [row("grade( x )", 9.0)])

    assert (entries, dropped, rekeyed) == ([], 1, 0)


def test_prune_keeps_the_key_a_function_holds_when_both_branches_re_keyed_one_mark():
    """mission-4-05's merge result: each branch moved the mark to another key."""
    prior = [mark(NEW, 7.0), mark("classify( a , b , d = None )", 7.0)]

    assert pruned(prior, [row(NEW, 7.0)]) == ([mark(NEW, 7.0)], 1, 0)


def test_a_re_key_keeps_every_value_s_bytes():
    prior = [mark(OLD, 7.0), mark("grade( x )", 31.5), mark("total( n )", 1234.5678)]
    fresh = [row(NEW, 7.0), row("grade( x , y )", 40.0, start=20), row("total( n , m )", 2000.0, start=40)]

    entries, _, rekeyed = pruned(prior, fresh)

    assert rekeyed == 3
    assert [f"{e.crap:.4f}" for e in entries] == ["7.0000", "31.5000", "1234.5678"]


def test_seed_counts_the_probe_s_carried_function_as_marked():
    entries, added, tightened = seed_ratchet([mark(OLD, 7.0)], [row(NEW, 7.0)], target=6)

    assert (entries, added, tightened) == ([mark(OLD, 7.0)], 0, 0)
    assert ratchet_delta([mark(OLD, 7.0)], entries, [row(NEW, 7.0)]).rekeyed == 0


def test_seed_re_keys_and_tightens_a_carried_mark_the_function_fell_under():
    entries, added, tightened = seed_ratchet([mark(OLD, 7.0)], [row(NEW, 6.5)], target=6)

    assert (entries, added, tightened) == ([mark(NEW, 6.5)], 0, 1)
    assert ratchet_delta([mark(OLD, 7.0)], entries, [row(NEW, 6.5)]).rekeyed == 1


def test_seed_adds_no_mark_for_a_function_whose_carry_refused():
    fresh = [row(NEW, 9.0), row(NEW, 9.0, start=20)]

    entries, added, tightened = seed_ratchet([mark(OLD, 7.0)], fresh, target=6)

    assert (entries, added, tightened) == ([mark(OLD, 7.0)], 0, 0)
    assert ambiguous_marks(entries, fresh) == {PATH: [OLD]}


def test_seed_with_scope_marks_rising_replaces_a_carried_mark_with_the_run_s_value():
    """A seed that flips a scope to measured takes the run's values, a carried
    mark included; the old key leaves."""
    entries, added, tightened = seed_ratchet([mark(OLD, 7.0)], [row(NEW, 9.0)], target=6,
                                             raises=lambda path: True)

    assert (entries, added, tightened) == ([mark(NEW, 9.0)], 0, 0)
    assert ambiguous_marks(entries, [row(NEW, 9.0)]) == {}


def test_seed_with_scope_marks_rising_marks_twins_at_the_run_s_value():
    """The flip replaces the scope's marks, so the refused carry stops no mark:
    each twin takes the run's value under its own key and no kept line names
    the scope. The mark that left stays for prune, as every seed leaves one."""
    fresh = [row(NEW, 9.0), row(NEW, 9.0, start=20)]

    entries, added, tightened = seed_ratchet([mark(OLD, 7.0)], fresh, target=6,
                                             raises=lambda path: True)

    assert (entries, added, tightened) == ([mark(OLD, 7.0), mark(NEW, 9.0), mark(f"{NEW}#2", 9.0)], 2, 0)
    assert ambiguous_marks(entries, fresh) == {}


def test_raised_marks_reads_a_re_keyed_mark_against_the_mark_it_carried():
    """The flip seed's line counts the rise of a carried mark it re-keyed."""
    fresh = [row(NEW, 9.0)]
    entries, _, _ = seed_ratchet([mark(OLD, 7.0)], fresh, target=6, raises=lambda path: True)

    assert raised_marks([mark(OLD, 7.0)], entries, fresh) == [(mark(NEW, 9.0), 7.0)]


def test_an_unproved_marks_file_seeds_and_prunes_by_the_exact_key():
    """Key version 0: no row proves which function a mark's key named, so seed
    adds the new key beside the old one and prune drops the old one."""
    fresh = [row(NEW, 7.0)]

    entries, added, _ = seed_ratchet([mark(OLD, 7.0)], fresh, target=6, carry=False)

    assert (entries, added) == ([mark(OLD, 7.0), mark(NEW, 7.0)], 1)
    assert exact_marks([mark(OLD, 7.0)], fresh) == []


def test_follow_renames_re_paths_a_mark_carried_at_the_new_path_and_prune_re_keys_it():
    prior, fresh = [mark(OLD, 7.0)], [row(NEW, 7.0, path=MOVED)]

    followed, moved = follow_renames(prior, fresh, {PATH: MOVED})
    entries, dropped, rekeyed = pruned(followed, fresh)

    assert (followed, moved) == ([mark(OLD, 7.0, path=MOVED)], 1)
    assert (entries, dropped, rekeyed) == ([mark(NEW, 7.0, path=MOVED)], 0, 1)


def test_follow_renames_leaves_a_copied_file_s_mark_where_it_is():
    prior = [mark(OLD, 7.0)]
    fresh = [row(OLD, 7.0), row(NEW, 7.0, path=MOVED)]

    assert follow_renames(prior, fresh, {PATH: MOVED}) == (prior, 0)


def test_follow_renames_of_an_unproved_marks_file_follows_only_the_same_key():
    """Key version 0 proves no carry: a rename plus a signature edit moves no
    mark, so prune drops it, as 0.8.1 did. A rename alone still moves it."""
    prior = [mark(OLD, 7.0)]

    signed = follow_renames(prior, [row(NEW, 7.0, path=MOVED)], {PATH: MOVED}, carry=False)
    renamed = follow_renames(prior, [row(OLD, 7.0, path=MOVED)], {PATH: MOVED}, carry=False)

    assert (signed, renamed) == ((prior, 0), ([mark(OLD, 7.0, path=MOVED)], 1))


def test_the_ratchet_help_says_prune_re_keys_a_carried_mark_and_keeps_an_ambiguous_one(capsys):
    from crapkit.cli import main

    with pytest.raises(SystemExit):
        main(["ratchet", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())

    assert "a mark a signature edit carried re-keys to its function" in help_text
    assert "a mark no single function carries stays" in help_text
