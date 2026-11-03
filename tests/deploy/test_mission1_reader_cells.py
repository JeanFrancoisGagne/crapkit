"""Mission 1's readers under every harness: an lcov lane gives a Rust repo, and a
Cobertura lane a C++ repo, coverage-based CRAP.

The lcov cells (row `m1-readers-03`) measure a Rust crate whose lane copies a
committed tracefile into place: the crate and the tracefile cargo-llvm-cov
0.9.1 wrote for it, from tests/fixtures/coverage/lcov. Each runs fresh and
upgrade under each harness tools/deploy/pins.toml pins, in that harness's
image, and asks crapkit's MCP server what an agent in that harness's shell
gets: get_next_item and list_worklist name `untouched` with cov 0 and the
add-tests remedy. The row's nightly = "core" keeps the 3 core-image harnesses
nightly, and the release candidate runs all 17 (MAP.toml [every_harness]).

Fresh: the candidate installed as the README's 60-second start installs it.
Upgrade: crapkit N-1 measures the crate cc-only (it reads no lcov), the
guide's pip row upgrades it, and the config gains the lane.

Cell `lin-up-0.8.1-cc-only-rust` (no harness): 0.8.1 seeds a cc-only Rust
scope, the guide's pip row upgrades it, and verify passes with no conflict,
since a new reader moves no existing score. Once the config adds the lcov lane,
verify refuses with the one flip conflict naming the scope and its reseed.

The cobertura cells (row `m1-readers-05`) do the same over the C++ CMake repo
of tests/fixtures/coverage/cobertura and the report gcovr 7.2 wrote for it:
fresh and upgrade under every harness, `lin-up-0.8.1-cc-only-cpp` for the
0.8.1 cc-only upgrade and its flip, and `win-cobertura-absolute-source` on
Windows for a report whose <source> is the checkout in a lower-case drive
spelling, then through a junction.

The jacoco cells (row `m1-readers-06`) do the same over the Java repo of
tests/fixtures/coverage/jacoco and the report Gradle 8.10.2 wrote for it:
fresh and upgrade under every harness, and `lin-up-0.8.1-cc-only-java` for the
0.8.1 cc-only upgrade and its flip.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kit import profiles, state, wheels
from kit.cells import cell
from kit.mcp_client import McpClient
from kit.state import output

PACKET = "m1-readers-03"
ROW = "m1-readers-03"
HARNESSES = profiles.pins()["harness"]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
PIP = "pip in the active environment"
USE_CASES = "coverage, lcov lane, list_worklist, get_next_item"
OLD = state.source_version("0.8.1")

SCOPE = '[crapkit]\ntarget = 5\n\n[[scope]]\nname = "rust"\npaths = ["rust"]\nlanguages = ["rust"]\n{optional}\n'
LANE = ('[exclude]\nglobs = ["make_lcov.py"]\n\n'
        '[[lane]]\nname = "rust"\ncommand = "python make_lcov.py"\n'
        'artifact = ".crapkit/cov/lcov.info"\nparser = "lcov"\nscopes = ["rust"]\n')
CC_ONLY = SCOPE.format(optional="coverage_optional = true\n")
MEASURED = SCOPE.format(optional="") + LANE
# The lane's command: the committed tracefile copied to the artifact path.
MAKE_LCOV = ("from pathlib import Path\n"
             "Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
             "Path('.crapkit/cov/lcov.info').write_bytes(Path('tracefile.lcov').read_bytes())\n")
# A ccn-8 function: over the ceiling cc-only, so a 0.8.1 seed marks it.
ROUTE = "pub fn route(code: i32) -> i32 {\n" + "".join(
    f"    if code == {n} {{\n        return {n};\n    }}\n" for n in range(1, 8)) + "    0\n}\n"


def _marks(decorator) -> list:
    """The marks a decorator puts on a test. A lambda would not do: pytest
    reads one as a mark argument, not as the test."""
    def test():
        pass
    return decorator(test).pytestmark


def _harness_cells(prefix: str, scenario: str, row: str = ROW, use_cases: str = USE_CASES) -> list:
    """One parameter per pinned harness, each marked by @cell as a cell of the
    reader's row (the lcov one by default), in the image pins.toml runs that harness in."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv", harness=key, scenario=scenario, use_cases=use_cases,
        os="linux", image=spec["image"], cadence="nightly", row=row, packet=row)))
        for key, spec in sorted(HARNESSES.items())]


