"""A non-ASCII path argument under a C locale with UTF-8 mode off.

`crapkit brief pkg/café.py résumé_ü` on Linux under LC_ALL=C PYTHONUTF8=0
PYTHONCOERCECLOCALE=0 reaches Python as ASCII: each byte of `é` becomes a lone
surrogate, and the store's sqlite query refuses it with a UnicodeEncodeError
traceback. That row and the function-name row below are strict xfails: they
fail today, and the day crapkit reads such an argument back into the bytes the
shell passed they pass, which strict=True reports as a failure until the
marker goes.

The two neighbouring locales answer today and must keep answering: plain
LC_ALL=C, where Python coerces the locale to UTF-8, and LANG=C.UTF-8. Windows
hands argv over as UTF-16 and has no C locale to decode it, so these rows run
on Linux only; the Windows encodings are test_encoding_e2e.py's rows.
"""
import os

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Windows argv is UTF-16: no C locale decodes it")

# The locale is what these rows vary, and it is fixed when an interpreter starts.
_run = cli_runner(timeout=120, encoding="utf-8", errors="replace", spawn=True)

NAME = "résumé_ü"
FILE = "pkg/café.py"
SOURCE = f"def {NAME}(a):\n    if a > 1:\n        return a\n    return 0\n"

MAKE_COV = f'''import json

entry = {{"start_line": 1, "executed_lines": [1, 2, 4], "missing_lines": [3],
          "summary": {{"covered_lines": 3, "num_statements": 4,
                       "num_branches": 2, "covered_branches": 1}}}}
files = {{{FILE!r}: {{"functions": {{{NAME!r}: entry}}, "missing_lines": [3]}}}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump({{"meta": {{"branch_coverage": True}}, "files": files}}, fh)
'''

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "pkg"
paths = ["pkg"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["pkg"]
"""

LOCALES = {
    # Python decodes argv as ASCII here: the row that raises a traceback.
    "c-no-coercion-no-utf8": {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
    "c-coerced": {"LC_ALL": "C", "PYTHONUTF8": None, "PYTHONCOERCECLOCALE": None},
    "c-utf8": {"LANG": "C.UTF-8", "LC_ALL": None},
}
# What the ASCII-locale rows wait for. Drop both markers when they pass.
ARGV_AS_SURROGATES = pytest.mark.xfail(strict=True, reason=(
    "under an ASCII locale Python hands each non-ASCII byte of an argument over as a lone "
    "surrogate, and the store's sqlite query refuses it (UnicodeEncodeError): crapkit does "
    "not yet read argv back into the bytes the shell passed"))


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    repo = git_init_repo(tmp_path_factory.mktemp("c-locale"))
    for rel, text in ((FILE, SOURCE), ("make_cov.py", MAKE_COV), ("crapkit.toml", CONFIG)):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    git_commit_all(repo, "init")
    done = _run(repo, "coverage")
    assert done.returncode == 0, done.stderr
    return repo


@pytest.mark.parametrize("locale", [
    pytest.param("c-no-coercion-no-utf8", marks=ARGV_AS_SURROGATES),
    "c-coerced",
    "c-utf8",
])
def test_brief_on_a_non_ascii_path_answers_under_every_locale(measured, locale):
    """The answer names the file git tracks and the function it holds. Under
    the ASCII locale it is a UnicodeEncodeError traceback today."""
    result = _run(measured, "brief", FILE, NAME, env_extra=LOCALES[locale])

    assert "Traceback" not in result.stderr, result.stderr
    assert result.returncode in (0, 1), result.stderr
    assert f"{FILE}" in result.stdout + result.stderr
    assert NAME in result.stdout + result.stderr


@ARGV_AS_SURROGATES
def test_brief_reads_a_non_ascii_function_name_under_an_ascii_locale(measured):
    result = _run(measured, "brief", FILE, NAME, env_extra=LOCALES["c-no-coercion-no-utf8"])

    assert result.returncode == 0, result.stderr
    assert NAME in result.stdout, result.stdout
