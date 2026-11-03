"""An lcov lane at config load and in doctor: `parser = "lcov"` loads, the
vitest file-filter refusal fires on a spelled runner word whatever the parser,
a command that runs vitest through a package.json script loads, and doctor
names the runner an lcov lane's command spells."""
import json
from pathlib import Path

import pytest

from crapkit.cli import admin
from crapkit.config import load_config_text
from crapkit.errors import ConfigError

TOML = """
[[scope]]
name = "web"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "web"
command = '{command}'
artifact = "coverage/lcov.info"
parser = "{parser}"
scopes = ["web"]
"""
FILTERED = "npx vitest run --coverage --coverage.reporter=lcov src/a.test.ts"


def _load(command: str, parser: str = "lcov", root: Path | None = None):
    return load_config_text(TOML.format(command=command, parser=parser), root=root)


def _refusal(command: str, parser: str = "lcov", root: Path | None = None) -> str:
    with pytest.raises(ConfigError) as refused:
        _load(command, parser, root)
    assert refused.value.exit_code == 3
    return str(refused.value)


def test_a_lane_with_parser_lcov_loads():
    (lane,) = _load("cargo llvm-cov --lcov --output-path coverage/lcov.info").lanes

    assert (lane.parser, lane.artifact) == ("lcov", "coverage/lcov.info")


def test_a_vitest_file_filter_is_refused_under_lcov_with_the_istanbul_sentence():
    assert _refusal(FILTERED) == _refusal(FILTERED, parser="istanbul")
    assert "file filter 'src/a.test.ts'" in _refusal(FILTERED)


def test_a_script_stem_that_spells_vitest_is_refused_the_same_way():
    """`run-vitest.mjs` spells vitest as a hyphen-separated part of its stem."""
    text = _refusal("node scripts/run-vitest.mjs --coverage src/a.test.ts")

    assert "file filter 'src/a.test.ts'" in text


def _package(root: Path) -> Path:
    scripts = {"test": "vitest run --coverage --coverage.reporter=lcov"}
    (root / "package.json").write_text(json.dumps({"scripts": scripts}), encoding="utf-8")
    return root


@pytest.mark.parametrize("command", ["npm run test", "npm run test -- src/a.test.ts"],
                         ids=["no-filter", "file-filter"])
def test_npm_run_test_loads_because_config_load_reads_the_command_alone(tmp_path, command):
    """Config load reads no package.json, so a command that wraps vitest in a
    script keeps no file-filter refusal, with or without a filter."""
    assert _load(command, root=_package(tmp_path)).lanes[0].parser == "lcov"


@pytest.mark.parametrize("command, runner", [
    ("bun test --coverage --coverage-reporter=lcov", "bun"),
    ("cargo llvm-cov --lcov --output-path coverage/lcov.info", "cargo llvm-cov"),
], ids=["bun", "cargo-llvm-cov"])
def test_doctor_names_the_runner_of_an_lcov_lane(command, runner):
    cfg = _load(command)

    assert [(f.level, f.text) for f in admin._doctor_runners(cfg, admin.NO_PACKAGES)] == [
        ("ok", f"lane 'web': runs {runner} (named in its command)")]
