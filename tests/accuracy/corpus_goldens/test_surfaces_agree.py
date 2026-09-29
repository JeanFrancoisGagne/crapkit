"""Every surface that prints a function's CRAP, coverage, flag or remedy prints
what the recorded counts say, at the precision that surface prints.

The expected values are independent of crapkit: counts from the artifacts
(oracles/corpus_counts.py), the README's rules (model_corpus.py) and exact
arithmetic (kit.exact). A row's analysis columns (path, start, end, ccn,
scope) are inputs here; the analysis-oracles packet checks them. Each surface
is parsed by kit.surfaces and every function it names is checked, so two
surfaces that agree with the counts agree with each other.
"""
from collections import Counter
from fractions import Fraction
import json
from pathlib import Path

import pytest

from accuracy.corpus_goldens import golden_runs, surface_expect as se
from accuracy.kit import exact, rulings, surfaces

pytestmark = pytest.mark.process

EXACT_ROWS = {"crap": se.FLOAT, "cov": se.FLOAT, "flag": se.TEXT, "remedy": se.TEXT}
TEXT_ROWS = {"crap": se.FIXED1, "cov": se.PERCENT}
MESSAGE = {"crap": se.FIXED1, "cov": se.PERCENT, "remedy": se.TEXT}
VUE = "src/web/Counter.vue"


@pytest.fixture(scope="module")
def small(small_corpus):
    rows = surfaces.read_tsv(small_corpus.output("scored.tsv"))[1]
    return small_corpus, surfaces.Roster(rows), se.expectations(rows)


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    run = golden_runs.session(golden_runs.shared_base(tmp_path_factory))
    run_id = json.loads(run.output("verify.json"))["run_id"]
    rows = _store_rows(run.root, run_id)
    return run, surfaces.Roster(rows), se.expectations(rows)


def _store_rows(root: Path, run_id: int) -> list[dict]:
    view = surfaces.from_store(root / ".crapkit" / "crap.sqlite", run_id)
    return [dict(fields) for fields in view.values()]


def _without_vue(expected: dict) -> dict:
    return {key: value for key, value in expected.items() if key[0] != VUE}


def _small_views(run, roster) -> list[tuple[str, dict, dict]]:
    output = run.output
    return [
        ("scored.tsv", surfaces.from_tsv(output("scored.tsv"), roster), EXACT_ROWS),
        ("worklist.json", surfaces.from_json(json.loads(output("worklist.json")), roster),
         EXACT_ROWS),
        ("next-item.json", surfaces.from_json(json.loads(output("next-item.json")), roster),
         EXACT_ROWS),
        ("worklist.txt", surfaces.from_worklist_text(output("worklist.txt"), roster), TEXT_ROWS),
        ("report.html", _headings(surfaces.from_html(output("report.html"), roster)),
         TEXT_ROWS),
        ("coverage.sarif", surfaces.from_sarif(json.loads(output("coverage.sarif")), roster),
         MESSAGE),
        ("store", _store_view(run.root, 2), EXACT_ROWS),
    ]


def _store_view(root: Path, run_id: int) -> dict:
    """The store keeps flag and remedy as ids; the joined names are the labels."""
    view = surfaces.from_store(root / ".crapkit" / "crap.sqlite", run_id)
    return {key: {**fields, "flag": fields["flag_name"], "remedy": fields["remedy_name"]}
            for key, fields in view.items()}


def _headings(view: dict) -> dict:
    """The report titles its columns CRAP and Cov and prints `83%`: the model's
    names, and the number without its percent sign."""
    return {key: {"crap": fields["CRAP"], "cov": fields["Cov"].rstrip("%")}
            for key, fields in view.items()}


def _checked(views: list, expected: dict) -> tuple[list, dict]:
    problems, counts = [], {}
    for name, view, rules in views:
        found, counts[name] = se.compare(name, view, rules, expected)
        problems += found
    return problems, counts


def test_every_small_run_surface_prints_the_counts(small):
    run, roster, expected = small

    problems, counts = _checked(_small_views(run, roster), _without_vue(expected))

    assert [str(problem) for problem in problems] == []
    assert [name for name, count in counts.items() if count == 0] == []


