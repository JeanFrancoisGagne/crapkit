"""Marks follow code that moved: the explicit `ratchet move` re-path, the
rename detection prune consults before it calls a function gone, and
`rekey_marks`, the whole-key rewrite every writer of a mark's key goes through.
Pure."""
from fractions import Fraction

import pytest

from accuracy.kit import exact

from crapkit.ratchet import (MetricStamp, RatchetEntry, dump_ratchet, follow_renames, load_ratchet,
                             move_marks, rekey_marks)
from crapkit.score import ScoredRow


def scored(path, name="hot( )", ccn=8, cov=0.0, scope="src"):
    c = float(exact.crap(ccn, Fraction(cov)))
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, "decompose")


def test_move_repaths_one_exact_file_at_its_recorded_value():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0), RatchetEntry("src/b.ts", "g( )", 12.0)]
    entries, moved = move_marks(prior, "src/a.ts", "lib/a.ts")
    assert moved == 1
    assert entries == [RatchetEntry("lib/a.ts", "f( )", 30.0),
                       RatchetEntry("src/b.ts", "g( )", 12.0)]


def test_move_of_a_directory_prefix_carries_the_whole_subtree():
    prior = [RatchetEntry("src/deep/a.ts", "f( )", 30.0),
             RatchetEntry("src/b.ts", "g( )", 12.0),
             RatchetEntry("other/c.ts", "h( )", 9.0)]
    entries, moved = move_marks(prior, "src/", "lib/")
    assert moved == 2
    assert [e.path for e in entries] == ["lib/b.ts", "lib/deep/a.ts", "other/c.ts"]


def test_a_directory_prefix_tolerates_a_missing_trailing_slash_on_the_target():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0)]
    entries, _ = move_marks(prior, "src/", "lib")
    assert entries == [RatchetEntry("lib/a.ts", "f( )", 30.0)]


def test_an_exact_path_never_matches_a_prefix():
    prior = [RatchetEntry("src/app.ts", "f( )", 30.0)]
    entries, moved = move_marks(prior, "src/app", "lib/app")
    assert moved == 0 and entries == prior


def test_move_reports_zero_when_nothing_matches():
    prior = [RatchetEntry("src/a.ts", "f( )", 30.0)]
    entries, moved = move_marks(prior, "src/gone.ts", "lib/gone.ts")
    assert moved == 0 and entries == prior


def test_follow_renames_moves_a_mark_whose_file_git_renamed():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/b.ts")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 1
    assert entries == [RatchetEntry("src/b.ts", "hot( )", 30.0)], "the recorded value travels"


def test_follow_renames_leaves_a_mark_whose_function_is_still_at_its_old_path():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/a.ts"), scored("src/b.ts")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 0 and entries == prior, "a copy is not a move; the mark stays put"


def test_follow_renames_leaves_a_mark_when_the_function_is_absent_at_the_new_path():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    fresh = [scored("src/b.ts", name="other( )")]
    entries, moved = follow_renames(prior, fresh, {"src/a.ts": "src/b.ts"})
    assert moved == 0 and entries == prior, "the file moved but the function did not survive it"


def test_follow_renames_without_a_rename_map_changes_nothing():
    prior = [RatchetEntry("src/a.ts", "hot( )", 30.0)]
    entries, moved = follow_renames(prior, [], {})
    assert moved == 0 and entries == prior


# --- rekey_marks: one move rewrites a mark's whole key ---------------------------

HEADER = "path\tlong_name\tcrap\n"
MARKS = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
         "src/b.py\tgrade( x )\t31.5000\n"
         "src/c.py\ttotal( n )\t1234.5678\n")
TWINS = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
         "src/b.py\tclassify( a )#2\t31.5000\n")
RANKED = (HEADER + "src/b.py\tclassify( a )\t7.0000\n"
          "src/b.py\trank( q )\t31.5000\n"
          "src/c.py\ttotal( n )\t1234.5678\n")


def b(name):
    return "src/b.py", name


# (case, the marks file, the moves, the marks file after them, the count)
REKEY = [
    ("one move keeps every value string and re-sorts", MARKS,
     [(("src/c.py", "total( n )"), ("src/a.py", "total( n , m )"))],
     HEADER + "src/a.py\ttotal( n , m )\t1234.5678\n"
              "src/b.py\tclassify( a )\t7.0000\n"
              "src/b.py\tgrade( x )\t31.5000\n", 1),
    ("an empty move set changes nothing", MARKS, [], MARKS, 0),
    ("a move whose old key holds no mark counts 0", MARKS,
     [(b("classify( z )"), b("classify( z , y )"))], MARKS, 0),
    ("a twin key moves like any other key", TWINS,
     [(b("classify( a )#2"), b("classify( a , b )"))],
     HEADER + "src/b.py\tclassify( a )\t7.0000\n"
              "src/b.py\tclassify( a , b )\t31.5000\n", 1),
    ("an unmarked move sorted first takes no key from a marked move", MARKS,
     [(b("alpha( q )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))], RANKED, 1),
    ("an unmarked move sorted last takes no key from a marked move", MARKS,
     [(b("zeta( q )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))], RANKED, 1),
]


@pytest.mark.parametrize("case, text, moves, after, count", REKEY, ids=[r[0] for r in REKEY])
def test_rekey_marks_answers_the_table(case, text, moves, after, count):
    entries, moved = rekey_marks(load_ratchet(text), moves)

    assert moved == count
    assert dump_ratchet(entries, stamp=MetricStamp.NONE) == after


def test_rekey_marks_refuses_a_move_onto_a_marked_key_and_names_both():
    with pytest.raises(ValueError) as refused:
        rekey_marks(load_ratchet(MARKS), [(b("classify( a )"), b("grade( x )"))])

    assert "src/b.py classify( a )" in str(refused.value)
    assert "src/b.py grade( x )" in str(refused.value)


def test_rekey_marks_refuses_two_moves_onto_one_key():
    moves = [(b("classify( a )"), b("rank( q )")), (b("grade( x )"), b("rank( q )"))]

    with pytest.raises(ValueError, match=r"another mark moves to src/b\.py rank\( q \)$"):
        rekey_marks(load_ratchet(MARKS), moves)
