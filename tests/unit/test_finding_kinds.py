"""verify.FINDING_KINDS: one row per kind of finding a verdict holds.

Each kind was named at about twelve sites in six modules (the exit order, the
dirty split, the override's refusal and grant, the text printers, the JSON
lists and the SARIF builders), and 0.8.1's unread file missed three of them.
Now every one of those sites loops over the rows, `verify --json`'s findings
list included, so a new kind is one row and the detector that fills its field. These tests hold each row to what verify
printed, exited and wrote before the table, and the locality test at the end
adds a row to a copy of the table and finds it at every site with no other
edit.
"""
from __future__ import annotations

import csv
import json
from itertools import combinations, product
from pathlib import Path
from types import SimpleNamespace

import pytest

from accuracy.verdict_model import model_verdict as model
from crapkit import sarif, universe, verify
from crapkit.cli import _shared, verifying
from crapkit.merge import UNREAD_ADVICE
from crapkit.verify import (FINDING_KINDS, FindingKind, GateViolation, RatchetRegression,
                            Refusal, Sarif, UncoveredViolation, UnreadableName, Unread,
                            Verdict)

ROOT = Path(__file__).resolve().parents[2]

GATE = GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose")
UNREAD = Unread("src/b.ts", "src/b.ts:12: arrow refused")
ROSE = RatchetRegression("lib/m.py", "g( y )", 4.0, 9.0)
FAILURE = "tests/t.py::test_a"
LINE = UncoveredViolation("src/a.py", 7)
NAME = UnreadableName("src/caf\udce9.py", "src")

# One entry per kind, a second one its file's uncommitted edits make dirty,
# and the Verdict field it lives in.
KINDS = {
    "unreadable_name": ("claimed_names", NAME, NAME._replace(path="src/d\udce9.py", dirty=True)),
    "gate_violation": ("gate_violations", GATE, GATE._replace(start=30, dirty=True)),
    "unread_file": ("unread_files", UNREAD, UNREAD._replace(path="src/c.ts", dirty=True)),
    "ratchet_regression": ("ratchet_regressions", ROSE, ROSE._replace(path="lib/n.py", dirty=True)),
    "new_failure": ("new_failures", FAILURE, "tests/u.py::test_b"),
    "diff_uncovered": ("uncovered_violations", LINE, LINE._replace(line=8, dirty=True)),
    "overridden": ("overridden", GATE, GATE._replace(start=30, dirty=True)),
}
ORDER = list(KINDS)


def holding(*kinds: str, dirty: bool = False) -> Verdict:
    """A verdict holding one entry of each kind named, settled as verify settles one;
    `dirty` adds each kind's dirty entry beside its clean one."""
    fields: dict[str, list] = {}
    for kind in kinds:
        field, clean, dirtied = KINDS[kind]
        fields[field] = [clean, dirtied] if dirty else [clean]
    dirty_failures = ["tests/u.py::test_b"] if dirty and "new_failure" in kinds else []
    planted = Verdict.passing()._replace(dirty_failures=dirty_failures, **fields)
    return verify.settle_verdict(planted)


def row(kind: str) -> FindingKind:
    return next(r for r in FINDING_KINDS if r.kind == kind)


# --- the table itself -----------------------------------------------------------

def test_the_table_lists_every_kind_in_exit_order():
    """3 first, then 6, 7, 8, 9; gate_violation before unread_file, both exit 6;
    overridden fails nothing and comes last."""
    assert [r.kind for r in FINDING_KINDS] == ORDER
    assert [r.exit for r in FINDING_KINDS] == [3, 6, 6, 7, 8, 9, None]
    assert [r.fails for r in FINDING_KINDS] == [True] * 6 + [False]
    assert [r.field for r in FINDING_KINDS] == [KINDS[k][0] for k in ORDER]


def test_each_row_labels_its_findings_items_with_its_rule():
    """The label the Action's comment prints for each kind's items; the two
    kinds of exit 6 share the complexity gate's."""
    assert [r.rule for r in FINDING_KINDS] == [
        "unreadable name", "complexity gate", "complexity gate", "ratchet regressions",
        "new test failures", "diff-coverage ceiling", "override"]
    assert [r.cap for r in FINDING_KINDS] == [None] * 5 + [50, None]


def test_every_row_names_a_verdict_field_and_the_verdict_names_no_kind_twice():
    assert {r.field for r in FINDING_KINDS} <= set(Verdict._fields)
    assert len({r.field for r in FINDING_KINDS}) == len(FINDING_KINDS)


