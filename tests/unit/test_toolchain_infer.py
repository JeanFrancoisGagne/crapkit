"""toolchain.infer names the runner a lane runs, and where it read that.

First the lane's own command, read on both dialects through lane_command's
tokenizer and through the wrappers that only hand the rest on; then the
package.json script a script runner names; then devDependencies, which feed
doctor's line and are never `spelled`. Every expected value below is
written by hand from the rules in the ticket, never computed by the code under
test.
"""
from pathlib import PurePosixPath, PureWindowsPath
import subprocess
import sys

import pytest

from crapkit import lane_command
from crapkit.lane_command import expand_launchers, pytest_step
from crapkit.scaffold import detect_lanes, npm_package
from crapkit.toolchain import TOOLCHAINS, Inferred, infer

DIALECTS = ("sh", "cmd")


def package(scripts: dict | None = None, *dev: str):
    return npm_package({"scripts": scripts or {}, "devDependencies": {name: "1" for name in dev}})


def named(command: str, dialect: str, **packages) -> tuple:
    found = infer(command, dialect=dialect, **packages)
    return found.name, found.source


# --- check 1: the command names its row --------------------------------------------

COMMAND_ROWS = [
    ("python -m pytest --cov", "pytest"),
    ("uv run pytest", "pytest"),
    ("coverage run -m pytest && coverage json", "pytest"),
    ("cd tests && python -m pytest", "pytest"),
    ("npx vitest run --coverage", "vitest"),
    ("pnpm --dir ui exec vitest run", "vitest"),
    ("npx jest --coverage", "jest"),
    ("bun test --coverage", "bun"),
    ("deno test --coverage", "deno"),
    ("cargo llvm-cov --lcov", "cargo llvm-cov"),
    ("go test -coverprofile=c.out ./...", "go test"),
    ("c8 --reporter=json mocha", "c8"),
    # the wrappers step 1 reads through
    ("pnpm -C ui exec vitest run", "vitest"),
    ("pnpm --filter web exec vitest run", "vitest"),
    ("pnpm dlx vitest run", "vitest"),
    ("yarn exec jest", "jest"),
    ("yarn dlx jest", "jest"),
    ("bunx vitest run", "vitest"),
    ("bun x vitest run", "vitest"),
    ("poetry run pytest --cov", "pytest"),
    ("pipenv run pytest", "pytest"),
    ("pdm run pytest", "pytest"),
    ("hatch run pytest", "pytest"),
    ("uv run --with pytest-cov python -m pytest", "pytest"),
    ("python3 -m pytest", "pytest"),
    ("cross-env CI=1 vitest run", "vitest"),
    ("bash -c \"cd web && npx jest --coverage\"", "jest"),
]

