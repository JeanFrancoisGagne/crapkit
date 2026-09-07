"""Automatic reuse cannot hide changed measurement inputs."""
import json
import sys
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo


run_cli = cli_runner(encoding="utf-8", timeout=30)
RUNNER = '''import json, os
from pathlib import Path
state = os.environ.get('CRAPKIT_TEST_MEASUREMENT_RESULT', Path('tests/state.txt').read_text().strip())
hit = int(state == 'pass')
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
count = folder / 'counter.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': hit},
    'statementMap': {'0': loc}, 's': {'0': hit}, 'branchMap': {}, 'b': {}}}
(folder/'cov.json').write_text(json.dumps(data), encoding='utf-8')
failure = '' if hit else '<failure message="changed test fails"/>'
(folder/'junit.xml').write_text('<testsuites><testsuite tests="1">'
    '<testcase classname="tests.test_app" name="test_f">' + failure
    + '</testcase></testsuite></testsuites>', encoding='utf-8')
'''


@pytest.fixture
def measured_repo(tmp_path: Path) -> Path:
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/app.js").write_text("function f() {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "tests/state.txt").write_text("pass", encoding="utf-8")
    (tmp_path / "measure.py").write_text(RUNNER, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n__pycache__/\n", encoding="utf-8")
    command = json.dumps(f'"{sys.executable}" measure.py')
    config = ('[[scope]]\nname="src"\npaths=["src"]\nlanguages=["javascript"]\n'
              '[[lane]]\nname="js"\nparser="istanbul"\nscopes=["src"]\n'
              f'command={command}\nartifact=".crapkit/cov.json"\n'
              'results_artifact=".crapkit/junit.xml"\n')
    (tmp_path / "crapkit.toml").write_text(config, encoding="utf-8")
    git_commit_all(tmp_path, "fixture")
    result = run_cli(tmp_path, "coverage", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    return tmp_path


def test_automatic_reuse_measures_a_changed_tracked_test_input(measured_repo):
    (measured_repo / "tests/state.txt").write_text("fail", encoding="utf-8")
    result = run_cli(measured_repo, "verify", "--reuse-unchanged", "--no-tighten", "--json")
    assert result.returncode == 8, result.stdout + result.stderr
    verdict = json.loads(result.stdout)
    assert verdict["ok"] is False
    assert verdict["new_failures"] == ["tests.test_app::test_f"]
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "2"


def test_a_dirty_measurement_cannot_describe_the_restored_clean_tree(measured_repo):
    state = measured_repo / "tests/state.txt"
    state.write_text("fail", encoding="utf-8")
    dirty = run_cli(measured_repo, "coverage", "--json")
    assert dirty.returncode == 0, dirty.stdout + dirty.stderr
    assert json.loads(dirty.stdout)["crap_load"] == 2
    state.write_text("pass", encoding="utf-8")
    restored = run_cli(measured_repo, "coverage", "--reuse-unchanged", "--json")
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert json.loads(restored.stdout)["crap_load"] == 1
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "3"


def test_automatic_reuse_accounts_for_the_runner_environment(measured_repo):
    result = run_cli(measured_repo, "verify", "--reuse-unchanged", "--no-tighten", "--json",
                     env_extra={"CRAPKIT_TEST_MEASUREMENT_RESULT": "fail"})
    assert result.returncode == 8, result.stdout + result.stderr
    assert json.loads(result.stdout)["new_failures"] == ["tests.test_app::test_f"]


def test_identical_clean_inputs_reuse_the_measured_bytes(measured_repo):
    fresh = run_cli(measured_repo, "coverage", "--json")
    reused = run_cli(measured_repo, "coverage", "--reuse-unchanged", "--json")
    assert fresh.returncode == reused.returncode == 0, reused.stdout + reused.stderr
    assert json.loads(reused.stdout)["lanes"]["js"]["exit_code"] is None
    assert "measurement inputs unchanged" in reused.stderr
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "2"


def test_repeated_dirty_edits_each_require_a_measurement(measured_repo):
    for count, state in enumerate(("fail", "pass\n", "fail\n"), 2):
        (measured_repo / "tests/state.txt").write_text(state, encoding="utf-8")
        result = run_cli(measured_repo, "coverage", "--reuse-unchanged", "--json")
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["crap_load"] == (1 if state.strip() == "pass" else 2)
        assert (measured_repo / ".crapkit/counter.txt").read_text() == str(count)


@pytest.mark.parametrize("path", ["measure.py", "crapkit.toml", "tests/new_case.txt"])
def test_changed_command_config_or_untracked_input_reruns(measured_repo, path):
    target = measured_repo / path
    previous = target.read_text(encoding="utf-8") if target.exists() else ""
    target.write_text(previous + "\n# changed input\n", encoding="utf-8")
    result = run_cli(measured_repo, "coverage", "--reuse-unchanged", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "2"


def test_explicit_reuse_keeps_its_deliberate_artifact_only_contract(measured_repo):
    (measured_repo / "tests/state.txt").write_text("fail", encoding="utf-8")
    result = run_cli(measured_repo, "coverage", "--reuse-artifacts", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["crap_load"] == 1
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "1"


@pytest.mark.parametrize("artifact", ["cov.json", "junit.xml"])
def test_automatic_reuse_requires_the_artifacts_that_measurement_produced(measured_repo, artifact):
    fresh = run_cli(measured_repo, "coverage", "--json")
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    path = measured_repo / ".crapkit" / artifact
    if artifact == "cov.json":
        data = json.loads(path.read_text(encoding="utf-8"))
        data["src/app.js"]["f"]["0"] = data["src/app.js"]["s"]["0"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_text(path.read_text().replace("</testcase>", "<failure/></testcase>"), encoding="utf-8")
    result = run_cli(measured_repo, "coverage", "--reuse-unchanged", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["crap_load"] == 1
    assert report["lanes"]["js"]["failures"] == []
    assert (measured_repo / ".crapkit/counter.txt").read_text() == "3"


@pytest.fixture
def nested_repo(tmp_path: Path) -> Path:
    git_init_repo(tmp_path)
    app = tmp_path / "app"
    (app / "src").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (app / "src/app.js").write_text("function f() {\n  return 1;\n}\n", encoding="utf-8")
    (tmp_path / "tests/state.txt").write_text("pass", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n__pycache__/\n", encoding="utf-8")
    runner = RUNNER.replace("Path('tests/state.txt')", "Path('../tests/state.txt')")
    runner = runner.replace("hit = int(state == 'pass')",
                            "added = Path('../tests/new_case.txt')\n"
                            "if added.exists():\n    state = added.read_text().strip()\n"
                            "hit = int(state == 'pass')")
    (app / "measure.py").write_text(runner, encoding="utf-8")
    command = json.dumps(f'"{sys.executable}" measure.py')
    (app / "crapkit.toml").write_text(
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["javascript"]\n'
        '[[lane]]\nname="js"\nparser="istanbul"\nscopes=["src"]\n'
        f'command={command}\nartifact=".crapkit/cov.json"\n'
        'results_artifact=".crapkit/junit.xml"\n', encoding="utf-8")
    git_commit_all(tmp_path, "nested project")
    return app


@pytest.mark.parametrize("path", ["state.txt", "new_case.txt"], ids=["tracked", "untracked"])
def test_nested_root_reuse_measures_changed_inputs_above_the_config(nested_repo, path):
    first = run_cli(nested_repo, "coverage", "--json")
    assert first.returncode == 0, first.stdout + first.stderr
    (nested_repo.parent / "tests" / path).write_text("fail", encoding="utf-8")

    result = run_cli(nested_repo, "verify", "--reuse-unchanged", "--no-tighten", "--json")

    assert result.returncode == 8, result.stdout + result.stderr
    assert json.loads(result.stdout)["new_failures"] == ["tests.test_app::test_f"]
    assert (nested_repo / ".crapkit/counter.txt").read_text() == "2"


def test_unchanged_nested_root_reuses_its_measurement(nested_repo):
    first = run_cli(nested_repo, "coverage", "--json")
    assert first.returncode == 0, first.stdout + first.stderr

    reused = run_cli(nested_repo, "coverage", "--reuse-unchanged", "--json")

    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert json.loads(reused.stdout)["lanes"]["js"]["exit_code"] is None
    assert (nested_repo / ".crapkit/counter.txt").read_text() == "1"
