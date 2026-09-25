"""A new user's first hour: install crapkit the way the README says, then walk
the 60-second start and the two quickstarts on a repo of that shape.

Every command comes from the fence that prints it. Each step asserts its exit
code and the line the user acts on next: the pytest-cov hint init prints, the
container guard's refusal and the docs/lanes.md#containers rule that answers
it, the MISSING DEPENDENCY a vitest repo meets before the README's `npm i -D`
line, the verdict verify prints. A quickstart's printed lines are held to the
page's transcript by shape: numbers and commit ids vary, the words may not.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from kit import docsnip, installers, repos, wheels
from kit.cells import cell
from kit.installers import README, START, said, shape

PACKET = "deploy-channels"
WINDOWS = os.name == "nt"
PYCOV_HINT = re.compile(r"cannot import pytest_cov - run `([^`]+)`")
GUARD = "runs the python suite, which is host-only (container runs OOM)"


# --- the 60-second start -------------------------------------------------------------

@dataclass(frozen=True)
class Expect:
    """What the start prints for one fixture repo: the lane init detects and the
    worklist row its over-ceiling function makes."""
    lane: str = "py"
    row: str = r"calc/grade\.py:\d+\s+grade\( score , attempts , late , bonus \)"


def _init(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert f"detected 1 lane(s) from this repo's own files: {want.lane} - next: run `crapkit coverage`" in said(step)
    hint = PYCOV_HINT.search(said(step))
    if hint:
        box.script(hint[1], cwd=repo, expect=0, note="the command init's note names")
    return step


def _doctor(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert "ok   lizard 1.24.0" in step.stdout
    return step


def _coverage(box, repo, line, want: Expect):
    """A coverage.py lane in a container meets the guard first; the user applies
    the rule the refusal names from docs/lanes.md#containers and reruns."""
    if installers.in_container() and want.lane == "py":
        refused = box.script(line, cwd=repo, expect=5)
        assert GUARD in said(refused) and "set container_ok = true" in said(refused)
        box.transcript.note(f"applied docs/lanes.md#containers: {installers.allow_containers(repo)}")
    step = box.script(line, cwd=repo, expect=0)
    assert "-> next: crapkit worklist" in step.stdout
    return step