# --- the exit code --------------------------------------------------------------

EXITS = {"unreadable_name": 3, "gate_violation": 6, "unread_file": 6, "ratchet_regression": 7,
         "new_failure": 8, "diff_uncovered": 9, "overridden": 0}


@pytest.mark.parametrize("kind", ORDER)
def test_each_kind_alone_exits_with_its_code(kind):
    verdict = holding(kind)

    assert verify.exit_code(verdict) == EXITS[kind]
    assert verifying._verify_exit_code(verdict) == EXITS[kind]
    assert verdict.ok is (EXITS[kind] == 0)


def test_a_verdict_with_no_finding_exits_0():
    assert verify.exit_code(Verdict.passing()) == 0
    assert verify.settle_verdict(Verdict.passing()).ok is True


@pytest.mark.parametrize("first, second", list(combinations(ORDER, 2)),
                         ids=lambda kind: kind)
def test_the_earlier_row_decides_the_exit_and_prints_first(first, second):
    """Any two kinds together: the earlier failing row's exit, and the earlier
    row's line above the later one's."""
    verdict = holding(first, second)
    expected = EXITS[first] or EXITS[second]

    assert verify.exit_code(verdict) == expected
    lines = verify.text_lines(verdict)
    printed = [kind for kind in (first, second) if row(kind).stream == "stdout"]
    assert lines == [line for kind in printed for line in verify.text_lines(holding(kind))]


# --- override eligibility -------------------------------------------------------

REFUSALS = {
    "unreadable_name": ("1 unreadable name (src/caf\\xe9.py)",
                        "rename it (git mv) to a UTF-8 name"),
    "unread_file": ("1 unread file (src/b.ts: src/b.ts:12: arrow refused)", UNREAD_ADVICE),
    "ratchet_regression": ("1 ratchet regression (lib/m.py g( y ) 4.0 -> 9.0)",
                           "raise the mark by hand and commit it"),
    "new_failure": ("1 new test failure (tests/t.py::test_a)", "fix the failing test first"),
}


@pytest.mark.parametrize("kind", ORDER)
def test_only_a_gate_violation_is_granted_and_each_never_granted_kind_names_its_escape(kind):
    verdict = holding(kind)
    refusal = verify.override_refusal(verdict)

    assert verify.override_grants(verdict) == ((GATE,) if kind == "gate_violation" else ())
    assert row(kind).granted is (kind == "gate_violation")
    if kind in REFUSALS:
        cause, escape = REFUSALS[kind]
        assert refusal == f"override refused: {cause} never qualifies for an override; {escape}"
    else:
        assert refusal is None


def test_the_refusal_is_one_line_naming_every_never_granted_kind_present():
    """In the order docs/ratchet.md names them: a ratchet regression, a new test
    failure, an unread file; then the unreadable name, which no 0.8.1 run reached."""
    verdict = holding(*ORDER)
    order = ("ratchet_regression", "new_failure", "unread_file", "unreadable_name")

    assert verify.override_refusal(verdict) == (
        f"override refused: {' and '.join(REFUSALS[k][0] for k in order)} never qualify for an "
        f"override; {'; '.join(REFUSALS[k][1] for k in order)}")
    assert verify.override_grants(verdict) == ()


def test_a_kind_that_is_never_granted_and_refuses_nothing_leaves_the_grant_to_the_gate():
    """A breached diff-coverage ceiling refuses no override, as in 0.8.1: the
    gate violation is granted and the verdict still fails on exit 9."""
    verdict = holding("gate_violation", "diff_uncovered")

    granted = verify.grant(verdict)

    assert verify.override_refusal(verdict) is None
    assert granted.gate_violations == [] and granted.overridden == (GATE,)
    assert verify.exit_code(granted) == 9 and granted.ok is False


def test_a_grant_on_a_refused_verdict_moves_nothing():
    verdict = holding("gate_violation", "ratchet_regression")

    assert verify.grant(verdict) == verdict


def test_the_hook_refuses_its_unread_files_with_the_line_verify_prints():
    unread = {"src/b.ts": "src/b.ts:12: arrow refused"}

    expected = verify.override_refusal(holding("unread_file"))
    assert verifying._hook_override_refusal(unread) == expected
    assert verifying._hook_override_refusal({}) is None


# --- dirty attribution ----------------------------------------------------------

