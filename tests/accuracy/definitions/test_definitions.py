"""Each field means one thing in every place a reader meets it.

definitions.tsv gives every field its definition, the README anchor or outside
source it comes from, and the phrases each place must carry: README.md,
CONTEXT.md, docs/agent-json.md, the MCP output schemas and the SARIF rules. A
place that drops a phrase fails here with the field and the phrase named. "-"
says the place defines no such field; if one appears there, the row has to say
what it must carry.

Three definition rulings of the accuracy plan are pinned by name as well:
- D9: every coverage definition names both fallbacks, statements and then
  invoked-or-not, and says Python's and/or give coverage.py no branch arc.
- D10: est_uncovered_paths rounds half to even, and no page a reader learns
  the field from (README, AGENTS.md, docs/, the plugin skills) says a bare
  round() instead.
- D11: doctor.parallel_seconds is an LPT estimate with Graham's bound, not an
  exact makespan.

Nothing here imports crapkit: the MCP schemas come from a live `crapkit mcp`
session, and sarif.py, doctor.py and errors.py are read as syntax trees.
"""
import ast
from fractions import Fraction
from itertools import product
from pathlib import Path
import re
import sys

import pytest

from accuracy.definitions import doc_places
from accuracy.kit import drive, rulings, tiers

FIELDS = ("cov", "crap", "flag", "remedy", "nesting", "nloc", "params", "target")
ROWS = {row["field"]: row for row in doc_places.rows()}
AGENT_JSON = "docs/agent-json.md"


def test_the_table_holds_one_row_per_field():
    assert tuple(ROWS) == FIELDS
    assert [field for field, row in ROWS.items() if not row["definition"].strip()] == []


def _anchor_problems(source: str) -> list[str]:
    anchors = [part.strip().split("#", 1)[1] for part in source.split(";")
               if part.strip().startswith("README.md#")]
    known = doc_places.sections(doc_places.read("README.md"))
    return [anchor for anchor in anchors if anchor not in known]


def _kind(part: str) -> str:
    if part.startswith("README.md#"):
        return "readme"
    return "url" if part.startswith("https://") else "other"


def _source_kinds(source: str) -> set[str]:
    return {_kind(part.strip()) for part in source.split(";") if part.strip()}


@pytest.mark.parametrize("field", FIELDS)
def test_each_source_is_a_readme_anchor_or_an_outside_url(field):
    source = ROWS[field]["source"]

    assert _source_kinds(source) <= {"readme", "url"} and _source_kinds(source)
    assert _anchor_problems(source) == []


# --- README and CONTEXT.md ---------------------------------------------------------------

@pytest.mark.parametrize("field", FIELDS)
def test_readme_states_each_definition(field):
    row = ROWS[field]
    wanted = doc_places.phrases(row["readme"])
    if row["readme_section"] == "-":
        assert (wanted, doc_places.readme_defines(field)) == ([], [])
        return
    text = doc_places.readme_text(row["readme_section"])

    assert doc_places.missing(text, wanted) == []


@pytest.mark.parametrize("field", FIELDS)
def test_context_states_each_definition(field):
    row = ROWS[field]
    glossary = doc_places.terms(doc_places.read("CONTEXT.md"))
    if row["context_term"] == "-":
        assert (doc_places.phrases(row["context"]), glossary.get(field, "")) == ([], "")
        return

    assert doc_places.missing(glossary[row["context_term"].lower()],
                              doc_places.phrases(row["context"])) == []


# --- docs/agent-json.md -----------------------------------------------------------------

@pytest.mark.parametrize("field", FIELDS)
def test_agent_json_states_each_definition_in_every_row(field):
    table = doc_places.table_rows(doc_places.read(AGENT_JSON), field)
    wanted = doc_places.phrases(ROWS[field]["agent_json"])

    assert table, f"{AGENT_JSON} has no table row for `{field}`"
    assert {row: doc_places.missing(row, wanted) for row in table
            if doc_places.missing(row, wanted)} == {}


# --- MCP output schemas ------------------------------------------------------------------

@pytest.fixture(scope="module")
def mcp_tools(tmp_path_factory):
    """The served tools/list, from a server started where no crapkit.toml is."""
    tiers.require_process("crapkit mcp")
    return doc_places.mcp_tools(sys.executable, tmp_path_factory.mktemp("no-config"),
                                drive.child_env())


@pytest.mark.process
def test_the_mcp_session_lists_twelve_tools_with_output_schemas(mcp_tools):
    assert len(mcp_tools) == 12
    assert [tool["name"] for tool in mcp_tools if "outputSchema" not in tool] == []


@pytest.mark.process
@pytest.mark.parametrize("field", FIELDS)
def test_every_mcp_property_states_each_definition(mcp_tools, field):
    found = doc_places.schema_descriptions(mcp_tools, field)
    wanted = doc_places.phrases(ROWS[field]["mcp"])

    assert found, f"no MCP output schema has a `{field}` property"
    assert [(tool, text, doc_places.missing(text, wanted)) for tool, text in found
            if doc_places.missing(text, wanted)] == []


