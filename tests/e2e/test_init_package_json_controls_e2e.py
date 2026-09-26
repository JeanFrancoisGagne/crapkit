"""Controls: the npm lane `crapkit init` wrote from package.json in 0.8.0, through the CLI.

test_init_detection.py pins these rows by calling scaffold.detect_lanes with an
NpmPackage, which 0.8.1 introduced, so on 0.8.0 that file does not import and
cannot show what 0.8.0 wrote. Each test here runs `crapkit init` over a repo
holding the package.json of one row and reads the lane from the crapkit.toml
init wrote, so it passes on 0.8.0 and on every later tree.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from crapkit.config import load_config_text
from test_init_doctor_e2e import _git_commit_all, run_cli

VITEST = {"vitest": "^2.0.0"}


def _init(tmp_path: Path, package: dict) -> list:
    """The lanes `crapkit init` writes for a TypeScript repo holding `package`."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                         encoding="utf-8")
    (repo / "package.json").write_text(json.dumps(package), encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _git_commit_all(repo, "init")

    res = run_cli(repo, "init")

    assert res.returncode == 0, res.stdout + res.stderr
    return list(load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8")).lanes)


def _commands(lanes: list) -> list[tuple[str, str]]:
    return [(lane.command, lane.artifact) for lane in lanes]


def test_a_devdependencies_list_names_the_runners_it_holds(tmp_path):
    malformed = _init(tmp_path / "a", {"devDependencies": ["vitest", 7]})
    meant = _init(tmp_path / "b", {"devDependencies": {"vitest": ""}})

    assert _commands(malformed) == _commands(meant)
    assert meant[0].command.startswith("npx vitest run --coverage "), meant[0].command


def test_a_runner_with_no_scripts_is_run_through_npx(tmp_path):
    (lane,) = _init(tmp_path, {"devDependencies": VITEST})

    assert lane.command.startswith("npx vitest run --coverage "), lane.command


def test_an_empty_package_json_object_writes_no_npm_lane(tmp_path):
    assert _init(tmp_path, {}) == []


@pytest.mark.parametrize("package, command", [
    ({"scripts": {"test": "vitest run"}}, "npm run test -- --coverage"),
    ({"name": "café-世界", "scripts": {"tést": "x", "test": "vitest run"}},
     "npm run test -- --coverage"),
    ({"scripts": {**{f"build:{n}": "tsc" for n in range(20000)}, "test:z": "vitest"}},
     "npm run test:z -- --coverage"),
], ids=["test-script", "non-ascii-names", "20000-scripts"])
def test_a_test_script_with_no_named_runner_keeps_the_default_directory(tmp_path, package,
                                                                        command):
    (lane,) = _init(tmp_path, package)

    assert (lane.command, lane.artifact) == (command, "coverage/coverage-final.json")


def test_a_test_script_beside_a_named_runner_gets_that_runners_flags(tmp_path):
    (lane,) = _init(tmp_path, {"scripts": {"test": "vitest run"}, "devDependencies": VITEST})

    assert (lane.command, lane.artifact) == (
        "npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js "
        "--coverage.reportOnFailure --reporter=default --reporter=junit "
        "--outputFile=.crapkit/cov/js/junit.xml", ".crapkit/cov/js/coverage-final.json")
