"""The toolchain table holds every runner fact init writes, one row per runner.

Seven dicts private to scaffold.py held these facts until 0.9.0, keyed by
runner name one fact at a time. The expected values below are copied from those
dicts as they stood at the merge base, never computed from the table, so a row
that drifts from what init used to write fails here before init's goldens move.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit import toolchain
from crapkit.toolchain import TOOLCHAINS, Toolchain

ROOT = Path(__file__).resolve().parents[2]
LCOV = ROOT / "tests" / "fixtures" / "coverage" / "lcov"
COBERTURA = ROOT / "tests" / "fixtures" / "coverage" / "cobertura"
JACOCO = ROOT / "tests" / "fixtures" / "coverage" / "jacoco"

# The runners mission-1 names, by the row name every reader keys on.
MISSION_1 = ("pytest", "vitest", "jest", "bun", "deno", "cargo llvm-cov", "gcovr", "gradle", "maven",
             "go test", "c8")


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


def test_the_row_without_facts_yet_carries_its_spelling_alone():
    row = TOOLCHAINS["go test"]

    assert row == Toolchain("go test", row.words)


def test_every_format_a_row_names_is_a_registered_parser():
    """toolchain.py imports nothing from coverage_format, so this test is what
    keeps a row from naming a parser crapkit does not have."""
    from crapkit.coverage_format import _FORMATS

    assert {fmt for row in TOOLCHAINS.values() for fmt in row.formats} <= set(_FORMATS)


def test_every_stanza_row_has_markers_languages_and_the_stanzas_parser():
    stanzas = [row for row in TOOLCHAINS.values() if row.stanza]

    assert [row.name for row in stanzas] == ["bun", "deno", "cargo llvm-cov", "gcovr", "gradle",
                                             "maven"]
    for row in stanzas:
        assert row.markers and row.languages and row.formats, row.name
        assert row.stanza.parser in row.formats, row.name
    assert len({row.stanza.name for row in stanzas}) == len(stanzas)
    assert all(row.stanza for row in TOOLCHAINS.values() if row.markers)


@pytest.mark.parametrize("name, languages, formats, markers", [
    ("pytest", ("python",), ("coveragepy",), ()),
    ("vitest", ("javascript", "typescript", "tsx", "vue"), ("istanbul",), ()),
    ("jest", ("javascript", "typescript", "tsx"), ("istanbul",), ()),
    ("bun", ("javascript", "typescript", "tsx"), ("lcov",), ("bun.lock", "bun.lockb", "bunfig.toml")),
    ("deno", ("javascript", "typescript", "tsx"), ("lcov",), ("deno.json", "deno.jsonc")),
    ("cargo llvm-cov", ("rust",), ("lcov",), ("Cargo.toml",)),
    ("gcovr", ("cpp",), ("cobertura",), ("CMakeLists.txt",)),
    ("gradle", ("java",), ("jacoco",), ("build.gradle", "build.gradle.kts")),
    ("maven", ("java",), ("jacoco",), ("pom.xml",)),
    ("c8", ("javascript", "typescript", "tsx"), ("istanbul", "lcov"), ()),
])
def test_each_row_carries_its_languages_formats_and_markers(name, languages, formats, markers):
    row = TOOLCHAINS[name]

    assert (row.languages, row.formats, row.markers) == (languages, formats, markers)


def test_the_stanza_commands_are_the_ones_the_fixture_readme_records():
    """init never prints a command no run wrote: each stanza command is the one
    tests/fixtures/coverage/lcov/, cobertura/ or jacoco/README.md records for
    its fixture."""
    readme = "".join((folder / "README.md").read_text(encoding="utf-8")
                     for folder in (LCOV, COBERTURA, JACOCO))
    recorded = dict(re.findall(r"^- Stanza `([^`]+)`: `([^`]+)`", readme, re.M))
    stanzas = {row.stanza.name: row.stanza.command for row in TOOLCHAINS.values() if row.stanza}

    assert recorded == stanzas


@pytest.mark.parametrize("recording, files", [
    ("bun-1.4.4.lcov", ["harness/drive.mjs", "js/shapes.js", "ts/shapes.ts"]),
    ("deno-2.9.8.lcov", ["harness/drive.mjs", "js/shapes.js"]),
    ("cargo-llvm-cov-0.9.1-stanza.lcov", ["rust/drive.rs", "rust/shapes.rs"]),
])
def test_each_stanza_recording_reads_through_the_lcov_reader(recording, files, tmp_path):
    from crapkit.config import Lane
    from crapkit.coverage_format import lane_format

    artifact = tmp_path / recording
    artifact.write_bytes((LCOV / recording).read_bytes())
    lane = Lane(name="js", command="true", artifact=recording, parser="lcov", scopes=())
    unplaced: dict = {}

    functions, evidence, _ = lane_format(lane).read(lane, tmp_path, artifact, unplaced=unplaced)

    assert sorted(evidence) == files
    assert unplaced == {}
    assert all(evidence[path].hit_lines for path in files)


def test_the_gcovr_stanza_recording_reads_through_the_cobertura_reader(tmp_path):
    from crapkit.config import Lane
    from crapkit.coverage_format import lane_format

    artifact = tmp_path / "cpp.xml"
    artifact.write_bytes((COBERTURA / "gcovr-7.2-stanza.xml").read_bytes())
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "shapes.cpp").write_text("", encoding="utf-8")
    lane = Lane(name="cpp", command="true", artifact="cpp.xml", parser="cobertura", scopes=())
    unplaced: dict = {}

    _, evidence, _ = lane_format(lane).read(lane, tmp_path, artifact, unplaced=unplaced)

    assert "src/shapes.cpp" in evidence
    assert unplaced == {}
    assert evidence["src/shapes.cpp"].hit_lines and evidence["src/shapes.cpp"].missed_lines


def test_the_gcovr_stanza_is_the_ticket_spelling():
    stanza = TOOLCHAINS["gcovr"].stanza

    assert TOOLCHAINS["gcovr"].words == (("gcovr",),)
    assert stanza.command.endswith("gcovr --root . --cobertura .crapkit/cov/cpp.xml")
    assert "ctest" in stanza.command and "--coverage" in stanza.command
    assert (stanza.name, stanza.artifact, stanza.parser) == ("cpp", ".crapkit/cov/cpp.xml",
                                                            "cobertura")


def test_the_gradle_and_maven_rows_are_the_ticket_spelling():
    gradle, maven = TOOLCHAINS["gradle"], TOOLCHAINS["maven"]

    assert gradle.words == (("gradle",), ("gradlew",))
    assert maven.words == (("mvn",), ("mvnw",))
    assert gradle.stanza.wrapper == "gradlew"
    assert gradle.stanza.wrapped == ("./gradlew test jacocoTestReport",
                                     "gradlew test jacocoTestReport")
    assert any("xml.required = true" in note for note in gradle.stanza.notes)
    assert (gradle.stanza.artifact, maven.stanza.artifact) == (
        "build/reports/jacoco/test/jacocoTestReport.xml", "target/site/jacoco/jacoco.xml")
    assert gradle.stanza.path_prefix == maven.stanza.path_prefix == "src/main/java"
    assert maven.stanza.command == ("mvn org.jacoco:jacoco-maven-plugin:0.8.12:prepare-agent test "
                                    "org.jacoco:jacoco-maven-plugin:0.8.12:report")


@pytest.mark.parametrize("markers, cmd, command", [
    (frozenset({"build.gradle"}), True, "gradle test jacocoTestReport"),
    (frozenset({"build.gradle", "gradlew"}), False, "./gradlew test jacocoTestReport"),
    (frozenset({"build.gradle", "gradlew"}), True, "gradlew test jacocoTestReport"),
])
def test_the_gradle_stanza_runs_through_a_root_gradlew_spelled_for_the_shell(markers, cmd,
                                                                            command):
    assert TOOLCHAINS["gradle"].stanza.resolved(markers, cmd).command == command
    assert "gradlew" in toolchain.MARKERS


@pytest.mark.parametrize("recording", ["gradle-8.10.2.xml", "maven-3.9.9.xml"])
def test_each_java_stanza_recording_reads_through_the_jacoco_reader(recording, tmp_path):
    from crapkit.config import Lane
    from crapkit.coverage_format import lane_format

    artifact = tmp_path / recording
    artifact.write_bytes((JACOCO / recording).read_bytes())
    lane = Lane(name="java", command="true", artifact=recording, parser="jacoco", scopes=(),
                path_prefix="src/main/java")

    _, evidence, _ = lane_format(lane).read(lane, tmp_path, artifact)

    assert sorted(evidence) == ["src/main/java/Greeter.java", "src/main/java/com/acme/Shapes.java"]


def test_the_rust_stanza_is_the_ticket_spelling():
    stanza = TOOLCHAINS["cargo llvm-cov"].stanza

    assert stanza == toolchain.Stanza("rust", "cargo llvm-cov --lcov --output-path "
                                              ".crapkit/cov/rust.lcov",
                                      ".crapkit/cov/rust.lcov", "lcov")


def test_the_measurable_languages_and_the_live_lane_languages():
    from crapkit.scaffold import LIVE_LANE_LANGUAGES

    assert toolchain.measurable_languages() == frozenset(
        {"python", "javascript", "typescript", "tsx", "vue", "rust", "cpp", "java"})
    assert LIVE_LANE_LANGUAGES == frozenset({"python", "javascript", "typescript", "tsx", "vue"})


def test_readers_name_each_parser_and_toolchain_for_a_language():
    assert toolchain.readers("rust") == (("lcov", "cargo llvm-cov"),)
    assert toolchain.readers("cpp") == (("cobertura", "gcovr"),)
    assert toolchain.readers("java") == (("jacoco", "gradle"), ("jacoco", "maven"))
    assert toolchain.readers("zig") == ()


def test_the_table_loads_without_any_cli_module():
    probe = ("import sys, crapkit.toolchain; "
             "print(sorted(m for m in sys.modules if m.startswith('crapkit.cli')))")

    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, encoding="utf-8", check=True)

    assert out.stdout.strip() == "[]"