# --- SARIF --------------------------------------------------------------------------------

@pytest.mark.parametrize("field", FIELDS)
def test_sarif_rules_and_messages_state_each_definition(field):
    texts = doc_places.sarif_texts()
    wanted = doc_places.phrases(ROWS[field]["sarif"])
    if not wanted:
        assert doc_places.names_word(texts, field) == []
        return

    assert doc_places.missing("\n".join(texts), wanted) == []


# --- each definition names the model test that exercises it -------------------------------

def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_bytes())
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _model_problem(node_id: str) -> str | None:
    file, _, name = node_id.partition("::")
    path = doc_places.REPO / file
    if not path.is_file():
        return f"{node_id}: no file {file}"
    return None if name.split("[")[0] in _test_names(path) else f"{node_id}: no test {name}"


def test_each_definition_names_a_model_test_that_exists():
    problems = [_model_problem(row["model_test"]) for row in ROWS.values()]

    assert [problem for problem in problems if problem] == []


def test_a_model_link_to_a_missing_file_or_test_is_named():
    here = "tests/accuracy/definitions/test_definitions.py"

    assert _model_problem("tests/accuracy/definitions/nope.py::t") == (
        "tests/accuracy/definitions/nope.py::t: no file tests/accuracy/definitions/nope.py")
    assert _model_problem(f"{here}::test_nope[x]") == f"{here}::test_nope[x]: no test test_nope[x]"


# --- the readers, on inputs small enough to check by eye --------------------------------------

def test_sections_follow_github_anchors_and_skip_fenced_hashes():
    text = "# Top\nintro\n```\n# not a heading\n```\n## Flags: why `x`?\nbody\n## Top\nagain\n"
    found = doc_places.sections(text)

    assert list(found) == ["top", "flags-why-x", "top-1"]
    assert "# not a heading" in found["top"]


def test_terms_rows_and_phrases_read_what_a_reader_sees():
    glossary = "**Coverage**:\nline one\n_Avoid_: other words\n\n**Next**:\nb\n"

    assert doc_places.terms(glossary) == {"coverage": "line one", "next": "b"}
    assert doc_places.table_rows("| `a`, `cov` | x |\n| `covx` | y |\n", "cov") == [
        "| `a`, `cov` | x |"]
    assert doc_places.missing("The `CRAP`  **score**", ["crap score", "absent"]) == ["absent"]
    assert doc_places.phrases(" - ") == [] and doc_places.phrases("a | b") == ["a", "b"]


# --- D9: both fallbacks and the and/or rule, wherever coverage is defined ---------------------

def test_d9_every_coverage_definition_names_both_fallbacks_and_the_and_or_rule():
    """CONTEXT.md, agent-json.md's `cov` row: the phrases the cov row asks of
    them include statements, invoked-or-not and "no branch arc". The MCP
    descriptions get the same check per property above."""
    row = ROWS["cov"]
    for cell in ("context", "agent_json", "mcp"):
        said = " ".join(doc_places.phrases(row[cell]))
        assert all(word in said for word in ("statement", "invoked-or-not", "no branch arc"))


# --- D10: est_uncovered_paths rounds half to even ------------------------------------------

HALF_EVEN = ("half to even", "2.5 reads 2")


def test_d10_agent_json_says_est_uncovered_paths_rounds_half_to_even():
    """The `item` row defines it; brief's row points there. No row may say a
    bare round(), which a reader takes for half up."""
    table = doc_places.table_rows(doc_places.read(AGENT_JSON), "est_uncovered_paths")

    assert [row for row in table if not doc_places.missing(row, list(HALF_EVEN))]
    assert [row for row in table if "round(" in row] == []


@pytest.mark.process
def test_d10_every_mcp_est_uncovered_paths_says_half_to_even(mcp_tools):
    found = doc_places.schema_descriptions(mcp_tools, "est_uncovered_paths")

    assert found
    assert [text for _, text in found if doc_places.missing(text, ["half to even"])] == []


# Every page a user or an agent reads a field's meaning from, beyond the five places.
READER_PAGES = ("README.md", "AGENTS.md", "CONTEXT.md", "docs/*.md", "docs/*.html",
                "plugin/skills/*/SKILL.md")


def _says_bare_round(text: str) -> bool:
    return any("est_uncovered_paths" in line and "round(" in line for line in text.splitlines())


def bare_round_pages(repo: Path = doc_places.REPO) -> list[str]:
    """The reader pages that give est_uncovered_paths as a bare round()."""
    pages = sorted({page for pattern in READER_PAGES for page in repo.glob(pattern)})
    return [page.relative_to(repo).as_posix() for page in pages
            if _says_bare_round(page.read_bytes().decode("utf-8"))]


