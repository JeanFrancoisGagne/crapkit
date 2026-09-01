"""`doctor`'s per-scope line: the count of files a scope's paths matched.

That line is the first proof a reader gets that a scope path reaches anything,
and the quickstart publishes it. It read `scope 'calc': 1 files` on the one-file
scope the quickstart builds, so the first crapkit output a new user sees was
ungrammatical.
"""
from types import SimpleNamespace

from crapkit.cli.admin import _doctor_scope_files


def _cfg(*names: str):
    return SimpleNamespace(scopes=[SimpleNamespace(name=n) for n in names])


def lines(files_by_scope: dict, *names: str) -> list[tuple[str, str]]:
    findings = _doctor_scope_files(files_by_scope, _cfg(*names), False)
    return [(f.level, f.text) for f in findings]


def test_a_scope_holding_one_file_counts_it_in_the_singular():
    assert lines({"calc": ["calc/grade.py"]}, "calc") == [("ok", "scope 'calc': 1 file")]


def test_a_scope_holding_more_than_one_stays_plural():
    assert lines({"calc": ["calc/grade.py", "calc/sum.py"]}, "calc") == \
        [("ok", "scope 'calc': 2 files")]


def test_a_scope_matching_nothing_fails_and_stays_plural():
    """Zero takes the plural in English, and the FAIL is the point of the line."""
    assert lines({}, "ghost") == [("FAIL", "scope 'ghost': 0 files")]


def test_show_files_still_trails_each_scope_with_its_paths():
    """Both halves pinned as literals: a test that compared the function with
    itself would stay green if the scope line went back to `1 files`."""
    findings = _doctor_scope_files({"calc": ["calc/grade.py"]}, _cfg("calc"), True)

    assert [(f.level, f.text) for f in findings] == [
        ("ok", "scope 'calc': 1 file"), ("", "       calc/grade.py")]
