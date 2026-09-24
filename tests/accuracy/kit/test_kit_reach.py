"""kit.reach: the functions a calc names run on the golden CLI run."""
import pytest

from accuracy.kit import corpus_run, reach


def test_body_lines_skip_the_def_line_and_the_docstring(tmp_path):
    source = tmp_path / "m.py"
    source.write_text('def f():\n    """Doc."""\n    return 1\n\n\nclass C:\n    def g(self):\n'
                      '        def inner():\n            return 2\n        return inner()\n',
                      encoding="utf-8")

    assert reach.body_lines(source, "f") == range(3, 4)
    assert reach.body_lines(source, "C.g") == range(8, 11)
    assert reach.body_lines(source, "C.g.inner") == range(9, 10)
    assert reach.body_lines(source, "missing") is None


def test_a_function_with_a_ran_body_line_is_reached(tmp_path):
    source = tmp_path / "src" / "m.py"
    source.parent.mkdir()
    source.write_text("def f():\n    return 1\n\n\ndef g():\n    return 2\n", encoding="utf-8")
    measured = {source.resolve(): {1, 2, 5}}

    assert reach.unreached(["src/m.py:f", "src/m.py:g", "src/m.py:h"], measured, tmp_path) == [
        "src/m.py:g: no line of its body ran", "src/m.py:h: no such function"]


@pytest.mark.nightly
@pytest.mark.process
def test_the_seed_run_reaches_the_crap_formula_and_not_mutation(tmp_path):
    measured = reach.measured_lines(corpus_run.SEED, tmp_path)

    assert reach.unreached(["src/crapkit/score.py:crap"], measured) == []
    assert reach.unreached(["src/crapkit/mutate.py:mutation_language"], measured) == [
        "src/crapkit/mutate.py:mutation_language: no line of its body ran"]