# A path spelled with `\` reads as a path under cmd.exe only: sh takes the
# backslash for an escape, so the sh rows quote it, as an sh user must.
DIALECT_ROWS = [
    ("cmd", r".venv\Scripts\pytest.exe --cov", "pytest"),
    ("cmd", r"node_modules\.bin\vitest.cmd run", "vitest"),
    ("cmd", r".VENV\SCRIPTS\PYTEST.EXE --cov", "pytest"),
    ("cmd", "env CI=1 npx vitest run", "vitest"),
    ("sh", r"'.venv\Scripts\pytest.exe' --cov", "pytest"),
    ("sh", r"'node_modules\.bin\vitest.cmd' run", "vitest"),
    ("sh", ".venv/bin/pytest --cov", "pytest"),
    ("sh", "node_modules/.bin/vitest run", "vitest"),
    ("sh", "CI=1 npx vitest run", "vitest"),
    ("sh", "env CI=1 npx vitest run", "vitest"),
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command, row", COMMAND_ROWS)
def test_the_command_names_its_row_on_both_dialects(command, row, dialect):
    found = infer(command, dialect=dialect)

    assert (found.name, found.source, found.script) == (row, "command", None)
    assert found.spelled


@pytest.mark.parametrize("dialect, command, row", DIALECT_ROWS)
def test_a_path_or_assignment_reads_as_its_dialect_reads_it(dialect, command, row):
    assert named(command, dialect) == (row, "command")


def test_letter_case_is_folded_on_windows_only():
    assert named("PYTEST --cov", "cmd") == ("pytest", "command")
    assert named("PYTEST --cov", "sh") == (None, None)


@pytest.mark.parametrize("host", [PurePosixPath, PureWindowsPath], ids=["posix-host", "windows-host"])
@pytest.mark.parametrize("dialect, command", [
    ("cmd", r".venv\Scripts\python.exe -m pytest --cov"),
    ("sh", r"'.venv\Scripts\python.exe' -m pytest --cov"),
    ("sh", ".venv/bin/python -m pytest --cov"),
])
def test_a_python_path_heading_the_step_reads_alike_on_every_host(monkeypatch, host, dialect,
                                                                   command):
    r"""A Linux host read `.venv\Scripts\python.exe` as one name, no python's,
    so the cmd.exe lane init writes for `{python:.venv}` named no runner there
    while Windows named pytest. The head's last part is read on `/` and `\`
    alike, whatever path rules the host's PurePath follows."""
    monkeypatch.setattr(lane_command, "PurePath", host)

    assert named(command, dialect) == ("pytest", "command")


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command", [
    "env CI=1",          # assignments, then no command
    "python --version",  # a python that runs no module
    "coverage run",      # coverage given nothing to run
    "node",              # a runtime given no script
    "bun",
])
def test_a_step_that_runs_nothing_names_no_runner(command, dialect):
    assert infer(command, dialect=dialect) == Inferred(None, None)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_words_found_are_kept_as_written(dialect):
    assert infer("npx vitest run", dialect=dialect).words == ("vitest",)
    assert infer("cargo llvm-cov --lcov", dialect=dialect).words == ("cargo llvm-cov",)


# --- the script-stem rule ---------------------------------------------------------

# A pnpm monorepo's lane shape as a command line: a wrapper script runs vitest.
MONOREPO_UNIT = ("pnpm exec node scripts/run-vitest.mjs run --config "
                 "test/vitest/vitest.unit.config.ts --coverage --coverage.reporter=json "
                 "--coverage.reportsDirectory=.crapkit/cov/unit --coverage.reportOnFailure "
                 "--coverage.exclude=**/node_modules/** --coverage.exclude=**/dist/** "
                 "--coverage.exclude=test/** --coverage.exclude=ui/** --coverage.exclude=apps/** "
                 "--coverage.exclude=**/*.test.ts --reporter=default --reporter=junit "
                 "--outputFile.junit=.crapkit/junit/unit.xml --pool=threads")


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command, row", [
    (MONOREPO_UNIT, "vitest"),
    ("node scripts/run_vitest.mjs", "vitest"),
    ("node scripts/run-jest.js --ci", "jest"),
    ("bun run scripts/run-vitest.ts", "vitest"),
    ("bun scripts/run-vitest.ts", "vitest"),
    ("deno run -A scripts/run-vitest.ts", "vitest"),
])
def test_a_runner_word_in_the_stem_of_the_script_a_runtime_runs_spells_it(command, row, dialect):
    found = infer(command, dialect=dialect)

    assert (found.name, found.source) == (row, "command")
    assert found.spelled


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_dot_does_not_separate_the_stem(dialect):
    assert infer("node scripts/run.vitest.mjs", dialect=dialect) == Inferred(None, None)


# --- step 2: the package.json script a script runner names -------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
def test_inits_quickstart_lane_names_vitest_from_its_test_script(dialect):
    """mission-9-03's premise: the lane init writes for a package with a test script."""
    found = infer("npm run test -- --coverage --coverage.reportsDirectory=.crapkit/cov/js",
                  cwd_package=package({"test": "vitest run"}), dialect=dialect)

    assert found == Inferred("vitest", "script", ("vitest",), "test")
    assert found.spelled


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command, scripts", [
    ("pnpm test", {"test": "jest --coverage"}),
    ("yarn cov", {"cov": "jest --coverage"}),
    ("yarn run cov", {"cov": "npx jest"}),
    ("npm test", {"test": "jest"}),
    ("npm run-script cov", {"cov": "jest"}),
    ("pnpm run cov", {"cov": "jest"}),
    ("bun run cov", {"cov": "jest"}),
])
def test_a_script_runner_is_followed_into_the_script_it_names(command, scripts, dialect):
    assert named(command, dialect, cwd_package=package(scripts)) == ("jest", "script")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_script_calling_another_script_is_followed(dialect):
    found = infer("npm test", cwd_package=package({"test": "npm run test:unit",
                                                    "test:unit": "vitest run"}), dialect=dialect)

    assert found == Inferred("vitest", "script", ("vitest",), "test:unit")


