"""A CLI child finds the suite's own interpreter first on PATH, and a lane's
coverage opt-out stays inside that lane.

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
    "coverage_config": os.environ.get("COVERAGE_PROCESS_CONFIG")}), encoding="utf-8")
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
        _lane("opted", 'env = { COVERAGE_PROCESS_CONFIG = "" }\n') + _lane("plain", ""),
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
    # Under the suite's coverage run the CLI child re-serializes its config, so
    # the plain lane is held to the same presence, not the same text.
    assert bool(seen["plain"]["coverage_config"]) == bool(os.environ.get("COVERAGE_PROCESS_CONFIG"))
