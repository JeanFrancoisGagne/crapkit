"""Digest seam: two scored row sets in, totals + delta out; silence when unchanged. Pure.

The tests at the end drive `crapkit digest` in-process over two coverage runs."""
import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.digest import build_digest, totals
from crapkit.config import Config, load_config_text
from crapkit.score import ScoredRow


def scored(path, name, ccn, cov, scope="src"):
    c = ccn * ccn * (1 - cov) ** 3 + ccn
    remedy = "decompose" if ccn > 6 else ("ok" if c <= 6 else "add-tests")
    return ScoredRow(scope, path, name, 1, 9, ccn, ccn, ccn, 5, 1, 1, cov, "measured", c, remedy)


BASE = [scored("src/a.ts", "f( )", 9, 0.5), scored("src/b.ts", "g( )", 3, 1.0)]
FLAT = Config(target=6).ceiling_of  # every scope judged at the repo ceiling


def test_totals_carry_the_five_numbers():
    t = totals(BASE, target=6)
    assert t.functions == 2
    assert t.over_target == 1
    assert round(t.crap_load, 2) == round(sum(r.crap for r in BASE), 2)
    assert t.avg > 0


def test_unchanged_runs_digest_to_silence():
    d = build_digest(BASE, BASE, ceiling_of=FLAT)
    assert d.quiet is True
    assert d.lines == []


def test_regression_produces_lines_and_names_the_function():
    worse = [scored("src/a.ts", "f( )", 9, 0.0), BASE[1]]
    d = build_digest(BASE, worse, ceiling_of=FLAT)
    assert d.quiet is False
    text = "\n".join(d.lines)
    assert "f( )" in text and "src/a.ts" in text


def test_improvement_also_speaks_but_marked_as_improvement():
    better = [scored("src/a.ts", "f( )", 9, 1.0), BASE[1]]
    d = build_digest(BASE, better, ceiling_of=FLAT)
    assert d.quiet is False
    assert any("improved" in line for line in d.lines)


def test_new_over_target_function_is_named():
    grown = BASE + [scored("src/new.ts", "n( )", 12, 0.0)]
    d = build_digest(BASE, grown, ceiling_of=FLAT)
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


REPORTS_AT_12 = """
[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[scope]]
name = "reports"
paths = ["reports"]
languages = ["typescript"]
target = 12

[exclude]
globs = ["**/node_modules/**"]
"""

SCOPED = load_config_text(REPORTS_AT_12).ceiling_of  # reports at 12, everything else at 6


def test_the_digest_counts_over_ceiling_per_scope_like_trend_does():
    grown = BASE + [reports_row(0.7)]

    d = build_digest(BASE, grown, ceiling_of=SCOPED)

    assert d.lines[0].endswith("over ceiling 1 -> 1; functions 2 -> 3"), d.lines
    assert not any("r( )" in line for line in d.lines), \
        "a function under its own scope's ceiling is not new debt"


REPORTS_BEFORE = [*BASE, scored("reports/q.ts", "q( )", 2, 1.0, scope="reports")]


def test_without_scope_ceilings_the_same_function_is_new_debt():
    d = build_digest(REPORTS_BEFORE, REPORTS_BEFORE + [reports_row(0.7)], ceiling_of=FLAT)

    assert "over ceiling 1 -> 2" in d.lines[0], d.lines
    assert any(line.startswith("new over ceiling: reports/r.ts r( )") for line in d.lines), d.lines


def test_an_improvement_is_judged_against_the_scopes_own_ceiling():
    """Dropping from 19.1 to 11.2 clears a ceiling of 12: an improvement of a
    function that WAS over, and one debt fewer. Under the repo ceiling of 6 it
    improved too, and is still over. Only the over-count differs."""
    before, after = BASE + [reports_row(0.5)], BASE + [reports_row(0.7)]

    scoped = build_digest(before, after, ceiling_of=SCOPED)
    flat = build_digest(before, after, ceiling_of=FLAT)

    assert any("improved" in line and "r( )" in line for line in scoped.lines), scoped.lines
    assert any("improved" in line and "r( )" in line for line in flat.lines), flat.lines
    assert "over ceiling 2 -> 1" in scoped.lines[0] and "over ceiling 2 -> 2" in flat.lines[0]



