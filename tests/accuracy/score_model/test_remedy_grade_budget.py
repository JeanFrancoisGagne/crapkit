"""Remedy label, grade letter and the work budget.

Expected values: the README's remedy and grade tables, agent-json.md's budget
formulas, all in exact arithmetic (model_score, kit.exact), and the hand rows.
"""
from __future__ import annotations

from collections import Counter
from fractions import Fraction
import math

import html5lib
from hypothesis import assume, event, given, strategies as st
import pytest

from accuracy.kit import exact, rulings, strategies
from accuracy.kit.settings import pure
from accuracy.score_model import cases, cli_repo, model_score, production

REMEDY_ROWS = cases.hand("Remedy label")
GRADE_ROWS = cases.hand("Grade letter")
BUDGET_ROWS = cases.hand("Work budget estimates")
SEVERITY = {"ok": 0, "add-tests": 1, "split-lines": 1, "decompose": 2}
LETTERS = ("A+", "A", "B", "C", "D", "F")
XHTML = "{http://www.w3.org/1999/xhtml}"


def crapkit_crap(ccn: int, covered: int, total: int) -> float:
    return production.load("score:crap")(ccn, covered / total)


def crapkit_remedy(ccn: int, covered: int, total: int, ceiling: int, shared: bool) -> str:
    return production.load("score:remedy")(ccn, crapkit_crap(ccn, covered, total), ceiling, shared)


def _ints(given_: dict, *names: str) -> list[int]:
    return [int(given_[name]) for name in names]


# --- remedy ---------------------------------------------------------------------------------

@pytest.mark.parametrize("given_,expected", [row[1:] for row in REMEDY_ROWS],
                         ids=[row[0] for row in REMEDY_ROWS])
def test_remedy_hand_rows(given_, expected):
    args = _ints(given_, "ccn", "covered", "total", "ceiling")

    assert crapkit_remedy(*args, given_["shared"] == "1") == expected["remedy"]


def ceilings():
    return st.integers(1, 40)


@given(st.integers(1, 60), strategies.counts(), ceilings(), st.booleans())
@pure
def test_remedy_matches_the_readme_table(ccn, pair, ceiling, shared):
    """Away from an exact CRAP equal to the ceiling, which SM-CEILING-EQ pins."""
    want = exact.crap(ccn, Fraction(*pair))
    if want == ceiling:
        event("shape:crap-equals-ceiling")
    assume(want != ceiling)

    assert crapkit_remedy(ccn, *pair, ceiling, shared) == model_score.remedy(ccn, want, ceiling, shared)


