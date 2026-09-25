"""Python functions as crapkit lists them, against Python's own ast.

The standard library's ast is the Python reader's outside oracle
(oracles/py_ast_oracle.py): which defs a file holds, the line each starts and
ends on, and its name. Three corpora are read: crapkit's own source tree
(every function, push), the named shapes of analysis_shapes (push), and the
running interpreter's standard library (nightly, one Lib per Python cell). A
file ast rejects is left out and counted in the run log, never compared.

crapkit's name for a Python def is documented, not PEP 3155's __qualname__:
classes are left out and each enclosing def is named once
(docs/upgrading.md#analysis-version-11). That transform is two rulings rows,
each pinned below by a hand case with both values.

The unread-def net (a file cut inside a def's signature is refused, never
scored) is checked under crapkit's reader and under lizard's stock reader,
which is what runs once crapkit's corrected reader retires.
"""
import pytest

from accuracy.analysis_oracles import analysis_inventory, analysis_shapes
from accuracy.analysis_oracles.oracles import py_ast_oracle
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process
SHAPES = sorted(analysis_shapes.py_shape_files())


def _text(data) -> str:
    return data.decode("utf-8") if isinstance(data, bytes) else data


def _ast_rows(source) -> list[tuple]:
    return sorted((fn.start, fn.end, py_ast_oracle.enclosing_defs_name(fn))
                  for fn in py_ast_oracle.functions(_text(source)))


def crapkit_name(row: dict) -> str:
    """The name part of a Python long name: the text before its parameter list."""
    return row["long_name"].split("(")[0].strip()


def _crapkit_rows(measured, path: str) -> list[tuple]:
    return sorted((row["start"], row["end"], crapkit_name(row)) for row in measured.in_file(path))


def _spans(rows: list[tuple]) -> list[tuple]:
    return [row[:2] for row in rows]


def _differences(files: dict, measured, pick) -> dict:
    """{path: (crapkit's, ast's)} for every file where `pick` of the two differs."""
    pairs = {path: (pick(_crapkit_rows(measured, path)), pick(_ast_rows(data)))
             for path, data in files.items()}
    return {path: pair for path, pair in pairs.items() if pair[0] != pair[1]}


def _names(rows: list[tuple]) -> list[tuple]:
    return rows


def test_spans_match_ast(src_corpus, src_inventory, py_shape_inventory):
    """Every def ast finds in crapkit's source and in the shapes is one row
    spanning the def line to its body's last line, and no other row exists."""
    runlog.note("skipped_files", oracle="ast", count=len(src_corpus.rejected))
    shapes = analysis_shapes.py_shape_files()

    assert _differences(src_corpus.files, src_inventory, _spans) == {}
    assert _differences(shapes, py_shape_inventory, _spans) == {}
    assert len(src_inventory.rows) == sum(map(len, map(_ast_rows, src_corpus.files.values())))


@pytest.mark.parametrize("path", SHAPES)
def test_function_count_matches_ast(path, py_shape_inventory):
    source = analysis_shapes.py_shape_files()[path]

    assert len(py_shape_inventory.in_file(path)) == len(py_ast_oracle.functions(source))


@pytest.mark.parametrize("path", SHAPES)
def test_names_match_ast(path, py_shape_inventory):
    source = analysis_shapes.py_shape_files()[path]

    assert _crapkit_rows(py_shape_inventory, path) == _ast_rows(source)


def test_qualified_names_match_ast(src_corpus, src_inventory):
    assert _differences(src_corpus.files, src_inventory, _names) == {}


def test_shapes_the_running_python_rejects_are_counted():
    """A 3.14 shape on an older Python is left out, never compared; on 3.14 it
    is compared like any other."""
    rejected = analysis_shapes.rejected_shapes()
    runlog.note("skipped_files", oracle="ast-shapes", count=len(rejected))

    assert set(rejected) <= {"pep750_tstring", "pep758_except"}


NAME_CASES = {"AO-PY-NAME-CLASS": ("shapes/methods.py", "method"),
              "AO-PY-NAME-LOCALS": ("shapes/three_deep.py", "inner")}


@pytest.mark.parametrize("ruling_id", sorted(NAME_CASES))
def test_the_documented_name_transform_is_pinned(ruling_id, py_shape_inventory):
    """PEP 3155's qualname against the documented name, for one hand case each."""
    path, name = NAME_CASES[ruling_id]
    source = analysis_shapes.py_shape_files()[path]
    fn = next(fn for fn in py_ast_oracle.functions(source) if fn.name == name)
    row = next(row for row in py_shape_inventory.in_file(path) if row["start"] == fn.start)

    assert crapkit_name(row) == py_ast_oracle.enclosing_defs_name(fn)
    rulings.pin_ruling(ruling_id, crapkit=crapkit_name(row), oracle=fn.qualname)


# --- the unread-def net -------------------------------------------------------------------

# A whole def ahead of every cut: a file dropped silently keeps its row, a
# refused one has none.
WHOLE = "def whole(x):\n    return x\n\n\n"


def _cuts(name: str, source: str) -> dict[str, str]:
    """Two files per def, each ending inside its signature: just after the
    parameter list opens, and just before the colon that ends it."""
    source = py_ast_oracle.normalized(source)
    cut = {}
    for number, (start, colon_end) in enumerate(py_ast_oracle.signature_spans(source)):
        cut[f"cut/{name}-{number}-open.py"] = WHOLE + source[:source.index("(", start) + 1]
        cut[f"cut/{name}-{number}-colon.py"] = WHOLE + source[:colon_end - 1]
    return cut


def cut_files(shapes: dict) -> dict[str, str]:
    """Every cut of every shape, each one a file ast rejects."""
    stem = {path: path.removeprefix("shapes/").removesuffix(".py") for path in shapes}
    return {cut: text for path, source in shapes.items()
            for cut, text in _cuts(stem[path], source).items()}


READERS = {"crapkit": analysis_inventory.PLAIN, "stock": analysis_inventory.STOCK_PYTHON}
# The stock reader runs serially; eight shapes keep its repo small.
NET_SHAPES = ("issue72", "annotated_return", "pep695", "three_deep", "one_line_defs",
              "methods", "wrapped_signature", "continued_signature")


def _net_files() -> dict[str, str]:
    shapes = analysis_shapes.py_shape_files()
    return {f"shapes/{name}.py": shapes[f"shapes/{name}.py"] for name in NET_SHAPES}


@pytest.mark.parametrize("reader", sorted(READERS))
def test_unread_def_net_under_both_readers(reader, measure_set):
    """Each cut file is one ast rejects, and crapkit refuses it: no row, not even
    the whole def ahead of the cut. The whole files belong to
    test_spans_match_ast under crapkit's reader; the stock reader misreads some
    of them on purpose (tests/unit/test_lizardpython.py pins each misread), so
    only the cut files are compared under it."""
    whole = _net_files()
    cuts = cut_files(whole)
    measured = measure_set({**whole, **cuts}, READERS[reader])

    assert [path for path, text in cuts.items() if py_ast_oracle.parses(text)] == []
    assert [path for path in cuts if measured.in_file(path)] == []


# --- nightly: the running interpreter's standard library ------------------------------------

@pytest.mark.nightly
def test_stdlib_spans_and_names_match_ast(stdlib_corpus, stdlib_inventory):
    runlog.note("skipped_files", oracle="ast-stdlib", count=len(stdlib_corpus.rejected))

    assert _differences(stdlib_corpus.files, stdlib_inventory, _names) == {}
