"""`git diff -U0` bodies consumed by their counts, and change coupling's cut-offs.

git-diff(1) "Combined diff format" and the unified format: a hunk header
`@@ -a,b +c,d @@` owes b old and d new lines, an omitted count is 1, and
`\\ No newline at end of file` is no line of either side. Paths follow `+++ `,
with `b/` only when git prefixes them. coupling.py: a pair is kept at or above
the minimum confidence, a low one does not end the scan, a log opening on a
severed commit header reads that header as no path, and `top` defaults to 50.
"""
from crapkit.coupling import _commit_file_sets, _rank_pairs, change_coupling
from crapkit.diffparse import changed_ranges

TWO_FILES = "+++ b/a.py\n{hunk}\n-x\n+y\n+++ b/c.py\n@@ -9 +9 @@\n-p\n+q\n"


def test_a_hunk_with_omitted_counts_owes_one_line_a_side():
    assert changed_ranges(TWO_FILES.format(hunk="@@ -5 +5 @@")) == {"a.py": [(5, 5)],
                                                                   "c.py": [(9, 9)]}


def test_a_hunk_owes_the_old_count_its_header_names():
    assert changed_ranges(TWO_FILES.format(hunk="@@ -5,1 +5,1 @@")) == {"a.py": [(5, 5)],
                                                                       "c.py": [(9, 9)]}


def test_a_no_newline_marker_is_no_line_and_an_added_plus_line_is_body():
    diff = ("+++ b/a.py\n@@ -1 +1,2 @@\n-a\n\\ No newline at end of file\n+b\n+++ c\n"
            "@@ -9 +9 @@\n-p\n+q\n")

    assert changed_ranges(diff) == {"a.py": [(1, 2), (9, 9)]}


def test_a_path_git_did_not_prefix_keeps_every_character():
    assert changed_ranges("+++ src/a.py\n@@ -1 +1 @@\n-a\n+b\n") == {"src/a.py": [(1, 1)]}


def test_a_hunk_before_any_file_header_lands_nowhere():
    assert changed_ranges("@@ -1 +1 @@\n-a\n+b\n") == {}


def test_a_pair_at_the_minimum_confidence_is_kept_and_a_low_one_ends_nothing():
    """(a, b): 2 of 4 commits, 0.5; (c, d): 1 of 4, 0.25; (e, f): 2 of 2, 1.0."""
    counts = {"a": 4, "b": 4, "c": 4, "d": 4, "e": 2, "f": 2}
    pairs = {("c", "d"): 1, ("a", "b"): 2, ("e", "f"): 2}

    assert [p["files"] for p in _rank_pairs(counts, pairs, 1, 0.5, None)] == [["e", "f"], ["a", "b"]]


def test_a_log_opening_mid_commit_reads_its_severed_header_as_no_path():
    assert list(_commit_file_sets(["abc123 severed", "a.py", "\x01next", "b.py"])) == [
        {"a.py"}, {"b.py"}]


def test_coupling_keeps_the_top_50_pairs_by_default():
    """51 pairs, each committed together 5 times: support 5, confidence 1.0."""
    log = "".join(f"\x01c\nf{n:02}.py\ng{n:02}.py\n" * 5 for n in range(51))

    assert len(change_coupling(log)) == 50
