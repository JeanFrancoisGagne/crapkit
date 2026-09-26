"""Every scored row of the measured corpus, recomputed from the lanes' own artifacts.

The counts come from oracles/coverage_counts.py (json.load of each lane's
artifact, no crapkit), the ratio from the README's definition, the CRAP from
kit.exact. Each row of the corpus's scored.tsv joins its counts by (path,
start line). A measured row that joins nothing fails, and so does a row
flagged untested that the docs cannot explain: README.md#flags says untested
means the artifact is silent on the function, and README.md#remedy puts a
function on a shared span, or a one-line Python def, at cov 0 as well.
"""
from __future__ import annotations

from fractions import Fraction
import math
import tomllib

import pytest

from accuracy.kit import exact, rulings, surfaces
from accuracy.score_model import cases, model_score
from accuracy.score_model.oracles import coverage_counts

pytestmark = pytest.mark.process
ULPS = 8


def _lanes(root) -> list[dict]:
    return tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8")).get("lane", [])


def _counts(root) -> dict[tuple[str, int], coverage_counts.Counts]:
    found = {}
    for lane in _lanes(root):
        for counts in coverage_counts.read(lane["parser"], root / lane["artifact"]):
            found[(counts.path, counts.start)] = counts
    return found


def _rows(corpus) -> list[dict]:
    return surfaces.read_tsv(corpus.outputs.joinpath("scored.tsv").read_text(encoding="utf-8"))[1]


# src/web/Counter.vue carries a reader defect on purpose: a .vue function's row
# starts on its <script> block's line count, not the file's (AO-VUE-LINES, CG1),
# so its rows join no artifact function. While SM-VUE-JOIN is a defect the joins
# below set the file aside and pin it in a test of its own; once the row reads
# fixed the file rejoins them.
VUE_JOIN = "SM-VUE-JOIN"
VUE_FILE = "src/web/Counter.vue"


def _joined_rows(corpus) -> list[dict]:
    aside = rulings.load()[VUE_JOIN].ruling == "defect"
    return [row for row in _rows(corpus) if not (aside and row["path"] == VUE_FILE)]


def _ratio(counts: coverage_counts.Counts) -> Fraction:
    return model_score.coverage_ratio(counts.branches, counts.statements, counts.invoked)


def _close(value: float, want: Fraction) -> bool:
    return abs(Fraction(value) - want) <= ULPS * Fraction(math.ulp(float(want)))


def _shared(rows: list[dict]) -> set[tuple]:
    spans = [(row["path"], row["start"], row["end"]) for row in rows]
    return {span for span in spans if spans.count(span) > 1}


def _floored(row: dict, shared: set[tuple]) -> bool:
    """A Python function on its def line, or any function on a shared span."""
    one_line = row["path"].endswith(".py") and row["start"] == row["end"]
    return one_line or (row["path"], row["start"], row["end"]) in shared


def _measured_problem(row: dict, counts) -> str | None:
    if counts is None:
        return "flag measured, but no lane artifact holds a function at this start line"
    want = _ratio(counts)
    if float(row["cov"]) != float(want):
        return f"cov {row['cov']}, the artifact's counts give {want}"
    return None


def _untested_problem(row: dict, counts, shared: set[tuple]) -> str | None:
    if float(row["cov"]) != 0.0:
        return f"flag untested at cov {row['cov']}"
    if counts is not None and not _floored(row, shared):
        return f"flag untested, but the artifact holds counts {counts}"
    return None


def _flag_problem(row: dict, counts, shared: set[tuple]) -> str | None:
    if row["flag"] == "measured":
        return _measured_problem(row, counts)
    if row["flag"] == "untested":
        return _untested_problem(row, counts, shared)
    return None if float(row["cov"]) == 0.0 else f"flag {row['flag']} at cov {row['cov']}"


def _exact_cov(row: dict, counts) -> Fraction:
    """The joined counts' exact ratio for a measured row; 0 for every other flag."""
    if row["flag"] == "measured" and counts is not None:
        return _ratio(counts)
    return Fraction(float(row["cov"]))


def _crap_problem(row: dict, counts) -> str | None:
    """Within ULPS of the exact CRAP of the cov double crapkit stored; the
    4 dp test below judges it against the counts' exact ratio."""
    want = model_score.crap(int(row["ccn"]), Fraction(float(row["cov"])), row["flag"])
    return None if _close(float(row["crap"]), want) else f"crap {row['crap']}, exact {want}"


def _problems(rows: list[dict], counts: dict) -> list[str]:
    shared = _shared(rows)
    found = []
    for row in rows:
        joined = counts.get((row["path"], int(row["start"])))
        for problem in (_flag_problem(row, joined, shared), _crap_problem(row, joined)):
            found += [f"{row['path']}:{row['start']} {row['long_name']}: {problem}"] if problem else []
    return found


def test_every_scored_row_matches_its_artifact_counts(scored_corpus):
    rows = _joined_rows(scored_corpus)

    assert rows, "the corpus scored no row"
    assert _problems(rows, _counts(scored_corpus.root)) == []


def test_every_measured_row_joins_by_start_line(scored_corpus):
    """The join is by (path, start): a measured row whose start no artifact
    function shares is how a span join hands a row its neighbour's number."""
    rows, counts = _joined_rows(scored_corpus), _counts(scored_corpus.root)
    measured = [row for row in rows if row["flag"] == "measured"]

    assert measured
    assert [row["long_name"] for row in measured
            if (row["path"], int(row["start"])) not in counts] == []


@rulings.applies(VUE_JOIN)
def test_vue_rows_match_their_artifact_counts(scored_corpus):
    """The set-aside file: each .vue row joins the counts the recorded istanbul
    fnMap holds at its start once the Vue reader numbers file lines."""
    rows = [row for row in _rows(scored_corpus) if row["path"] == VUE_FILE]
    problems = _problems(rows, _counts(scored_corpus.root))

    assert rows
    rulings.pin_ruling(VUE_JOIN, crapkit=f"{len(problems)} problems", oracle="0 problems")


def _four_places(row: dict, counts: dict, ties: dict) -> tuple[str, str]:
    cov = _exact_cov(row, counts.get((row["path"], int(row["start"]))))
    listed = ties.get((4, int(row["ccn"]), cov.numerator, cov.denominator))
    want = listed["crapkit"] if listed else exact.fixed(exact.crap(int(row["ccn"]), cov), 4)
    return f"{float(row['crap']):.4f}", want


def test_the_crap_column_rounds_half_even_at_four_places(scored_corpus):
    """The exported double, read back, prints the exact CRAP's 4 dp value, bar
    the exact ties rulings D5 lists."""
    counts, ties = _counts(scored_corpus.root), cases.tie_table()
    printed = {row["long_name"]: _four_places(row, counts, ties) for row in _rows(scored_corpus)
               if row["flag"] != "cc-only"}

    assert dict(filter(lambda item: item[1][0] != item[1][1], printed.items())) == {}
