"""Calls chosen so the corpus holds fully, partly and never covered functions."""
from src.py import (bom, cr_only, crlf, excepts, fstrings, generics, grades, nested, oneline,
                    pragmas, signatures, tstrings, twins)


def test_grades():
    assert grades.letter(95) == "A"
    assert grades.letter(85) == "B"
    assert grades.curve([50, None], "flat", 10, 100, False) == [55, 15]
    assert grades.weighted([1, -1, 2], [1, 1, 9], 5) == 11
    assert grades.spread([1, 4]) == 3


def test_signatures():
    assert signatures.split_pair("a,b") == ("a", "b")
    assert signatures.make("x", bases=(1, 2), skip=frozenset({2})) == ("x", [1])
    assert signatures.joined(["a", "b"], "-") == "a-b"
    assert signatures.Launcher(["a", "b"], shell=True, text=True).mode == "text"


def test_generics():
    assert generics.first([None, 3], 0) == 3
    stack = generics.Stack()
    stack.push(1)
    assert stack.pop_or(0) == 1
    assert stack.pop_or(0) == 0


def test_tstrings_and_excepts():
    assert tstrings.render(tstrings.greeting("ann", True)) == "Dear ANN,"
    assert excepts.parse_number("x") is None
    assert excepts.first_float(["a", "2.5"]) == 2.5


def test_nested_and_oneline():
    assert nested.outer([[None, 5], None], 3) == [[0, 3]]
    assert oneline.twice(2) == 4
    assert oneline.signed(-4) == -1
    assert oneline.called_on_two_lines(1) == 2


def test_fstrings_and_pragmas():
    assert fstrings.cell({"a": 1}, "a", 3) == "  1"
    assert fstrings.label({"name": "n", "tags": ["x"]}) == "n [x]"
    assert fstrings.nested_quotes({"name": "z"}) == "z"
    assert pragmas.safe_ratio(1, 2) == 0.5
    assert pragmas.platform_name("win32") == "windows"


def test_twins():
    assert twins.Fast().run() == 1
    assert twins.Slow().run() == -1
    assert twins.helper([3, 1]) == [1, 3]


def test_line_endings():
    assert crlf.normalize(" A ", True) == "a"
    assert cr_only.count_words("a b 1") == 2
    assert bom.normalize("", False) is None


def test_odd_paths(load):
    assert load("src/py/données.py").café([1, 3], True) == 5
    assert load("src/py/with space.py").spaced(1, 2) == 3
    assert load("src/py/hash#tag.py").tagged(["a"], "b") is False


def test_matching():
    from src.py import matching
    assert matching.describe(None) == "nothing"
    assert matching.describe([1, 2]) == "list of 2 from 1"
