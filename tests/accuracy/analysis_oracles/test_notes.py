"""Unanalyzable files and twin-name notes: what the analysis names on stderr.

Expected values come from the docs, the language grammar and McCabe, never
from a crapkit run:
- CONTEXT.md "Unanalyzable file": a file the analysis names on stderr and
  scores as zero functions, because lizard failed on it or a Python def in it
  was read no further than its signature; every run tries it again.
- src/crapkit/lizardtypescript.py's module text (the refusal's only statement):
  an unparenthesized arrow body with an unmatched `<` before a comma is
  refused, and parentheses around the body give the reader its delimiter.
- docs/ratchet.md "Twin keys": one line per file that defines a name more than
  once, five files at most in path order, then one line counting the rest.
- A file ast.parse rejects is the only kind of Python file the net may refuse.
"""
from __future__ import annotations

import ast
import re

import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive, rulings

pytestmark = pytest.mark.process

GOOD = {"ok/a.py": "def a(x):\n    if x:\n        return 1\n    return 2\n",
        "ok/b.py": "def b():\n    return 1\n",
        "ok/c.ts": "export function c(x: number): number {\n  return x > 0 ? 1 : 2;\n}\n"}
# A signature the file ends inside: ast rejects it, and the reader never reaches a body.
TRUNCATED = "def cut(a,\n        b"
REFUSAL_COUNT = re.compile(r"crapkit: (\d+) file\(s\) could not be tokenized")


def _refused_count(stderr: str) -> int:
    found = REFUSAL_COUNT.search(stderr)
    return int(found.group(1)) if found else 0


def _measure(files: dict, tmp_path):
    measured = analysis_inventory.measure(files, tmp_path)
    assert measured.code == 0, measured.stderr
    return measured


# --- same-span twins (R01) ----------------------------------------------------------------------

# Two function expressions on one line: one span, one name, two functions.
# McCabe: the first has no decision (1), the second one `if` (2).
SAME_SPAN = ("export const both = [function () { return 1; }, "
             "function (x: number) { if (x) { return 2; } return 3; }];\n")


def test_same_span_twins_keep_both_rows(tmp_path):
    measured = _measure({"twins.ts": SAME_SPAN}, tmp_path)
    rows = measured.in_file("twins.ts")
    assert [(row["start"], row["end"], row["ccn"]) for row in rows] == [(1, 1, 1), (1, 1, 2)]
    assert [row["occurrence"] for row in rows] == [1, 2]


# --- unanalyzable files ---------------------------------------------------------------------------

def test_a_truncated_signature_refuses_its_file_and_nothing_else(tmp_path):
    measured = _measure({**GOOD, "bad/cut.py": TRUNCATED}, tmp_path)
    assert measured.in_file("bad/cut.py") == []
    assert sorted({row["path"] for row in measured.rows}) == sorted(GOOD)
    assert _refused_count(measured.stderr) == 1 and "bad/cut.py" in measured.stderr


def test_each_truncated_file_moves_the_count_by_one(tmp_path):
    counts = []
    for number in range(3):
        cut = {f"bad/cut{index}.py": TRUNCATED.replace("cut", f"cut{index}")
               for index in range(number)}
        counts.append(_refused_count(_measure({**GOOD, **cut}, tmp_path / str(number)).stderr))
    assert counts == [0, 1, 2]


@pytest.mark.parametrize("body, refused", [("a < 1", True), ("(a < 1)", False)])
def test_an_ambiguous_arrow_body_refuses_only_without_parentheses(body, refused, tmp_path):
    text = f"export const pair = [(a: number) => {body}, (b: number) => b];\n"
    measured = _measure({**GOOD, "arrow.ts": text}, tmp_path)
    assert (_refused_count(measured.stderr), measured.in_file("arrow.ts") == []) == (
        int(refused), refused)