def _worklist(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert re.search(want.row, step.stdout), step.stdout
    return step


def _seed(box, repo, line, want: Expect):
    step = box.script(line, cwd=repo, expect=0)
    assert said(step).startswith("crapkit-ratchet.tsv: added 1, tightened 0")
    return step


def _plain(box, repo, line, want: Expect):
    return box.script(line, cwd=repo, expect=0, env=box.commit_env())


START_STEPS = {"crapkit init": _init, "crapkit doctor": _doctor, "crapkit coverage": _coverage,
               "crapkit worklist": _worklist, "crapkit ratchet seed": _seed}


def _step_rule(line: str):
    return next((rule for prefix, rule in START_STEPS.items() if line.startswith(prefix)), _plain)


def readme_start(box, repo: Path, want: Expect = Expect()) -> dict[str, object]:
    """The README's 60-second start after its install line, then the verify its
    prose says establishes the first passing verdict. `cd your-repo` is the
    cell's cwd."""
    steps = {}
    for line in installers.fence_commands(README, START)[1:]:
        if not line.startswith("cd "):
            steps[line] = _step_rule(line)(box, repo, line, want)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    verify = installers.inline(README, START, "crapkit verify")
    steps[verify] = box.script(verify, cwd=repo, expect=0)
    assert said(steps[verify]).startswith("verify OK")
    return steps


def pip_start(box, templates, candidate, python: str) -> dict[str, object]:
    install = installers.pip_venv(box, python)
    assert candidate.version in install.run(box, box.root, "--version").stdout
    repo = repos.checkout(box, "py-pytest", cache=templates)
    return readme_start(box, repo)


@cell("lin-pip-start-py311", channel="pip venv (wheelhouse + candidate)", harness="none (sh + git)",
      scenario="fresh: README 60-second start verbatim; container refusal at coverage, then docs/lanes.md#containers "
               "fix; worklist; seed", use_cases="60-second start, init, doctor, coverage, worklist, ratchet seed",
      os="linux", image="core", cadence="push")
def test_the_readme_start_on_311_meets_the_container_guard_and_its_documented_rule(box, templates, candidate):
    assert installers.in_container(), "a container cell: the guard is its scenario"
    pip_start(box, templates, candidate, "3.11")

    assert "container_ok = true" in (box.root / "py-pytest" / "crapkit.toml").read_text(encoding="utf-8")


@cell("lin-pip-start-py311", channel="pip venv", harness="none (sh + git)",
      scenario="fresh: doctor names the container guard before coverage refuses", use_cases="doctor",
      os="linux", image="core", cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-1: in a container `crapkit doctor` exits 0 "
                                       "with no WARN, then `crapkit coverage` refuses the python lane with exit 5")
def test_doctor_warns_about_the_container_guard_before_coverage_refuses(box, templates, candidate):
    installers.pip_extra(box, "3.11")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    doctor = box.run(["crapkit", "doctor"], cwd=repo, expect=0)
    box.run(["crapkit", "coverage"], cwd=repo, expect=5)

    assert [line for line in doctor.stdout.splitlines() if line.startswith("WARN") and "container_ok" in line]


@cell("lin-pip-start-py314", channel="pip venv", harness="none", scenario="fresh: same start on CPython 3.14",
      use_cases="60-second start", os="linux", image="core", cadence="push")
def test_the_readme_start_runs_on_314(box, templates, candidate):
    steps = pip_start(box, templates, candidate, "3.14")

    assert "3.14" in box.run(["python", "--version"], expect=0).stdout
    assert steps


@cell("lin-native-start", channel="pip venv", harness="none (bare runner)",
      scenario="fresh: lin-pip-start-py311 and Route 1 on a bare runner, no container_ok",
      use_cases="60-second start, commit gate", os="linux", image=None, cadence="push")
def test_the_readme_start_and_route_1_on_a_bare_runner(box, templates, candidate):
    if installers.in_container():
        pytest.skip("a bare-runner cell: `run.py --native` on ubuntu-24.04 runs it (deploy-linux-native)")
    pip_start(box, templates, candidate, "3.11")
    repo = box.root / "py-pytest"
    route_1(box, repo)

    assert "container_ok" not in (repo / "crapkit.toml").read_text(encoding="utf-8")


@cell("win-pip-start", channel="pip venv", harness="none (cmd.exe, PortableGit)",
      scenario="fresh: start verbatim, no container_ok, cp1252 console", use_cases="60-second start",
      os="windows", image=None, cadence="push")
def test_the_readme_start_on_windows_needs_no_container_rule(box, templates, candidate):
    steps = pip_start(box, templates, candidate, "3.12")

    assert "container_ok" not in (box.root / "py-pytest" / "crapkit.toml").read_text(encoding="utf-8")
    assert all(line in steps for line in ("crapkit init", "crapkit coverage", "crapkit ratchet seed"))


# --- the Python quickstart -------------------------------------------------------------

PY_QUICK = "Quickstart: Python"
GRADE_FIXED = '''def _top(score, late):
    return "A" if score > 90 and not late else None


def _high(score, attempts):
    if score <= 80:
        return None
    return "B" if attempts < 3 else "C"


def _low(attempts, late, bonus):
    if bonus:
        return "C"
    return "F" if late and attempts > 2 else "D"


def grade(score, attempts, late, bonus):
    return _top(score, late) or _high(score, attempts) or _low(attempts, late, bonus)


def curve(scores, floor):
    return [max(score, floor) for score in scores]
'''
GRADE_TABLE = '''

import pytest


@pytest.mark.parametrize("args, letter", [
    ((95, 1, True, False), "B"), ((85, 5, False, False), "C"), ((50, 1, False, True), "C"),
    ((50, 3, True, False), "F"), ((50, 1, False, False), "D")])
def test_every_band(args, letter):
    assert grade(*args) == letter
'''


def quickstart_repo(box, templates) -> Path:
    """py-pytest cut to the quickstart's shape: calc/grade.py, tests/test_grade.py
    and a pyproject.toml naming no testpaths, nothing else, so init prints the
    page's own lines and writes the page's own crapkit.toml."""
    repo = repos.checkout(box, "py-pytest", cache=templates)
    pyproject = repo / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace('testpaths = ["tests"]\n', ""),
                         encoding="utf-8")
    box.run(["git", "rm", "-q", "calc/__init__.py", ".gitignore"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "the quickstart's shape"], cwd=repo, env=box.commit_env(), expect=0)
    return repo


