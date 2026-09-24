"""Attribution: which findings a concurrent session's uncommitted edits produced.

A verify run measures the working tree, so another session editing tracked files
lands its half-finished functions in this verdict. The finding still fires — exit
codes are unchanged — but it is tagged, and the summary splits the two counts, so
nobody spends an afternoon on 377 regressions that belong to somebody else.
"""
from crapkit.junitparse import failed_test_ids
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import dirty_counts, dirty_failure_ids, evaluate


def scored(path="src/a.ts", name="f( )", start=1, end=9, ccn=5, cov=1.0, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    remedy = "decompose" if ccn > 6 else ("ok" if c <= 6 else "add-tests")
    return ScoredRow(scope, path, name, start, end, ccn + 1, ccn, ccn, 5, 1, 1, cov, "measured", c, remedy)


def test_a_gate_violation_in_a_committed_file_is_not_dirty():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={"src/a.ts": [(3, 4)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6,
                 dirty_paths={"src/other.ts"})

    assert v.gate_violations[0].dirty is False
    assert dirty_counts(v) == (1, 0)


def test_a_gate_violation_in_a_dirty_file_is_tagged():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={"src/a.ts": [(3, 4)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6,
                 dirty_paths={"src/a.ts"})

    assert v.gate_violations[0].dirty is True
    assert v.ok is False, "attribution never changes the verdict"
    assert dirty_counts(v) == (0, 1)


def test_a_ratchet_regression_in_a_dirty_file_is_tagged():
    mark = RatchetEntry(path="src/a.ts", long_name="f( )", crap=20.0)
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={}, ratchet=[mark],
                 baseline_failures=set(), fresh_failures=set(), target=6,
                 dirty_paths={"src/a.ts"})

    assert v.ratchet_regressions[0].dirty is True
    assert dirty_counts(v) == (0, 1)


def test_a_new_failure_is_dirty_when_its_test_file_has_uncommitted_edits():
    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={}, ratchet=[],
                 baseline_failures=set(),
                 fresh_failures={"pylib.test_new::test_x", "src/keep.test.ts::renders"},
                 target=6, dirty_paths={"pylib/test_new.py"})

    assert v.new_failures == ["pylib.test_new::test_x", "src/keep.test.ts::renders"]
    assert v.dirty_failures == ["pylib.test_new::test_x"]
    assert dirty_counts(v) == (1, 1)


def test_a_junit_classname_matches_a_dirty_path_in_both_shapes():
    ids = ["pylib.test_new::test_x", "src/keep.test.ts::renders", "pylib.other::test_y"]

    assert dirty_failure_ids(ids, {"pylib/test_new.py", "src/keep.test.ts"}) == \
        ["pylib.test_new::test_x", "src/keep.test.ts::renders"]


def test_a_junit_file_in_windows_spelling_matches_its_dirty_test_file():
    """bun on Windows writes classname="" and the file with backslashes, so the
    id is `src\\deep\\keep.test.ts::renders` while git names the dirty file
    `src/deep/keep.test.ts`. The failure read as committed: `findings: 1
    committed / 0 dirty` for a test file with uncommitted edits."""
    report = ('<testsuite><testcase name="renders" classname="" '
              'file="src\\deep\\keep.test.ts"><failure/></testcase></testsuite>')

    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={}, ratchet=[],
                 baseline_failures=set(), fresh_failures=failed_test_ids(report),
                 target=6, dirty_paths={"src/deep/keep.test.ts"})

    assert v.dirty_failures == ["src\\deep\\keep.test.ts::renders"]
    assert dirty_counts(v) == (0, 1)


