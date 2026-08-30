"""Attribution: which findings a concurrent session's uncommitted edits produced.

A verify run measures the working tree, so another session editing tracked files
lands its half-finished functions in this verdict. The finding still fires — exit
codes are unchanged — but it is tagged, and the summary splits the two counts, so
nobody spends an afternoon on 377 regressions that belong to somebody else.
"""
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