def _doc_step(heading: str, *, index: int = 0) -> tuple[str, str]:
    """The first (command, output) pair of the transcript fence in a step."""
    return docsnip.outputs(installers.section_fences(README, heading, index)[0])[0]


def _row(line: str) -> str:
    """A printed line by shape, a row cut after its `path:line`: the page's repo
    names its function classify, the fixture's is grade."""
    return re.sub(r"(\.(py|ts|js):<n>) .*$", r"\1", shape(line))


ROW = re.compile(r"\.(py|ts|js):<n>$")


def _held_to_the_page(step, printed: str) -> None:
    """Each line the page prints under a command, by shape, in what the command
    printed. A table row names a function the page's repo has and the fixture
    may not, so rows count only as present: the page prints some, so must we."""
    mine = {_row(line) for line in said(step).splitlines()}
    wanted = [_row(line) for line in printed.splitlines() if line.strip()]
    assert [line for line in wanted if line not in mine and not ROW.search(line)] == [], said(step)
    assert any(ROW.search(line) for line in mine) or not any(ROW.search(line) for line in wanted), said(step)


def _doctor_lines(text: str) -> list[str]:
    """doctor's lines without the machine's own: worker counts, the lane's interpreter and versions."""
    return [re.sub(r"-> \S+ \(.*\)$", "-> <python> (<versions>)", line) for line in text.splitlines()
            if line.strip() and not line.startswith("resources:")]


def _run_step(box, repo, heading: str, *, index: int = 0) -> list:
    """Every command of a step's transcript fence, each held to the lines printed under it."""
    steps = []
    for command, printed in docsnip.outputs(installers.section_fences(README, heading, index)[0]):
        steps.append(box.script(command, cwd=repo, expect=0, env=box.commit_env()))
        _held_to_the_page(steps[-1], printed)
    return steps


def _scaffold(box, repo) -> None:
    command, printed = _doc_step("1. Scaffold the config")
    step = box.script(command, cwd=repo, expect=0)
    assert said(step).splitlines() == printed.splitlines()
    written = (repo / "crapkit.toml").read_text(encoding="utf-8")
    assert written.strip() == installers.section_fences(README, "1. Scaffold the config")[1].text.strip()


def _doctor_step(box, repo) -> None:
    command, printed = _doc_step("2. Check the config against the repo")
    step = box.script(command, cwd=repo, expect=0)
    assert _doctor_lines(said(step)) == _doctor_lines(printed)


def _next_item(box, repo) -> dict:
    command, printed = _doc_step("4. Take the top item")
    mine, page = json.loads(said(box.script(command, cwd=repo, expect=0))), json.loads(printed)
    assert sorted(mine) == sorted(page) and sorted(mine["item"]) == sorted(page["item"])
    return mine


@cell("lin-pyextra-quickstart-py313", channel="pip [py]", harness="none",
      scenario="fresh: Python quickstart steps 1-6; printed lines match page transcripts",
      use_cases="Python quickstart, next-item, verify", os="linux", image="core", cadence="push")
