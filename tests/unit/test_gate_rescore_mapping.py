"""rescore --gate's mapping of the gate module's findings, and check_gate's over it.

rescore knows a function's fresh ccn and its CRAP over the baseline's stale
coverage, so it hands the gate `CrapBound(ccn, overlay CRAP)`. The ceiling
reads the ccn, and a mark pardons only at or above the CRAP: a mark between the
two ends is unproven and counts as a breach, the verdict 0.8.1 gave. The cases
are built with gate-group-06's case builder (tests/unit/test_gate_cases.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_gate_cases import NAME, PATH, changed, claimed, fn, judged, unread

from crapkit import gate
from crapkit.cli._shared import _name_refusal
from crapkit.cli.scoring import (GATE_VERDICT_EXITS, _gate_breaches, _gate_changes, _gate_function,
                                 gate_block, gate_exit, gate_unread_files)
from crapkit.errors import UNREAD_NAME_REASON, UnreadableNameError
from crapkit.keys import MarkIndex
from crapkit.mcp_server import TOOLS, _verdict_answer
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow

CLAIMED = "src/caf\udce9.py"


def row(name: str = NAME, ccn: int = 8, crap: float = 72.0, start: int = 1) -> ScoredRow:
    return ScoredRow("src", PATH, name, start, start + 8, ccn, ccn, ccn, 12, 2, 1, 0.0, "measured", crap,
                     "decompose")


def rescored(rows: list, marks=()) -> list:
    """The breaches the rescore adapter fails on, each file taken whole."""
    changes = _gate_changes(rows, {}, {r.path for r in rows}, {})
    return _gate_breaches(gate.judge(changes, lambda scope: 6, lambda: MarkIndex(marks)), rows)


# --- the bound and the pardon ------------------------------------------------------------

def test_a_rescored_row_is_judged_on_its_ccn_and_pardoned_on_its_crap():
    function = _gate_function(row(ccn=8, crap=72.0))

    assert (function.bound, function.record.crap) == (gate.CrapBound(8, 72.0), 72.0)


def test_a_mark_between_the_ccn_and_the_crap_is_unproven_and_a_breach():
    result, _ = judged(changed(PATH, fn(low=8, high=72.0)), marks={(PATH, NAME): 40.0})

    assert [b.key_name for b in result.unproven] == [NAME]
    assert (gate_exit(result), gate_block(result, {})["ok"]) == (6, False)
    assert [v.long_name for v in rescored([row()], [RatchetEntry(PATH, NAME, 40.0)])] == [NAME]


def test_a_mark_at_or_over_both_ends_pardons():
    for mark in (72.0, 80.0):
        result, _ = judged(changed(PATH, fn(low=8, high=72.0)), marks={(PATH, NAME): mark})

        assert ([b.key_name for b in result.pardoned], gate_exit(result)) == ([NAME], 0)
        assert rescored([row()], [RatchetEntry(PATH, NAME, mark)]) == []


def test_a_ccn_at_the_ceiling_passes_whatever_its_crap():
    result, marks = judged(changed(PATH, fn(low=6, high=42.0)))

    assert (gate_exit(result), result.judged, marks.calls) == (0, 1, 0)


@pytest.mark.parametrize(("mark", "kind"), [(None, "over_ceiling"), (7.0, "marked_rise")])
def test_a_breach_with_no_mark_or_a_mark_under_its_ccn_fails_at_6(mark, kind):
    result, _ = judged(changed(PATH, fn(low=8, high=72.0)), marks={} if mark is None else {(PATH, NAME): mark})

    assert ([b.key_name for b in getattr(result, kind)], gate_exit(result)) == ([NAME], 6)


# --- the files the gate refuses ----------------------------------------------------------

def test_an_unreadable_name_exits_3_with_the_error_objects_item():
    """The one `gate.unread_files` item is the one the CLI's error object carries."""
    result, _ = judged(claimed(CLAIMED, dirty=True))
    refusal = UnreadableNameError("refused", [CLAIMED], frozenset({CLAIMED}))

    assert gate_exit(result) == 3
    assert gate_unread_files(result) == refusal.json_fields()["unread_files"]
    assert gate_unread_files(result) == [{"path": "src/caf\\xe9.py", "reason": UNREAD_NAME_REASON,
                                          "dirty": True}]


