"""Two readers' last words: the refusal when registration does not take, and the
cognitive increments the Sonar paper gives a few words.

G. Ann Campbell, Cognitive Complexity v1.7 (SonarSource, 2023): each sequence of
like binary logical operators +1 (B1), `goto LABEL` +1 (B1), a break or continue
to a label +1 and one without a label nothing (B1), and every flow break nested
in another +1 per level (B3).
"""
import lizard
import pytest

from crapkit import lizardpython
from crapkit.analyze import analyze_source


def cognitive(path: str, code: str) -> list[int]:
    return [row.cognitive for row in analyze_source(path, code)]


@pytest.mark.parametrize("resolved, named", [(None, "no reader"), (lizard.CLikeReader, "CLikeReader")])
def test_a_python_registration_that_did_not_take_names_what_lizard_resolved(
        monkeypatch, resolved, named):
    monkeypatch.setattr(lizard, "get_reader_for", lambda name: resolved)

    with pytest.raises(RuntimeError) as refused:
        lizardpython.register()

    assert str(refused.value) == (
        f"crapkit.lizardpython.register() did not take: lizard resolves '.py' to {named}, not "
        f"PythonSignatureReader. lizard {lizard.version} picks readers some other way than "
        "lizard_languages.languages(); rewrite register() against the new mechanism, or drop "
        "this module if lizard reads these signatures itself.")


@pytest.mark.parametrize("path, code, score", [
    # `and` then `or`: two sequences, +1 each
    ("a.py", "def f(a, b):\n    return a and b or a\n", 2),
    ("a.py", "def f(a, b):\n    return a or b\n", 1),
    # an `if` inside a `for`: +1, then +2 at nesting 1
    ("a.py", "def f(xs):\n    for x in xs:\n        if x:\n            return x\n", 3),
    # the expression form of `if` inside a `for`: +1, then +2
    ("a.py", "def f(xs):\n    for x in xs:\n        y = 1 if x else 2\n    return y\n", 3),
    # `goto out` +1 beside the `if`'s +1
    ("a.c", "void f(int a) {\n  if (a) goto out;\nout:\n  return;\n}\n", 2),
    # a `break` right before `}` names no label: the `for` +1 and the `if` +2
    ("a.js", "function f(a) {\n  for (;;) {\n    if (a) break\n  }\n}\n", 3),
])
def test_a_word_s_cognitive_increment(path, code, score):
    assert cognitive(path, code) == [score]


@pytest.mark.parametrize("path, code, score", [
    # C++'s alternative tokens: `and` is `&&` and `or` is `||`, so each line is one sequence
    ("a.cpp", "bool f(bool a, bool b, bool c) {\n  return a and b && c;\n}\n", 1),
    ("a.cpp", "bool f(bool a, bool b, bool c) {\n  return a || b or c;\n}\n", 1),
    # a function calling itself twice is one recursion: the `if` +1, the recursion +1
    ("a.c", "int f(int n) {\n  if (n) return f(n - 1) + f(n - 2);\n  return 0;\n}\n", 2),
])
def test_a_spelling_that_names_one_operator_or_one_recursion_counts_once(path, code, score):
    assert cognitive(path, code) == [score]
