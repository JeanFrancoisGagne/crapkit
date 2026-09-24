"""A CLI child finds the suite's own interpreter first on PATH, a lane's
coverage opt-out stays inside that lane, and a lane or mutation child writes
its output in UTF-8.

Fixture lanes spell a bare `python`, which the lane's shell resolves through
PATH. With another project's virtualenv first on PATH, every nested pytest in
test_inventory_e2e.py loaded twelve foreign plugins and ran seven version-control
probes: 303 s of CPU per run of that file against 147 s with the suite's
interpreter found first.
"""
import json
import os
from pathlib import Path
import sys

import pytest

from conftest import child_env, git_commit_all, git_init_repo, run_cli
from legacy_locale import latin1_env

SUITE_BIN = str(Path(sys.executable).parent)


def test_the_suite_interpreter_directory_comes_first_on_path():
    assert child_env()["PATH"].split(os.pathsep)[0] == SUITE_BIN


def test_a_test_that_sets_path_still_decides_it():
    assert child_env({"PATH": "only-this"})["PATH"] == "only-this"


def test_the_cli_child_keeps_the_parent_coverage_config(monkeypatch):
    """An empty value here would stop the CLI child's own measurement, and every
    cmd_* function would read 0% coverage."""
    monkeypatch.setenv("COVERAGE_PROCESS_CONFIG", ":data:the parent's config")

    assert child_env()["COVERAGE_PROCESS_CONFIG"] == ":data:the parent's config"


RECORDER = '''import json, os, sys
from pathlib import Path
name = sys.argv[1]
Path(name + ".json").write_text(json.dumps({
    "python": sys.executable,
    "coverage_config": os.environ.get("COVERAGE_PROCESS_CONFIG"),
    "coverage_started": "coverage" in sys.modules}), encoding="utf-8")
loc = {"start": {"line": 1, "column": 0}, "end": {"line": 3, "column": 1}}
Path("coverage").mkdir(exist_ok=True)
Path("coverage", name + ".json").write_text(json.dumps({"src/" + name + ".js": {
    "path": "src/" + name + ".js", "fnMap": {"0": {"name": "f", "decl": loc, "loc": loc}},
    "f": {"0": 1}, "statementMap": {"0": loc}, "s": {"0": 1}, "branchMap": {}, "b": {}}}),
    encoding="utf-8")
'''


def _lane(name, env):
    return (f'[[scope]]\nname = "{name}"\npaths = ["src/{name}.js"]\nlanguages = ["javascript"]\n'
            f'[[lane]]\nname = "{name}"\ncommand = "python record.py {name}"\n'
            f'artifact = "coverage/{name}.json"\nparser = "istanbul"\nscopes = ["{name}"]\n{env}')


