"""Verify seam: fresh scored rows + baseline + ratchet + changed ranges in, Verdict out. Pure.

The last tests hold docs/agent-json.md's findings kind table and its 50-item
sentence to the table and the cap verify builds its findings from."""
from fractions import Fraction
from itertools import takewhile
from pathlib import Path
import re

from accuracy.kit import exact

from crapkit import verify
from crapkit.ratchet import RatchetEntry
from crapkit.score import ScoredRow
from crapkit.verify import (FINDING_KINDS, GateViolation, RatchetRegression, UncoveredViolation,
                            UnreadableName, Unread, Verdict, evaluate, unmarked_over_ceiling)

ROOT = Path(__file__).resolve().parents[2]


def scored(path="src/a.ts", name="f( )", start=1, end=9, ccn=5, cov=1.0, crap=None, scope="src", flag="measured"):
    c = crap if crap is not None else float(exact.crap(ccn, Fraction(cov)))
    remedy = "decompose" if ccn > 6 else ("ok" if c <= 6 else "add-tests")
    return ScoredRow(scope, path, name, start, end, ccn + 1, ccn, ccn, 5, 1, 1, cov, flag, c, remedy)


def test_clean_change_passes():
    v = evaluate(
        fresh=[scored(ccn=4, cov=1.0)],
        changed_ranges={"src/a.ts": [(1, 9)]},
        ratchet=[],
        baseline_failures=set(),
        fresh_failures=set(),
        target=6,
    )
    assert isinstance(v, Verdict)
    assert v.ok
    assert v.gate_violations == [] and v.ratchet_regressions == [] and v.new_failures == []