def _whole_craps(covered: int, total: int, limit: int) -> list[tuple[int, int, int, int]]:
    cube, dark = total ** 3, (total - covered) ** 3
    whole = [(ccn, ccn * ccn * dark // cube + ccn) for ccn in range(1, 61)
             if ccn * ccn * dark % cube == 0]
    return [(ccn, covered, total, value) for ccn, value in whole if value <= limit]


def exact_integer_craps(limit: int = 200) -> list[tuple[int, int, int, int]]:
    """(ccn, covered, total, crap) where the exact CRAP ccn^2 (t - c)^3 / t^3 + ccn is
    a whole number no larger than `limit`: ccn up to 60, covered/total in lowest
    terms strictly between 0 and 1, total up to 240."""
    return [case for pair in _lowest_terms(240) for case in _whole_craps(*pair, limit)]


def _lowest_terms(largest: int) -> list[tuple[int, int]]:
    """(covered, total) strictly between 0 and 1, in lowest terms, total up to `largest`."""
    pairs = ((covered, total) for total in range(2, largest + 1) for covered in range(1, total))
    return [pair for pair in pairs if math.gcd(*pair) == 1]


def test_the_integer_craps_are_kit_exact_s():
    cases_ = exact_integer_craps()

    assert (18, 2, 3, 30) in cases_
    assert [case for case in cases_ if exact.crap(case[0], Fraction(*case[1:3])) != case[3]] == []


@rulings.applies("SM-CEILING-EQ")
def test_a_crap_exactly_at_the_ceiling_reads_ok():
    """README.md#remedy: ok when crap <= ceiling. CRAP(18, 2/3) is 30 exactly,
    and a scope at `target = 30` (the README's crap4j setting) must read ok."""
    wrong = [case for case in exact_integer_craps()
             if case[0] <= case[3] and crapkit_remedy(*case[:3], case[3], False) != "ok"]

    rulings.pin_ruling("SM-CEILING-EQ", crapkit=crapkit_remedy(18, 2, 3, 30, False),
                       oracle=model_score.remedy(18, exact.crap(18, Fraction(2, 3)), 30))
    assert wrong == []


@given(st.integers(1, 60), strategies.counts(), ceilings(), st.integers(1, 50))
@pure
def test_raising_coverage_never_turns_ok_into_work(ccn, pair, ceiling, more):
    covered, total = pair
    before = crapkit_remedy(ccn, covered, total, ceiling, False)
    after = crapkit_remedy(ccn, min(total, covered + more), total, ceiling, False)

    assert SEVERITY[after] <= SEVERITY[before]


@given(st.integers(1, 60), strategies.counts(), ceilings(), st.integers(1, 20))
@pure
def test_raising_the_ceiling_never_worsens_the_remedy(ccn, pair, ceiling, more):
    before = crapkit_remedy(ccn, *pair, ceiling, False)

    assert SEVERITY[crapkit_remedy(ccn, *pair, ceiling + more, False)] <= SEVERITY[before]


def _scored(rows, lane_scopes: set, **kwargs):
    return production.call("score:score_rows", rows, {}, lane_scopes=lane_scopes, **kwargs)


def _covered_ccn_8_remedy(**ceilings) -> list[str]:
    """A fully covered ccn-8 function: CRAP 8, decompose at 6, ok at 12."""
    fn = production.fn_coverage("f", 1, 9, invoked=True, branches=(8, 8))
    row = production.inventory_row("src", "src/a.ts", "f( )", 1, 9, 8)
    return [r.remedy for r in production.call("score:score_rows", [row], {"src/a.ts": [fn]},
                                              lane_scopes={"src"}, **ceilings)]


def test_remedy_reads_the_configured_ceiling():
    """R03: the remedy reads `target`, not a hard-coded 6 (README.md#remedy)."""
    assert _covered_ccn_8_remedy() == ["decompose"]
    assert _covered_ccn_8_remedy(target=12) == ["ok"]


def test_remedy_reads_the_scope_ceiling():
    """A scope's own `target` overrides the repo's (configuration.md:147)."""
    assert _covered_ccn_8_remedy(target=6, scope_targets={"src": 12}) == ["ok"]


def test_shared_span_reads_split_lines():
    """R25: two functions on one span score uncovered whatever their tests do,
    so the README's remedy for them is split-lines, never add-tests; a lone
    function in the same state still reads add-tests."""
    twins = [production.inventory_row("src", "src/a.ts", name, 3, 3, 3, occurrence=number)
             for number, name in enumerate(("(anonymous)", "other( )"), start=1)]
    alone = production.inventory_row("src", "src/b.ts", "f( )", 1, 9, 3)
    remedies = {row.path + row.long_name: row.remedy
                for row in _scored([*twins, alone], {"src"}, target=6)}

    assert remedies == {"src/a.ts(anonymous)": "split-lines", "src/a.tsother( )": "split-lines",
                        "src/b.tsf( )": "add-tests"}


# --- grade --------------------------------------------------------------------------------------

@pytest.mark.parametrize("given_,expected", [row[1:] for row in GRADE_ROWS],
                         ids=[row[0] for row in GRADE_ROWS])
def test_grade_hand_rows(given_, expected):
    assert production.load("score:grade")(*_ints(given_, "over", "total")) == expected["grade"]


def test_grade_matches_the_band_table_for_every_count_to_400():
    grade = production.load("score:grade")
    wrong = [(over, total) for total in range(1, 401) for over in range(total + 1)
             if grade(over, total) != model_score.grade(over, total)]

    assert wrong == []


@given(st.integers(1, 5000).flatmap(lambda total: st.tuples(st.integers(0, total), st.just(total))))
@pure
def test_a_function_under_its_ceiling_never_worsens_the_letter(pair):
    over, total = pair
    grade = production.load("score:grade")

    assert LETTERS.index(grade(over, total + 1)) <= LETTERS.index(grade(over, total))


# --- budget -------------------------------------------------------------------------------------

def crapkit_budget(ccn: int, covered: int, total: int, ceiling: int) -> dict:
    row = production.scored_row("src", "src/a.py", "f( )", 1, 9, ccn, covered / total,
                                "measured", crapkit_crap(ccn, covered, total), "ok")
    return production.load("packet:budget")(row, ceiling)


@pytest.mark.parametrize("given_,expected", [row[1:] for row in BUDGET_ROWS],
                         ids=[row[0] for row in BUDGET_ROWS])
def test_budget_hand_rows(given_, expected):
    got = crapkit_budget(*_ints(given_, "ccn", "covered", "total", "ceiling"))

    assert {key: str(value) for key, value in got.items()} == expected


@given(st.integers(1, 5000), ceilings())
@pure
def test_est_splits_is_the_ceiling_of_ccn_over_the_ceiling(ccn, ceiling):
    assert crapkit_budget(ccn, 1, 1, ceiling)["est_splits"] == model_score.est_splits(ccn, ceiling)


@given(st.integers(1, 5000), ceilings(), st.integers(1, 20))
@pure
def test_raising_the_ceiling_never_raises_est_splits(ccn, ceiling, more):
    before = crapkit_budget(ccn, 1, 1, ceiling)["est_splits"]

    assert crapkit_budget(ccn, 1, 1, ceiling + more)["est_splits"] <= before


def half_even_int(numerator: int, denominator: int) -> int:
    """numerator/denominator rounded to an integer, ties to even, in integers.
    test_half_even_int_is_the_model holds it to model_score.est_uncovered_paths."""
    whole, rest = divmod(numerator, denominator)
    return whole + (2 * rest > denominator or (2 * rest == denominator and whole % 2 == 1))


def test_half_even_int_is_the_model():
    sample = list(cases.full_grid())[::89]

    assert [case for case in sample if half_even_int((case[2] - case[1]) * case[0], case[2])
            != model_score.est_uncovered_paths(case[0], Fraction(case[1], case[2]))] == []


def _uncovered_problems(grid, ties_only: bool) -> list:
    template = production.scored_row("src", "src/a.py", "f( )", 1, 9, 1, 0.0, "measured", 2.0, "ok")
    budget = production.load("packet:budget")
    wrong = []
    for ccn, covered, total in grid:
        numerator = (total - covered) * ccn
        if (2 * numerator % (2 * total) == total) != ties_only:
            continue
        got = budget(template._replace(ccn=ccn, cov=covered / total), 6)["est_uncovered_paths"]
        wrong += [(ccn, covered, total, got)] if got != half_even_int(numerator, total) else []
    return wrong


def test_uncovered_paths_match_away_from_exact_halves():
    assert _uncovered_problems(cases.reduced_grid(), ties_only=False)[:20] == []


def test_uncovered_paths_round_half_even():
    """D10: 2.5 rounds to 2 (half-even), where a schoolbook half-up reading of
    agent-json.md:135 gives 3."""
    rulings.pin_ruling("D10", crapkit=crapkit_budget(5, 1, 2, 6)["est_uncovered_paths"],
                       oracle=math.floor(Fraction(5, 2) + Fraction(1, 2)))


@rulings.applies("SM-BUDGET-TIE")
def test_uncovered_paths_round_the_exact_product():
    """(1 - 5/12) * 6 is 3.5 exactly, which rounds to 4 half-even and half-up
    alike; crapkit rounds the double 3.4999999999999996 and says 3."""
    wrong = _uncovered_problems(cases.reduced_grid(), ties_only=True)

    rulings.pin_ruling("SM-BUDGET-TIE", crapkit=crapkit_budget(6, 5, 12, 6)["est_uncovered_paths"],
                       oracle=model_score.est_uncovered_paths(6, Fraction(5, 12)))
    assert wrong == []


# --- the same numbers on every surface (CLI) ---------------------------------------------------

def _model_fields(row) -> tuple:
    """remedy, est_splits, est_uncovered_paths and target for one written function."""
    crap = exact.crap(row.ccn, row.cov)
    return (model_score.remedy(row.ccn, crap, row.ceiling),
            model_score.est_splits(row.ccn, row.ceiling),
            model_score.est_uncovered_paths(row.ccn, row.cov), row.ceiling)


def _fields(payload: dict) -> tuple:
    return (payload["remedy"], payload["est_splits"], payload["est_uncovered_paths"],
            payload["target"])


def _by_start(items: list[dict]) -> dict:
    return {(item["path"], item["start"]): _fields(item) for item in items}


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.cross_surface
def test_remedy_and_budget_read_alike_on_next_item_brief_worklist_and_mcp(make_repo):
    """Every function next-item hands out carries the README remedy and the
    agent-json.md:134-135 budget, and next-item, brief and get_next_item print
    the same four values; the worklist and rescore print every row's remedy."""
    cli = cli_repo.driver(make_repo, cli_repo.SURFACES)
    assert cli.run("coverage").code == 0
    want = {(row.path, row.start): _model_fields(row) for row in cli_repo.expected(cli_repo.SURFACES)}
    handed = _by_start(cli.run("next-item", "--top", "20").json()["items"])
    (mcp,) = cli.mcp([("get_next_item", {"top": 20})])
    briefs = {key: _fields(cli.json("brief", key[0], str(key[1]))) for key in handed}

    assert handed == _not_ok(want)
    assert _by_start(mcp["structuredContent"]["items"]) == handed == briefs
    assert _worklist_remedies(cli) == _rescore_remedies(cli) == {
        key: value[0] for key, value in want.items()}


def _rescore_remedies(cli) -> dict:
    """rescore over the unchanged tree: fresh complexity on the run's own coverage."""
    paths = sorted({row.path for row in cli_repo.expected(cli_repo.SURFACES)})
    return {(f["path"], f["start"]): f["remedy"] for f in cli.json("rescore", *paths)["functions"]}


def _not_ok(fields: dict) -> dict:
    """The rows next-item hands out: every remedy but ok (agent-json.md:55)."""
    return {key: value for key, value in fields.items() if value[0] != "ok"}


def _worklist_remedies(cli) -> dict:
    return {(e["path"], e["start"]): e["remedy"] for e in cli.json("worklist")["active"]}


def _html_grades(text: str) -> dict[str, str]:
    """The report's `Grades by scope` table: scope -> the grade chip's text."""
    rows = html5lib.parse(text).iter(f"{XHTML}tr")
    return {row.get("data-scope"): _last_cell(row) for row in rows if row.get("data-scope")}


def _last_cell(row) -> str:
    return "".join(row.findall(f"{XHTML}td")[-1].itertext()).strip()


def _model_grades(rows) -> dict[str, str]:
    functions = Counter(row.scope for row in rows)
    over = Counter(row.scope for row in rows if exact.crap(row.ccn, row.cov) > row.ceiling)
    return {scope: model_score.grade(over[scope], count) for scope, count in functions.items()}


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.cross_surface
def test_grades_read_alike_on_the_summary_trend_and_report(make_repo, tmp_path):
    """Scope a holds 2 of 3 functions over 6 (F), b 1 of 2 over 12 (F), c none
    (A+): the coverage summary, trend and the HTML report print those letters."""
    cli = cli_repo.driver(make_repo, cli_repo.SURFACES)
    summary = cli.json("coverage")
    trend = cli.json("trend")["runs"][-1]
    assert cli.run("report", "--out", str(tmp_path / "report.html")).code == 0
    page = _html_grades((tmp_path / "report.html").read_text(encoding="utf-8"))
    want = _model_grades(cli_repo.expected(cli_repo.SURFACES))

    assert want == {"a": "F", "b": "F", "c": "A+"}
    assert {scope: block["grade"] for scope, block in summary["by_scope"].items()} == want
    assert {scope: block["grade"] for scope, block in trend["by_scope"].items()} == want
    assert page == want and summary["grade"] == model_score.grade(3, 6)