def test_the_cli_refusal_and_the_gate_item_agree_on_an_untracked_name(tmp_path):
    (tmp_path / "src").mkdir()
    result, _ = judged(claimed(CLAIMED, dirty=True))

    assert _name_refusal(CLAIMED, tmp_path).json_fields()["unread_files"] == gate_unread_files(result)


def test_a_changed_file_no_reader_could_read_fails_at_6_and_an_untouched_one_does_not():
    touched, _ = judged(unread("src/b.ts", "why", dirty=True))
    untouched, _ = judged(unread("src/b.ts", "why", spans=()))

    assert (gate_exit(touched), gate_unread_files(touched)) == (
        6, [{"path": "src/b.ts", "reason": "why", "dirty": True}])
    assert (gate_exit(untouched), gate_unread_files(untouched)) == (0, [])


def test_the_gate_block_keeps_its_081_keys_in_order():
    result, _ = judged()

    assert gate_block(result, {}) == {"ok": True, "judged": 0, "ceilings": {}, "breaches": [],
                                      "untracked": [], "unread_files": []}


# --- what the adapter hands the gate -----------------------------------------------------

def test_an_untracked_file_is_taken_whole_and_a_tracked_one_by_its_spans():
    rows = [row(), row(start=20)._replace(path="src/new.py")]

    changes = _gate_changes(rows, {PATH: [(3, 4)]}, {"src/new.py"}, {"src/c.ts": "why", "src/d.ts": "no"})

    assert [(c.path, tuple(c.spans), type(c.content).__name__) for c in changes] == [
        (PATH, ((3, 4),), "tuple"), ("src/new.py", gate.WHOLE, "tuple"),
        ("src/c.ts", (), "Unread"), ("src/d.ts", (), "Unread")]


def test_an_unread_file_the_change_touched_is_taken_whole_and_dirty():
    changes = _gate_changes([], {"src/c.ts": [(1, 1)]}, {"src/d.ts"}, {"src/c.ts": "why", "src/d.ts": "no"})

    assert [(c.spans, c.content.dirty) for c in changes] == [(gate.WHOLE, True), (gate.WHOLE, True)]


def test_twins_on_one_line_keep_the_overlays_order_among_equal_ccn():
    first, second = row("a( )", ccn=9, crap=9.0), row("b( )", ccn=9, crap=9.0)

    assert [v.long_name for v in rescored([second, first])] == ["b( )", "a( )"]


# --- check_gate over the mapping --------------------------------------------------------

def _check_gate() -> dict:
    return next(tool for tool in TOOLS if tool["name"] == "check_gate")


def test_check_gate_answers_every_exit_rescore_gate_maps_a_finding_to():
    assert GATE_VERDICT_EXITS == (3, 6)
    assert _check_gate()["verdict_exits"] == GATE_VERDICT_EXITS


@pytest.mark.parametrize(("code", "stdout", "verdict"), [
    (3, '{"gate": {"ok": false}}', True),
    (6, '{"gate": {"ok": false}}', True),
    (3, '{"error": {"exit": 3, "kind": "config"}}', False),
    (3, "", False),
    (1, '{"gate": {"ok": false}}', False),
], ids=["3-gate-payload", "6-gate-payload", "3-error-object", "3-no-stdout", "1-not-declared"])
def test_an_exit_check_gate_declares_is_a_verdict_only_with_its_payload(code, stdout, verdict):
    proc = SimpleNamespace(returncode=code, stdout=stdout)

    assert _verdict_answer(_check_gate(), proc) is verdict


def test_check_gates_description_is_the_081_text():
    """A registry listing shows the description; a change would need a manual re-sync."""
    assert _check_gate()["description"] == (
        "Checks an edited file by rescore --gate's rule: each changed function's ccn against its "
        "scope's ceiling, pardoned only while its crap is at or under its ratchet mark. The hook's "
        "commit gate pardons any marked function, so this is stricter and a breach predicts a verify "
        "refusal. Call it after an edit once get_function_brief states the rule. It runs no tests, "
        "and a breach reads gate.ok false, not an error. Marks are read only on a breach, so a clean "
        "gate skips a broken marks file. A tracked file is judged on its diff from HEAD, an "
        "untracked one in full.")