def test_the_python_quickstart_prints_what_the_page_prints(box, templates):
    installers.pip_extra(box, "3.13", heading=PY_QUICK)
    repo = quickstart_repo(box, templates)
    _scaffold(box, repo)
    _doctor_step(box, repo)
    installers.allow_containers_here(repo)
    _run_step(box, repo, "3. Score the repo, and read the queue")
    item = _next_item(box, repo)
    _run_step(box, repo, "5. Seed the ratchet")
    (repo / "calc" / "grade.py").write_text(GRADE_FIXED, encoding="utf-8")
    with (repo / "tests" / "test_grade.py").open("a", encoding="utf-8") as tests:
        tests.write(GRADE_TABLE)
    installers.commit(box, repo, "grade: split into bands")
    _run_step(box, repo, "6. Fix it and verify")
    box.script(installers.inline(README, "6. Fix it and verify", "git commit -am"), cwd=repo, expect=0,
               env=box.commit_env())
    done = json.loads(said(box.run(["crapkit", "next-item"], cwd=repo, expect=0)))

    assert item["item"]["path"] == "calc/grade.py" and item["item"]["remedy"] == "decompose"
    assert done["empty"] is True
    assert "calc/grade.py" not in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


# --- the TypeScript quickstart ------------------------------------------------------------

TS_FIXED = '''function top(score: number, late: boolean): string | null {
  return score > 90 && !late ? "A" : null;
}

function high(score: number, attempts: number): string | null {
  if (score <= 80) return null;
  return attempts < 3 ? "B" : "C";
}

function low(attempts: number, late: boolean, bonus: boolean): string {
  if (bonus) return "C";
  return late && attempts > 2 ? "F" : "D";
}

export function grade(score: number, attempts: number, late: boolean, bonus: boolean): string {
  return top(score, late) ?? high(score, attempts) ?? low(attempts, late, bonus);
}
'''
TS_TABLE = '''import { describe, expect, it } from "vitest";
import { grade } from "./grade";

describe("grade", () => {
  it("gives an early high score an A", () => expect(grade(95, 1, false, false)).toBe("A"));
  it.each([
    [95, 1, true, false, "B"], [85, 5, false, false, "C"], [50, 1, false, true, "C"],
    [50, 3, true, false, "F"], [50, 1, false, false, "D"],
  ])("grade(%i, %i, %s, %s) is %s", (score, attempts, late, bonus, letter) =>
    expect(grade(score, attempts, late, bonus)).toBe(letter));
});
'''
TS_LANE_SPAN = "npm run test -- --coverage"
MISSING = "MISSING DEPENDENCY"


def _ts_scaffold(box, repo) -> None:
    """Step 1: init's three lines as printed, the lane the prose names, and the
    WARN doctor gives a scope with no scoped_tests template."""
    command, printed = docsnip.outputs(installers.section_fences(README, "1. Scaffold the config", 1)[0])[0]
    assert said(box.script(command, cwd=repo, expect=0)).splitlines() == printed.splitlines()
    lane = installers.inline(README, "Quickstart: TypeScript", TS_LANE_SPAN)
    assert f'command = "{lane}"' in (repo / "crapkit.toml").read_text(encoding="utf-8")
    warn = installers.inline(README, "Quickstart: TypeScript", "scope 'src' has a lane but no")
    assert warn in said(box.run(["crapkit", "doctor"], cwd=repo, expect=0))


def _ts_provider(box, repo) -> None:
    """Step 2: coverage exits 5 on the missing provider and writes no store; the
    page's `npm i -D` line with the vitest major filled in installs it offline."""
    command, printed = docsnip.outputs(installers.section_fences(README, "2. Install a coverage provider")[0])[0]
    failed = box.script(command, cwd=repo, expect=5)
    assert MISSING in said(failed) and MISSING in printed
    assert said(failed).splitlines()[-1] == printed.splitlines()[-1]
    assert not (repo / ".crapkit" / "crap.sqlite").exists()
    major = json.loads((repo / "package.json").read_text(encoding="utf-8"))["devDependencies"]["vitest"].split(".")[0]
    install = installers.section_fences(README, "2. Install a coverage provider")[1].text
    box.script(install.replace("<your vitest major>", major), cwd=repo, expect=0, env={"npm_config_offline": "true"})


def _ts_fix(box, repo) -> None:
    """Steps 5 and 6: the split passes rescore --gate, the table tests pass vitest."""
    (repo / "src" / "grade.ts").write_text(TS_FIXED, encoding="utf-8")
    _run_step(box, repo, "5. Fix it")
    (repo / "src" / "grade.test.ts").write_text(TS_TABLE, encoding="utf-8")
    _run_step(box, repo, "6. Cover the new pieces")


