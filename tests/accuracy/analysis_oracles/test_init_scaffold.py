"""init: the crapkit.toml and .gitignore lines `crapkit init` proposes for a tree.

Expected values come from the docs, never from a crapkit run:
- README "Languages": each language key's suffixes, and which languages have a
  coverage parser (python, typescript, tsx, javascript, vue).
- README:335: init writes `coverage_optional = true` on every scope whose
  languages all lack a parser and leaves it off any scope a lane could measure.
- README "Quickstart: Python" (README:995): one scope per top-level source
  directory; a pytest marker file (pyproject.toml, pytest.ini, setup.cfg)
  writes a live lane, and so does a `test` script or vitest or jest in
  package.json; every lane reports into .crapkit/cov/; the [exclude] globs
  listed there; test directories leave the corpus; the .gitignore lines.
- README "Quickstart: TypeScript" (README:1168): a vitest repo's .gitignore
  gains `.crapkit/` alone, and doctor's one WARN on the fresh config.
- docs/lanes.md "The interpreter a lane binds to": the lockfile table, where
  the first match in table order wins; with no lockfile the lane runs the
  launcher token `{python}`, or `{python:.venv}` for a venv in the tree.
- docs/lanes.md "Containers": doctor WARNs on a coverage.py lane without
  container_ok when /.dockerenv exists or CRAPKIT_INSIDE_CONTAINER=1.
- docs/configuration.md `scoped_tests` row: `{files}` only where the scope's
  own paths hold a test file; the whole-suite form otherwise, naming the test
  directory unless pytest's testpaths already collects it.
- README command table: init refuses to clobber an existing config.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig
import tomllib
import venv

from hypothesis import given, strategies as st
import pytest

from accuracy.analysis_oracles import analysis_inventory
from accuracy.kit import drive, rulings
from accuracy.kit.settings import process

pytestmark = pytest.mark.process

# README "Languages", one suffix per row plus the extra JavaScript spellings.
LANGUAGE_OF = {".py": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript",
               ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".vue": "vue",
               ".swift": "swift", ".go": "go", ".rs": "rust", ".sh": "shell", ".bash": "shell",
               ".ps1": "powershell", ".psm1": "powershell", ".c": "cpp", ".cpp": "cpp",
               ".java": "java", ".zig": "zig"}
PARSED = {"python", "typescript", "tsx", "javascript", "vue"}
# README:1017-1034, the [exclude] globs of the starter config.
README_EXCLUDES = ["**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**",
                   "**/generated/**", "**/__generated__/**", "**/*.generated.*", "**/*.test.*",
                   "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py",
                   "**/*_test.go", "**/*.config.ts", "**/*.config.js", "**/*.config.mts"]
PYTEST_GITIGNORE = [".crapkit/", ".coverage", "__pycache__/"]
# README:1186, the WARN a vitest scope carries until its scoped_tests line is uncommented.
VITEST_WARN = "scope 'src' has a lane but no [crapkit.scoped_tests] template"
# docs/lanes.md#containers: either trigger is enough, and doctor WARNs on a
# coverage.py lane that does not set container_ok.
IN_CONTAINER = Path("/.dockerenv").exists() or os.environ.get("CRAPKIT_INSIDE_CONTAINER") == "1"
CONTAINER_WARN = "lane 'py' runs a coverage.py suite and this is a container"
SOURCE = {"calc/grade.py": "def grade(score):\n    return score\n"}
PYPROJECT = {"pyproject.toml": "[project]\nname = \"calc\"\nversion = \"0\"\n"}


def _init(files: dict, tmp_path, env: dict | None = None):
    root = analysis_inventory.build(files, tmp_path / "repo")
    done = drive.Driver(root, env=env).run("init")
    return root, done


def _config(root) -> dict:
    return tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8"))


def _scopes(config: dict) -> dict:
    return {scope["name"]: (scope["paths"], sorted(scope["languages"]),
                            scope.get("coverage_optional", False))
            for scope in config.get("scope", [])}


def _gitignore(root) -> list[str]:
    lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line and not line.startswith("#")]


# --- hand fixtures per marker --------------------------------------------------------------------

@pytest.mark.parametrize("marker, text", [("pyproject.toml", PYPROJECT["pyproject.toml"]),
                                          ("pytest.ini", "[pytest]\n"),
                                          ("setup.cfg", "[tool:pytest]\n")])
def test_a_pytest_marker_writes_a_live_py_lane(marker, text, tmp_path):
    root, done = _init({marker: text, **SOURCE}, tmp_path)
    assert done.code == 0, done.stderr
    lanes = _config(root)["lane"]
    assert [(lane["name"], lane["parser"], lane["scopes"]) for lane in lanes] == [
        ("py", "coveragepy", ["calc"])]
    assert lanes[0]["command"].startswith("{python} -m pytest ")
    assert _gitignore(root) == PYTEST_GITIGNORE


def test_no_runner_marker_writes_no_live_lane(tmp_path):
    root, done = _init(SOURCE, tmp_path)
    assert done.code == 0, done.stderr
    assert "lane" not in _config(root)
    assert _gitignore(root) == [".crapkit/"]


# docs/lanes.md: the lockfile table, first match in table order wins.
@pytest.mark.parametrize("lockfiles, launcher", [
    (["uv.lock"], "uv run python -m pytest "),
    (["poetry.lock"], "poetry run python -m pytest "),
    (["pdm.lock"], "pdm run python -m pytest "),
    (["Pipfile.lock"], "pipenv run python -m pytest "),
    (["Pipfile.lock", "pdm.lock", "poetry.lock", "uv.lock"], "uv run python -m pytest "),
    (["Pipfile.lock", "pdm.lock", "poetry.lock"], "poetry run python -m pytest "),
    (["Pipfile.lock", "pdm.lock"], "pdm run python -m pytest "),
])
def test_the_lockfile_picks_the_launcher(lockfiles, launcher, tmp_path):
    root, done = _init({**PYPROJECT, **SOURCE, **{name: "" for name in lockfiles}}, tmp_path)
    assert done.code == 0, done.stderr
    config = _config(root)
    assert config["lane"][0]["command"].startswith(launcher)
    assert config["crapkit"]["scoped_tests"]["calc"].startswith(launcher)


@pytest.mark.parametrize("package", [
    {"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^3.0.0"}},
    {"devDependencies": {"vitest": "^3.0.0"}},
    {"devDependencies": {"jest": "^30.0.0"}},
])
def test_package_json_writes_a_live_js_lane(package, tmp_path):
    files = {"package.json": json.dumps(package), "src/a.ts": "export const a = 1;\n"}
    root, done = _init(files, tmp_path)
    assert done.code == 0, done.stderr
    lanes = _config(root)["lane"]
    assert [(lane["parser"], lane["scopes"]) for lane in lanes] == [("istanbul", ["src"])]
    assert _gitignore(root) == [".crapkit/"]


@pytest.mark.parametrize("files", [
    {**PYPROJECT, **SOURCE},
    {"package.json": json.dumps({"devDependencies": {"jest": "^30.0.0",
                                                     "jest-junit": "^16.0.0"}}),
     "src/a.ts": "export const a = 1;\n"},
])
def test_every_lane_reports_into_crapkit_cov(files, tmp_path):
    root, _ = _init(files, tmp_path)
    for lane in _config(root)["lane"]:
        assert lane["artifact"].startswith(".crapkit/cov/")
        assert lane["results_artifact"].startswith(".crapkit/cov/")


@rulings.applies("AO-INIT-JEST-NO-JUNIT")
def test_a_jest_lane_without_jest_junit_declares_no_results_artifact(tmp_path):
    """agent-json.md:1017 says init writes results_artifact on every lane it
    detects; docs/lanes.md:495 says a jest lane without jest-junit drops it."""
    files = {"package.json": json.dumps({"devDependencies": {"jest": "^30.0.0"}}),
             "src/a.ts": "export const a = 1;\n"}
    root, _ = _init(files, tmp_path)
    lane = _config(root)["lane"][0]
    rulings.pin_ruling("AO-INIT-JEST-NO-JUNIT",
                       crapkit="present" if "results_artifact" in lane else "absent",
                       oracle="present")


def test_scopes_follow_top_level_dirs_and_parsers(tmp_path):
    files = {"cmd/main.go": "package main\n", "lib/a.rs": "fn a() {}\n",
             "web/a.ts": "export const a = 1;\n", "mix/a.py": "a = 1\n", "mix/b.go": "package b\n",
             "mix/deep/c.sh": "c() { :; }\n"}
    root, done = _init(files, tmp_path)
    assert done.code == 0, done.stderr
    assert _scopes(_config(root)) == {
        "cmd": (["cmd"], ["go"], True), "lib": (["lib"], ["rust"], True),
        "mix": (["mix"], ["go", "python", "shell"], False), "web": (["web"], ["typescript"], False)}


def test_test_directories_and_excluded_files_leave_the_corpus(tmp_path):
    files = {**SOURCE, "calc/grade.test.ts": "export const t = 1;\n",
             "tests/test_grade.py": "def test_grade():\n    pass\n",
             "dist/bundle.js": "var a = 1;\n", "vendor/lib.go": "package lib\n"}
    root, _ = _init(files, tmp_path)
    config = _config(root)
    assert _scopes(config) == {"calc": (["calc"], ["python"], False)}
    assert config["exclude"]["globs"] == README_EXCLUDES


# docs/configuration.md scoped_tests: the form follows where the test files live.
@pytest.mark.parametrize("extra, form", [
    ({"tests/test_grade.py": "def test_grade():\n    pass\n"}, "names tests"),
    ({"calc/test_grade.py": "def test_grade():\n    pass\n"}, "files"),
    ({"tests/test_grade.py": "def test_grade():\n    pass\n",
      "pyproject.toml": PYPROJECT["pyproject.toml"]
      + "\n[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"}, "whole suite"),
])
def test_scoped_tests_form_follows_the_test_files(extra, form, tmp_path):
    root, _ = _init({**PYPROJECT, **SOURCE, **extra}, tmp_path)
    words = _config(root)["crapkit"]["scoped_tests"]["calc"].split()
    has = ("{files}" in words, "tests" in words)
    assert has == {"files": (True, False), "names tests": (False, True),
                   "whole suite": (False, False)}[form]


# gitformat-index(5), index entry: "'/' is used as path separator", so every
# path git lists uses / and a backslash is part of a name, which Linux and macOS
# allow.
@pytest.mark.platform("linux", "darwin")
def test_a_backslash_in_a_listed_name_is_part_of_the_filename(tmp_path):
    """R135: a root-level file named `tests\\test_grade.py` is one file in no
    directory, so no tests/ directory exists and the scoped_tests line names
    none (docs/configuration.md: the whole-suite form names the test directory
    only when there is one), and no scope but calc is proposed."""
    test = {"tests\\test_grade.py": "def test_grade():\n    pass\n"}
    root, done = _init({**PYPROJECT, **SOURCE, **test}, tmp_path)
    assert done.code == 0, done.stderr
    config = _config(root)

    assert _scopes(config) == {"calc": (["calc"], ["python"], False)}
    assert "tests" not in config["crapkit"]["scoped_tests"]["calc"].split()


# docs/configuration.md "[[scope]]": `paths = ["."]` claims the repo root; docs/lanes.md
# "nearest wins": init refuses, exit 3, under a directory an ancestor's scope
# path already claims, since a nested configuration would shadow it.
ROOT_SCOPE = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "whole"\npaths = ["."]\n'
              'languages = ["python"]\n')


def test_init_refuses_a_scope_an_ancestor_claims(tmp_path):
    """R147: the root scope `.` claims child/, so init there exits 3 and writes
    no crapkit.toml of its own."""
    files = {"crapkit.toml": ROOT_SCOPE, **{f"child/{path}": text for path, text in SOURCE.items()}}
    top = analysis_inventory.build(files, tmp_path / "repo")
    done = drive.Driver(top / "child").run("init")

    assert (done.code, (top / "child" / "crapkit.toml").exists()) == (3, False), done.stderr


def _commented_command(root) -> str:
    """The command line of the commented coveragepy [[lane]] template."""
    lines = (root / "crapkit.toml").read_text(encoding="utf-8").splitlines()
    return next(line for line in lines if line.startswith("# command = ") and "pytest" in line)


def test_the_commented_template_keeps_the_lockfile_prefix(tmp_path):
    """R165: docs/lanes.md "The interpreter a lane binds to": every python line
    init writes carries the lockfile's prefix, the commented [[lane]] template
    of a repo with no pytest marker file too, so on a uv.lock repo it reads
    `uv run python -m pytest`."""
    root, done = _init({**SOURCE, "uv.lock": ""}, tmp_path)
    assert done.code == 0, done.stderr

    assert _commented_command(root).startswith('# command = "uv run python -m pytest ')


WORKSPACES = {
    "package.json": json.dumps({"private": True, "workspaces": ["web"],
                                "scripts": {"test": "npm run test --workspaces"}}),
    "web/package.json": json.dumps({"name": "web", "scripts": {"test": "vitest run"},
                                    "devDependencies": {"vitest": "^3.0.0"}}),
    "web/src/a.ts": "export const a = 1;\n",
}


def test_init_writes_a_monorepo_js_lane_that_runs(tmp_path):
    """R162: CHANGELOG "`init` puts the js lane in the workspace that owns the
    runner": when the root package.json names no runner and exactly one
    workspace does, the lane's `cwd` is that directory, every path in its command
    climbs back to the root (`--coverage.reportsDirectory=../.crapkit/cov/js`),
    and `artifact` stays root-relative."""
    root, done = _init(WORKSPACES, tmp_path)
    assert done.code == 0, done.stderr
    (lane,) = _config(root)["lane"]

    assert (lane.get("cwd"), lane["artifact"]) == ("web", ".crapkit/cov/js/coverage-final.json")
    assert "--coverage.reportsDirectory=../.crapkit/cov/js" in lane["command"].split()


def _venv_with_pytest(root) -> None:
    """A real venv at root/.venv whose interpreter imports pytest: a .pth file in
    its site-packages names the site-packages this suite runs from."""
    venv_dir = root / ".venv"
    venv.create(venv_dir, with_pip=False)
    site = Path(sysconfig.get_paths(vars={"base": str(venv_dir), "platbase": str(venv_dir)})
                ["purelib"])
    site.mkdir(parents=True, exist_ok=True)
    (site / "outer.pth").write_text(Path(pytest.__file__).parents[1].as_posix() + "\n",
                                    encoding="utf-8")


# docs/lanes.md "The interpreter a lane binds to", the lockfile table's row "none,
# and a venv in the tree": `{python:.venv} -m pytest …`, which runs .venv/bin/python
# (.venv\Scripts\python.exe on Windows). Before 0.8.1 init wrote that path itself,
# the spelling R161's fix commit prints; either one binds the lane to the venv.
VENV_LAUNCHERS = ("{python:.venv}",
                  ".venv\\Scripts\\python.exe" if os.name == "nt" else ".venv/bin/python")


def test_init_binds_the_lane_to_the_repo_venv(tmp_path):
    """R161: with no lockfile, a .venv holding pyvenv.cfg whose interpreter imports
    pytest is the environment the lane runs, not the bare name the PATH answers."""
    root = analysis_inventory.build({**PYPROJECT, **SOURCE}, tmp_path / "repo")
    _venv_with_pytest(root)
    done = drive.Driver(root).run("init")
    assert done.code == 0, done.stderr

    command = _config(root)["lane"][0]["command"]
    assert command.startswith(tuple(f"{launcher} -m pytest " for launcher in VENV_LAUNCHERS))


def _shim_path(tmp_path, shims: dict[str, str]) -> dict:
    """A PATH holding only `shims` ({name.bat: body}), git and System32: no python a
    machine may carry elsewhere can answer."""
    folder = tmp_path / "shims"
    folder.mkdir()
    for name, body in shims.items():
        (folder / name).write_text(body, encoding="ascii")
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return {"PATH": os.pathsep.join([str(folder), str(Path(shutil.which("git")).parent),
                                     str(system)])}


@pytest.mark.platform("win32")
def test_init_falls_back_to_the_py_launcher(tmp_path):
    """R179: docs/lanes.md's lockfile table, row "none at all": `python -m pytest …`
    (or python3, or py on Windows: the first that resolves). On a PATH where only
    the py launcher resolves, the lane runs py, not a python3 nothing can start."""
    env = _shim_path(tmp_path, {"py.bat": "@exit /b 1\n"})
    root = analysis_inventory.build({**PYPROJECT, **SOURCE}, tmp_path / "repo")
    done = drive.Driver(root, env=env).run("init")
    assert done.code == 0, done.stderr

    assert _config(root)["lane"][0]["command"].startswith("py -m pytest ")


@pytest.mark.platform("win32")
def test_a_9009_is_not_a_missing_pytest_cov(tmp_path):
    """R180: README: when cmd.exe cannot start the interpreter at all (exit 9009,
    the Store alias), init names that instead of guessing at pytest-cov, so no note
    asks for pytest_cov in an interpreter that never ran."""
    env = _shim_path(tmp_path, {"python.bat": "@exit /b 9009\n"})
    root = analysis_inventory.build({**PYPROJECT, **SOURCE}, tmp_path / "repo")
    done = drive.Driver(root, env=env).run("init")
    assert done.code == 0, done.stderr

    assert "cannot import pytest_cov" not in done.stdout + done.stderr


# docs/lanes.md "The interpreter a lane binds to": every python line init writes
# carries the same prefix, so the scoped-tests entry takes a lane's python exactly
# when init reads that lane as the one that runs pytest. The lane commands below
# end at `-m pytest` or carry flags after it; init's own lanes always do the latter.
PYTEST_COMMANDS = ("uv run python -m pytest", "uv run python -m pytest --cov",
                   "poetry run python -m pytest", "python -m pytest -q")


@pytest.mark.parametrize("command", PYTEST_COMMANDS)
def test_one_rule_names_the_pytest_lane(command):
    """R166: the launcher init writes into the scoped-tests entry and the lane it
    confirms as the pytest lane come from one reading of the lane command: the
    lane's own python (the words before `-m pytest`) when init confirms it, the
    bare default when it does not. crapkit.scaffold is loaded at run time, so
    the crapkit under test answers."""
    scaffold = importlib.import_module("crapkit.scaffold")
    lanes = (scaffold.LaneSpec("py", command, ".crapkit/cov/py.json", "coveragepy",
                               ("python",)),)
    confirmed = "python" in scaffold._confirmed_languages(lanes)
    lane_python = command.partition(" -m pytest")[0]

    assert scaffold.python_launcher(lanes) == (lane_python if confirmed else "python")


GO = "package main\n\nfunc main() {\n}\n"


def test_a_cc_only_repo_gets_coverage_optional(tmp_path):
    """R181: README "Languages": init writes `coverage_optional = true` on every
    scope whose languages all lack a parser, so the 60-second start runs on a Go
    repo: `crapkit coverage` scores it with no lane at all and writes the run."""
    root, done = _init({"cmd/main.go": GO}, tmp_path)
    assert done.code == 0, done.stderr

    assert _scopes(_config(root)) == {"cmd": (["cmd"], ["go"], True)}
    assert drive.Driver(root).run("coverage").code == 0


def test_init_refuses_to_clobber_a_config(tmp_path):
    existing = "# mine\n[crapkit]\ntarget = 9\n"
    root, done = _init({**PYPROJECT, **SOURCE, "crapkit.toml": existing}, tmp_path)
    assert done.code != 0
    assert (root / "crapkit.toml").read_text(encoding="utf-8") == existing


# --- cross-surface: init, then doctor ------------------------------------------------------------

# The file names a `crapkit` launcher can have in a PATH directory.
LAUNCHER_NAMES = ["crapkit", *("crapkit" + ext
                               for ext in os.environ.get("PATHEXT", "").split(os.pathsep) if ext)]


def _holds_crapkit(entry: str) -> bool:
    return any((Path(entry.strip('"')) / name).is_file() for name in LAUNCHER_NAMES)


def _one_crapkit() -> dict:
    """The PATH of a machine with one crapkit: the driving interpreter's directory,
    whose launcher (if any) is the crapkit under test, then every host PATH entry
    that holds no `crapkit` launcher. Doctor WARNs when PATH holds launchers that
    answer different versions (a pipx or uv tool install, another venv), which
    describes the host, not the fresh config."""
    python = Path(os.environ.get(drive.PYTHON_ENV) or sys.executable)
    others = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
              if entry and not _holds_crapkit(entry)]
    return {"PATH": os.pathsep.join([str(python.parent), *others])}


@pytest.mark.parametrize("files, warnings", [
    ({**PYPROJECT, **SOURCE, "tests/test_grade.py": "def test_grade():\n    pass\n"},
     [CONTAINER_WARN] if IN_CONTAINER else []),
    ({"cmd/main.go": "package main\n"}, []),
    ({"package.json": json.dumps({"scripts": {"test": "vitest run"},
                                  "devDependencies": {"vitest": "^3.0.0"}}),
      "src/a.ts": "export const a = 1;\n"}, [VITEST_WARN]),
])
def test_doctor_finds_nothing_wrong_with_a_fresh_config(files, warnings, tmp_path):
    env = _one_crapkit()
    root, _ = _init(files, tmp_path, env)
    report = drive.Driver(root, env=env).json("doctor")
    assert report["problems"] == []
    assert [line for line in report["warnings"]
            if not any(line.startswith(prefix) for prefix in warnings)] == []
    assert len(report["warnings"]) == len(warnings)


# --- model: README init rules over drawn trees ---------------------------------------------------

DIRS = ["alpha", "beta", "gamma", "delta"]
TREES = st.dictionaries(st.tuples(st.sampled_from(DIRS), st.sampled_from(sorted(LANGUAGE_OF))),
                        st.integers(min_value=0, max_value=2), min_size=1, max_size=6)


def _tree_files(tree: dict) -> dict:
    return {f"{top}/f{index}{suffix}": f"x{index}\n" for (top, suffix), index in tree.items()}


def _model(tree: dict) -> dict:
    """README: one scope per top-level directory, its languages from the suffix
    table, coverage_optional exactly when none of them has a parser."""
    languages: dict[str, set] = {}
    for top, suffix in tree:
        languages.setdefault(top, set()).add(LANGUAGE_OF[suffix])
    return {top: ([top], sorted(found), not found & PARSED) for top, found in languages.items()}


@process
@given(tree=TREES)
def test_scopes_match_the_readme_model(tree, tmp_path_factory):
    root, done = _init(_tree_files(tree), tmp_path_factory.mktemp("init"))
    assert done.code == 0, done.stderr
    assert _scopes(_config(root)) == _model(tree)


# --- metamorphic: file order and unrelated files -------------------------------------------------

def test_file_order_and_unrelated_files_leave_the_proposal_unchanged(tmp_path):
    files = {**PYPROJECT, **SOURCE, "web/a.ts": "export const a = 1;\n",
             "cmd/main.go": "package main\n", "tests/test_grade.py": "def test_g():\n    pass\n"}
    unrelated = {"README.md": "# calc\n", "docs/notes.txt": "notes\n",
                 "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00"}
    first, _ = _init(files, tmp_path / "one")
    second, _ = _init({**unrelated, **dict(reversed(list(files.items())))}, tmp_path / "two")
    read = lambda root: (root / "crapkit.toml").read_bytes()  # noqa: E731
    assert read(first) == read(second)
