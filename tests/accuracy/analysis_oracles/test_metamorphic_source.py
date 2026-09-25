"""Edits whose effect on crapkit's numbers is known without knowing the numbers.

The variants of analysis_variants run over every probe file and Python shape
in one crapkit run: lines added above shift every span and move nothing else,
module code after the last def moves nothing, a comment as a body's first line
moves no count, and a function appended at the end is listed with its hand
values while no earlier row moves. Hand-written pairs cover what a generic
edit cannot: a body on the colon line against the same body on its own line,
and a signature wrapped over several lines against the same one on one line.
A Python file cut inside a def's signature is refused, and the refusal count
on stderr moves by exactly that file.

A difference that is a known defect is pinned by its rulings row: the TypeScript
end-of-function defect (analysis-oracles-11) and D1's backtick regex both show
up only once a function follows the one they misread.
"""
import re

import pytest

from accuracy.analysis_oracles import (analysis_inventory, analysis_shapes, analysis_tables,
                                       analysis_variants)

pytestmark = pytest.mark.process
V = analysis_variants


def _files() -> dict:
    return {**analysis_tables.probe_files(), **analysis_shapes.py_shape_files()}


@pytest.fixture(scope="module")
def variants(measure_set):
    return measure_set(V.variant_files(_files()))


def _rows(measured, path: str) -> list[dict]:
    return sorted(measured.in_file(path), key=lambda row: (row["start"], row["end"]))


def _counts(rows: list[dict]) -> list[tuple]:
    return [tuple(row[name] for name in V.COUNTS) for row in rows]


def _spans(rows: list[dict], shift: int = 0) -> list[tuple]:
    return [(row["start"] + shift, row["end"] + shift) for row in rows]


def _paths(variant: str) -> list[str]:
    return sorted(path for path in _files() if V.applies(variant, path))


SHIFTS = {"blank_above": V.BLANK_LINES, "comment_above": V.COMMENT_LINES}


def _pairs(variant: str, measured) -> dict:
    return {path: (_rows(measured, f"base/{path}"), _rows(measured, f"{variant}/{path}"))
            for path in _paths(variant)}


@pytest.mark.parametrize("variant", sorted(SHIFTS))
def test_lines_added_above_shift_every_span_and_nothing_else(variant, variants):
    moved = _pairs(variant, variants)
    shift = SHIFTS[variant]

    assert [path for path, (base, edited) in moved.items() if _counts(base) != _counts(edited)] == []
    assert [path for path, (base, edited) in moved.items()
            if _spans(base, shift) != _spans(edited)] == []


def test_module_code_after_a_def_leaves_it_unchanged(variants):
    changed = [path for path in _paths("module_after")
               if _strip(_rows(variants, f"base/{path}"))
               != _strip(_rows(variants, f"module_after/{path}"))]

    assert changed == []


def _strip(rows: list[dict]) -> list[dict]:
    """Rows with their variant directory taken off the path."""
    return [{**row, "path": row["path"].split("/", 1)[1]} for row in rows]


def test_a_comment_as_the_first_body_line_moves_no_count(variants):
    changed = [path for path in _paths("comment_in_body")
               if _counts(_rows(variants, f"base/{path}"))
               != _counts(_rows(variants, f"comment_in_body/{path}"))]

    assert changed == []


# --- an appended function --------------------------------------------------------------------

# A function with a return type annotation, last in its file, grows to the
# line of the function appended after it (analysis-oracles-11); the regex
# holding a backtick swallows the rest of its file (D1).
APPEND_RULINGS = {"ts/regex.ts": ("D1c", "D1c-APPEND"),
                  **{path: ("", "AO-TS-END-APPEND") for path in
                     ("ts/flow.ts", "ts/fntype.ts", "ts/generic.ts", "ts/sonar.ts",
                      "ts/templates.ts")}}


def _append_params(position: int) -> list:
    return [pytest.param(path, id=path, marks=analysis_tables.marks(
        APPEND_RULINGS.get(path, ("", ""))[position])) for path in _paths("append")]


def _appended(measured, path: str) -> list[dict]:
    name = V.appended(path)[0]
    return [row for row in _rows(measured, f"append/{path}")
            if analysis_inventory.bare(row["long_name"]) == name]


@pytest.mark.parametrize("path", _append_params(0))
def test_appended_function_is_listed(path, variants):
    found = _appended(variants, path)
    ruling = APPEND_RULINGS.get(path, ("", ""))[0]

    analysis_tables.check(str(len(found)), "1", ruling)
    expected = V.appended_values(path)
    assert {name: found[0][name] for name in expected} == expected


def _largest_move(base: list[dict], edited: list[dict]) -> int:
    """How far the furthest-moving earlier row's end moved, in lines."""
    return max((abs(after["end"] - before["end"]) for before, after in zip(base, edited)),
               default=0)