def ts_quickstart(box, templates) -> Path:
    """The TypeScript quickstart from step 1 to step 7 on a vitest-only repo
    whose node_modules the user already installed."""
    repo = repos.checkout(box, "ts-vitest-only", cache=templates)
    box.run(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=repo, expect=0)
    _ts_scaffold(box, repo)
    _ts_provider(box, repo)
    _run_step(box, repo, "3. Score the repo")
    _run_step(box, repo, "4. Seed the ratchet and commit")
    _ts_fix(box, repo)
    _run_step(box, repo, "7. Verify")
    return repo


@cell("lin-uvtool-ts-quickstart", channel="uv tool", harness="none (sh + node)",
      scenario="fresh: vitest-only repo; step 2 exits 5 'MISSING DEPENDENCY'; README npm i -D line with major filled, "
               "offline; coverage, rescore --gate, verify", use_cases="TypeScript quickstart, istanbul coverage",
      os="linux", image="core", cadence="push")
def test_the_typescript_quickstart_from_a_uv_tool_install(box, templates, candidate):
    install = installers.uv_tool(box)
    repo = ts_quickstart(box, templates)

    assert candidate.version in install.run(box, repo, "--version").stdout
    assert "src/grade.ts" not in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


@cell("win-ts-quickstart", channel="uv tool", harness="node via cmd.exe", scenario="fresh: TS quickstart on Windows",
      use_cases="TypeScript quickstart", os="windows", image=None, cadence="nightly")
def test_the_typescript_quickstart_on_windows(box, templates, candidate):
    install = installers.uv_tool(box)
    repo = ts_quickstart(box, templates)

    assert install.launcher.suffix == ".exe"
    assert "src/grade.ts" not in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


@cell("lin-jest-start", channel="pip venv", harness="none (node)",
      scenario="fresh: init's jest lane with jest-junit, coverage, worklist", use_cases="istanbul coverage",
      os="linux", image="core", cadence="nightly")
def test_the_readme_start_on_a_jest_repo_writes_a_jest_junit_lane(box, templates, candidate):
    installers.pip_venv(box, "3.12")
    repo = repos.checkout(box, "jest", cache=templates)
    box.run(["npm", "ci", "--offline", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=repo, expect=0)
    readme_start(box, repo, Expect(lane="js", row=r"src/grade\.js:\d+\s+grade \( score , attempts , late , bonus \)"))
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")

    assert "npm run test -- --coverage" in config and "--reporters=jest-junit" in config
    assert (repo / ".crapkit" / "cov").is_dir()


# --- Route 1, for the bare-runner start ------------------------------------------------

ROUTE_1 = "Route 1: `.git/hooks/pre-commit` (local, not committed)"
GATE_BREACH = '''

def breach(a, b, c, d):
    if a and b:
        return 1
    if b or c:
        return 2
    if c and d:
        return 3
    return 4 if a else 5
'''


def route_1(box, repo: Path) -> None:
    """README Route 1: the hook refuses a staged breach, then passes its fix."""
    box.script(docsnip.fence(README, ROUTE_1).text, cwd=repo, expect=0, note="README Route 1, the whole heredoc")
    grade = repo / "calc" / "grade.py"
    grade.write_text(grade.read_text(encoding="utf-8") + GATE_BREACH, encoding="utf-8")
    box.run(["git", "add", "calc/grade.py"], cwd=repo, expect=0)
    refused = box.run(["git", "commit", "-q", "-m", "add breach"], cwd=repo, env=box.commit_env(), expect=1)
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6" in said(refused)
    grade.write_text(grade.read_text(encoding="utf-8").replace(GATE_BREACH, "\n\ndef fine(a):\n    return a\n"),
                     encoding="utf-8")
    box.run(["git", "add", "calc/grade.py"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "add fine"], cwd=repo, env=box.commit_env(), expect=0)


# --- the interpreter a user already has ---------------------------------------------------

REFUSAL_WAYS = ("-m venv", "pipx install", "uv tool install")


def _answers_a_refusal(line: str) -> bool:
    return any(way in line for way in REFUSAL_WAYS)


def pep668_fallback() -> str:
    """What the README's Install section says to run when pip refuses with
    externally-managed-environment: a fence line or a code span naming a venv,
    pipx or uv tool, in a section that names the refusal at all. The pipx and
    uv tool spans in 'A repo that is not Python' answer another question."""
    prose = " ".join(installers.section_prose(README, "Install"))
    if "externally-managed" not in prose:
        raise docsnip.DocSnipError("README.md > Install: the refusal `externally-managed-environment` is not named")
    fenced = [line for block in installers.section_fences(README, "Install") for line in docsnip.commands(block)]
    return next(line for line in [*fenced, *installers.spans(README, "Install")] if _answers_a_refusal(line))


@cell("lin-sys-python-start", channel="system pip (Debian python3, EXTERNALLY-MANAGED)", harness="none",
      scenario="fresh: README `pip install crapkit` verbatim; assert PEP 668 refusal line", use_cases="install",
      os="linux", image="core", cadence="push")
def test_the_system_pip_refuses_the_readme_line_with_pep_668(box):
    python = box.toolchain["system_python"]
    assert installers.marker(box, python).exists(), "Debian marks its python3 externally managed"
    refused = installers.system_pip(box)

    assert refused.exit == 1
    assert "error: externally-managed-environment" in said(refused)
    assert box.which("crapkit") is None


@cell("lin-sys-python-start", channel="system pip (Debian python3, EXTERNALLY-MANAGED)", harness="none",
      scenario="fresh: after the PEP 668 refusal, the README fallback runs", use_cases="install",
      os="linux", image="core", cadence="push")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-2: README Install names no command to run when "
                                       "pip refuses with externally-managed-environment")
