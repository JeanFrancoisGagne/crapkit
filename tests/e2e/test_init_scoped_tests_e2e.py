"""`crapkit init` writes a scoped-test command that collects a test, and
`doctor` repeats init's lane probe.

Four personas hit the 0.4.x template. On the ordinary pkg/ + tests/ layout
init wrote `python -m pytest {files} ...`, so `crapkit test-scoped pkg/x.py`
handed pytest a source file to collect from and exited 5 ("no tests ran"),
which every one of them read as the suite failing. A jest workspace got a
vitest command. And doctor said `no problems found` on a lane whose python
could not import pytest_cov, a fact init's own first-run note had already
printed once.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from conftest import run_cli

_MOD = "def g(x):\n    return x or 0\n"
_TEST = "from pkg.x import g\n\n\ndef test_g():\n    assert g(0) == 0\n"
_PYPROJECT = '[project]\nname = "app"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n'
_TESTPATHS = _PYPROJECT + 'testpaths = ["tests"]\n'
_APP_TS = "export function f(a: number) { return a ? 1 : 2; }\n"
_LAUNCHER = r"(python3?|py|[^\s]+python(\.exe)?)"


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, body in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit_all(repo, "init")
    return repo


def _layout_repo(tmp_path: Path, pyproject: str = _PYPROJECT) -> Path:
    """The ordinary layout: sources in pkg/, tests in a top-level tests/."""
    return _repo(tmp_path, {"pkg/x.py": _MOD, "tests/test_x.py": _TEST,
                            "pyproject.toml": pyproject})


def _config(repo: Path) -> str:
    return (repo / "crapkit.toml").read_text(encoding="utf-8")


def _entry(config: str, scope: str) -> str:
    """The scoped_tests entry for `scope`, live or commented."""
    (line,) = [ln for ln in config.splitlines()
               if re.match(rf"(# )?{scope} = ", ln)]
    return line


def _comment_above(config: str, scope: str) -> str:
    lines = config.splitlines()
    index = lines.index(_entry(config, scope))
    return lines[index - 1]


# --- the python form follows where the tests live -----------------------------

def test_init_writes_the_whole_suite_form_for_a_scope_holding_no_test_file(tmp_path: Path):
    repo = _layout_repo(tmp_path)

    assert run_cli(repo, "init").returncode == 0

    entry = _entry(_config(repo), "pkg")
    assert re.fullmatch(rf'pkg = "{_LAUNCHER} -m pytest tests -q -p no:cacheprovider"', entry), entry
    assert _comment_above(_config(repo), "pkg").startswith("# pkg:")
    assert "whole suite" in _comment_above(_config(repo), "pkg")


def test_the_whole_suite_form_init_wrote_collects_a_test(tmp_path: Path):
    """The failure every persona read as the suite failing: runner exit 5."""
    repo = _layout_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0

    res = run_cli(repo, "test-scoped", "pkg/x.py")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 passed" in res.stdout, res.stdout


def test_init_omits_the_positional_when_testpaths_already_covers_it(tmp_path: Path):
    """`pytest` alone collects what testpaths names, so `tests` on the line
    would only repeat the config."""
    repo = _layout_repo(tmp_path, _TESTPATHS)

    assert run_cli(repo, "init").returncode == 0

    entry = _entry(_config(repo), "pkg")
    assert re.fullmatch(rf'pkg = "{_LAUNCHER} -m pytest -q -p no:cacheprovider"', entry), entry
    assert run_cli(repo, "test-scoped", "pkg/x.py").returncode == 0


def test_init_keeps_files_for_a_scope_that_holds_its_own_tests(tmp_path: Path):
    repo = _repo(tmp_path, {"pkg/x.py": _MOD, "pkg/test_x.py": _TEST, "pyproject.toml": _PYPROJECT})

    assert run_cli(repo, "init").returncode == 0

    entry = _entry(_config(repo), "pkg")
    assert re.fullmatch(rf'pkg = "{_LAUNCHER} -m pytest {{files}} -q -p no:cacheprovider"', entry), entry
    assert "{files}" in _comment_above(_config(repo), "pkg")
    assert run_cli(repo, "test-scoped", "pkg/test_x.py").returncode == 0


# --- doctor repeats init's lane probe -----------------------------------------

def test_doctor_fails_a_files_template_whose_scope_holds_no_test_file(tmp_path: Path):
    repo = _layout_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0
    config = _config(repo).replace(_entry(_config(repo), "pkg"),
                                   'pkg = "python -m pytest {files} -q -p no:cacheprovider"')
    (repo / "crapkit.toml").write_text(config, encoding="utf-8")

    res = run_cli(repo, "doctor")

    assert res.returncode == 1, res.stdout + res.stderr
    (line,) = [ln for ln in res.stdout.splitlines() if "{files}" in ln]
    assert line.startswith("FAIL") and "'pkg'" in line, line
    assert "no problems found" not in res.stdout
    report = json.loads(run_cli(repo, "doctor", "--json").stdout)
    assert any("{files}" in p for p in report["problems"])


def test_doctor_names_the_interpreter_and_plugin_versions_the_lane_resolves_to(tmp_path: Path):
    repo = _layout_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0

    res = run_cli(repo, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    (line,) = [ln for ln in res.stdout.splitlines() if ln.startswith("ok   lane 'py'")]
    assert re.fullmatch(rf"ok   lane 'py': {_LAUNCHER} -> .+ \(pytest [\d.]+\S*, pytest-cov [\d.]+\S*\)",
                        line), line
    assert "no problems found" in res.stdout


def _without_pytest_cov(tmp_path: Path) -> dict:
    """A PYTHONPATH shim whose pytest_cov refuses to import, the trick the init
    e2e tests already play."""
    shim_dir = tmp_path / "no-cov-shim"
    shim_dir.mkdir()
    (shim_dir / "pytest_cov.py").write_text(
        'raise ImportError("shimmed out for the doctor probe test")\n', encoding="utf-8")
    inherited = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(p for p in (str(shim_dir), inherited) if p)}


def test_doctor_fails_with_inits_own_sentence_when_pytest_cov_cannot_import(tmp_path: Path):
    """doctor said `no problems found` on the lane init had just warned about."""
    repo = _layout_repo(tmp_path)
    assert run_cli(repo, "init").returncode == 0

    res = run_cli(repo, "doctor", env_extra=_without_pytest_cov(tmp_path))

    assert res.returncode == 1, res.stdout + res.stderr
    (line,) = [ln for ln in res.stdout.splitlines() if "pytest_cov" in ln]
    assert line.startswith("FAIL lane 'py' names"), line
    assert "pip install pytest-cov" in line
    assert "no problems found" not in res.stdout


# --- the js form is keyed by the runner, not the language ---------------------

def _js_repo(tmp_path: Path, package: dict, extra: dict | None = None) -> Path:
    files = {"src/app.ts": _APP_TS, "package.json": json.dumps(package)}
    files.update(extra or {})
    return _repo(tmp_path, files)


def test_a_jest_repo_gets_jests_related_tests_mode(tmp_path: Path):
    """init wrote `npx vitest run {files}` for a jest repo."""
    repo = _js_repo(tmp_path, {"scripts": {"test": "jest"}, "devDependencies": {"jest": "^29.0.0"}})

    assert run_cli(repo, "init").returncode == 0

    assert _entry(_config(repo), "src") == '# src = "npx jest --findRelatedTests {files}"'
    assert "jest" in _comment_above(_config(repo), "src")


def test_a_vitest_repo_gets_vitests_related_tests_mode(tmp_path: Path):
    repo = _js_repo(tmp_path, {"scripts": {"test": "vitest run"},
                               "devDependencies": {"vitest": "^2.0.0"}})

    assert run_cli(repo, "init").returncode == 0

    assert _entry(_config(repo), "src") == '# src = "npx vitest related --run {files}"'


def test_a_workspace_scope_runs_its_own_test_script_from_the_root(tmp_path: Path):
    """The web template ran vitest at the root and found no test files; the
    workspace's own script, run with -w, is the one that knows its config."""
    repo = _repo(tmp_path, {
        "web/src/app.ts": _APP_TS,
        "package.json": json.dumps({"private": True, "workspaces": ["web"],
                                    "scripts": {"test": "npm run --workspaces test"}}),
        "web/package.json": json.dumps({"name": "web", "scripts": {"test": "vitest run"},
                                        "devDependencies": {"vitest": "^2.0.0"}}),
    })

    assert run_cli(repo, "init").returncode == 0

    assert _entry(_config(repo), "web") == 'web = "npm run test -w web"', "live: the script is the repo's own"
    assert "workspace" in _comment_above(_config(repo), "web")


def test_init_says_when_two_workspaces_name_a_runner_and_no_js_lane_was_written(tmp_path: Path):
    repo = _repo(tmp_path, {
        "api/src/a.ts": _APP_TS,
        "web/src/b.ts": _APP_TS,
        "package.json": json.dumps({"private": True, "workspaces": ["api", "web"]}),
        "api/package.json": json.dumps({"name": "api", "scripts": {"test": "jest"},
                                        "devDependencies": {"jest": "^29.0.0"}}),
        "web/package.json": json.dumps({"name": "web", "scripts": {"test": "vitest run"},
                                        "devDependencies": {"vitest": "^2.0.0"}}),
    })

    res = run_cli(repo, "init")

    assert res.returncode == 0, res.stderr
    assert "2 workspaces name a runner (api: jest, web: vitest)" in res.stdout, res.stdout
    assert "no js lane was written" in res.stdout
    assert 'parser = "istanbul"' not in [ln for ln in _config(repo).splitlines()
                                         if not ln.startswith("#")]