@pytest.mark.parametrize("path", _append_params(1))
def test_an_appended_function_moves_no_earlier_row(path, variants):
    new = {id(row) for row in _appended(variants, path)}
    base = _rows(variants, f"base/{path}")
    edited = [row for row in _rows(variants, f"append/{path}") if id(row) not in new][:len(base)]

    analysis_tables.check(str(_largest_move(base, edited)), "0",
                          APPEND_RULINGS.get(path, ("", ""))[1])
    assert _counts(edited) == _counts(base) and _spans(edited) == _spans(base)


# --- hand-written pairs -------------------------------------------------------------------------

LAYOUT = ("ccn_std", "ccn_mod", "ccn", "cognitive", "nesting", "params")
ONE_LINE = {
    "ternary": ("def f(a, b):\n    return a if b else 0\n", "def f(a, b): return a if b else 0\n"),
    "comprehension": ("def f(x):\n    return [a for a in x if a or x]\n",
                      "def f(x): return [a for a in x if a or x]\n"),
    "boolean_run": ("def f(a, b, c):\n    return a and b or c\n",
                    "def f(a, b, c): return a and b or c\n"),
    "call_with_lambda": ("def f(xs):\n    return sorted(xs, key=lambda v: v or 0)\n",
                         "def f(xs): return sorted(xs, key=lambda v: v or 0)\n"),
}
REWRAP = {
    "one_per_line": ("def f(a, b, c=1):\n    if a or b:\n        return c\n    return None\n",
                     "def f(a,\n      b,\n      c=1):\n    if a or b:\n        return c\n"
                     "    return None\n"),
    "hanging": ("def f(a, b, c=1):\n    if a or b:\n        return c\n    return None\n",
                "def f(\n    a,\n    b,\n    c=1,\n):\n    if a or b:\n        return c\n"
                "    return None\n"),
    "wrapped_return": ("def f(a: int) -> dict[str, int]:\n    if a:\n        return {}\n"
                       "    return {'a': a}\n",
                       "def f(a: int) -> dict[str,\n                   int]:\n    if a:\n"
                       "        return {}\n    return {'a': a}\n"),
    "backslash": ("def f(a) -> int:\n    if a:\n        return 1\n    return 0\n",
                  "def f(a) \\\n        -> int:\n    if a:\n        return 1\n    return 0\n"),
}


def _pair_files() -> dict[str, str]:
    pairs = {**{f"one_line_{k}": v for k, v in ONE_LINE.items()},
             **{f"rewrap_{k}": v for k, v in REWRAP.items()}}
    return {f"pairs/{name}/{side}.py": source for name, both in pairs.items()
            for side, source in zip(("a", "b"), both)}


@pytest.fixture(scope="module")
def pairs(measure_set):
    return measure_set(_pair_files())


def _layout(measured, path: str) -> dict:
    rows = measured.in_file(path)
    assert len(rows) == 1, f"{path} declares one def and reads {len(rows)} rows"
    return {name: rows[0][name] for name in LAYOUT}


@pytest.mark.parametrize("name", sorted(ONE_LINE))
def test_one_line_body_equals_two_line_body(name, pairs):
    """A body on the colon line counts as the same body on its own line: ccn,
    cognitive, nesting and parameters alike (the Python reference: a suite may
    be one simple statement on the header's line)."""
    stem = f"pairs/one_line_{name}"

    assert _layout(pairs, f"{stem}/b.py") == _layout(pairs, f"{stem}/a.py")


@pytest.mark.parametrize("name", sorted(REWRAP))
def test_signature_rewrap_keeps_ccn(name, pairs):
    """Where a signature's lines break changes no count (Python reference 2.1.6:
    brackets and a backslash join physical lines into one logical line)."""
    stem = f"pairs/rewrap_{name}"

    assert _layout(pairs, f"{stem}/b.py") == _layout(pairs, f"{stem}/a.py")


# --- a file cut inside a signature -------------------------------------------------------------

REFUSED = re.compile(r"crapkit: (\d+) file\(s\) could not be tokenized")
WHOLE = "def whole(x):\n    return x\n\n\ndef other(y):\n    if y:\n        return 1\n    return 0\n"


def test_truncated_signature_refuses_the_file(measure_set):
    """The same file whole and cut inside its last def's signature: the cut
    copy has no row at all (its whole defs included), the whole copy keeps
    both, and stderr's refusal count is exactly one."""
    cut = WHOLE + "\n\ndef cut(a, b=(1,\n"
    measured = measure_set({"whole.py": WHOLE, "cut.py": cut})

    assert measured.in_file("cut.py") == []
    assert len(measured.in_file("whole.py")) == 2
    assert REFUSED.findall(measured.stderr) == ["1"]
    assert f"cut.py:{cut[:cut.index('def cut')].count(chr(10)) + 1}" in measured.stderr