def test_after_the_pep_668_refusal_the_readme_fallback_installs_crapkit(box, candidate):
    installers.system_pip(box)
    fallback = pep668_fallback()
    box.prepend_path(box.home / ".local" / "bin")
    step = box.script(f"{fallback}\ncrapkit --version\n", cwd=box.root, expect=0)

    assert f"crapkit {candidate.version}" in said(step)


def _older_than_311(box):
    install = installers.pip_venv(box, "3.10", expect=1)
    assert "3.10" in install.output and "'>=3.11'" in install.output
    return install


@cell("lin-pip-old-python", channel="pip", harness="none",
      scenario="fresh: Python 3.10 gets no matching distribution; uvx picks an interpreter that can",
      use_cases="install", os="linux", image="core", cadence="nightly")
def test_python_310_is_refused_and_uvx_runs_crapkit_on_a_newer_python(box, candidate):
    _older_than_311(box)
    version = installers.uvx(box).steps[0]

    assert f"crapkit {candidate.version}" in said(version)


@cell("lin-pip-old-python", channel="pip", harness="none",
      scenario="fresh: the README sends a Python 3.10 user to uvx", use_cases="install",
      os="linux", image="core", cadence="nightly")
@pytest.mark.xfail(strict=True, reason="deploy-bug deploy-channels-3: README Install says Python 3.11 or newer and "
                                       "does not tell a 3.10 user that uvx brings its own Python")
def test_the_readme_sends_a_python_310_user_to_uvx(box):
    _older_than_311(box)
    sentences = re.split(r"(?<=\.)\s+", " ".join(installers.section_prose(README, "Install")))

    assert [sentence for sentence in sentences if "3.11" in sentence and "uvx" in sentence]


@cell("lin-py315-start", channel="pip venv", harness="none", scenario="fresh: start on 3.15 pre-release; non-blocking",
      use_cases="60-second start", os="linux", image="core", cadence="weekly", nonblocking=True)
def test_the_readme_start_on_the_315_prerelease(box, templates, candidate):
    if "3.15" not in box.toolchain.pythons():
        pytest.skip("no CPython 3.15 in this toolchain: pins.py passes PYTHON_PRERELEASE empty to every build")
    pip_start(box, templates, candidate, "3.15")


@cell("lin-arm64", channel="pip venv", harness="none", scenario="fresh: push start and Route 1 on arm64; non-blocking",
      use_cases="start, gate", os="linux", image="cells", cadence="weekly", nonblocking=True)
