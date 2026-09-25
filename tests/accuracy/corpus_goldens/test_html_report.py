"""The HTML report: its grades by scope, its trend row, its worklist and its banner.

README.md:806: the page renders what `worklist --json` and `trend --json`
answer at their defaults: the worklist capped at worklist_top, the per-scope
grades off the newest run, the trend series, and a banner naming every stale
lane. The expected values:

- grades by scope and the trend row: the scored rows added up with kit.exact
  from the counts table (model_corpus), graded by the README's bands, and
  the mean CRAP at 4 dp;
- the worklist: rows in non-increasing risk ("ranked by risk"), capped at
  50 (worklist_top's documented default, docs/configuration.md), each
  row's risk ccn times its weight at 1 dp;
- the banner, per docs/agent-json.md (uncovered_lines_note): a lane is stale
  when files in its scopes changed since its artifact was written, and
  uncommitted edits count. Fresh on the measured tree; an uncommitted edit
  under lane py's scope names py and only py; an edit under legacy, which no
  lane measures, leaves it fresh; committing the py edit also says HEAD moved.
"""
from decimal import Decimal
from pathlib import Path
import re

import html5lib
import pytest

from accuracy.corpus_goldens import surface_expect as se
from accuracy.kit import drive, exact, repos, surfaces

pytestmark = pytest.mark.process
VUE = "src/web/Counter.vue"
WORKLIST_TOP = 50
LANES = ("py", "js")
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _tree(text: str):
    return html5lib.parse(text, namespaceHTMLElements=False)


def _text(element) -> str:
    return "".join(element.itertext()).strip()


def _rows(tree, attribute: str) -> list:
    """The table rows that carry `attribute` (data-scope, data-run)."""
    return [row for row in tree.iter("tr") if row.get(attribute) is not None]


def _cells(row) -> list[str]:
    return [_text(cell) for cell in row.findall("td")]


@pytest.fixture(scope="module")
def small(small_corpus):
    rows = surfaces.read_tsv(small_corpus.output("scored.tsv"))[1]
    report = (small_corpus.outputs / "report.html").read_text(encoding="utf-8")
    return small_corpus, _tree(report), se.expectations(rows)


def _totals(run, expected) -> list:
    return se.row_totals(run.output("scored.tsv"), expected, VUE)


def _as_numbers(cells: list[str]) -> tuple:
    """Integers and decimals compared by value: the report prints a load of 34
    as `34.0`, the JSON number crapkit rounds to 2 dp (docs/agent-json.md)."""
    return tuple(Decimal(cell) if _NUMBER.fullmatch(cell) else cell for cell in cells)


def test_each_scope_s_grade_is_its_rows_added_up(small):
    run, tree, expected = small
    totals = _totals(run, expected)
    printed = {row.get("data-scope"): _as_numbers(_cells(row)) for row in _rows(tree, "data-scope")}

    assert printed == {scope: _as_numbers([scope, *map(str, se.scope_totals(totals, scope))])
                       for scope in printed}
    assert sorted(printed) == sorted({scope for scope, *_ in totals})


def test_the_trend_row_adds_up_the_newest_run(small):
    run, tree, expected = small
    totals = _totals(run, expected)
    functions, over, load, _ = se.scope_totals(totals, None)
    printed = [_cells(row) for row in _rows(tree, "data-run")]

    assert [_as_numbers(cells[3:]) for cells in printed] == [
        _as_numbers([str(functions), str(over), load, se.average(totals)])]


def _worklist(tree) -> list[list[str]]:
    return [_cells(row) for row in tree.iter("tr") if row.get("class") == "wl"]


def test_the_worklist_is_ranked_by_risk_and_capped_at_worklist_top(small):
    _, tree, _ = small
    rows = _worklist(tree)
    risks = [float(cells[0]) for cells in rows]

    assert len(rows) == WORKLIST_TOP
    assert risks == sorted(risks, reverse=True)


def test_each_row_s_risk_is_ccn_times_its_weight(small):
    """Every small-corpus file has one commit, so every weight is 1: a log whose
    timestamps are all equal weighs each commit fully (kit.exact.churn_weight)."""
    _, tree, _ = small
    weight = exact.churn_weight([0], 0, 0)

    assert [cells[0] for cells in _worklist(tree)] == [
        exact.fixed(int(cells[1]) * weight, 1) for cells in _worklist(tree)]


# --- the banner ------------------------------------------------------------------------

def _banner(root: Path, date_now: int) -> tuple[str, list[str], str]:
    out = root.parent / "report.html"
    result = drive.Driver(root, date_now=date_now, spawn=True).run("report", "--out", str(out))
    assert result.code == 0, result.stderr
    tree = _tree(out.read_text(encoding="utf-8"))
    banner = next(div for div in tree.iter("div") if "banner" in (div.get("class") or ""))
    lanes = [_text(item.find("b")) for item in banner.iter("li")]
    return banner.get("class"), lanes, _text(banner)


def _append(root: Path, path: str) -> None:
    with (root / path).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("\n# edited after the run\n")


def test_the_measured_tree_reads_fresh(small, tmp_path):
    run, _, _ = small

    kind, lanes, text = _banner(run.private_copy(tmp_path / "repo"), run.date_now)

    assert (kind, lanes) == ("banner fresh", [])
    assert f"All {len(LANES)} lane artifact(s)" in text


def test_an_uncommitted_edit_in_a_lane_s_scope_names_that_lane(small, tmp_path):
    run, _, _ = small
    root = run.private_copy(tmp_path / "repo")
    _append(root, "src/py/grades.py")

    kind, lanes, text = _banner(root, run.date_now)

    assert (kind, lanes) == ("banner stale", ["py"])
    assert "HEAD has moved on" not in text


def test_an_edit_no_lane_measures_leaves_the_banner_fresh(small, tmp_path):
    run, _, _ = small
    root = run.private_copy(tmp_path / "repo")
    _append(root, "src/legacy/old.py")

    assert _banner(root, run.date_now)[:2] == ("banner fresh", [])


def test_a_commit_after_the_run_names_the_lane_and_head(small, tmp_path):
    run, _, _ = small
    root = run.private_copy(tmp_path / "repo")
    _append(root, "src/py/grades.py")
    repos.git(root, "commit", "-q", "-am", "edit after the run", date=run.date_now - 60)

    kind, lanes, text = _banner(root, run.date_now)

    assert (kind, lanes) == ("banner stale", ["py"])
    assert "HEAD has moved on" in text