@pytest.fixture
def recording_repo(tmp_path):
    (tmp_path / "repo" / "src").mkdir(parents=True)
    repo = git_init_repo(tmp_path / "repo")
    for name in ("opted", "plain"):
        (repo / "src" / f"{name}.js").write_text("function f() {\n  return 1;\n}\n", encoding="utf-8")
    (repo / "record.py").write_text(RECORDER, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncoverage/\n*.json\n", encoding="utf-8")
    (repo / "crapkit.toml").write_text(
        _lane("opted", 'env = { COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }\n')
        + _lane("plain", ""),
        encoding="utf-8")
    git_commit_all(repo, "two recording lanes")
    return repo


def _decoy_python(folder):
    """A `python` that fails, standing in for another project's interpreter."""
    folder.mkdir()
    if os.name == "nt":
        (folder / "python.bat").write_text("@echo decoy python 1>&2\r\n@exit /b 3\r\n")
    else:
        decoy = folder / "python"
        decoy.write_text("#!/bin/sh\necho decoy python >&2\nexit 3\n")
        decoy.chmod(0o755)
    return str(folder)


def test_a_bare_python_lane_runs_the_suite_interpreter_and_keeps_its_opt_out(
        recording_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join([_decoy_python(tmp_path / "decoy"), os.environ["PATH"]]))

    result = run_cli(recording_repo, "coverage", "--json", encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    seen = {name: json.loads((recording_repo / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("opted", "plain")}
    assert {Path(row["python"]).parent for row in seen.values()} == {Path(SUITE_BIN)}
    assert not seen["opted"]["coverage_config"]
    assert seen["opted"]["coverage_started"] is False
    # The CLI call runs in the pytest worker, so the plain lane inherits the
    # worker's config through child_env. A spawn=True file's CLI child would
    # re-serialize that config, which is why the plain lane is held to the same
    # presence, not the same text. Its coverage starts exactly when the suite
    # measures subprocesses.
    measured = bool(os.environ.get("COVERAGE_PROCESS_CONFIG"))
    assert bool(seen["plain"]["coverage_config"]) == measured
    assert seen["plain"]["coverage_started"] is measured


# A lane or mutation child writes UTF-8 whatever the shell that ran crapkit
# hands it. A Python child below 3.15 wrote the ANSI code page on Windows, the
# locale's encoding on POSIX, or an inherited PYTHONIOENCODING, and crapkit
# reads the lane log as UTF-8. Every row forces PYTHONUTF8=0 unless it sets
# PYTHONUTF8 itself, so Python 3.15's UTF-8 default (PEP 686) cannot make a row
# pass, and every lane is spelled with this interpreter.

PYTHON = f'"{sys.executable}"'
POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="a POSIX locale row: Windows reads no LANG or LC_ALL")
INHERITED = [
    pytest.param({}, id="no-env"),
    pytest.param({"PYTHONIOENCODING": "cp1252"}, id="inherited-PYTHONIOENCODING-cp1252"),
    pytest.param({"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}, id="LC_ALL-C", marks=POSIX_ONLY),
    pytest.param("latin1", id="LANG-en_US.ISO-8859-1"),
    pytest.param({"PYTHONUTF8": "1"}, id="control-PYTHONUTF8-1"),
    pytest.param({"PYTHONIOENCODING": "utf-8"}, id="control-PYTHONIOENCODING-utf-8"),
]

APP = "def f(x):\n    if x > 0:\n        return 1\n    return 2\n"
MISSING_MODULE = ("import sys\n"
                  "sys.stderr.write('ModuleNotFoundError: No module named ' + repr('caf\\u00e9') + '\\n')\n"
                  "sys.exit(2)\n")
EMOJI_TEST = ("import sys\nsys.path.insert(0, 'src')\nimport app\n\n\n"
              "def test_f():\n    print('done \\U0001f680')\n    assert app.f(1) == 1\n")
PYTEST_S = (f"{PYTHON} -m pytest -s -q -p no:cacheprovider -p no:randomly --cov=src --cov-branch"
            " --cov-report=json:.crapkit/cov/py.json --junitxml=.crapkit/cov/junit-py.xml")


def _config(lane_command: str, lane_env: str = "") -> str:
    """One python scope, one lane, and a mutation command that runs the same
    tests with -s. TOML literal strings, so the interpreter path's
    backslashes and quotes stay as they are."""
    return ("[crapkit]\ntarget = 6\n"
            f"mutation_command = '{PYTHON} -m pytest -s -q -p no:cacheprovider -p no:randomly tests'\n"
            "mutation_workers = 1\n\n"
            '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
            f"[[lane]]\nname = \"py\"\ncommand = '{lane_command}'\n"
            'artifact = ".crapkit/cov/py.json"\nresults_artifact = ".crapkit/cov/junit-py.xml"\n'
            'parser = "coveragepy"\nscopes = ["src"]\n'
            f'env = {{ COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = ""{lane_env} }}\n')


def _repo(root: Path, config: str) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git_init_repo(repo)
    for name, text in {"src/app.py": APP, "tests/test_app.py": EMOJI_TEST,
                       "missing_module.py": MISSING_MODULE, "crapkit.toml": config,
                       ".gitignore": ".crapkit/\n.coverage*\n"}.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    git_commit_all(repo, "one scope, one lane")
    return repo


@pytest.fixture
def inherited(request, tmp_path) -> dict:
    """The row's variables as the shell running crapkit would hand them over,
    over PYTHONUTF8=0 and no PYTHONIOENCODING."""
    row = request.param
    pairs = latin1_env(tmp_path / "locales") if row == "latin1" else row
    return {"PYTHONUTF8": "0", "PYTHONIOENCODING": None, **pairs}


@pytest.mark.parametrize("inherited", INHERITED, indirect=True)
def test_a_lane_refusal_quotes_what_the_child_printed(tmp_path, inherited):
    repo = _repo(tmp_path, _config(f"{PYTHON} missing_module.py"))

    result = run_cli(repo, "coverage", env_extra=inherited, encoding="utf-8")

    assert result.returncode == 5, result.stdout + result.stderr
    assert "No module named 'caf\u00e9'" in result.stderr, result.stderr


def test_a_lane_that_sets_pythonioencoding_keeps_its_own(tmp_path):
    """The lane's env is the user's word: its child writes cp1252, so the
    refusal quotes a byte UTF-8 cannot read."""
    repo = _repo(tmp_path, _config(f"{PYTHON} missing_module.py", ', PYTHONIOENCODING = "cp1252"'))

    result = run_cli(repo, "coverage", env_extra={"PYTHONUTF8": "0"}, encoding="utf-8")

    assert result.returncode == 5, result.stdout + result.stderr
    assert "No module named 'caf\ufffd'" in result.stderr, result.stderr


@pytest.mark.parametrize("inherited", INHERITED, indirect=True)
def test_a_pytest_s_lane_that_prints_an_emoji_scores_as_in_a_console(tmp_path, inherited):
    """The print raised under a cp1252 or Latin-1 pipe, the test failed before
    it called f, and coverage still exited 0 with the CRAP load at 6.0."""
    repo = _repo(tmp_path, _config(PYTEST_S))

    result = run_cli(repo, "coverage", env_extra=inherited, encoding="utf-8")

    log = (repo / ".crapkit" / "lane-py.log").read_bytes().decode("utf-8")
    assert result.returncode == 0 and "UnicodeEncodeError" not in log, log
    assert "CRAP load 2.5," in result.stdout, result.stdout


@pytest.mark.parametrize("inherited", INHERITED, indirect=True)
def test_mutate_runs_a_suite_that_prints_an_emoji_on_the_unmutated_tree(tmp_path, inherited):
    repo = _repo(tmp_path, _config(PYTEST_S))

    result = run_cli(repo, "mutate", "--files", "src/app.py", "--json", env_extra=inherited,
                     encoding="utf-8", timeout=300)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["mutants"] > 0