@pytest.mark.parametrize("kind", ORDER)
def test_each_failing_kind_counts_its_clean_and_its_dirty_entry(kind):
    """overridden is a report: it fails nothing, so it counts nowhere."""
    expected = (1, 1) if row(kind).fails else (0, 0)

    assert verify.dirty_counts(holding(kind, dirty=True)) == expected


def test_a_new_failure_is_dirty_when_its_id_is_among_the_dirty_failures():
    assert row("new_failure").dirty(holding("new_failure", dirty=True),
                                    [FAILURE, "tests/u.py::test_b"]) == [False, True]


# --- the text lines -------------------------------------------------------------

CLAIMED = ("crapkit: src/caf\\xe9.py is in scope 'src', but git names it in bytes that are not "
           "UTF-8 and crapkit reads every path as UTF-8; a file a scope takes is refused, not left "
           "out, so no gate passes it unread: rename it (git mv) to a UTF-8 name")
LINES = {
    "unreadable_name": [CLAIMED],
    "gate_violation": ["  GATE  crap     84.0  ccn   9 cov 50%  src/a.py:3  f( x )  -> decompose"],
    "unread_file": ["  UNREAD  src/b.ts: src/b.ts:12: arrow refused"],
    "ratchet_regression": ["  RATCHET  lib/m.py  g( y ): 4.0 -> 9.0"],
    "new_failure": ["  NEW FAILURE  tests/t.py::test_a"],
    "diff_uncovered": ["  uncovered src/a.py:7"],
    "overridden": ["  OVERRIDDEN  src/a.py:3  f( x )"],
}


def _clean(kind: str) -> list[str]:
    """The kind's clean line, looked up by kind name. Two kind names spell
    0.8.1 verify keys, and test_no_old_verify_keys reads a literal one as a
    payload read."""
    return LINES[kind]


DIRTY_LINES = {
    "unreadable_name": [CLAIMED.replace("caf\\xe9.py", "caf\\xe9.py (and 1 more)")],
    "gate_violation": [LINES["gate_violation"][0],
                       "  GATE  crap     84.0  ccn   9 cov 50%  src/a.py:30  f( x )  -> decompose"
                       "  [dirty]"],
    "unread_file": [LINES["unread_file"][0],
                    "  UNREAD  src/c.ts: src/b.ts:12: arrow refused  [dirty]"],
    "ratchet_regression": [LINES["ratchet_regression"][0],
                           "  RATCHET  lib/n.py  g( y ): 4.0 -> 9.0  [dirty]"],
    "new_failure": [LINES["new_failure"][0], "  NEW FAILURE  tests/u.py::test_b  [dirty]"],
    "diff_uncovered": [*_clean("diff_uncovered"), "  uncovered src/a.py:8"],
    "overridden": [*_clean("overridden"), "  OVERRIDDEN  src/a.py:30  f( x )"],
}


@pytest.mark.parametrize("kind", ORDER)
def test_each_kind_s_text_is_the_line_verify_printed(kind):
    stream = row(kind).stream

    assert verify.text_lines(holding(kind), stream) == LINES[kind]
    assert verify.text_lines(holding(kind, dirty=True), stream) == DIRTY_LINES[kind]
    other = "stderr" if stream == "stdout" else "stdout"
    assert verify.text_lines(holding(kind), other) == []


def test_verify_prints_every_stdout_kind_in_table_order(capsys):
    verdict = holding(*ORDER, dirty=True)

    verifying._print_verify_findings(verdict)

    printed = capsys.readouterr().out.splitlines()
    stdout_kinds = [k for k in ORDER if row(k).stream == "stdout"]
    assert printed == [line for k in stdout_kinds for line in DIRTY_LINES[k]]


def test_the_unreadable_name_line_is_universe_s_sentence_called_not_copied(monkeypatch):
    calls = []
    monkeypatch.setattr(universe, "claimed_text",
                        lambda claimed: calls.append(claimed) or "SENTENCE")

    assert verify.text_lines(holding("unreadable_name"), "stderr") == ["crapkit: SENTENCE"]
    assert calls == [[("src/caf\udce9.py", "src")]]


def test_universe_still_refuses_a_claimed_name_with_the_same_sentence():
    claimed = [("src/caf\udce9.py", "src")]

    assert f"crapkit: {universe.claimed_text(claimed)}" == CLAIMED


