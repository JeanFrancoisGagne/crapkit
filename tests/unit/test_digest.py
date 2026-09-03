"""Digest seam: two scored row sets in, totals + delta out; silence when unchanged. Pure."""
from crapkit.digest import build_digest, totals
from crapkit.score import ScoredRow


def scored(path, name, ccn, cov, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    remedy = "decompose" if ccn > 6 else ("ok" if c <= 6 else "add-tests")
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, remedy)


BASE = [scored("src/a.ts", "f( )", 9, 0.5), scored("src/b.ts", "g( )", 3, 1.0)]


def test_totals_carry_the_five_numbers():
    t = totals(BASE, target=6)
    assert t.functions == 2
    assert t.over_target == 1
    assert round(t.crap_load, 2) == round(sum(r.crap for r in BASE), 2)
    assert t.avg > 0


def test_unchanged_runs_digest_to_silence():
    d = build_digest(BASE, BASE, target=6)
    assert d.quiet is True
    assert d.lines == []


def test_regression_produces_lines_and_names_the_function():
    worse = [scored("src/a.ts", "f( )", 9, 0.0), BASE[1]]
    d = build_digest(BASE, worse, target=6)
    assert d.quiet is False
    text = "\n".join(d.lines)
    assert "f( )" in text and "src/a.ts" in text


def test_improvement_also_speaks_but_marked_as_improvement():
    better = [scored("src/a.ts", "f( )", 9, 1.0), BASE[1]]
    d = build_digest(BASE, better, target=6)
    assert d.quiet is False
    assert any("improved" in line for line in d.lines)


def test_new_over_target_function_is_named():
    grown = BASE + [scored("src/new.ts", "n( )", 12, 0.0)]
    d = build_digest(BASE, grown, target=6)
    assert d.quiet is False
    assert any("n( )" in line for line in d.lines)


def test_matching_lane_pair_selection_skips_partial_runs():
    from crapkit.digest import latest_comparable_pair
    runs = [
        {"id": 1, "lanes": {"unit": {}, "py": {}}},
        {"id": 2, "lanes": {"unit": {}, "py": {}}},
        {"id": 3, "lanes": {"unit": {}}},
    ]
    pair = latest_comparable_pair(runs)
    assert (pair[0]["id"], pair[1]["id"]) == (1, 2), "a --lane subset run never pairs with a full run"
    assert latest_comparable_pair(runs[:1]) is None


def test_totals_carry_normalized_ratios():
    t = totals(BASE, target=6)
    assert t.pct_over == 50.0, "1 of 2 over target; growth must not read as regression"
    assert totals([], target=6).pct_over == 0.0


def test_scope_totals_roll_up_per_scope():
    from crapkit.digest import scope_totals
    rows = BASE + [scored("ui/x.ts", "u( )", 9, 0.0, scope="ui")]
    by_scope = scope_totals(rows, target=6)
    assert by_scope["src"].functions == 2 and by_scope["ui"].over_target == 1


def test_scope_rollup_publishes_four_numbers_and_a_letter_per_scope():
    from crapkit.digest import scope_rollup, scope_totals
    # ui holds one ccn-9 function at cov 0: crap 81 + 9 = 90, over the target of 6
    rows = BASE + [scored("ui/x.ts", "u( )", 9, 0.0, scope="ui")]

    out = scope_rollup(scope_totals(rows, target=6))

    assert out["ui"] == {"functions": 1, "over_target": 1, "crap_load": 90.0, "grade": "F"}
    assert out["src"]["over_target"] == 1 and out["src"]["grade"] == "F"


def test_a_scope_with_no_debt_grades_apart_from_a_scope_with_debt():
    from crapkit.digest import scope_rollup, scope_totals
    rows = [scored("ui/x.ts", "u( )", 3, 1.0, scope="ui"),
            scored("src/a.ts", "f( )", 9, 0.0)]

    out = scope_rollup(scope_totals(rows, target=6))

    assert out["ui"] == {"functions": 1, "over_target": 0, "crap_load": 3.0, "grade": "A+"}
    assert out["src"]["grade"] == "F"


# --- the per-scope ceiling (0.5.0) ---------------------------------------------
#
# digest counted every row against the repo ceiling while trend counted each
# row against its scope's, so the two disagreed on the same run pair.

def reports_row(cov: float):
    """ccn 9 in the `reports` scope: over a ceiling of 6, under one of 12 at
    cov 0.7 (crap 11.2)."""
    return scored("reports/r.ts", "r( )", 9, cov, scope="reports")


def test_the_digest_counts_over_ceiling_per_scope_like_trend_does():
    grown = BASE + [reports_row(0.7)]

    d = build_digest(BASE, grown, target=6, scope_targets={"reports": 12})

    assert d.lines[0].endswith("over ceiling 1 -> 1; functions 2 -> 3"), d.lines
    assert not any("r( )" in line for line in d.lines), \
        "a function under its own scope's ceiling is not new debt"


def test_without_scope_ceilings_the_same_function_is_new_debt():
    d = build_digest(BASE, BASE + [reports_row(0.7)], target=6)

    assert "over ceiling 1 -> 2" in d.lines[0], d.lines
    assert any(line.startswith("new over ceiling: reports/r.ts r( )") for line in d.lines), d.lines


def test_an_improvement_is_judged_against_the_scopes_own_ceiling():
    """Dropping from 19.1 to 11.2 clears a ceiling of 12: an improvement of a
    function that WAS over, and one debt fewer. Under the repo ceiling of 6 it
    improved too, and is still over. Only the over-count differs."""
    before, after = BASE + [reports_row(0.5)], BASE + [reports_row(0.7)]

    scoped = build_digest(before, after, target=6, scope_targets={"reports": 12})
    flat = build_digest(before, after, target=6)

    assert any("improved" in line and "r( )" in line for line in scoped.lines), scoped.lines
    assert any("improved" in line and "r( )" in line for line in flat.lines), flat.lines
    assert "over ceiling 2 -> 1" in scoped.lines[0] and "over ceiling 2 -> 2" in flat.lines[0]
