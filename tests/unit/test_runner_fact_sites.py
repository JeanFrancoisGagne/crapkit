"""Every runner fact reads the runner a lane spells, never its coverage format.

Six sites used to guess the runner from `parser`: the vitest file-filter
refusal, the pytest narrowing refusal, the container refusal, doctor's
pytest-cov probe, doctor's missing-results_artifact hint and init's workspace
note. Each now asks the toolchain table. Where the answer comes from depends on
where the site runs: config load reads crapkit.toml alone (claude-hook loads it
on every edit), so the two config-load refusals and the container refusal read
the command's own words; doctor and init already hold the package map, so the
probe and the hint also count a runner the package.json script spells.
devDependencies alone turn on no check.

Each cell of the site x inference table below is written by hand: the lane
command, the package.json beside it, and whether the site fires.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crapkit import config as config_module
from crapkit import lanes as lanes_module
from crapkit.cli import admin
from crapkit.config import Lane, load_config_text
from crapkit.errors import ConfigError, ToolError
from crapkit.scaffold import LaneSpec, npm_package


@pytest.fixture(autouse=True)
def sh_reads_the_commands(monkeypatch):
    """Every command here reads the same under sh and cmd.exe but one: the
    quoted filter, read under sh. Pinned so the table means one thing."""
    monkeypatch.setattr(config_module, "SHELL_IS_CMD", False)


# --- the inferences: what the lane runs, and the package.json beside it -----------

SCRIPT_VITEST = {"scripts": {"cov": "vitest run --coverage"}}
SCRIPT_PYTEST = {"scripts": {"cov": "pytest --cov"}}
DEV_ONLY = {"scripts": {"cov": "react-scripts test --coverage"}, "devDependencies": {"vitest": "1"}}

# (site, inference, command, root package.json or None, fires)
FILE_FILTER = [
    ("command", "npx vitest run --coverage src/a.test.ts", None, True),
    ("script", "npm run cov -- --coverage src/a.test.ts", SCRIPT_VITEST, False),
    ("devDependencies only", "npm run cov -- --coverage src/a.test.ts", DEV_ONLY, False),
    ("unknown", "make cov -- --coverage src/a.test.ts", None, False),
    ("two runners", "npx vitest run --coverage src/a.test.ts && python -m pytest --cov", None, True),
]
NARROWING = [
    ("command", "python -m pytest tests/unit --cov", None, True),
    ("script", "npm run cov -- tests/unit", SCRIPT_PYTEST, False),
    ("devDependencies only", "npm run cov -- tests/unit", DEV_ONLY, False),
    ("unknown", "make cov tests/unit", None, False),
    ("two runners", "python -m pytest tests/unit --cov && npx vitest run --coverage", None, True),
]
CONTAINER = [
    ("command", "python -m pytest --cov", None, True),
    ("script", "npm run cov", SCRIPT_PYTEST, False),
    ("devDependencies only", "npm run cov", DEV_ONLY, False),
    ("unknown", "make cov", None, False),
    ("two runners", "python -m pytest --cov && npx vitest run --coverage", None, True),
]
PROBE = [
    ("command", "python -m pytest --cov", None, True),
    ("script", "npm run cov", SCRIPT_PYTEST, True),
    ("devDependencies only", "npm run cov", DEV_ONLY, False),
    ("unknown", "make cov", None, False),
    ("two runners", "python -m pytest --cov && npx vitest run --coverage", None, False),
]
# fires: which hint the WARN carries
RESULTS_HINT = [
    ("command", "python -m pytest --cov", None, "pytest"),
    ("command", "npx vitest run --coverage", None, "vitest"),
    ("command", "npx jest --coverage", None, "jest"),
    ("script", "npm run cov", SCRIPT_VITEST, "vitest"),
    ("devDependencies only", "npm run cov", DEV_ONLY, "generic"),
    ("unknown", "make cov", None, "generic"),
    ("two runners", "python -m pytest --cov && npx vitest run --coverage", None, "generic"),
]
# fires: init prints the note. Two workspaces name a runner in every row.
WORKSPACE_NOTE = [
    ("command", "npx vitest run --coverage", {}, False),
    ("script", "npm run cov", SCRIPT_VITEST, False),
    ("devDependencies only", "npm run cov", DEV_ONLY, False),
    ("unknown", "npm test", {"scripts": {"test": "pnpm -r test"}}, True),
    ("two runners", "npx vitest run --coverage && npx jest --coverage", {}, True),
]


def _ids(rows) -> list[str]:
    return [f"{row[0]}: {row[1]}" for row in rows]


def _packages(root_package: dict | None, **workspaces: dict) -> admin.PackageMap:
    found = {"": root_package} if root_package is not None else {}
    found.update(workspaces)
    return admin.PackageMap({rel: npm_package(data) for rel, data in found.items()}, {})


def _lane(command: str, parser: str = "coveragepy", **fields) -> Lane:
    return Lane(name="t", command=command, artifact=".crapkit/cov/t.json", parser=parser,
                scopes=("src",), **fields)


def _loads(tmp_path: Path, command: str, parser: str, root_package: dict | None) -> bool:
    """Whether crapkit.toml with this one lane loads; a package.json written
    beside it proves config load never reads one."""
    if root_package is not None:
        (tmp_path / "package.json").write_text(json.dumps(root_package), encoding="utf-8")
    text = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
            f"[[lane]]\nname = \"t\"\ncommand = '{command}'\n"
            f'artifact = ".crapkit/cov/t.json"\nparser = "{parser}"\nscopes = ["src"]\n')
    try:
        load_config_text(text, root=tmp_path)
    except ConfigError as exc:
        assert exc.exit_code == 3
        return False
    return True


# --- config load: the two refusals, whatever the parser ---------------------------

@pytest.mark.parametrize("parser", ["istanbul", "coveragepy"])
@pytest.mark.parametrize("inference, command, root_package, fires", FILE_FILTER, ids=_ids(FILE_FILTER))
def test_the_file_filter_refusal_fires_on_a_step_that_spells_vitest(tmp_path, parser, inference,
                                                                    command, root_package, fires):
    assert _loads(tmp_path, command, parser, root_package) is not fires


@pytest.mark.parametrize("parser", ["istanbul", "coveragepy"])
@pytest.mark.parametrize("inference, command, root_package, fires", NARROWING, ids=_ids(NARROWING))
def test_the_narrowing_refusal_fires_on_a_step_that_spells_pytest(tmp_path, parser, inference,
                                                                  command, root_package, fires):
    assert _loads(tmp_path, command, parser, root_package) is not fires


@pytest.mark.parametrize("command, fires", [
    ("node scripts/run-pytest.mjs tests/unit --cov", True),
    ("node scripts/run.pytest.mjs tests/unit --cov", False),
])
def test_the_narrowing_refusal_reads_a_script_stem_that_spells_pytest(tmp_path, command, fires):
    """The script-stem rule of step 1 holds for pytest as for vitest: a hyphen
    separates a part of the stem, a dot does not."""
    assert _loads(tmp_path, command, "coveragepy", None) is not fires


# (command, spells pytest): a python and `coverage run` read past their own
# options, a value-taking one with its value, to the `-m` that names the module.
INTERPRETER_OPTIONS = [
    ("python -X utf8 -m pytest tests/unit --cov", True),
    ("python -W error -m pytest tests/unit --cov", True),
    ("python -X dev -m pytest tests/unit --cov", True),
    ("python -Xutf8 -B -m pytest tests/unit --cov", True),
    ("python --check-hash-based-pycs never -m pytest tests/unit --cov", True),
    ("coverage run --source src -m pytest tests/unit", True),
    ("python -X utf8 tools/pytest.py tests/unit --cov", False),
]


@pytest.mark.parametrize("command, spells", INTERPRETER_OPTIONS)
def test_a_python_reads_past_its_own_options_to_the_module(tmp_path, monkeypatch, command, spells):
    """`-X utf8` and `-W error` take a value; the value is not the word that
    ends the options, so the narrowing and container refusals still read the
    pytest that `-m` runs, as they did when the parser chose them."""
    assert _loads(tmp_path, command, "coveragepy", None) is not spells
    monkeypatch.setattr(lanes_module, "_in_container", lambda: True)

    refused = True
    try:
        lanes_module._refuse_container_python(_lane(command.replace(" tests/unit", "")))
    except ToolError:
        pass
    else:
        refused = False

    assert refused is spells


def test_the_refusals_keep_their_text(tmp_path):
    with pytest.raises(ConfigError, match="file filter 'src/a.test.ts' combined with --coverage"):
        load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
                         '[[lane]]\nname = "t"\ncommand = "npx vitest run --coverage src/a.test.ts"\n'
                         'artifact = "t.json"\nparser = "istanbul"\nscopes = ["src"]\n')
    with pytest.raises(ConfigError, match="positional argument 'tests/unit' narrows a full-suite"):
        load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
                         '[[lane]]\nname = "t"\ncommand = "python -m pytest tests/unit --cov"\n'
                         'artifact = "t.json"\nparser = "coveragepy"\nscopes = ["src"]\n')


def test_full_suite_false_still_opts_out_of_the_narrowing_refusal(tmp_path):
    text = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
            '[[lane]]\nname = "t"\ncommand = "python -m pytest tests/unit --cov"\n'
            'artifact = "t.json"\nparser = "istanbul"\nscopes = ["src"]\nfull_suite = false\n')
    assert load_config_text(text).lanes[0].full_suite is False


@pytest.mark.parametrize("command, refused", [
    ("vitest run src/foo.ts --coverage", "src/foo.ts"),
    ("npx vitest run --coverage 'src/foo.test.ts'", "src/foo.test.ts"),
    ("node scripts/run-vitest.mjs run --coverage src/thing.test.ts", "src/thing.test.ts"),
    ("pnpm --dir ui exec vitest run --project unit --coverage src/a.test.ts", "src/a.test.ts"),
    ("CI=1 vitest --coverage src/a.ts", "src/a.ts"),
])
def test_the_filter_scan_starts_past_the_word_that_spells_vitest(command, refused):
    """The scan starts after the runner token, the bare word or the script the
    stem rule reads, and past a `run` right behind it; the script path itself
    is never a filter, and a `run` elsewhere is not where the scan starts."""
    with pytest.raises(ConfigError, match=f"file filter '{refused}'"):
        load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
                         f"[[lane]]\nname = \"t\"\ncommand = \"{command}\"\n"
                         'artifact = "t.json"\nparser = "istanbul"\nscopes = ["src"]\n')


@pytest.mark.parametrize("command", [
    "node scripts/run.mjs --coverage",        # a script whose stem names no runner
    "node scripts/vitest-run.ts --coverage",  # the script path is the runner token, never a filter
    "vitest run --coverage --config vitest.ci.ts",
])
def test_the_runner_token_and_its_flag_values_are_never_filters(command):
    assert load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n'
                            f"[[lane]]\nname = \"t\"\ncommand = \"{command}\"\n"
                            'artifact = "t.json"\nparser = "istanbul"\nscopes = ["src"]\n').lanes


# --- the container refusal: the command alone --------------------------------------

@pytest.mark.parametrize("inference, command, root_package, fires", CONTAINER, ids=_ids(CONTAINER))
def test_the_container_refusal_fires_on_a_command_that_spells_pytest(monkeypatch, inference, command,
                                                                     root_package, fires):
    monkeypatch.setattr(lanes_module, "_in_container", lambda: True)

    refused = True
    try:
        lanes_module._refuse_container_python(_lane(command))
    except ToolError as exc:
        assert "host-only" in str(exc)
    else:
        refused = False

    assert refused is fires


@pytest.mark.parametrize("parser", ["istanbul", "coveragepy"])
def test_container_ok_clears_the_container_refusal_whatever_the_parser(monkeypatch, parser):
    monkeypatch.setattr(lanes_module, "_in_container", lambda: True)

    lanes_module._refuse_container_python(_lane("python -m pytest --cov", parser, container_ok=True))
    with pytest.raises(ToolError, match="host-only"):
        lanes_module._refuse_container_python(_lane("python -m pytest --cov", parser))


# --- doctor and init: the command, or the script it runs ---------------------------

@pytest.mark.parametrize("inference, command, root_package, fires", PROBE, ids=_ids(PROBE))
def test_the_pytest_cov_probe_runs_on_a_lane_that_spells_pytest_with_cov(inference, command,
                                                                         root_package, fires):
    lane = _lane(command, "istanbul")

    assert admin._probed_lanes((lane,), _packages(root_package)) == ([lane] if fires else [])


def test_the_probe_reads_cov_where_pytest_is_spelled():
    """`npm run cov` whose script runs pytest without --cov has no plugin
    flag to probe, and --cov in the command beside a pytest the script spells
    is still read from the script."""
    bare = _packages({"scripts": {"cov": "pytest"}})

    assert admin._probed_lanes((_lane("npm run cov"),), bare) == []
    assert admin._probed_lanes((_lane("npm run cov -- --cov"),), bare) == []


_HINTS = {
    "pytest": "add --junitxml=.crapkit/cov/junit-t.xml to the command and "
              'results_artifact = ".crapkit/cov/junit-t.xml" to the lane',
    "vitest": "add --reporter=default --reporter=junit --outputFile=.crapkit/cov/t/junit.xml to the "
              'command and results_artifact = ".crapkit/cov/t/junit.xml" to the lane',
    "jest": "add the jest-junit package, --reporters=default --reporters=jest-junit to the command, "
            'JEST_JUNIT_OUTPUT_DIR = ".crapkit/cov/t" and JEST_JUNIT_OUTPUT_NAME = "junit.xml" to its '
            '[lane.env], and results_artifact = ".crapkit/cov/t/junit.xml" to the lane',
    "generic": "add the runner's junit reporter to the command and a results_artifact naming the file "
               "it writes",
}


@pytest.mark.parametrize("inference, command, root_package, hint", RESULTS_HINT, ids=_ids(RESULTS_HINT))
def test_the_results_hint_follows_the_spelled_runner(inference, command, root_package, hint):
    cfg = config_module.Config(lanes=(_lane(command, "istanbul"),))

    (warn,) = admin._doctor_results_artifacts(cfg, _packages(root_package))

    assert warn.level == "WARN"
    assert warn.text == ("lane 't' declares no results_artifact: the crashed-worker check and the "
                         "no-new-failures check (exit 8) cannot run for it; " + _HINTS[hint])


def test_every_hint_names_results_artifact():
    assert all("results_artifact" in hint for hint in _HINTS.values())


@pytest.mark.parametrize("inference, command, root_package, fires", WORKSPACE_NOTE, ids=_ids(WORKSPACE_NOTE))
def test_the_workspace_note_asks_whether_init_wrote_a_js_runner_lane(inference, command, root_package,
                                                                     fires):
    packages = {"": npm_package(root_package),
                "api": npm_package({"devDependencies": {"jest": "1"}}),
                "web": npm_package({"devDependencies": {"vitest": "1"}})}
    written = (LaneSpec("js", command, ".crapkit/cov/js/coverage-final.json", "istanbul",
                        ("typescript",)),)

    note = admin._unrouted_workspaces_note(written, packages)

    assert (note is not None) is fires
    if fires:
        assert note.startswith("2 workspaces name a runner (api: jest, web: vitest)"), note


def test_a_python_lane_beside_no_js_lane_still_gets_the_note():
    packages = {"api": npm_package({"devDependencies": {"jest": "1"}}),
                "web": npm_package({"devDependencies": {"vitest": "1"}})}
    written = (LaneSpec("py", "python -m pytest --cov", ".crapkit/cov/py.json", "coveragepy",
                        ("python",)),)

    assert admin._unrouted_workspaces_note(written, packages) is not None


# --- a pnpm monorepo's lane commands, merge base against branch ---------------------
#
# Command lines only, pasted word for word from a real monorepo's crapkit.toml, in
# its lane order: twelve lanes run vitest through `node scripts/run-vitest.mjs`,
# whose stem names it, one runs `pnpm --dir ui exec vitest`, and one runs
# `python -m pytest`. Each loads on the merge base and on the branch, and each refuses a
# file filter (or a pytest positional) appended to it on both: the base read `run`
# and the parser, the branch reads the spelled runner (the script-stem rule). Three
# rows below pin where a rule of this change moves a verdict.

MONOREPO = [
    ("pnpm exec node scripts/run-vitest.mjs run --config test/vitest/vitest.unit.config.ts "
     "--coverage --coverage.reporter=json --coverage.reportsDirectory=.crapkit/cov/unit "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --coverage.exclude=ui/** "
     "--coverage.exclude=apps/** --coverage.exclude=**/*.test.ts --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/unit.xml --pool=threads", "istanbul"),
    ("pnpm --dir ui exec vitest run --project unit --project unit-node --coverage "
     "--coverage.reporter=json --coverage.reportOnFailure --reporter=default "
     "--reporter=junit --outputFile.junit=crapkit-junit-ui.xml", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.crapkit-extensions-a.config.ts --coverage "
     "--coverage.reporter=json --coverage.reportsDirectory=.crapkit/cov/ext-a "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/ext-a.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.crapkit-extensions-b.config.ts --coverage "
     "--coverage.reporter=json --coverage.reportsDirectory=.crapkit/cov/ext-b "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/ext-b.xml", "istanbul"),
    ("python -m pytest --cov --cov-branch --cov-report=json:coverage-py.json "
     "--junitxml=crapkit-junit.xml", "coveragepy"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.auto-reply.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/auto-reply --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/auto-reply.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config test/vitest/vitest.cron.config.ts "
     "--coverage --coverage.reporter=json --coverage.reportsDirectory=.crapkit/cov/cron "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --coverage.exclude=ui/** "
     "--coverage.exclude=apps/** --coverage.exclude=**/*.test.ts --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/cron.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.unit-fast.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/unit-fast --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/unit-fast.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.gateway-core.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/gateway-core --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/gateway-core.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.gateway-client.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/gateway-client --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/gateway-client.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.agents-core-isolated.config.ts --coverage "
     "--coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/agents-core-isolated "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --coverage.exclude=ui/** "
     "--coverage.exclude=apps/** --coverage.exclude=**/*.test.ts --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/agents-core-isolated.xml "
     "--pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.agents-embedded-agent.config.ts --coverage "
     "--coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/agents-embedded-agent "
     "--coverage.reportOnFailure --coverage.exclude=**/node_modules/** "
     "--coverage.exclude=**/dist/** --coverage.exclude=test/** --coverage.exclude=ui/** "
     "--coverage.exclude=apps/** --coverage.exclude=**/*.test.ts --reporter=default "
     "--reporter=junit --outputFile.junit=.crapkit/junit/agents-embedded-agent.xml "
     "--pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.agents-tools.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/agents-tools --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/agents-tools.xml --pool=threads", "istanbul"),
    ("pnpm exec node scripts/run-vitest.mjs run --config "
     "test/vitest/vitest.agents-support.config.ts --coverage --coverage.reporter=json "
     "--coverage.reportsDirectory=.crapkit/cov/agents-support --coverage.reportOnFailure "
     "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
     "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
     "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
     "--outputFile.junit=.crapkit/junit/agents-support.xml --pool=threads", "istanbul"),
]


@pytest.mark.parametrize("command, parser", MONOREPO)
def test_a_monorepo_lane_loads_on_base_and_branch_alike(tmp_path, command, parser):
    assert _loads(tmp_path, command, parser, None)


@pytest.mark.parametrize("command, parser", MONOREPO)
def test_a_monorepo_lane_refuses_an_appended_filter_on_base_and_branch_alike(tmp_path, command, parser):
    narrowed = command + (" src/a.test.ts" if parser == "istanbul" else " tests/test_a.py")

    assert not _loads(tmp_path, narrowed, parser, None)


# (command, parser, merge base loads, branch loads, the rule that moves it)
DIFFERENCES = [
    ("pnpm exec node scripts/test-runner.mjs run --coverage src/a.test.ts", "istanbul", False, True,
     "script-stem rule: the stem names no runner, and the `run` heuristic is gone"),
    ("npm run test -- --coverage src/foo.test.ts", "istanbul", False, True,
     "package.json scripts: doctor and init follow them; config load reads no package.json"),
    ("python -m pytest tests/unit --cov", "istanbul", True, False,
     "spelled runner: the command names pytest, so the narrowing refusal reads it, any parser"),
]


@pytest.mark.parametrize("command, parser, base, branch, rule", DIFFERENCES)
def test_each_difference_from_the_merge_base_is_one_rule(tmp_path, command, parser, base, branch,
                                                         rule):
    assert base is not branch, rule
    assert _loads(tmp_path, command, parser, None) is branch, rule
