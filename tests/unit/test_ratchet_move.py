"""Marks follow code that moved: the explicit `ratchet move` re-path, and the
rename detection prune consults before it calls a function gone. Pure."""
from crapkit.ratchet import RatchetEntry, follow_renames, move_marks
from crapkit.score import ScoredRow


def scored(path, name="hot( )", ccn=8, cov=0.0, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
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