def _crate(box, config: str, extra: dict | None = None) -> Path:
    """The fixture crate under rust/, its committed tracefile and `config`,
    in a git repo."""
    repo = box.root / "repo"
    files = {f"rust/{name}": (FIXTURE / "rust" / name).read_text(encoding="utf-8")
             for name in ("Cargo.toml", "drive.rs", "shapes.rs")}
    files.update({"tracefile.lcov": (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8"),
                  "make_lcov.py": MAKE_LCOV, ".gitignore": ".crapkit/\n", **(extra or {})})
    (repo / "rust").mkdir(parents=True)
    state.write(repo, files)
    _configure(repo, config)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a rust crate")
    return repo


def _configure(repo: Path, config: str) -> None:
    """crapkit.toml as `config`, a lane keyed for the container it runs in."""
    state.write(repo, {"crapkit.toml": config})
    if profiles.in_container():
        profiles.allow_container_lane(repo)


def _in_the_harness_shell(box, key: str) -> None:
    """The pinned harness CLIs on PATH, as their user has them; a harness with
    a command of its own must resolve to it."""
    profiles.add_harnesses(box)
    command = HARNESSES[key].get("command")
    assert command is None or box.which(command), f"{key}: {command} is not on PATH: {box.env['PATH']}"


def assert_the_agent_gets_cov_and_add_tests(box, repo: Path, path: str = "rust/shapes.rs",
                                            handle: str = "untouched") -> None:
    """crapkit coverage from the shell exits 0, and over MCP stdio
    get_next_item and list_worklist name `handle` at cov 0, add-tests."""
    box.script("crapkit coverage", shell="sh", cwd=repo, expect=0)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        item = client.call("get_next_item", {})
        worklist = client.call("list_worklist", {"top": 50})

    assert not item["isError"] and not worklist["isError"], (item, worklist)
    picked = json.loads(item["content"][0]["text"])["item"]
    rows = {row["handle"]: row for row in json.loads(worklist["content"][0]["text"])["active"]}
    for row in (picked, rows[handle]):
        assert (row["path"], row["handle"], row["cov"], row["remedy"]) == (
            path, handle, 0.0, "add-tests"), box.transcript.text()


@pytest.mark.parametrize("key", _harness_cells(
    "lin-lcov", "fresh: README pip start; a Rust crate's lcov lane (cargo-llvm-cov 0.9.1 tracefile) measures "
                "it, and get_next_item and list_worklist show cov 0 and add-tests on untouched"))
def test_lcov_lane_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _crate(box, MEASURED)
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo)


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-lcov", "upgrade from N-1 by the guide's pip row: N-1 scores the crate cc-only, the config gains "
                   "an lcov lane, and get_next_item and list_worklist show cov 0 and add-tests"))
def test_lcov_lane_upgrade(box, candidate, record_property, key):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _crate(box, CC_ONLY)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    _configure(repo, MEASURED)
    profiles.commit(box, repo, "the rust scope gains an lcov lane")
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo)


@cell("lin-up-0.8.1-cc-only-rust", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 by the guide's pip row: a seeded cc-only Rust scope verifies with no "
               "conflict; once the config adds an lcov lane, verify names the flip once, exit 3",
      use_cases="upgrade guide, ratchet lifecycle, lcov lane", os="linux", image="core", cadence="nightly")
def test_lin_up_0_8_1_cc_only_rust(box, candidate):
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={OLD}")
    repo = _crate(box, CC_ONLY, {"rust/route.rs": ROUTE})
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seeded cc-only under 0.8.1")

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.verify(box, repo)

    _configure(repo, MEASURED)
    state.commit(box, repo, "the rust scope gains an lcov lane")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    refusal = output(box.run(["crapkit", "verify"], cwd=repo, expect=3))

    assert "rust was cc-only and is now measured by its lcov lane(s)" in refusal, box.transcript.text()
    assert refusal.count("ratchet seed --scope rust") == 1, box.transcript.text()


# --- m1-readers-05: a Cobertura lane over a C++ CMake repo --------------------------------------

COBERTURA_ROW = "m1-readers-05"
COBERTURA = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "cobertura"
CPP_SOURCES = ("CMakeLists.txt", "src/shapes.cpp", "src/shapes.h", "tests/test_shapes.cpp")
COBERTURA_USE_CASES = "coverage, cobertura lane, list_worklist, get_next_item"
CPP_SCOPE = '[crapkit]\ntarget = {target}\n\n[[scope]]\nname = "cpp"\npaths = ["src"]\nlanguages = ["cpp"]\n{optional}\n'
CPP_LANE = ('[exclude]\nglobs = ["make_report.py", "tests/**"]\n\n'
            '[[lane]]\nname = "cpp"\ncommand = "python make_report.py"\n'
            'artifact = ".crapkit/cov/cpp.xml"\nparser = "cobertura"\nscopes = ["cpp"]\n')
