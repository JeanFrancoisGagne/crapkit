"""`rescore --gate` decides on the pre-commit hook's policy, mid-session.

The hook gates on ccn against the file's scope ceiling and reads no coverage at
all, so the two selections worked by hand below are the whole contract: a fully
covered ccn-7 function is a violation, and a ccn-6 function at zero coverage
(CRAP 42) is not. Same ceilings the commit will use, hours earlier.

Parity is more than the policy: the hook judges the functions the diff touched,
and a ratchet mark is a recorded decision to carry a function as it stands. Both
filters run before the ceiling rule, or a repo with seeded debt gates red forever.
Each case runs the rescore adapter's own path: its changed files, the gate
module's judge, and the breaches it fails on.
"""
from crapkit import invocation
from crapkit.cli.scoring import _gate_breaches, _gate_changes, _report_gate, _RescoreGate, gate_block
from crapkit.config import Config, Scope
from crapkit.gate import judge
from crapkit.hook import file_ceilings
from crapkit.keys import MarkIndex
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow

CFG = Config(target=6, scopes=(Scope("src", ("src",), ("python",)),
                               Scope("legacy", ("legacy",), ("python",), target=10)))


def row(path: str, name: str, ccn: int, cov: float, crap: float, start: int = 10,
        scope: str = "src") -> ScoredRow:
    return ScoredRow(scope, path, name, start, start + 5, ccn, ccn, ccn,
                     12, 2, 1, cov, "measured", crap, "decompose")


def judged(rows: list, marks=(), ranges: dict | None = None):
    """The gate module's result on rescore --gate's changes. With no `ranges`
    every file is untracked, so the gate takes each whole."""
    untracked = {r.path for r in rows} if ranges is None else set()
    changes = _gate_changes(rows, ranges or {}, untracked, {})
    return judge(changes, CFG.ceiling_of, lambda: MarkIndex(marks))


def gated(rows: list, marks=(), ranges: dict | None = None) -> list:
    """The breaches rescore --gate fails on."""
    return _gate_breaches(judged(rows, marks, ranges), rows)


def test_coverage_never_saves_a_function_over_the_ceiling():
    rows = [row("src/a.py", "over( n )", 7, 1.0, 7.0)]

    breaches = gated(rows)

    assert [(b.path, b.ccn, b.cov, b.crap) for b in breaches] == [("src/a.py", 7, 1.0, 7.0)]


def test_a_ccn_at_the_ceiling_passes_however_bad_its_crap_is():
    rows = [row("src/a.py", "at_ceiling( n )", 6, 0.0, 42.0)]

    assert gated(rows) == []


def test_breaches_are_ordered_worst_ccn_first_then_by_position():
    rows = [row("src/b.py", "mild( n )", 7, 1.0, 7.0, start=40),
            row("src/a.py", "worst( n )", 9, 1.0, 9.0, start=90),
            row("src/a.py", "mild( x )", 7, 0.5, 16.5, start=10)]

    breaches = gated(rows)

    assert [(b.path, b.start) for b in breaches] == [
        ("src/a.py", 90), ("src/a.py", 10), ("src/b.py", 40)]


def test_the_ceilings_come_from_the_hooks_own_per_scope_map():
    in_scope = {"src": ["src/a.py"], "legacy": ["legacy/old.py"]}

    ceilings = file_ceilings(CFG, in_scope, ["src/a.py", "legacy/old.py"])

    assert ceilings == {"src/a.py": 6, "legacy/old.py": 10}
    rows = [row("src/a.py", "eight( n )", 8, 1.0, 8.0),
            row("legacy/old.py", "eight( n )", 8, 1.0, 8.0, scope="legacy")]
    assert [b.path for b in gated(rows)] == ["src/a.py"]


def test_only_functions_a_changed_span_overlaps_reach_the_ceiling_rule():
    rows = [row("src/a.py", "edited( n )", 9, 1.0, 9.0, start=10),
            row("src/a.py", "legacy( n )", 15, 0.4, 63.6, start=100)]

    assert [b.long_name for b in gated(rows, ranges={"src/a.py": [(12, 12)]})] == ["edited( n )"]


def test_a_file_with_no_changed_range_contributes_no_candidate():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.6)]

    assert gated(rows, ranges={"src/b.py": [(1, 400)]}) == []


def test_a_change_at_the_last_line_of_a_function_still_selects_it():
    rows = [row("src/a.py", "edge( n )", 9, 1.0, 9.0, start=10)]  # spans 10..15

    assert len(gated(rows, ranges={"src/a.py": [(15, 15)]})) == 1


def test_a_breach_sitting_at_its_recorded_mark_is_carried_debt():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.6)]

    assert gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []


def test_a_breach_past_its_recorded_mark_still_fires():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 70.0)]

    kept = gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.crap for b in kept] == [70.0]


def test_a_mark_on_another_function_exempts_nothing():
    rows = [row("src/a.py", "fresh( n )", 9, 1.0, 9.0)]

    kept = gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)])

    assert [b.long_name for b in kept] == ["fresh( n )"]