def test_counts_add_up_across_all_three_finding_kinds():
    mark = RatchetEntry(path="src/b.ts", long_name="g( )", crap=20.0)
    v = evaluate(
        fresh=[scored(ccn=5, cov=0.0), scored(path="src/b.ts", name="g( )", ccn=5, cov=0.0)],
        changed_ranges={"src/a.ts": [(3, 4)]},
        ratchet=[mark], baseline_failures=set(), fresh_failures={"pylib.test_new::test_x"},
        target=6, dirty_paths={"src/b.ts"})

    assert [g.dirty for g in v.gate_violations] == [False]
    assert [r.dirty for r in v.ratchet_regressions] == [True]
    assert v.dirty_failures == []
    assert dirty_counts(v) == (2, 1)


def test_no_dirty_set_leaves_every_finding_committed():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={"src/a.ts": [(3, 4)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)

    assert v.gate_violations[0].dirty is False
    assert v.dirty_failures == []


def test_the_split_line_does_not_call_the_dirty_set_tracked(capsys):
    """The dirty set is `status_names`, which unions `ls-files --others`, so a
    new failure in a test file git has never seen is counted here. The line said
    "uncommitted tracked edits", and that row is neither tracked nor an edit."""
    from crapkit.cli.verifying import _print_finding_split

    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={}, ratchet=[],
                 baseline_failures=set(), fresh_failures={"pylib.test_new::test_x"},
                 target=6, dirty_paths={"pylib/test_new.py"})
    _print_finding_split(v)
    line = capsys.readouterr().out.strip()

    assert line.startswith("findings: 0 committed / 1 dirty")
    assert "uncommitted tracked edits" not in line
    assert "untracked" in line, "the set includes files git has never seen"



# --- a JUnit id whose file part is spelled another way ---------------------------
#
# A runner's classname or file attribute names the test file however the runner
# was started: jest-junit's `{filepath}` is absolute, and a runner handed
# `./web/...` keeps the dot. Matched as text against git's `web/src/app.test.ts`,
# a failure in the file under edit read as committed: `findings: 1 committed /
# 0 dirty`, and a pre-push check reading committed_findings blamed the tree.

import os as _os

import pytest as _pytest


def _dirty_ids(root, classname: str) -> list[str]:
    report = (f'<testsuite><testcase name="renders" classname="{classname}">'
              '<failure/></testcase></testsuite>')
    return evaluate(fresh=[scored(ccn=2)], changed_ranges={}, ratchet=[],
                    baseline_failures=set(), fresh_failures=failed_test_ids(report), target=6,
                    dirty_paths={"web/src/app.test.ts"}, root=root).dirty_failures


def _test_file(tmp_path):
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "web" / "src" / "app.test.ts").write_text("test('x', () => {});\n",
                                                          encoding="utf-8")
    return tmp_path.resolve()


@_pytest.mark.parametrize("spell", [
    lambda root: "web/src/app.test.ts",
    lambda root: "./web/src/app.test.ts",
    lambda root: str(root / "web" / "src" / "app.test.ts"),
    lambda root: (root / "web" / "src" / "app.test.ts").as_posix(),
], ids=["relative", "dot-slash", "absolute-native", "absolute-forward"])
def test_a_junit_file_in_any_spelling_of_the_dirty_test_file_is_dirty(tmp_path, spell):
    root = _test_file(tmp_path)
    classname = spell(root)

    assert _dirty_ids(root, classname) == [f"{classname}::renders"]


@_pytest.mark.skipif(_os.name != "nt", reason="needs Windows path rules")
@_pytest.mark.parametrize("spell", [
    lambda root: "web\\src\\app.test.ts",
    lambda root: str(root / "web" / "src" / "app.test.ts")[0].lower()
    + str(root / "web" / "src" / "app.test.ts")[1:],
], ids=["backslash", "absolute-lower-drive"])
def test_windows_matches_a_junit_file_in_its_own_spellings(tmp_path, spell):
    root = _test_file(tmp_path)
    classname = spell(root)

    assert _dirty_ids(root, classname) == [f"{classname}::renders"]


def test_a_junit_file_elsewhere_stays_committed(tmp_path):
    root = _test_file(tmp_path / "repo")
    other = _test_file(tmp_path / "other")

    assert _dirty_ids(root, str(other / "web" / "src" / "app.test.ts")) == []