def _chain(levels: int) -> dict:
    """s1 runs s2 ... and the last one runs vitest."""
    scripts = {f"s{n}": f"npm run s{n + 1}" for n in range(1, levels)}
    return {**scripts, f"s{levels}": "vitest run"}


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_walk_goes_three_scripts_deep_and_stops_at_the_fourth(dialect):
    assert named("npm run s1", dialect, cwd_package=package(_chain(3))) == ("vitest", "script")
    assert named("npm run s1", dialect, cwd_package=package(_chain(4))) == (None, None)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_cycle_stops_the_walk_with_unknown(dialect):
    scripts = {"a": "npm run b", "b": "npm run a"}

    assert infer("npm run a", cwd_package=package(scripts), dialect=dialect) == Inferred(None, None)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_root_package_answers_when_the_lane_has_none_of_its_own(dialect):
    assert named("npm test", dialect, root_package=package({"test": "jest"})) == ("jest", "script")


@pytest.mark.parametrize("dialect", DIALECTS)
def test_pnpm_names_a_script_by_its_bare_name_and_runs_a_binary_otherwise(dialect):
    assert named("pnpm cov", dialect, cwd_package=package({"cov": "vitest run --coverage"})) == \
        ("vitest", "script")
    assert named("pnpm vitest run", dialect, cwd_package=package({})) == ("vitest", "command")


# --- step 3: devDependencies -------------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command, scripts", [
    ("npm test", {"test": "node test.js"}),
    ("make cov", {}),
    ("npm run test:cov -- --coverage", {"test:cov": "node test.js"}),
])
def test_devdependencies_name_the_row_when_nothing_spells_one(command, scripts, dialect):
    found = infer(command, cwd_package=package(scripts, "vitest"), dialect=dialect)

    assert (found.name, found.source, found.spelled) == ("vitest", "package.json", False)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_two_runners_in_devdependencies_name_none(dialect):
    assert infer("npm test", cwd_package=package({"test": "node t.js"}, "vitest", "jest"),
                 dialect=dialect) == Inferred(None, None)


@pytest.mark.parametrize("dialect", DIALECTS)
def test_npm_test_whose_script_and_devdependencies_name_no_runner_is_unknown(dialect):
    """The release's own proof: doctor prints 'unknown' for an `npm test` lane."""
    found = infer("npm test", cwd_package=package({"test": "node test.js"}, "typescript"),
                  dialect=dialect)

    assert found == Inferred(None, None)
    assert not found.spelled


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_q81_rows(dialect):
    make = infer("make cov", cwd_package=package({}, "vitest"), dialect=dialect)
    pnpm = infer("pnpm cov", cwd_package=package({"cov": "vitest run --coverage"}), dialect=dialect)

    assert (make.name, make.source, make.spelled) == ("vitest", "package.json", False)
    assert (pnpm.name, pnpm.source, pnpm.spelled) == ("vitest", "script", True)


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command", ["make cov", "just test", "tox -e py311", "nox -s tests"])
def test_make_just_tox_and_nox_are_opaque(command, dialect):
    assert infer(command, dialect=dialect) == Inferred(None, None)


# --- two runners ---------------------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
def test_two_runners_in_two_segments_name_none_and_keep_both_words(dialect):
    found = infer("npx vitest run && python -m pytest --cov", dialect=dialect)

    assert found == Inferred(None, None, ("vitest", "pytest"))


@pytest.mark.parametrize("dialect", DIALECTS)
def test_a_runner_in_the_command_and_another_in_its_script_name_none(dialect):
    found = infer("npm test && npx jest", cwd_package=package({"test": "vitest run"}),
                  dialect=dialect)

    assert (found.name, found.source, set(found.words)) == (None, None, {"vitest", "jest"})