def test_the_readme_start_and_route_1_on_arm64(box, templates, candidate):
    if platform.machine().lower() not in ("aarch64", "arm64"):
        pytest.skip(f"an arm64 cell on {platform.machine()}: run.py builds linux/amd64 only (pins.toml platform)")
    pip_start(box, templates, candidate, "3.12")
    route_1(box, box.root / "py-pytest")


@cell("lin-online-pypi", channel="PyPI", harness="none", scenario="fresh: latest release start; lizard resolved "
      "equals stamp", use_cases="start, uvx", os="linux", image="core", cadence="weekly+published", online=True)
def test_the_readme_start_from_pypi_resolves_the_lizard_the_stamp_names(box, templates):
    box.env.update(installers.online_env(box))
    install = installers.pip_venv(box, "3.12")
    release = install.run(box, box.root, "--version").stdout.split()[-1]
    repo = repos.checkout(box, "py-pytest", cache=templates)
    readme_start(box, repo)
    lizard = box.run(["python", "-m", "pip", "show", "lizard"], expect=0).stdout
    stamp = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()[0]

    assert wheels._key(release) >= wheels._key(wheels.n_minus_1())
    assert f"lizard={re.search(r'^Version: (.+)$', lizard, re.M)[1]}" in stamp


# --- Windows: a PATH with only the py launcher ----------------------------------------------

def _holds_python(directory: str) -> bool:
    return any((Path(directory) / name).exists() for name in ("python.exe", "python3.exe"))


def _only_py(box, templates):
    """crapkit from a venv holding pytest-cov, with no python or python3 anywhere
    on PATH and `py` answering. py.exe finds interpreters through the registry,
    which would reach this machine's own Pythons, so `py` here is a cmd stub
    that runs the venv's python: the lane init writes runs through it."""
    install = installers.pip_extra(box, "3.12")
    bindir = box.root / "only-py"
    bindir.mkdir()
    shutil.copy2(install.launcher, bindir / install.launcher.name)
    (bindir / "py.cmd").write_text(f'@"{install.path_entry / "python.exe"}" %*\n', encoding="utf-8")
    box.env["PATH"] = os.pathsep.join([str(bindir), *(d for d in box.path_dirs() if not _holds_python(d))])
    return repos.checkout(box, "py-pytest", cache=templates)


@cell("win-py-only-path", channel="py.exe only", harness="none", scenario="fresh: init and doctor with only `py` "
      "on PATH", use_cases="init", os="windows", image=None, cadence="nightly")
def test_init_writes_py_when_py_is_the_only_launcher_on_path(box, templates):
    repo = _only_py(box, templates)
    init = box.run(["crapkit", "init"], cwd=repo, expect=0)
    doctor = box.run(["crapkit", "doctor"], cwd=repo, expect=0)
    coverage = box.run(["crapkit", "coverage"], cwd=repo, expect=0)

    assert 'command = "py -m pytest' in (repo / "crapkit.toml").read_text(encoding="utf-8")
    assert "pytest_cov" not in said(init)
    assert "ok   lane 'py': py -> " in doctor.stdout
    assert "-> next: crapkit worklist" in coverage.stdout


@cell("win-py-only-path", channel="stub python.exe exiting 9009", harness="none", scenario="fresh: init and doctor "
      "when cmd.exe cannot start python", use_cases="init", os="windows", image=None, cadence="nightly")
def test_init_and_doctor_name_a_python_cmd_cannot_start(box, templates):
    repo = _only_py(box, templates)
    dead = box.root / "store-alias"
    dead.mkdir()
    (dead / "python.bat").write_text("@exit /b 9009\n", encoding="utf-8")
    box.prepend_path(dead)
    init = box.run(["crapkit", "init"], cwd=repo, expect=0)
    doctor = box.run(["crapkit", "doctor"], cwd=repo, expect=1)

    assert "cannot run it" in said(init) and "9009" in said(init) and "pytest_cov" not in said(init)
    assert "cannot run" in doctor.stdout and "'python'" in doctor.stdout
    assert "no problems found" not in doctor.stdout