@rulings.applies("AO-REFUSED-SAME-BYTES")
def test_identical_refused_files_are_each_named(tmp_path):
    """Two files are refused, so the hand count is 2 whatever their bytes."""
    measured = _measure({**GOOD, "bad/cut0.py": TRUNCATED, "bad/cut1.py": TRUNCATED}, tmp_path)
    rulings.pin_ruling("AO-REFUSED-SAME-BYTES", crapkit=_refused_count(measured.stderr), oracle=2)


def test_every_run_names_the_refused_file_again(tmp_path):
    files = {**GOOD, "bad/cut.py": TRUNCATED}
    measured = _measure(files, tmp_path)
    again = drive.Driver(tmp_path / "repo").run("inventory", "--export",
                                                str(tmp_path / "again.tsv"))
    assert again.code == 0, again.stderr
    assert (_refused_count(measured.stderr), _refused_count(again.stderr)) == (1, 1)


def test_the_net_refuses_only_python_files_ast_rejects(tmp_path):
    files = {**GOOD, "bad/cut.py": TRUNCATED, "bad/half.py": "def half(x):\n    if x:\n",
             "bad/fine.py": "def fine(\n    a,\n):\n    return a\n"}
    measured = _measure(files, tmp_path)
    refused = {path for path in files if path.endswith(".py") and not measured.in_file(path)}
    for path in refused:
        with pytest.raises(SyntaxError):
            ast.parse(files[path])
    assert "bad/fine.py" not in refused


def _store_mentions(driver, path: str) -> int:
    tables = [row["name"] for row in driver.store(
        "SELECT name FROM sqlite_master WHERE type = 'table'")]
    total = 0
    for table in tables:
        columns = [row["name"] for row in driver.store(f"PRAGMA table_info('{table}')")]
        if "path" in columns:
            total += driver.store(f"SELECT COUNT(*) AS n FROM '{table}' WHERE path = ?",
                                  (path,))[0]["n"]
    return total


def test_a_refused_file_has_no_row_in_inventory_store_or_brief(tmp_path):
    measured = _measure({**GOOD, "bad/cut.py": TRUNCATED}, tmp_path)
    driver = drive.Driver(tmp_path / "repo")
    assert measured.in_file("bad/cut.py") == []
    assert (_store_mentions(driver, "bad/cut.py"), _store_mentions(driver, "ok/b.py") > 0) == (
        0, True)
    assert driver.run("brief", "bad/cut.py", "cut").code != 0


# --- twin-name notes ------------------------------------------------------------------------------

TWIN = ("class A:\n    def __post_init__(self):\n        return 1\n\n\n"
        "class B:\n    def __post_init__(self):\n        return 2\n")
TWIN_LINE = ("crapkit: {path} defines __post_init__( self ) more than once; each one takes its "
             "own ratchet key")


def _twin_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "more than once" in line]


def test_duplicating_a_def_adds_the_note(tmp_path):
    single = TWIN.split("\n\n\nclass B")[0] + "\n"
    before = _measure({**GOOD, "twin.py": single}, tmp_path / "single").stderr
    after = _measure({**GOOD, "twin.py": TWIN}, tmp_path / "twin").stderr
    assert _twin_lines(before) == []
    assert [line.startswith(TWIN_LINE.format(path="twin.py")) for line in _twin_lines(after)] == [
        True]


def test_twin_notes_name_five_files_then_count_the_rest(tmp_path):
    twins = {f"pkg/t{index}.py": TWIN for index in range(7)}
    lines = _twin_lines(_measure({**GOOD, **twins}, tmp_path).stderr)
    named = [f"pkg/t{index}.py" for index in range(5)]
    assert [line.startswith(TWIN_LINE.format(path=path)) for line, path in zip(lines, named)] == [
        True] * 5
    assert lines[5:] == ["crapkit: ... and 2 more file(s) define a name more than once"]


def test_twins_keep_both_rows_in_source_order(tmp_path):
    rows = _measure({"twin.py": TWIN}, tmp_path).in_file("twin.py")
    assert [(row["start"], row["ccn"]) for row in rows] == [(2, 1), (7, 1)]