@rulings.applies("definitions-2")
def test_d10_no_reader_page_gives_est_uncovered_paths_as_a_bare_round():
    """D10 asks every place to say half to even. A bare round() reads as half up
    to anyone who does not know Python rounds ties to even."""
    found = bare_round_pages()

    rulings.pin_ruling("definitions-2", crapkit=", ".join(found) or "none", oracle="none")


def test_the_bare_round_reader_names_each_page_once(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "AGENTS.md").write_text("| `est_uncovered_paths` | `round(x)` |\n", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text("`est_uncovered_paths`, half to even\n"
                                            "round(x) elsewhere\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("est_uncovered_paths = round(a)\nround(b) "
                                        "est_uncovered_paths\n", encoding="utf-8")

    assert bare_round_pages(tmp_path) == ["AGENTS.md", "README.md"]


def test_d10_the_worked_example_is_half_to_even():
    """2.5 reads 2 and 3.5 reads 4 under half to even; half up would say 3 and 4."""
    halves = [Fraction(5, 2), Fraction(7, 2)]

    assert [_half_even(value) for value in halves] == [2, 4]


def _half_even(value: Fraction) -> int:
    floor = value.numerator // value.denominator
    rest = value - floor
    return floor + (rest > Fraction(1, 2) or (rest == Fraction(1, 2) and floor % 2 == 1))


# --- D11: parallel_seconds is LPT, with Graham's bound -------------------------------------

def _docstring(relative: str, function: str) -> str:
    tree = ast.parse(doc_places.read(relative))
    node = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == function)
    return ast.get_docstring(node) or ""


def _lpt(durations: tuple, slots: int) -> float:
    ends = [0.0] * slots
    for seconds in sorted(durations, reverse=True):
        ends[ends.index(min(ends))] += seconds
    return max(ends)


def _optimum(durations: tuple, slots: int) -> float:
    """The best makespan over every assignment of lanes to slots."""
    best = float("inf")
    for assignment in product(range(slots), repeat=len(durations)):
        loads = [0.0] * slots
        for slot, seconds in zip(assignment, durations):
            loads[slot] += seconds
        best = min(best, max(loads))
    return best


def test_d11_parallel_seconds_docstring_states_the_bound_not_exactness():
    text = " ".join(_docstring("src/crapkit/doctor.py", "parallel_seconds").split())

    assert "exact for" not in text
    assert doc_places.missing(text, ["4/3 - 1/(3", "(3, 3, 2, 2, 2) on 2 slots",
                                     "7.0", "6.0"]) == []


def test_d11_the_docstring_counterexample_holds():
    """Longest first puts 3, 3 on the two slots, then 2, 2, then the last 2 on
    one of them: 7. Splitting {3, 3} from {2, 2, 2} gives 6."""
    lanes = (3, 3, 2, 2, 2)

    assert (_lpt(lanes, 2), _optimum(lanes, 2)) == (7.0, 6.0)
    assert Fraction(7, 6) == Fraction(4, 3) - Fraction(1, 3 * 2)


# --- the `internal` error kind -------------------------------------------------------------

def _cells(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def _errors_table() -> list[list[str]]:
    """The (exit, kind, raised when) rows of agent-json.md's Errors table."""
    section = doc_places.sections(doc_places.read(AGENT_JSON))["errors"]
    rows = [_cells(line) for line in section.splitlines() if line.startswith("| ")]
    return [row for row in rows if row[0].isdigit()]


def test_agent_json_lists_the_internal_kind_with_exit_5_and_no_retry():
    [row] = [row for row in _errors_table() if row[1] == "internal"]

    assert row[0] == "5"
    assert doc_places.missing(row[2], ["crapkit bug", "do not retry", "report"]) == []


def _own(node: ast.ClassDef) -> dict:
    found = {}
    for statement in node.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Constant):
            found.update({target.id: statement.value.value for target in statement.targets})
    return found


def _resolved(name: str, classes: dict) -> dict:
    node = classes[name]
    inherited = {}
    for base in (getattr(base, "id", "") for base in node.bases):
        inherited.update(_resolved(base, classes) if base in classes else {})
    return {**inherited, **_own(node)}


def _classes() -> dict[str, ast.ClassDef]:
    tree = ast.parse(doc_places.read("src/crapkit/errors.py"))
    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _error_kinds() -> set[tuple[str, str]]:
    """(exit code, kind) of every errors.py class that has both, its own or inherited."""
    classes = _classes()
    resolved = [_resolved(name, classes) for name in classes]
    return {(str(fields["exit_code"]), fields["kind"]) for fields in resolved
            if {"kind", "exit_code"} <= fields.keys()}


def test_every_error_kind_errors_py_raises_has_its_row():
    documented = {(row[0], row[1]) for row in _errors_table()}

    assert _error_kinds() - documented == set()


def test_the_errors_table_reads_as_exit_and_kind_pairs():
    assert {row[1] for row in _errors_table()} >= {"state", "config", "git", "tool"}
    assert all(re.fullmatch(r"[a-z]+", row[1]) for row in _errors_table())
