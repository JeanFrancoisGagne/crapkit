"""nloc and the parameter count against Python's tokenize and ast.

nloc: oracles/tokenize_nloc.py counts the lines a code token covers, with the
documented transforms (a standalone triple-quoted string is not code, a
nested def's body counts on its own row). params: py_ast_oracle's count of
the parameters a def declares (the Python reference 8.7). Both run over
crapkit's own source on push and CPython's Lib at the running Python's pinned
tag nightly; the defs a known defect shape covers (py_line_shapes) are set aside
under its rulings id and counted.

Two relations hold whatever the counts are: nloc lies between 0 and the span's
line count on every row crapkit wrote, and a comment or blank line added to
a body leaves nloc alone where a statement adds one.
"""
import pytest

from accuracy.analysis_oracles import analysis_pydiff, py_line_shapes
from accuracy.analysis_oracles.oracles import py_ast_oracle, tokenize_nloc
from accuracy.analysis_oracles import analysis_tables, analysis_tstests
from accuracy.kit import runlog

pytestmark = pytest.mark.process


def _nloc_outcome(corpus, measured) -> analysis_pydiff.Outcome:
    outcome = analysis_pydiff.Outcome()
    for path, data in corpus.files.items():
        _nloc_file(outcome, path, data.decode("utf-8"), measured)
    runlog.note("skipped_files", oracle="tokenize nloc: defs set aside",
                count=sum(outcome.set_aside.values()), compared=outcome.compared,
                misses=len(outcome.differing))
    return outcome


def _nloc_file(outcome, path: str, source: str, measured) -> None:
    lines = py_line_shapes.Lines(source)
    rows = {row["start"]: row for row in measured.in_file(path)}
    for fn, expected in tokenize_nloc.per_def(source):
        reasons = lines.reasons(fn)
        if reasons:
            outcome.set_aside["+".join(reasons)] += 1
            continue
        _judge(outcome, (path, fn.lineno, fn.name), rows.get(fn.lineno), expected)


def _judge(outcome, where: tuple, row, expected: int) -> None:
    outcome.compared += 1
    if row is None or row["nloc"] != expected:
        outcome.differing.append((*where, None if row is None else row["nloc"], expected))


def test_python_nloc_matches_tokenize(src_corpus, src_inventory):
    outcome = _nloc_outcome(src_corpus, src_inventory)

    assert outcome.differing == []
    assert outcome.compared > 2000


@pytest.mark.nightly
def test_python_nloc_matches_tokenize_on_the_stdlib(stdlib_corpus, stdlib_inventory):
    assert _nloc_outcome(stdlib_corpus, stdlib_inventory).differing == []


# --- params ---------------------------------------------------------------------------------

def _params_outcome(corpus, measured) -> analysis_pydiff.Outcome:
    outcome = analysis_pydiff.Outcome()
    for path, data in corpus.files.items():
        _params_file(outcome, path, py_ast_oracle.normalized(data.decode("utf-8")), measured)
    runlog.note("skipped_files", oracle="ast params: defs set aside",
                count=sum(outcome.set_aside.values()), compared=outcome.compared,
                misses=len(outcome.differing))
    return outcome


def _params_file(outcome, path: str, source: str, measured) -> None:
    colons = py_ast_oracle.signature_colons(source)
    offsets = py_ast_oracle._line_offsets(source)
    rows = {row["start"]: row for row in measured.in_file(path)}
    for fn, _ in analysis_pydiff.defs(source):
        if py_line_shapes.params_shape(fn, source, colons, offsets):
            outcome.set_aside["AO-PY-PARAMS-DEFAULTS"] += 1
            continue
        _judge_params(outcome, (path, fn.lineno, fn.name), rows.get(fn.lineno), fn)


def _judge_params(outcome, where: tuple, row, fn) -> None:
    outcome.compared += 1
    expected = py_ast_oracle.param_count(fn)
    if row is None or row["params"] != expected:
        outcome.differing.append((*where, None if row is None else row["params"], expected))


def test_python_params_match_ast(src_corpus, src_inventory):
    outcome = _params_outcome(src_corpus, src_inventory)

    assert outcome.differing == []
    assert outcome.compared > 2000


@pytest.mark.nightly
def test_python_params_match_ast_on_the_stdlib(stdlib_corpus, stdlib_inventory):
    assert _params_outcome(stdlib_corpus, stdlib_inventory).differing == []


# --- relations ------------------------------------------------------------------------------

def _outside_span(rows) -> list:
    return [row for row in rows if not 0 <= row["nloc"] <= row["end"] - row["start"] + 1]


def test_nloc_lies_within_the_span_on_every_row(src_inventory, probe_inventory,
                                               py_shape_inventory):
    assert _outside_span(src_inventory.rows) == []
    assert _outside_span(probe_inventory.rows) == []
    assert _outside_span(py_shape_inventory.rows) == []


BODY = "def f(a):\n    b = a + 1\n{extra}    return b\n"
EDITS = {"comment": ("    # a comment\n", 0), "blank": ("\n", 0),
         "statement": ("    b += 1\n", 1), "two_statements": ("    b += 1\n    b -= 1\n", 2)}


@pytest.fixture(scope="module")
def edits(measure_set):
    files = {f"edits/{name}.py": BODY.format(extra=extra) for name, (extra, _) in EDITS.items()}
    return measure_set({"edits/base.py": BODY.format(extra=""), **files})


@pytest.mark.parametrize("name", sorted(EDITS))
def test_a_comment_or_blank_moves_no_nloc_and_a_statement_adds_one(name, edits):
    (base,) = edits.in_file("edits/base.py")
    (edited,) = edits.in_file(f"edits/{name}.py")

    assert edited["nloc"] - base["nloc"] == EDITS[name][1]


# --- brace languages and shell: the tree-sitter counters (nloc and params) -------------------------

@pytest.mark.parametrize("language", sorted(analysis_tstests.LANGUAGES))
def test_sizes_match_the_treesitter_counters_on_the_probes(language, probe_inventory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), language)

    outcome = analysis_tstests.check(files, probe_inventory, "sizes", language)
    assert outcome.compared > 0


@pytest.mark.nightly
@pytest.mark.parametrize("language", analysis_tstests.CORPUS_LANGUAGES)
def test_sizes_match_the_treesitter_counters_on_the_corpus(language, corpus_language):
    files, measured = corpus_language(language)

    outcome = analysis_tstests.check(files, measured, "sizes", language)
    assert outcome.compared > 0