@rulings.applies("CG1")
def test_vue_rows_join_the_counts_at_their_file_lines(small):
    run, roster, expected = small
    view = surfaces.from_tsv(run.output("scored.tsv"), roster)
    vue = {key: value for key, value in expected.items() if key[0] == VUE}
    problems, _ = se.compare("scored.tsv", view, EXACT_ROWS, vue)
    lines = (se.SMALL / VUE).read_text(encoding="utf-8").splitlines()
    file_line = 1 + next(number for number, line in enumerate(lines)
                         if line.startswith("export function tone("))

    rulings.pin_ruling("CG1", crapkit=f"start {view[(VUE, 'tone')]['start']}, "
                                      f"{len(problems)} wrong fields",
                       oracle=f"start {file_line}, 0 wrong fields")


def _printed(totals: dict) -> tuple[int, int, str, str]:
    return (totals["functions"], totals["over_target"], f"{totals['crap_load']:.2f}",
            totals["grade"])


def test_the_run_totals_add_up_the_rows(small):
    run, _, expected = small
    summary = json.loads(run.output("coverage.json"))
    totals = se.row_totals(run.output("scored.tsv"), expected, VUE)

    assert _printed(summary) == se.scope_totals(totals, None)
    assert {scope: _printed(values) for scope, values in summary["by_scope"].items()} == {
        scope: se.scope_totals(totals, scope) for scope in summary["by_scope"]}


def test_the_flag_counts_add_up_the_rows(small):
    run, _, _ = small
    rows = surfaces.read_tsv(run.output("scored.tsv"))[1]
    summary = json.loads(run.output("coverage.json"))
    flags = _expected_flags(rows)

    assert {flag: summary[flag.replace("-", "_")] for flag in flags} == dict(flags)


def _expected_flags(rows: list[dict]) -> Counter:
    """The model's flag per row; a Vue row counts as crapkit flagged it (CG1)."""
    model = [item.flag for item in se.per_row(rows)]
    return Counter(row["flag"] if row["path"] == VUE else flag for row, flag in zip(rows, model))


def _row_fields(row: dict) -> dict:
    return {"crap": row["crap"], "cov": row["cov"], "flag": row["flag"], "remedy": row["remedy"]}


def test_functions_that_share_a_line_read_untested_on_every_row(small):
    """R22 and R23: two functions on one line. kit.surfaces keys them as one, so
    they are checked here row by row, off the scored export."""
    run, _, _ = small
    rows = surfaces.read_tsv(run.output("scored.tsv"))[1]
    shared = se.shared_starts(rows)
    wanted = {id(row): item for row, item in zip(rows, se.per_row(rows))}
    problems = [_row_problems(row, wanted[id(row)]) for row in shared]

    assert len(shared) == 4
    assert [line for lines in problems for line in lines] == []


def _row_problems(row: dict, expected) -> list[str]:
    found = [se.check("scored.tsv", (row["path"], row["long_name"]), field, rule,
                      _row_fields(row)[field], expected) for field, rule in EXACT_ROWS.items()]
    return [str(problem) for problem in found if problem]


# --- the session's surfaces -------------------------------------------------------------

def _run_rows(run, run_id: int) -> list[dict]:
    return _store_rows(run.root, run_id)


def _json(run, name: str):
    return json.loads(run.output(name))


def _read_views(run, roster) -> list[tuple[str, dict, dict]]:
    """Surfaces the session read before it changed anything: they describe run 2."""
    names = ["brief.json", "brief-twin.json", "brief-batch.json", "rescore.json", "gate.json",
             "next-item-top.json", "worklist-batches.json"]
    names += [f"cli-{tool}.json" for tool in ("get_next_item", "list_worklist",
                                             "get_function_brief", "check_gate")]
    return [(name, surfaces.from_json(_startless_unrowed(_json(run, name)), roster), EXACT_ROWS)
            for name in names]


MCP_ROW_TOOLS = ("get_next_item", "list_worklist", "get_function_brief", "check_gate")


def _mcp_views(run, roster) -> list[tuple[str, dict, dict]]:
    """The MCP tools whose results carry function rows, read as parsed JSON."""
    return [(f"mcp {tool}", surfaces.mcp_result(_mcp(run, tool), roster), EXACT_ROWS)
            for tool in MCP_ROW_TOOLS]


def _unrowed_dict(node: dict) -> dict:
    drop = "path" if "start" not in node else None
    return {key: _startless_unrowed(value) for key, value in node.items() if key != drop}


_UNROW = {list: lambda node: [_startless_unrowed(item) for item in node], dict: _unrowed_dict}


def _startless_unrowed(node):
    """A payload whose function rows without a start line (brief's own header
    names its function by path and name only) no longer read as rows: a twin's
    name alone keys no single function. Their children stay."""
    handler = _UNROW.get(type(node))
    return handler(node) if handler else node


