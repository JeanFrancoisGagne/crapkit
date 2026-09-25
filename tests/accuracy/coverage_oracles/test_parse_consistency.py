"""Parse consistency: crapkit's reading of a coverage.py report against the producer's other outputs.

These checks compare crapkit with coverage.py itself, so they are labelled
parse-consistency and never count as an independent method: coverage.py wrote
both sides. They catch a reader that drops or doubles what the JSON says.

- The lcov report of the same data file (coverage lcov): per function, the BRDA
  arms inside its FN range, nested functions cut out, and its FNDA call count.
- The Cobertura xml report: the file's branch totals.
- The coverage.py API: statements and missing lines per region.

The metamorphic half holds for both readers: the window size, the JSON layout,
ensure_ascii and path_prefix never change a parsed FnCoverage.
"""
import json
from pathlib import Path
import xml.etree.ElementTree as ElementTree

import pytest

from accuracy.coverage_oracles import probe_repo
from crapkit.coverage_istanbul import parse_istanbul_both_file
from crapkit.coverage_py import parse_coveragepy_both_file

REPORTS = probe_repo.RECORDED / "coveragepy-reports-7.16.1"


def _fn(rest: str, functions: dict, arms: list) -> None:
    start, end, name = rest.split(",", 2)
    functions[name] = [int(start), int(end), 0]


def _fnda(rest: str, functions: dict, arms: list) -> None:
    count, name = rest.split(",", 1)
    functions[name][2] = int(count)


def _brda(rest: str, functions: dict, arms: list) -> None:
    parts = rest.split(",")
    arms.append((int(parts[0]), parts[-1] not in ("-", "0")))


LCOV_TAGS = {"FN": _fn, "FNDA": _fnda, "BRDA": _brda}


def _lcov(text: str) -> dict:
    """{name: (start, end, calls, [(line, taken)])} from one SF record."""
    functions, arms = {}, []
    for line in text.splitlines():
        tag, _, rest = line.partition(":")
        LCOV_TAGS.get(tag, lambda *_: None)(rest, functions, arms)
    return {name: (*span, _arms_in(functions, span, arms)) for name, span in functions.items()}


def _innermost(functions: dict, line: int) -> tuple:
    holders = [span for span in functions.values() if span[0] <= line <= span[1]]
    return min(holders, key=lambda span: span[1] - span[0])


def _arms_in(functions: dict, span: list, arms: list) -> list:
    return [arm for arm in arms if _innermost(functions, arm[0]) is span]


def _parsed(scenario: str) -> dict:
    per_file = parse_coveragepy_both_file(REPORTS / f"{scenario}.json", path_prefix="")[0]
    return {fn.name: fn for fn in per_file["py/shapes.py"]}


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_lcov_arms_and_calls_match_the_json_parse(scenario):
    lcov = _lcov((REPORTS / f"{scenario}.lcov").read_text(encoding="utf-8"))
    parsed = _parsed(scenario)

    for name, (start, _, calls, arms) in lcov.items():
        fn = parsed[name]
        assert (fn.start, fn.branches_total, fn.branches_covered, fn.invoked) == (
            start, len(arms), sum(taken for _, taken in arms), calls > 0), name


@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_cobertura_branch_totals_match_the_json_parse(scenario):
    root = ElementTree.parse(REPORTS / f"{scenario}.xml").getroot()
    functions = _parsed(scenario).values()

    assert (sum(fn.branches_total for fn in functions),
            sum(fn.branches_covered for fn in functions)) == (
        int(root.get("branches-valid")), int(root.get("branches-covered")))


@pytest.mark.process
@pytest.mark.parametrize("scenario", probe_repo.SCENARIOS)
def test_the_coverage_api_counts_each_region_as_the_json_parse_does(probe_run, scenario,
                                                                   monkeypatch):
    """The live run's data file, read through coverage.py's API from the
    directory it was measured in (its paths are relative)."""
    import coverage
    from coverage.regions import code_regions
    work = probe_run.slot("coveragepy-live", scenario).artifact.parent
    monkeypatch.chdir(work)
    cov = coverage.Coverage(data_file=".coverage", config_file="py/coveragerc")
    cov.load()
    source = work / "py" / "shapes.py"
    _, statements, _, missing, _ = cov.analysis2(str(source))
    parsed = _parsed(scenario)
    regions = [r for r in code_regions(source.read_text(encoding="utf-8")) if r.kind == "function"]

    for region in regions:
        lines = region.lines & set(statements)
        fn = parsed[region.name]
        assert (fn.statements_total, fn.statements_covered) == (
            len(lines), len(lines - set(missing))), region.name


# --- metamorphic: the reader's input form never moves a number ------------------------------

def _rewritten(path: Path, tmp_path: Path, layout: dict, prefix: str = "") -> Path:
    artifact = json.loads(path.read_bytes())
    if prefix:
        artifact["files"] = {key.removeprefix(prefix + "/"): value
                             for key, value in artifact["files"].items()}
    out = tmp_path / path.name
    out.write_bytes(json.dumps(artifact, **layout).encode("utf-8"))
    return out


LAYOUTS = [{}, {"indent": 3}, {"separators": (",", ":")}, {"ensure_ascii": False, "indent": 1}]
CHUNKS = [1, 7, 4096, None]


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("layout", LAYOUTS, ids=["plain", "indent", "compact", "utf8"])
def test_window_size_and_layout_never_change_the_coveragepy_parse(tmp_path, layout, chunk):
    original = REPORTS / "call.json"
    rewritten = _rewritten(original, tmp_path, layout)
    extra = {"chunk": chunk} if chunk else {}

    assert (parse_coveragepy_both_file(rewritten, path_prefix="", **extra)[:2]
            == parse_coveragepy_both_file(original, path_prefix="")[:2])


def test_path_prefix_only_moves_the_key(tmp_path):
    original = REPORTS / "call.json"
    stripped = _rewritten(original, tmp_path, {}, prefix="py")
    prefixed = parse_coveragepy_both_file(stripped, path_prefix="py")[0]

    assert prefixed == parse_coveragepy_both_file(original, path_prefix="")[0]


@pytest.mark.parametrize("chunk", CHUNKS)
@pytest.mark.parametrize("layout", LAYOUTS, ids=["plain", "indent", "compact", "utf8"])
def test_window_size_and_layout_never_change_the_istanbul_parse(tmp_path, layout, chunk):
    original = probe_repo.RECORDED / "vitest-istanbul-5.0.1" / "call.json"
    rewritten = _rewritten(original, tmp_path, layout)
    extra = {"chunk": chunk} if chunk else {}

    assert (parse_istanbul_both_file(rewritten, repo_root="", **extra)[:2]
            == parse_istanbul_both_file(original, repo_root="")[:2])
