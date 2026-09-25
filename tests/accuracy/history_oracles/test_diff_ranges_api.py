"""crapkit's -U0 diff reader (diffparse.changed_ranges) against unidiff, pygit2 and
the edit itself.

Random edits: difflib writes a -U0 diff of two drawn line lists, and the
expected ranges come from the SequenceMatcher opcodes that made it (the lines
an edit inserted or replaced; a deletion marks the line before it, ruling
H9), and from unidiff reading the same text. The lines are drawn from text
that looks like diff structure: '++ ' and '-- ' content reads '+++ ' and
'--- ' in a hunk body (R03).

Real diffs: a commit that edits a CRLF file, drops a final newline, edits a
file with a non-ASCII name, adds a '++ ' line and removes a '-- ' line, and
moves a file, read the way crapkit reads them (gitio.diff_since), against the
hand table hand_ranges.tsv, unidiff over `git diff`, and nightly pygit2's own diff.
"""
from __future__ import annotations

import difflib

from hypothesis import given, strategies as st
import pytest

from accuracy.kit import repos
from accuracy.kit.settings import pure
from accuracy.history_oracles import history_hand
from accuracy.history_oracles.oracles import pygit2_hunks, unidiff_ranges
from accuracy.history_oracles.oracles.history_git import text as git_text
from accuracy.history_oracles.repos import history_specs as specs
from crapkit.diffparse import changed_ranges
from crapkit.gitio import diff_since

LINES = st.sampled_from(["x", "y", "++ a", "-- b", "+++ c", "--- d", "@@ -1 +1 @@", "\\ e",
                         "diff --git a/f b/f", ""])


def _diff(old: list[str], new: list[str]) -> str:
    return "\n".join(difflib.unified_diff(old, new, "a/f.py", "b/f.py", n=0, lineterm=""))


def _truth(old: list[str], new: list[str]) -> list[tuple[int, int]]:
    """The new-side lines each opcode of the edit changed; a deletion marks the
    line before it, and line 1 at the top (H9)."""
    codes = difflib.SequenceMatcher(None, old, new).get_opcodes()
    return [(j1 + 1, j2) if j2 > j1 else (max(j1, 1), max(j1, 1))
            for tag, _, _, j1, j2 in codes if tag != "equal"]


@given(st.lists(LINES, max_size=12), st.lists(LINES, max_size=12))
@pure
def test_random_edits_match_the_opcodes_and_unidiff(old, new):
    text = _diff(old, new)
    expected = {"f.py": _truth(old, new)} if old != new else {}

    assert changed_ranges(text) == expected
    assert unidiff_ranges.ranges(text + "\n") == expected


@pytest.fixture
def edited(make_repo):
    built = make_repo(specs.DIFF_CASES)
    return built, repos.git(built.top, "rev-parse", "HEAD^1").strip()


@pytest.mark.process
def test_real_diffs_match_the_hand_table(edited):
    built, base = edited

    assert changed_ranges(diff_since(built.root, base)) == history_hand.ranges()


@pytest.mark.process
def test_real_diffs_match_unidiff(edited):
    built, base = edited
    diff = git_text(built.root, "-c", "core.quotePath=false", "diff", "-U0", "--no-renames",
                    base, "HEAD")

    assert changed_ranges(diff_since(built.root, base)) == unidiff_ranges.ranges(diff + "\n")


@pytest.mark.nightly
@pytest.mark.process
def test_real_diffs_match_pygit2(edited, oracle):
    oracle("pygit2")
    built, base = edited

    assert changed_ranges(diff_since(built.root, base)) == pygit2_hunks.ranges(built.root, base)


@pytest.mark.platform("linux")
@pytest.mark.process
def test_a_c_quoted_path_decodes(make_repo):
    """A tab and a double quote in a name: git C-quotes the header, and unidiff
    cannot read it (oracles/unidiff_ranges.py), so its ranges are matched by
    file order and the name against the one git lists with -z."""
    name = 'src/a\tb "q".py'
    built = make_repo(repos.Spec(steps=(
        repos.Commit(files={name: b"1\n2\n3\n"}, date=repos.EPOCH),
        repos.Commit(files={name: b"1\nTWO\n3\n"}, date=repos.EPOCH + 1))))
    diff = git_text(built.root, "diff", "-U0", "HEAD^1", "HEAD")

    said = changed_ranges(diff_since(built.root, "HEAD^1"))

    assert said == {name: [(2, 2)]}
    assert list(said.values()) == unidiff_ranges.spans(diff + "\n")