def test_the_digest_reads_the_ceiling_through_the_config_accessor():
    """cmd_digest hands build_digest `Config.ceiling_of`, the one spelling of
    "a scope's own target, else the repo's": reports at 12, everything else at 6."""
    from crapkit.config import load_config_text

    cfg = load_config_text(REPORTS_AT_12)

    d = build_digest(BASE, BASE + [reports_row(0.7)], ceiling_of=cfg.ceiling_of)

    assert d.lines[0].endswith("over ceiling 1 -> 1; functions 2 -> 3"), d.lines
    assert not any("r( )" in line for line in d.lines), \
        "a function under its own scope's ceiling is not new debt"


# --- a scope the previous run never scored --------------------------------------
#
# The older run of the pair holds no row for a function either because the
# function did not exist or because that run never measured its scope (the
# scope was added to crapkit.toml since, with the lane set unchanged). Reading
# the second as the first announced old debt as "new over ceiling".

def test_a_function_in_a_scope_the_previous_run_did_not_score_is_newly_scored():
    d = build_digest(BASE, BASE + [reports_row(0.0)], ceiling_of=FLAT)

    assert any(line.startswith("newly scored over ceiling in scope reports: reports/r.ts r( )")
               for line in d.lines), d.lines
    assert not any(line.startswith("new over ceiling") for line in d.lines), d.lines


def test_a_newly_scored_function_under_its_ceiling_is_not_listed():
    d = build_digest(BASE, BASE + [reports_row(0.7)], ceiling_of=SCOPED)

    assert d.lines[0].endswith("functions 2 -> 3"), d.lines
    assert len(d.lines) == 1, d.lines


def test_new_code_in_a_scope_both_runs_scored_is_still_new():
    d = build_digest(REPORTS_BEFORE, REPORTS_BEFORE + [reports_row(0.0)], ceiling_of=FLAT)

    assert any(line.startswith("new over ceiling: reports/r.ts r( )") for line in d.lines), d.lines
    assert not any(line.startswith("newly scored") for line in d.lines), d.lines


def test_the_newly_scored_list_keeps_to_top_worst_first():
    rows = [scored(f"reports/r{i}.ts", f"r{i}( )", 7 + i, 0.0, scope="reports") for i in range(3)]

    d = build_digest(BASE, BASE + rows, ceiling_of=FLAT, top=2)

    listed = [line for line in d.lines if line.startswith("newly scored")]
    assert [line.split(": ")[1].split(" (")[0] for line in listed] == [
        "reports/r2.ts r2( )", "reports/r1.ts r1( )"], listed


LEGACY_SCOPE = """
[[scope]]
name = "legacy"
paths = ["legacy"]
languages = ["typescript"]
coverage_optional = true
"""


def _digest_after(repo, capsys, change) -> str:
    """Two coverage runs with the same lane set around `change`, then the digest."""
    from cli_inproc_repo import KNOTTY, commit_all, seed_artifacts

    from crapkit.cli import main

    seed_artifacts(repo)
    (repo / "legacy").mkdir()
    (repo / "legacy" / "old.ts").write_text(KNOTTY.lstrip(), encoding="utf-8")
    commit_all(repo, "legacy code no scope measures yet")
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    change(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert main(["digest", "--repo", str(repo)]) == 0
    return capsys.readouterr().out


def _add_legacy_scope(repo) -> None:
    from cli_inproc_repo import commit_all

    with open(repo / "crapkit.toml", "a", encoding="utf-8") as fh:
        fh.write(LEGACY_SCOPE)
    commit_all(repo, "measure legacy/ too")


def _new_function_in_a_scored_scope(repo) -> None:
    from cli_inproc_repo import KNOTTY, commit_all

    from crapkit.cli import main

    _add_legacy_scope(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    (repo / "legacy" / "new.ts").write_text(KNOTTY.lstrip().replace("knotty", "fresh"),
                                            encoding="utf-8")
    commit_all(repo, "a new function")


@pytest.mark.parametrize(("change", "said", "unsaid"), [
    pytest.param(_add_legacy_scope,
                 "newly scored over ceiling in scope legacy: legacy/old.ts knotty ( n )",
                 "new over ceiling", id="scope-added-since-previous-run"),
    pytest.param(_new_function_in_a_scored_scope,
                 "new over ceiling: legacy/new.ts fresh ( n )",
                 "newly scored", id="control-function-really-new"),
])
def test_the_digest_command_tells_newly_scored_code_from_new_code(repo, capsys, change, said, unsaid):
    out = _digest_after(repo, capsys, change)

    assert said in out, out
    assert unsaid not in out, out