# grade's ccn is 7: under a ceiling of 8 it owes tests, not a split, so its
# remedy is add-tests; under 5 the cc-only seed marks it.
CPP_MEASURED = CPP_SCOPE.format(target=8, optional="") + CPP_LANE
CPP_CC_ONLY_5 = CPP_SCOPE.format(target=5, optional="coverage_optional = true\n")
CPP_MEASURED_5 = CPP_SCOPE.format(target=5, optional="") + CPP_LANE
# The lane's command: the committed gcovr report copied to the artifact path.
MAKE_REPORT = ("from pathlib import Path\n"
               "Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
               "Path('.crapkit/cov/cpp.xml').write_bytes(Path('report.xml').read_bytes())\n")


def _report(source: str) -> str:
    """The report gcovr 7.2 wrote through init's stanza, its `.` source respelled."""
    recorded = (COBERTURA / "gcovr-7.2-stanza.xml").read_text(encoding="utf-8")
    return recorded.replace("<source>.</source>", f"<source>{source}</source>")


def _cpp_repo(box, config: str) -> Path:
    """The CMake fixture repo, its committed gcovr report and `config`, in a git repo."""
    repo = box.root / "repo"
    files = {rel: (COBERTURA / "cmake-repo" / rel).read_text(encoding="utf-8") for rel in CPP_SOURCES}
    files.update({"report.xml": _report("."), "make_report.py": MAKE_REPORT, ".gitignore": ".crapkit/\n"})
    state.write(repo, files)
    _configure(repo, config)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a C++ repo")
    return repo


@pytest.mark.parametrize("key", _harness_cells(
    "lin-cobertura", "fresh: README pip start; a C++ CMake repo's cobertura lane (gcovr 7.2 report) "
                     "measures it, and get_next_item and list_worklist show cov 0 and add-tests on grade",
    row=COBERTURA_ROW, use_cases=COBERTURA_USE_CASES))
def test_cobertura_lane_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _cpp_repo(box, CPP_MEASURED)
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo, "src/shapes.cpp", "grade")


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-cobertura", "upgrade from N-1 by the guide's pip row: N-1 scores the C++ repo cc-only, the "
                        "config gains a cobertura lane, and get_next_item and list_worklist show cov 0 "
                        "and add-tests", row=COBERTURA_ROW, use_cases=COBERTURA_USE_CASES))
def test_cobertura_lane_upgrade(box, candidate, record_property, key):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _cpp_repo(box, CPP_SCOPE.format(target=8, optional="coverage_optional = true\n"))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    _configure(repo, CPP_MEASURED)
    profiles.commit(box, repo, "the cpp scope gains a cobertura lane")
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo, "src/shapes.cpp", "grade")


@cell("lin-up-0.8.1-cc-only-cpp", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 by the guide's pip row: a seeded cc-only C++ scope verifies with no "
               "conflict; once the config adds a cobertura lane, verify names the flip once, exit 3",
      use_cases="upgrade guide, ratchet lifecycle, cobertura lane", os="linux", image="core",
      cadence="nightly", packet=COBERTURA_ROW)
def test_lin_up_0_8_1_cc_only_cpp(box, candidate):
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={OLD}")
    repo = _cpp_repo(box, CPP_CC_ONLY_5)
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seeded cc-only under 0.8.1")

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.verify(box, repo)

    _configure(repo, CPP_MEASURED_5)
    state.commit(box, repo, "the cpp scope gains a cobertura lane")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    refusal = output(box.run(["crapkit", "verify"], cwd=repo, expect=3))

    assert "cpp was cc-only and is now measured by its cobertura lane(s)" in refusal, box.transcript.text()
    assert refusal.count("ratchet seed --scope cpp") == 1, box.transcript.text()


def _worklist_cov(box, repo: Path) -> dict:
    """crapkit coverage exits 0; {handle: cov} of worklist's active C++ rows."""
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    ranked = json.loads(box.run(["crapkit", "worklist", "--json"], cwd=repo, expect=0).stdout)
    return {row["handle"]: row["cov"] for row in ranked["active"] if row["path"] == "src/shapes.cpp"}


@cell("win-cobertura-absolute-source", channel="pip venv", harness="none",
      scenario="fresh: a gcovr report whose <source> is the checkout's absolute Windows path, once in a "
               "lower-case drive spelling and once through a junction; coverage exits 0 and worklist "
               "shows cov for the C++ functions",
      use_cases="coverage, cobertura lane, worklist", os="windows", image=None, cadence="nightly",
      packet=COBERTURA_ROW)
def test_win_cobertura_absolute_source(box, templates):
    profiles.session_crapkit(box, templates)
    repo = _cpp_repo(box, CPP_MEASURED)
    link = box.root / "checkout-link"
    box.run(["cmd", "/c", "mklink", "/J", str(link), str(repo)], expect=0)
    spelled = str(repo)
    for source in (spelled[0].lower() + spelled[1:], str(link)):
        state.write(repo, {"report.xml": _report(source)})
        profiles.commit(box, repo, f"the report's source is {source}")

        assert _worklist_cov(box, repo) == {"grade": 0.0}, box.transcript.text()


