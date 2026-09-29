"""Python functions as crapkit lists them, against Python's own ast.

The standard library's ast is the Python reader's outside oracle
(oracles/py_ast_oracle.py): which defs a file holds, the line each starts and
ends on, and its name. Three corpora are read: crapkit's own source tree
(every function, 2,145 of 2,145, push), the named shapes of analysis_shapes
(push), and CPython's Lib at the tag the full corpus pins for the running
Python (member cpython-<major>.<minor>, nightly, one run per Python). A file
ast rejects is left out and counted in the run log, never compared.

crapkit's name for a Python def is documented, not PEP 3155's __qualname__:
classes are left out and each enclosing def is named once
(docs/upgrading.md#analysis-version-11). That transform is two rulings rows,
each pinned below by a hand case with both values.

The unread-def net (a file cut inside a def's signature is refused, never
scored) is checked under crapkit's reader and under lizard's stock reader,
which is what runs once crapkit's corrected reader retires.
"""
import re
import sys

import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_inventory, analysis_shapes
from accuracy.analysis_oracles.oracles import py_ast_oracle
from accuracy.kit import rulings, runlog

pytestmark = pytest.mark.process
# The first Python whose grammar holds each version-gated shape, from the
# PEP's Python-Version header: 695 type parameters 3.12, 750 t-strings and
# 758 unparenthesized except 3.14.
SHAPE_NEEDS = {"pep695": (3, 12), "pep750_tstring": (3, 14), "pep758_except": (3, 14)}


def _shape_param(name: str):
    """One shape's parameter: a shape newer than the running Python carries the
    python marker, so that Python deselects it and its node id still exists for
    the tables that name it (retro.tsv's R38 row names pep695)."""
    marks = [pytest.mark.python(*SHAPE_NEEDS[name])] if name in SHAPE_NEEDS else []
    return pytest.param(f"shapes/{name}.py", id=f"shapes/{name}.py", marks=marks)


SHAPES = [_shape_param(name) for name in sorted(analysis_shapes.PY_SHAPES)]


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


# ast's def count over src/crapkit: 2,145 at the program's base commit
# (901c6986), 2,198 once the runtime guards (invariants.py and its call sites)
# landed, 2,387 once the fixes to the Rust, Go, Zig and line-end readers, the
# churn window, the coverage joins and the score arithmetic landed, 2,388 once
# the cognitive pass read its `?` rule through lizardcognitive._counts_question,
# 2,554 once the C-family and Java readers (lizardclike, lizardjava) landed.
# A src change that adds or drops a def moves it on purpose: recount with
# py_ast_oracle.functions and change it in the same commit.
SRC_DEFS = 2554


def test_spans_match_ast(src_corpus, src_inventory, py_shape_inventory):
    """Every def ast finds in crapkit's source and in the shapes is one row
    spanning the def line to its body's last line, and no other row exists:
    2,554 of 2,554 on crapkit's source."""
    shapes = analysis_shapes.py_shape_files()
    misses = _differences(src_corpus.files, src_inventory, _spans)
    ast_defs = sum(map(len, map(_ast_rows, src_corpus.files.values())))
    runlog.note("skipped_files", oracle="ast", count=len(src_corpus.rejected), defs=ast_defs,
                misses=len(misses))

    assert misses == {}
    assert _differences(shapes, py_shape_inventory, _spans) == {}
    assert (src_corpus.rejected, ast_defs, len(src_inventory.rows)) == ((), SRC_DEFS, SRC_DEFS)


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


def shapes_too_new(version: tuple) -> list[str]:
    return sorted(name for name, needs in SHAPE_NEEDS.items() if version < needs)


def test_shapes_the_running_python_rejects_are_counted():
    """A shape newer than the running Python is left out, never compared; on
    a Python that has its syntax it is compared like any other. Exactly the
    shapes the PEPs date after the running Python are rejected."""
    rejected = analysis_shapes.rejected_shapes()
    runlog.note("skipped_files", oracle="ast-shapes", count=len(rejected))

    assert rejected == shapes_too_new(sys.version_info[:2])


def test_the_shape_dates_follow_the_peps():
    assert shapes_too_new((3, 11)) == ["pep695", "pep750_tstring", "pep758_except"]
    assert shapes_too_new((3, 13)) == ["pep750_tstring", "pep758_except"]
    assert shapes_too_new((3, 14)) == []


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
    """The net shapes the running Python parses: ast finds each cut point, so a
    shape newer than the interpreter (pep695 on 3.11) is left to the others."""
    shapes = analysis_shapes.py_shape_files()
    paths = (f"shapes/{name}.py" for name in NET_SHAPES)
    return {path: shapes[path] for path in paths if path in shapes}


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


def test_the_src_corpus_reads_the_tree_the_source_variable_names(tmp_path, monkeypatch):
    """The calc mutation stage names its checkout's src/crapkit, since mutmut's
    copy is rewritten with trampolines; unset, the corpus is this checkout's."""
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "a.py").write_bytes(b"def f():\n    return 1\n")
    monkeypatch.setenv(analysis_corpora.SOURCE_ENV, str(tmp_path))

    assert sorted(analysis_corpora.crapkit_sources().files) == ["crapkit/cli/a.py"]
    monkeypatch.delenv(analysis_corpora.SOURCE_ENV)
    assert analysis_corpora.source_root() == analysis_corpora.REPO / "src" / "crapkit"


# --- CPython Lib at one pinned tag per Python -------------------------------------------------

def test_stdlib_sources_read_the_running_pythons_member(tmp_path):
    """The member named for the running Python is read, under its own prefix;
    another Python's member is not, and a file ast rejects is counted."""
    member = analysis_corpora.cpython_member(analysis_corpora.running_minor())
    files = {f"{member}/Lib/pkg/a.py": "def f():\n    return 1\n",
             f"{member}/Lib/bad.py": "def f(:\n", f"{member}/Lib/test/t.py": "x = 1\n",
             "cpython-3.0/Lib/other.py": "y = 1\n"}
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(text.encode("utf-8"))

    corpus = analysis_corpora.stdlib_sources(tmp_path)

    assert sorted(corpus.files) == [f"{member}/Lib/pkg/a.py"]
    assert corpus.rejected == (f"{member}/Lib/bad.py",)


def test_a_missing_cpython_member_names_the_folder_looked_for(tmp_path):
    looked = tmp_path / "cpython-3.99" / "Lib"

    with pytest.raises(analysis_corpora.CorpusMissing, match=re.escape(str(looked))):
        analysis_corpora.stdlib_sources(tmp_path, "3.99")


@pytest.mark.nightly
def test_stdlib_spans_and_names_match_ast(stdlib_corpus, stdlib_inventory):
    """Every def ast finds in the pinned Lib is one row with ast's span and
    name. The run log carries the member, the files and defs read, the
    misses and the files ast rejected, once per Python."""
    misses = _differences(stdlib_corpus.files, stdlib_inventory, _names)
    member = analysis_corpora.cpython_member(analysis_corpora.running_minor())
    runlog.note("skipped_files", oracle=f"ast-stdlib {member}", count=len(stdlib_corpus.rejected),
                files=len(stdlib_corpus.files),
                defs=sum(map(len, map(_ast_rows, stdlib_corpus.files.values()))),
                misses=len(misses))

    assert misses == {}
    assert len(stdlib_corpus.files) > 90