def test_the_shared_printers_read_the_rows(capsys):
    """rescore --gate and the hook print a gate violation and an unread file
    through _shared, with the same lines verify prints."""
    assert _shared._gate_line(GATE) == LINES["gate_violation"][0]
    assert _shared._gate_line(GATE._replace(dirty=True), True) == (
        "  GATE  crap     84.0  ccn   9 cov -  src/a.py:3  f( x )  -> decompose  [dirty]")

    _shared._print_unread({"src/b.ts": UNREAD.reason}, "staged")

    assert capsys.readouterr().out.splitlines() == [
        "crapkit gate: 1 staged file(s) could not be read, so no function in them was judged:",
        LINES["unread_file"][0], UNREAD_ADVICE]


def test_the_uncovered_line_warning_prints_the_rows_line(capsys):
    verifying._warn_diff_uncovered([("src/a.py", 7)])

    assert capsys.readouterr().err.splitlines() == [
        "warning: 1 changed line(s) have no coverage", "  uncovered src/a.py:7"]


# --- SARIF ----------------------------------------------------------------------

def _result(rule: str, level: str, text: str, path: str, line: int) -> dict:
    return {"ruleId": rule, "level": level, "message": {"text": text},
            "locations": [{"physicalLocation": {"artifactLocation": {"uri": path},
                                                "region": {"startLine": line}}}]}


SARIF = {
    "unreadable_name": [_result("crapkit/unreadable-name", "error", CLAIMED.removeprefix("crapkit: "),
                                "src/caf%E9.py", 1)],
    "gate_violation": [_result("crapkit/gate", "error",
                               "f( x ): CRAP 84.0 (ccn 9, cov 50%) -> decompose", "src/a.py", 3)],
    "unread_file": [_result("crapkit/unread", "error",
                            "no reader could read this file, so the gate judged none of its "
                            "functions: src/b.ts:12: arrow refused", "src/b.ts", 1)],
    "ratchet_regression": [_result("crapkit/ratchet-regression", "error",
                                   "g( y ): recorded 4.0 -> fresh 9.0", "lib/m.py", 1)],
    "diff_uncovered": [_result("crapkit/diff-uncovered", "warning",
                               "changed line has no coverage: no lane ran it", "src/a.py", 7)],
}


@pytest.mark.parametrize("kind", ORDER)
def test_each_kind_s_sarif_rule_and_level_are_the_ones_verify_wrote(kind):
    """new_failure and overridden write no result. unreadable_name writes its
    own since verify renders the name as a verdict: its uri percent-encodes
    the name's own bytes, and its message is universe's sentence."""
    assert verify.sarif_results(holding(kind)) == SARIF.get(kind, [])


def test_sarif_reports_every_uncovered_line_whether_or_not_it_breaches():
    """The verdict holds the lines only past diff_uncovered_max; SARIF has always
    written a warning for each one the diff left dark."""
    results = verify.sarif_results(holding("gate_violation"), [("src/a.py", 7)])

    assert results == [result for kind in ("gate_violation", "diff_uncovered") for result in SARIF[kind]]


# --- the hand exit table --------------------------------------------------------

HAND = {"unreadable_name": "unreadable_name", "unread": "unread_file", "gate": "gate_violation",
        "ratchet": "ratchet_regression", "failures": "new_failure",
        "diff_uncovered": "diff_uncovered"}
OLD = ("gate", "ratchet", "failures", "diff_uncovered")