# --- m1-readers-06: a JaCoCo lane over a Java repo -----------------------------------------------

JACOCO_ROW = "m1-readers-06"
JACOCO = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "jacoco"
JAVA_SOURCES = ("build.gradle", "pom.xml", "src/main/java/Greeter.java",
                "src/main/java/com/acme/Shapes.java", "src/test/java/com/acme/ShapesTest.java")
JACOCO_USE_CASES = "coverage, jacoco lane, list_worklist, get_next_item"
JAVA_SCOPE = ('[crapkit]\ntarget = {target}\n\n[[scope]]\nname = "java"\npaths = ["src/main/java"]\n'
              'languages = ["java"]\n{optional}\n')
JAVA_LANE = ('[exclude]\nglobs = ["make_report.py", "src/test/**"]\n\n'
             '[[lane]]\nname = "java-gradle"\ncommand = "python make_report.py"\n'
             'artifact = "build/reports/jacoco/test/jacocoTestReport.xml"\nparser = "jacoco"\n'
             'path_prefix = "src/main/java"\nscopes = ["java"]\n')
# grade's ccn is 8: under a ceiling of 8 it owes tests, not a split, so its
# remedy is add-tests; under 5 the cc-only seed marks it.
JAVA_MEASURED = JAVA_SCOPE.format(target=8, optional="") + JAVA_LANE
JAVA_CC_ONLY_5 = JAVA_SCOPE.format(target=5, optional="coverage_optional = true\n")
JAVA_MEASURED_5 = JAVA_SCOPE.format(target=5, optional="") + JAVA_LANE
# The lane's command: the committed Gradle report copied to the artifact path.
MAKE_JACOCO = ("from pathlib import Path\n"
               "out = Path('build/reports/jacoco/test')\n"
               "out.mkdir(parents=True, exist_ok=True)\n"
               "(out / 'jacocoTestReport.xml').write_bytes(Path('report.xml').read_bytes())\n")
JAVA_SHAPES = "src/main/java/com/acme/Shapes.java"


def _java_repo(box, config: str) -> Path:
    """The Java fixture repo, its committed Gradle JaCoCo report and `config`, in a git repo."""
    repo = box.root / "repo"
    files = {rel: (JACOCO / "java-repo" / rel).read_text(encoding="utf-8") for rel in JAVA_SOURCES}
    files.update({"report.xml": (JACOCO / "gradle-8.10.2.xml").read_text(encoding="utf-8"),
                  "make_report.py": MAKE_JACOCO, ".gitignore": "build/\n.crapkit/\n"})
    state.write(repo, files)
    _configure(repo, config)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a Java repo")
    return repo


@pytest.mark.parametrize("key", _harness_cells(
    "lin-jacoco", "fresh: README pip start; a Java repo's jacoco lane (a Gradle 8.10.2 JaCoCo report) "
                  "measures it, and get_next_item and list_worklist show cov 0 and add-tests on grade",
    row=JACOCO_ROW, use_cases=JACOCO_USE_CASES))
def test_jacoco_lane_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _java_repo(box, JAVA_MEASURED)
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo, JAVA_SHAPES, "Shapes::grade")


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-jacoco", "upgrade from N-1 by the guide's pip row: N-1 scores the Java repo cc-only, the "
                     "config gains a jacoco lane, and get_next_item and list_worklist show cov 0 "
                     "and add-tests", row=JACOCO_ROW, use_cases=JACOCO_USE_CASES))
def test_jacoco_lane_upgrade(box, candidate, record_property, key):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _java_repo(box, JAVA_SCOPE.format(target=8, optional="coverage_optional = true\n"))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    _configure(repo, JAVA_MEASURED)
    profiles.commit(box, repo, "the java scope gains a jacoco lane")
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo, JAVA_SHAPES, "Shapes::grade")


@cell("lin-up-0.8.1-cc-only-java", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 by the guide's pip row: a seeded cc-only Java scope verifies with no "
               "conflict; once the config adds a jacoco lane, verify names the flip once, exit 3",
      use_cases="upgrade guide, ratchet lifecycle, jacoco lane", os="linux", image="core",
      cadence="nightly", packet=JACOCO_ROW)
def test_lin_up_0_8_1_cc_only_java(box, candidate):
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={OLD}")
    repo = _java_repo(box, JAVA_CC_ONLY_5)
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seeded cc-only under 0.8.1")

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.verify(box, repo)

    _configure(repo, JAVA_MEASURED_5)
    state.commit(box, repo, "the java scope gains a jacoco lane")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    refusal = output(box.run(["crapkit", "verify"], cwd=repo, expect=3))

    assert "java was cc-only and is now measured by its jacoco lane(s)" in refusal, box.transcript.text()
    assert refusal.count("ratchet seed --scope java") == 1, box.transcript.text()