def test_touched_function_over_target_is_a_gate_violation():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={"src/a.ts": [(3, 4)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok
    assert v.gate_violations[0].long_name == "f( )"
    assert v.gate_violations[0].crap == 30.0


def test_untouched_function_over_target_is_not_a_gate_violation():
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert v.ok, "legacy debt is trend, not a blocker, until touched"


def test_cc7_at_full_coverage_still_fails_the_gate():
    v = evaluate(fresh=[scored(ccn=7, cov=1.0)], changed_ranges={"src/a.ts": [(1, 9)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok and v.gate_violations[0].ccn == 7


def test_ratchet_regression_fires_even_untouched():
    entry = RatchetEntry(path="src/a.ts", long_name="f( )", crap=20.0)
    v = evaluate(fresh=[scored(ccn=5, cov=0.0)], changed_ranges={},
                 ratchet=[entry], baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok
    assert v.ratchet_regressions[0].fresh_crap == 30.0
    assert v.ratchet_regressions[0].recorded == 20.0


def test_ratchet_improvement_is_not_a_regression():
    entry = RatchetEntry(path="src/a.ts", long_name="f( )", crap=40.0)
    v = evaluate(fresh=[scored(ccn=5, cov=0.5)], changed_ranges={},
                 ratchet=[entry], baseline_failures=set(), fresh_failures=set(), target=6)
    assert v.ok


def test_new_test_failures_vs_baseline_fail_the_verdict():
    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={},
                 ratchet=[], baseline_failures={"t_old"}, fresh_failures={"t_old", "t_new"}, target=6)
    assert not v.ok and v.new_failures == ["t_new"]


def test_preexisting_failures_do_not_fail_the_verdict():
    v = evaluate(fresh=[scored(ccn=2)], changed_ranges={},
                 ratchet=[], baseline_failures={"t_old"}, fresh_failures={"t_old"}, target=6)
    assert v.ok


def test_twin_functions_regression_cannot_hide_behind_its_sibling():
    entry = RatchetEntry(path="src/a.ts", long_name="handlers ( )", crap=20.0)
    twin_bad = scored(name="handlers ( )", start=1, end=1, ccn=9, cov=0.0)   # crap 90
    twin_ok = scored(name="handlers ( )", start=2, end=2, ccn=2, cov=1.0)
    v = evaluate(fresh=[twin_bad, twin_ok], changed_ranges={}, ratchet=[entry],
                 baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v.ok and v.ratchet_regressions, "the WORST twin is compared against the mark"
    v2 = evaluate(fresh=[twin_ok, twin_bad], changed_ranges={}, ratchet=[entry],
                  baseline_failures=set(), fresh_failures=set(), target=6)
    assert not v2.ok, "input order must not decide"


def test_gate_uses_the_scope_ceiling():
    from crapkit.score import ScoredRow
    from crapkit.verify import evaluate
    rows = [
        ScoredRow("src", "src/a.ts", "f( )", 1, 9, 8, 8, 8, 5, 1, 1, 1.0, "measured", 8.0, "decompose"),
        ScoredRow("legacy", "old/b.py", "g( )", 1, 9, 8, 8, 8, 5, 1, 1, 1.0, "measured", 8.0, "ok"),
    ]
    changed = {"src/a.ts": [(1, 9)], "old/b.py": [(1, 9)]}
    v = evaluate(fresh=rows, changed_ranges=changed, ratchet=[], baseline_failures=set(),
                 fresh_failures=set(), target=6, scope_targets={"src": 6, "legacy": 10})
    assert [g.path for g in v.gate_violations] == ["src/a.ts"], \
        "the legacy scope's ceiling of 10 admits ccn 8; src's ceiling of 6 does not"


def test_marks_that_rose_by_the_same_amount_list_in_path_order():
    """a.ts and b.ts rose by 0.2 each, c.ts by 1.0. In binary floating point
    10.3 - 10.1 is 0.20000000000000107 and 20.3 - 20.1 is 0.1999999999999993,
    which listed b.ts ahead of a.ts."""
    marks = [RatchetEntry(path="src/a.ts", long_name="f( )", crap=20.1),
             RatchetEntry(path="src/b.ts", long_name="f( )", crap=10.1),
             RatchetEntry(path="src/c.ts", long_name="f( )", crap=7.0)]
    fresh = [scored(path="src/a.ts", crap=20.3), scored(path="src/b.ts", crap=10.3),
             scored(path="src/c.ts", crap=8.0)]
    v = evaluate(fresh=fresh, changed_ranges={}, ratchet=marks, baseline_failures=set(),
                 fresh_failures=set(), target=6)
    assert [(r.path, r.recorded, r.fresh_crap) for r in v.ratchet_regressions] == [
        ("src/c.ts", 7.0, 8.0), ("src/a.ts", 20.1, 20.3), ("src/b.ts", 10.1, 10.3)]


# ccn 25 at 80% coverage and ccn 5 at none both score 30 exactly. The floats read
# 29.999999999999996 and 30.0, and a sort on the float put the second first
# whatever the paths said.
SCORE_30 = [scored(path="src/a.ts", ccn=25, cov=0.8), scored(path="src/b.ts", ccn=5, cov=0.0)]


def test_gate_violations_with_the_same_crap_list_in_path_order():
    v = evaluate(fresh=SCORE_30, changed_ranges={"src/a.ts": [(1, 9)], "src/b.ts": [(1, 9)]},
                 ratchet=[], baseline_failures=set(), fresh_failures=set(), target=6)

    assert [g.path for g in v.gate_violations] == ["src/a.ts", "src/b.ts"]


def test_unmarked_debt_with_the_same_crap_lists_in_path_order():
    rows = unmarked_over_ceiling(SCORE_30, [], target=6)

    assert [r.path for r in rows] == ["src/a.ts", "src/b.ts"]


# --- docs/agent-json.md's findings kind table ------------------------------------------

AGENT_JSON = ROOT / "docs" / "agent-json.md"

# One entry of each kind, keyed by kind; a verdict holds it in the kind's field.
ENTRY = {
    "unreadable_name": UnreadableName("src/caf\udce9.py", "src"),
    "gate_violation": GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose"),
    "unread_file": Unread("src/b.ts", "src/b.ts:12: arrow refused"),
    "ratchet_regression": RatchetRegression("lib/m.py", "g( y )", 4.0, 9.0),
    "new_failure": "tests/t.py::test_a",
    "diff_uncovered": UncoveredViolation("src/a.py", 7),
    "overridden": GateViolation("src/a.py", "f( x )", 3, 9, 0.5, 84.0, "decompose"),
}
COMMON = {"kind", "fails", "exit_code", "overridable", "dirty", "rule"}


def _holding(*kinds: str) -> Verdict:
    entries = {row.field: [ENTRY[row.kind]] for row in FINDING_KINDS if row.kind in kinds}
    return Verdict.passing()._replace(**entries)


def _kind_table() -> list[list[str]]:
    """The cells of each row of the page's table whose first column is Kind."""
    lines = AGENT_JSON.read_text(encoding="utf-8").splitlines()
    header = next(i for i, line in enumerate(lines) if re.match(r"\|\s*Kind\s*\|", line))
    rows = takewhile(lambda line: line.startswith("|"), lines[header + 2:])
    return [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]


def _documented(rows: list[list[str]]) -> dict[str, tuple]:
    """kind: (its own fields, its exit code, its rule) as the table gives them.
    A fields cell naming another kind (`the gate_violation fields`) means that
    kind's fields; `none` in the exit column means no exit code."""
    named = {row[0].strip("`"): set(re.findall(r"`(\w+)`", row[1])) for row in rows}
    documented = {}
    for row in rows:
        kind = row[0].strip("`")
        fields = set().union(*(named.get(name, {name}) for name in named[kind]))
        code = re.match(r"\d+", row[2])
        documented[kind] = (fields, int(code.group()) if code else None, row[3].strip("`"))
    return documented


def test_the_agent_page_s_kind_table_lists_verify_s_kinds_fields_exits_and_rules():
    """Each row against the item verify lists for one entry of that kind, in
    the order verify lists the kinds, which is the exit order."""
    rows = _kind_table()
    items = verify.finding_items(_holding(*ENTRY))
    product = {item["kind"]: (set(item) - COMMON, item["exit_code"], item["rule"]) for item in items}

    assert [row[0].strip("`") for row in rows] == [row.kind for row in FINDING_KINDS]
    assert _documented(rows) == product


def test_each_exit_code_the_kind_table_names_is_verify_s_and_a_row_of_readme_s_exit_table():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    exit_table = readme.split("\n## Exit codes\n", 1)[1].split("\n## ", 1)[0]
    readme_codes = {int(code) for code in re.findall(r"^\| (\d+) \|", exit_table, re.MULTILINE)}

    for kind, (_, code, _) in _documented(_kind_table()).items():
        assert verify.exit_code(_holding(kind)) == (code or 0), kind
        assert code is None or code in readme_codes, kind


def test_the_agent_page_says_diff_uncovered_items_stop_where_verify_stops_them():
    """The page says the items stop and the count does not, and every sentence
    giving the cap names verify's."""
    text = AGENT_JSON.read_text(encoding="utf-8")
    stops = re.findall(r"`diff_uncovered` items stop at (\d+); `counts\.diff_uncovered_count` does not",
                       text)
    caps = [*stops, *re.findall(r"`findings` lists the first (\d+)", text),
            *re.findall(r"Above (\d+) the\s+two disagree", text)]

    assert stops
    assert {int(cap) for cap in caps} == {verify.LISTED_LINES}