def _hand_rows() -> list[dict]:
    with (ROOT / "tests/accuracy/verdict_model/hand_exit.tsv").open(encoding="utf-8",
                                                                     newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def test_the_hand_table_holds_every_subset_of_the_six_exit_bearing_kinds():
    rows = _hand_rows()
    subsets = {frozenset(name for name in HAND if row[name] == "1") for row in rows}

    assert len(rows) == 64 and len(subsets) == 64
    assert all(row["source"].startswith("README.md#exit-codes: ") for row in rows)


@pytest.mark.parametrize("hand", _hand_rows(),
                         ids=lambda hand: "+".join(n for n in HAND if hand[n] == "1") or "none")
def test_the_model_and_the_table_give_the_hand_table_s_exit(hand):
    subset = frozenset(name for name in HAND if hand[name] == "1")

    assert model.exit_code(subset) == int(hand["exit"])
    assert verify.exit_code(holding(*(HAND[name] for name in subset))) == int(hand["exit"])


def test_the_hand_table_ends_each_four_kind_subset_on_its_0_8_1_row():
    """test_exit_and_settle reads the four 0.8.1 columns and keeps the last row
    for each subset of them, so that row must be the one with neither new
    kind present."""
    last = {}
    for hand in _hand_rows():
        last[frozenset(name for name in OLD if hand[name] == "1")] = hand

    assert len(last) == 16
    assert all(hand["unread"] == hand["unreadable_name"] == "0" for hand in last.values())


# --- locality: one new row reaches every site -----------------------------------

def _probe_line(entry, dirty: bool) -> str:
    return f"  PROBE  {entry}"


PROBE = FindingKind(
    kind="probe", field="retried_passes", exit=4, granted=False,
    refusal=Refusal(place=9, noun="probe", first=str, escape="drop the probe"),
    dirty=lambda verdict, entries: [entry.endswith("!") for entry in entries],
    text=verify.each(_probe_line), stream="stdout",
    sarif=Sarif("crapkit/probe", "note", lambda entry: (entry, 1), lambda entry: "a probe"),
    rule="probe rule", item=lambda entry: {"probe": entry})


def test_a_new_row_reaches_every_site_with_no_other_edit(monkeypatch, tmp_path, capsys):
    """A test-only kind on a copy of the table, reading a field no row reads:
    the exit code, the settled ok, the dirty split, the printer, SARIF and the
    annotation, the override refusal and the findings list all find it, and
    the payload gives it no key of its own."""
    monkeypatch.setattr(verify, "FINDING_KINDS", (*FINDING_KINDS, PROBE))
    verdict = verify.settle_verdict(Verdict.passing()._replace(retried_passes=("p.py", "q.py!")))

    assert verifying._verify_exit_code(verdict) == 4 and verdict.ok is False
    assert verify.dirty_counts(verdict) == (1, 1)
    verifying._print_verify_findings(verdict)
    verifying._refuse_override(verdict, "ship it")
    verifying._emit_verify_findings(tmp_path, SimpleNamespace(sarif="out.sarif", github=True),
                                    verdict, [])
    out, err = capsys.readouterr()

    assert out.splitlines() == ["  PROBE  p.py", "  PROBE  q.py!",
                                "::note file=p.py,line=1,title=crapkit/probe::a probe",
                                "::note file=q.py!,line=1,title=crapkit/probe::a probe"]
    assert err == ("override refused: 2 probes (p.py) never qualify for an override; "
                   "drop the probe\n")
    document = json.loads((tmp_path / "out.sarif").read_text(encoding="utf-8"))
    assert [r["ruleId"] for r in document["runs"][0]["results"]] == ["crapkit/probe"] * 2
    payload = verifying._verify_result(verdict, 1, {"id": 1, "commit": "a" * 40}, "a" * 40,
                                       {}, [], None, 0)
    passing = verifying._verify_result(Verdict.passing(), 1, {"id": 1, "commit": "a" * 40},
                                       "a" * 40, {}, [], None, 0)
    assert sorted(payload) == sorted(passing) and payload["counts"] == passing["counts"]
    assert (payload["committed_findings"], payload["dirty_findings"]) == (1, 1)
    common = {"kind": "probe", "fails": True, "exit_code": 4, "overridable": False, "rule": "probe rule"}
    assert payload["findings"] == [{**common, "dirty": False, "probe": "p.py"},
                                   {**common, "dirty": True, "probe": "q.py!"}]


def test_the_per_kind_sites_are_gone():
    """The deletion test: these named one kind each; FINDING_KINDS replaces them."""
    gone = {verify: ("_any_finding", "_FLAGGED_FINDINGS", "json_lists"),
            verifying: ("_EXIT_ORDER", "_RECORD_FINDINGS", "_override_applies", "_never_granted",
                        "_refusal_parts", "_override_refusal", "_regression_cause",
                        "_failure_cause", "_unread_cause", "_print_gate_findings",
                        "_finding_lists"),
            _shared: ("_unread_line", "_dirty_tag"),
            sarif: ("unread_results", "gate_results", "regression_results",
                    "diff_uncovered_results"),
            universe: ("_claimed_text",)}

    assert [(m.__name__, n) for m, names in gone.items() for n in names if hasattr(m, n)] == []


def test_every_subset_of_the_six_kinds_settles_ok_only_at_exit_0():
    for bits in product((0, 1), repeat=6):
        kinds = [kind for kind, on in zip(ORDER[:6], bits) if on]
        verdict = holding(*kinds)
        assert verdict.ok is (verify.exit_code(verdict) == 0) is (not kinds)