# --- check 2: real inputs ------------------------------------------------------------------

# Command lines from one pnpm monorepo's crapkit.toml, one per lane shape it holds.
MONOREPO_LANES = [
    (MONOREPO_UNIT, "vitest"),
    ("pnpm --dir ui exec vitest run --project unit --project unit-node --coverage "
     "--coverage.reporter=json --coverage.reportOnFailure --reporter=default --reporter=junit "
     "--outputFile.junit=crapkit-junit-ui.xml", "vitest"),
    ("python -m pytest --cov --cov-branch --cov-report=json:coverage-py.json "
     "--junitxml=crapkit-junit.xml", "pytest"),
]


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("command, row", MONOREPO_LANES)
def test_a_monorepos_lanes_name_their_runner_from_the_command(command, row, dialect):
    assert named(command, dialect) == (row, "command")


# The deploy templates' package.json files (tests/deploy/kit/repos.py) and the
# interpreters init binds the pytest lane to, each with the answer by hand.
TS_VITEST = {"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^2.0.0"}}
JEST = {"scripts": {"test": "jest"}, "devDependencies": {"jest": "^29", "jest-junit": "^16"}}
VITEST_NO_SCRIPT = {"devDependencies": {"vitest": "^2.0.0"}}


@pytest.mark.parametrize("data, answer", [
    (TS_VITEST, ("vitest", "script")),
    (JEST, ("jest", "script")),
    (VITEST_NO_SCRIPT, ("vitest", "command")),
], ids=["ts-vitest", "jest", "vitest-no-script"])
def test_the_js_lane_init_writes_names_its_runner(data, answer):
    (lane,) = detect_lanes(frozenset(), npm_package(data))
    root = npm_package(data)

    assert named(lane.command, "sh", cwd_package=root, root_package=root) == answer


@pytest.mark.parametrize("dialect, interpreter", [
    ("sh", "{python}"), ("cmd", "{python}"),
    ("sh", "{python:.venv}"), ("cmd", "{python:.venv}"),
    ("sh", "uv run {python}"), ("cmd", "poetry run {python}"),
    ("sh", "pipenv run {python}"), ("cmd", "pdm run {python}"),
])
def test_the_pytest_lane_init_writes_names_pytest(dialect, interpreter):
    (lane,) = detect_lanes(frozenset({"pyproject.toml"}), None, interpreter=interpreter)
    command = expand_launchers(lane.command, windows=dialect == "cmd")

    assert named(command, dialect) == ("pytest", "command")


# --- pytest_step asks the table --------------------------------------------------------


@pytest.mark.parametrize("command, cmd, step", [
    # missed on the merge base (`endswith("pytest")`), found now
    (r".venv\Scripts\pytest.exe --cov", True, [r".venv\Scripts\pytest.exe", "--cov"]),
    (r"node_modules\x\pytest.cmd --cov", True, [r"node_modules\x\pytest.cmd", "--cov"]),
    # found on the merge base, not a pytest spelling now
    ("run-mypytest --cov", False, []),
    ("python tools/mypytest --cov", False, []),
])
def test_pytest_step_reads_pytest_by_the_tables_spellings(command, cmd, step):
    assert pytest_step(command, cmd=cmd) == step


def test_every_row_name_answers_from_a_command_that_spells_it():
    """No row the table holds is out of infer's reach."""
    spelled = {words[0] if len(words) == 1 else " ".join(words)
               for row in TOOLCHAINS.values() for words in row.words}
    commands = {"bun": "bun test", "deno": "deno test"}

    assert {infer(commands.get(word, word), dialect="sh").name for word in spelled} == \
        set(TOOLCHAINS)


@pytest.mark.parametrize("first, second", [("crapkit.toolchain", "crapkit.lane_command"),
                                           ("crapkit.lane_command", "crapkit.toolchain")])
def test_the_two_modules_import_in_either_order(first, second):
    done = subprocess.run([sys.executable, "-c", f"import {first}, {second}"],
                          capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