def _function_results(sarif: dict) -> dict:
    """The SARIF results that name a function; diff-uncovered results name a line."""
    runs = [{**run, "results": [result for result in run["results"]
                                if ": CRAP " in result["message"]["text"]]}
            for run in sarif["runs"]]
    return {**sarif, "runs": runs}


def _mcp(run, tool: str) -> dict:
    return json.loads((run.raw / f"mcp-{tool}.json").read_text(encoding="utf-8"))


def _session_roster(tmp_path_factory):
    base = golden_runs.shared_base(tmp_path_factory)
    roster = surfaces.Roster(surfaces.read_tsv(golden_runs.small(base).output("scored.tsv"))[1])
    return golden_runs.session(base), roster


def test_every_session_read_prints_the_counts(small, tmp_path_factory):
    _, _, expected = small
    run, roster = _session_roster(tmp_path_factory)

    problems, counts = _checked(_read_views(run, roster), _without_vue(expected))

    assert [str(problem) for problem in problems] == []
    assert [name for name, count in counts.items() if count == 0] == []


def test_every_mcp_tool_prints_the_counts(small, tmp_path_factory):
    """structuredContent of every row-bearing MCP tool, against the model, not
    against the CLI (test_mcp_equals_cli does that)."""
    _, _, expected = small
    run, roster = _session_roster(tmp_path_factory)

    problems, counts = _checked(_mcp_views(run, roster), _without_vue(expected))

    assert [str(problem) for problem in problems] == []
    assert sorted(name for name, count in counts.items() if count > 0) == sorted(
        f"mcp {tool}" for tool in MCP_ROW_TOOLS)


def _history_rows(run, expected: dict) -> list:
    """explain --json: each coverage run in the function's history, against the model."""
    payload = _json(run, "explain.json")
    key = (payload["path"], payload["name"])
    return [se.check("explain.json", key, field, se.FLOAT, entry[field], expected[key])
            for entry in payload["functions"][0]["history"] if entry["kind"] == "coverage"
            for field in ("crap", "cov")]


def test_explain_history_prints_the_counts(small, tmp_path_factory):
    _, _, expected = small
    run = golden_runs.session(golden_runs.shared_base(tmp_path_factory))

    found = _history_rows(run, expected)

    assert found and [str(problem) for problem in found if problem] == []


def _judged_views(run, roster) -> list[tuple[str, dict, dict]]:
    """The verdict surfaces: they describe the verify run over the edited tree."""
    commands, _ = surfaces.split_workflow_commands(run.output("verify-github.txt"))
    return [
        ("verify.json", surfaces.from_json(_json(run, "verify.json"), roster),
         {"crap": se.FLOAT, "cov": se.FLOAT, "remedy": se.TEXT}),
        ("verify.sarif", surfaces.from_sarif(_function_results(_json(run, "verify.sarif")),
                                             roster), MESSAGE),
        ("verify --github", surfaces.from_annotations(commands, roster), MESSAGE),
        ("pr-comment.md gate", _gate_bullets(run.output("pr-comment.md"), roster), TEXT_ROWS),
    ]


def _gate_bullets(text: str, roster) -> dict:
    view = surfaces.from_pr_comment(text, roster)
    return {key: {"crap": fields["gate.crap"], "cov": fields["gate.cov"]}
            for key, fields in view.items() if "gate.crap" in fields}


def test_every_verdict_surface_prints_the_counts(tmp_path_factory):
    run = golden_runs.session(golden_runs.shared_base(tmp_path_factory))
    rows = _run_rows(run, _json(run, "verify.json")["run_id"])
    expected = se.expectations(rows, config=run.root / "crapkit.toml")

    problems, counts = _checked(_judged_views(run, surfaces.Roster(rows)), expected)

    assert [str(problem) for problem in problems] == []
    assert [name for name, count in counts.items() if count == 0] == []


def test_the_emitted_baseline_prints_the_counts_of_its_run(tmp_path_factory):
    run = golden_runs.session(golden_runs.shared_base(tmp_path_factory))
    text = (run.outputs / "baseline.tsv").read_text(encoding="utf-8")
    rows = surfaces.read_tsv(text)[1]
    expected = se.expectations(rows, config=run.root / "crapkit.toml")
    view = surfaces.from_tsv(text, surfaces.Roster(rows))

    problems, checked = se.compare("baseline.tsv", view, EXACT_ROWS, _without_vue(expected))

    assert checked > 0
    assert [str(problem) for problem in problems] == []