def test_marks_compare_at_the_four_decimals_they_are_stored_at():
    rows = [row("src/a.py", "legacy( n )", 15, 0.4, 63.60001)]

    assert gated(rows, [RatchetEntry("src/a.py", "legacy( n )", 63.6)]) == []


OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"
CARRY_LINE = (f"crapkit gate: src/a.py {NEW} carries the mark 7.0000 recorded for {OLD}; a passing "
              f'verify re-keys it, or run crapkit ratchet move --function src/a.py "{OLD}" "{NEW}"')


def test_a_signature_edit_passes_on_the_mark_its_old_key_carries(monkeypatch, capsys):
    """The probe: seed marked classify( a , b ) at 7.0; the edit added `c=None`
    at ccn 7. The JSON lists the carry and the stderr line names both keys."""
    monkeypatch.setattr(invocation, "_self", lambda: "crapkit")
    rows = [row("src/a.py", NEW, 7, 1.0, 7.0)]
    result = judged(rows, [RatchetEntry("src/a.py", OLD, 7.0)])
    verdict = _RescoreGate(result, _gate_breaches(result, rows), {"src/a.py": 6}, [])

    block = gate_block(result, verdict.ceilings, verdict.breaches)

    assert (block["ok"], block["breaches"]) == (True, [])
    assert block["carried"] == [{"path": "src/a.py", "long_name": NEW, "key_name": NEW,
                                 "carried_from": OLD, "mark": 7.0}]
    assert _report_gate(verdict, as_json=True) == 0
    assert capsys.readouterr().err.splitlines() == [CARRY_LINE]


def test_a_carried_function_is_judged_on_the_coverage_its_old_key_holds(tmp_path):
    """The overlay joins the baseline's coverage by name, so the edit's new
    name joins nothing and its CRAP spans 7 to 56 across the mark of 7.0.
    The adapter joins it again under the key the mark carried from: the stale
    number a body edit keeps under its own name."""
    from functools import partial

    from crapkit.cli.scoring import _carried_judgement, _overlay_named
    from crapkit.snapshot import InventoryRow
    from crapkit.store import SnapshotStore

    latest = {"id": 1, "commit": "0123456789abcdef", "lanes": {"unit": {"scopes": ["src"]}}}
    store = SnapshotStore(tmp_path / "crap.sqlite")
    store.write_run(commit=latest["commit"], tool_versions={}, rows=[row("src/a.py", OLD, 7, 1.0, 7.0)],
                    lanes=latest["lanes"])
    fresh = [InventoryRow("src", "src/a.py", NEW, 10, 15, 7, 7, 7, 12, 3, 1)]
    overlay_of = partial(_overlay_named, store, latest, fresh, ["src/a.py"], CFG)
    first = overlay_of({})
    marks = [RatchetEntry("src/a.py", OLD, 7.0)]
    assert [b.carried_from for b in judged(first.rows, marks).unproven] == [OLD]

    result, view = _carried_judgement(lambda rows: judged(rows, marks), first, overlay_of)
    store.close()

    assert ([(b.carried_from, b.mark) for b in result.carried], result.unproven) == ([(OLD, 7.0)], ())
    assert [(r.long_name, r.cov, r.crap) for r in view.rows] == [(NEW, 1.0, 7.0)]
    assert view.unjoined == set()


def test_a_carried_function_past_its_mark_fails_with_no_carried_item(capsys):
    rows = [row("src/a.py", NEW, 8, 1.0, 8.0)]
    result = judged(rows, [RatchetEntry("src/a.py", OLD, 7.0)])
    verdict = _RescoreGate(result, _gate_breaches(result, rows), {"src/a.py": 6}, [])

    block = gate_block(result, verdict.ceilings, verdict.breaches)

    assert (block["ok"], block["carried"]) == (False, [])
    assert [(b["function"], b["mark_ambiguity"]) for b in block["breaches"]] == [(NEW, None)]
    assert _report_gate(verdict, as_json=True) == 6
    assert "carries the mark" not in capsys.readouterr().err


def test_twins_left_by_the_edit_refuse_and_the_breach_names_the_ambiguity():
    """`classify` and `classify#2` after the edit: the refusal names the move."""
    rows = [row("src/a.py", NEW, 7, 1.0, 7.0, start=10), row("src/a.py", NEW, 7, 1.0, 7.0, start=40)]
    result = judged(rows, [RatchetEntry("src/a.py", OLD, 7.0)])

    block = gate_block(result, {"src/a.py": 6}, _gate_breaches(result, rows))

    assert (block["ok"], block["carried"]) == (False, [])
    ambiguity = {b["key_name"]: b["mark_ambiguity"] for b in block["breaches"]}
    assert set(ambiguity) == {NEW, f"{NEW}#2"}
    assert all("twins never carry a mark" in text for text in ambiguity.values())
    assert f'ratchet move --function src/a.py "{OLD}" "{NEW}#2"' in ambiguity[f"{NEW}#2"]
