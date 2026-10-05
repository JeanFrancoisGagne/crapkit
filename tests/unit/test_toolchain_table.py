"""The toolchain table holds every runner fact init writes, one row per runner.

Seven dicts private to scaffold.py held these facts until 0.9.0, keyed by
runner name one fact at a time. The expected values below are copied from those
dicts as they stood at the merge base, never computed from the table, so a row
that drifts from what init used to write fails here before init's goldens move.
"""
import subprocess
import sys

from crapkit import toolchain
from crapkit.toolchain import TOOLCHAINS, Toolchain

# The runners mission-1 names, by the row name every reader keys on.
MISSION_1 = ("pytest", "vitest", "jest", "bun", "deno", "cargo llvm-cov", "go test", "c8")


def test_every_row_is_keyed_by_its_own_unique_name():
    assert all(isinstance(row, Toolchain) for row in TOOLCHAINS.values())
    assert len(TOOLCHAINS) == len(toolchain._ROWS), "two rows share a name"
    assert [row.name for row in TOOLCHAINS.values()] == list(TOOLCHAINS)
    assert sorted(TOOLCHAINS) == sorted(MISSION_1)


def test_every_row_has_a_spelling_and_no_spelling_names_two_rows():
    spellings = [words for row in TOOLCHAINS.values() for words in row.words]

    assert all(row.words for row in TOOLCHAINS.values())
    assert all(words and all(words) for words in spellings)
    assert len(spellings) == len(set(spellings))


def test_two_word_runners_spell_themselves_in_two_tokens():
    assert TOOLCHAINS["cargo llvm-cov"].words == (("cargo", "llvm-cov"),)
    assert TOOLCHAINS["go test"].words == (("go", "test"),)
    assert all(len(words) == 1 for name, row in TOOLCHAINS.items()
               if name not in ("cargo llvm-cov", "go test") for words in row.words)


def test_the_jest_row_carries_what_the_seven_dicts_held_for_jest():
    jest = TOOLCHAINS["jest"]

    assert jest.dev_dependency == "jest"
    assert jest.init_command == "npx jest --coverage"
    assert jest.reports_dir_flag == "--coverageDirectory="
    assert jest.junit_flags == "--reporters=default --reporters=jest-junit"
    assert jest.extra_flags == ""
    assert jest.junit_package == "jest-junit"
    assert jest.junit_env == (("JEST_JUNIT_OUTPUT_DIR", "{cov}"),
                              ("JEST_JUNIT_OUTPUT_NAME", "junit.xml"))
    assert jest.related_tests == "npx jest --findRelatedTests {files}"


def test_the_vitest_row_carries_what_the_seven_dicts_held_for_vitest():
    vitest = TOOLCHAINS["vitest"]

    assert vitest.dev_dependency == "vitest"
    assert vitest.init_command == "npx vitest run --coverage"
    assert vitest.reports_dir_flag == "--coverage.reportsDirectory="
    assert vitest.junit_flags == ("--reporter=default --reporter=junit "
                                  "--outputFile={cov}/junit.xml")
    assert vitest.extra_flags == " --coverage.reportOnFailure"
    assert vitest.junit_package is None
    assert vitest.junit_env == ()
    assert vitest.related_tests == "npx vitest related --run {files}"


def test_the_pytest_row_carries_the_flags_the_pytest_lane_wrote():
    """`_pytest_lane` wrote `{python} -m pytest --cov --cov-branch
    --cov-report=json:<artifact> --junitxml=<results>
    --continue-on-collection-errors` at the merge base."""
    pytest_row = TOOLCHAINS["pytest"]

    assert pytest_row.dev_dependency is None
    assert pytest_row.init_command == "{python} -m pytest --cov --cov-branch"
    assert pytest_row.reports_dir_flag == "--cov-report=json:"
    assert pytest_row.junit_flags == "--junitxml={cov}/junit-py.xml"
    assert pytest_row.extra_flags == " --continue-on-collection-errors"
    assert pytest_row.junit_package is None
    assert pytest_row.junit_env == ()


def test_the_rows_without_facts_yet_carry_their_spelling_alone():
    for name in ("bun", "deno", "cargo llvm-cov", "go test", "c8"):
        row = TOOLCHAINS[name]
        assert row == Toolchain(name, row.words), name


def test_the_table_loads_without_any_cli_module():
    probe = ("import sys, crapkit.toolchain; "
             "print(sorted(m for m in sys.modules if m.startswith('crapkit.cli')))")

    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, encoding="utf-8", check=True)

    assert out.stdout.strip() == "[]"
