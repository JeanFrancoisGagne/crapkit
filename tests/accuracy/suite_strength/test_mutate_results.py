"""`crapkit mutate`'s verdicts against fixtures whose every mutant was worked out by hand.

The rules the expected values come from:

- README.md:809: mutate flips comparisons, boundary shifts, boolean connectives
  and boolean literals on the lines it targets; `--files` targets whole files,
  and a file outside the scored corpus (a test file among them) grows none.
- docs/configuration.md:80: a nonzero exit of mutation_command kills the
  mutant, and the command must exit 0 on the unmutated tree before the first
  mutant, so a command that cannot start never scores 100%.
- docs/configuration.md:82: mutation_workers says how many mutants run at once;
  the verdicts do not depend on it.
- docs/agent-json.md:1177: `--json` prints mutants, killed, survived and
  survivors, each survivor {path, line, op, original, mutated}.

Each fixture says, beside its source, which mutants each line grows and which
line of its suite kills each one. Nothing here reads crapkit's output to learn
what to expect.

The push tier runs the known kills, the broken runner and the diff that
touches a test (about 9 s serial); the other fixtures spawn a mutate run each
and run nightly. Every run spawns crapkit through kit.drive, so tools/accuracy/retro.py can point
CRAPKIT_ACCURACY_PYTHON at an older wheel and replay a past bug's check on its
before commit. A run that older crapkit cannot read (a refused config, no JSON
on stdout) raises Unreadable, which retro records as not replayable, never red.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import tomllib
from xml.etree import ElementTree

import pytest

import hang_guard
from accuracy.kit import drive, repos, rulings

pytestmark = pytest.mark.process

# The second the kk suite pins same.py's mtime to. Python trusts a cached .pyc
# whose recorded source size and whole-second mtime match the file (PEP 552's
# timestamp mode), so two same-size edits inside one second read the older
# bytecode. Pinning the second makes that race happen on every run.
PINNED = 1_750_000_000
PASS = 'python -c "pass"'


class Unreadable(RuntimeError):
    """This crapkit refused the fixture's config or printed no payload."""


@dataclass(frozen=True)
class Mutated:
    code: int
    payload: dict
    stderr: str

    def survivors(self, path: str | None = None) -> list[tuple[str, int, str]]:
        rows = [(row["path"], row["line"], row["op"]) for row in self.payload["survivors"]]
        return [row for row in rows if path is None or row[0] == path]

    def mutated_lines(self, path: str) -> list[str]:
        return [row["mutated"].strip() for row in self.payload["survivors"]
                if row["path"] == path]


def _toml(command: str, languages=("python",), workers: int | None = None) -> str:
    """The smallest config every crapkit since 0.2 reads: no key a release added later."""
    lines = ["[crapkit]", "target = 6", f"mutation_command = {json.dumps(command)}",
             f"mutation_timeout_seconds = {hang_guard.HANG_SECONDS}"]
    lines += [f"mutation_workers = {workers}"] if workers else []
    lines += ["", "[[scope]]", 'name = "src"', 'paths = ["src"]',
              f"languages = {json.dumps(list(languages))}"]
    return "\n".join(lines) + "\n"


def _payload(result: drive.Result) -> Mutated:
    """The run's payload. Exit 3 is a refused config; no JSON on a clean exit is
    a crapkit that predates the payload. Both are unreadable, not a verdict."""
    if result.code == 3:
        raise Unreadable(f"crapkit refused the fixture: {result.stderr.strip()[:300]}")
    assert result.code == 0, f"mutate exited {result.code}:\n{result.stdout}\n{result.stderr}"
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise Unreadable(f"no JSON payload:\n{result.stdout[:300]}") from None
    return Mutated(result.code, payload, result.stderr)


def _counts_hold(run: Mutated) -> None:
    """docs/agent-json.md:1177: every mutant is killed or survived, and the
    survivors list is the survived count."""
    body = run.payload
    assert body["killed"] + body["survived"] == body["mutants"], body
    assert len(body["survivors"]) == body["survived"], body
    assert 0 <= body["killed"] <= body["mutants"], body


def _mutate(root: Path, *args: str) -> Mutated:
    run = _payload(drive.Driver(root, spawn=True).run("mutate", *args, "--json"))
    _counts_hold(run)
    return run


def _built(repo_templates, directory: Path, files: dict, root: str = "") -> Path:
    spec = repos.Spec(steps=(repos.Commit(files=files, message="fixture"),), root=root)
    return repo_templates.copy(spec, directory).root


# --- kk: known kills -----------------------------------------------------------------------
#
# limits.py:2 `a >= b` grows `>= -> >` and `>= -> <`. The suite checks
# at_least(2, 1) and not at_least(1, 2), never the boundary (1, 1): `>` agrees
# with `>=` on both calls and survives; `<` fails at_least(2, 1) and dies.
# same.py:2 `a == b` grows `== -> !=` and same.py:6 `a != b` grows `!= -> ==`.
# Each is the size of the line it replaces, and they run back to back. The
# suite's same(1, 1) kills the first and differ(1, 2) kills the second, provided
# each run imports the mutant and not bytecode cached from an earlier one.

KK_SUITE = f"""\
import os
import sys

sys.dont_write_bytecode = False  # a suite that writes bytecode, as most do
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
os.utime(os.path.join(SRC, "kk", "same.py"), ({PINNED}, {PINNED}))
sys.path.insert(0, SRC)
from kk.limits import at_least
from kk.same import differ, same

assert at_least(2, 1)
assert not at_least(1, 2)
assert same(1, 1) and not same(1, 2)
assert differ(1, 2) and not differ(1, 1)
"""
KK_FILES = {
    "src/kk/__init__.py": "",
    "src/kk/limits.py": "def at_least(a, b):\n    return a >= b\n",
    "src/kk/same.py": "def same(a, b):\n    return a == b\n\n\ndef differ(a, b):\n    return a != b\n",
    "checks/kk_suite.py": KK_SUITE,
}
KK_MUTANTS = [("src/kk/limits.py", 2, ">= -> >"), ("src/kk/limits.py", 2, ">= -> <"),
              ("src/kk/same.py", 2, "== -> !="), ("src/kk/same.py", 6, "!= -> ==")]
KK_SURVIVORS = [("src/kk/limits.py", 2, ">= -> >")]
KK_FILES_ARGS = ("--files", "src/kk/limits.py", "src/kk/same.py")


def _kk_repo(repo_templates, directory: Path, workers: int | None = None) -> Path:
    files = {**KK_FILES, "crapkit.toml": _toml("python checks/kk_suite.py", workers=workers)}
    root = _built(repo_templates, directory, files)
    # A user who ran the suite before mutate leaves same.py's original bytecode
    # behind, stamped with the pinned second and the original's size.
    ran = hang_guard.run([drive.Driver(root).python, "checks/kk_suite.py"], cwd=root,
                         env=drive.child_env(), text=True, encoding="utf-8", errors="replace")
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return root


@pytest.fixture(scope="module")
def kk_run(repo_templates, tmp_path_factory) -> Mutated:
    return _mutate(_kk_repo(repo_templates, tmp_path_factory.mktemp("kk") / "repo"),
                   *KK_FILES_ARGS)


def test_negation_dies_and_boundary_survives(kk_run):
    assert kk_run.payload["mutants"] == len(KK_MUTANTS)
    assert kk_run.survivors("src/kk/limits.py") == KK_SURVIVORS


def test_back_to_back_same_size_mutants_both_die(kk_run):
    """R08: before fbf0df2 the runner kept __pycache__ and let the suite write
    bytecode, so a same-size mutant inside one second ran the older bytecode
    and survived."""
    assert kk_run.survivors("src/kk/same.py") == []


@pytest.mark.nightly
def test_shard_split_merges_to_mutant_order(kk_run, repo_templates, tmp_path):
    """Three workers take the four mutants round-robin; the merged payload is
    the one-worker payload, survivors in mutant order."""
    three = _mutate(_kk_repo(repo_templates, tmp_path / "repo", workers=3), *KK_FILES_ARGS)

    assert three.payload == kk_run.payload


# --- broken: a runner that cannot start ------------------------------------------------------

def test_broken_runner_refuses(repo_templates, tmp_path):
    """R85: a mutation_command that cannot start exits nonzero on every mutant.
    Before 0a4a55b that read as every mutant killed and printed 100%."""
    files = {**KK_FILES, "crapkit.toml": _toml("crapkit-accuracy-missing-runner --x")}
    root = _built(repo_templates, tmp_path / "repo", files)

    result = drive.Driver(root, spawn=True).run("mutate", "--files", "src/kk/limits.py", "--json")

    if result.code == 3:
        raise Unreadable(result.stderr.strip()[:300])
    assert result.code != 0, f"a runner that cannot start was scored:\n{result.stdout}"
    assert "crapkit-accuracy-missing-runner" in result.stderr + result.stdout
    assert '"killed"' not in result.stdout


# --- diff: a diff that touches a test ------------------------------------------------------------
#
# The committed tree holds src/t/calc.py and tests/check_t.py. The working tree
# edits both: calc.py:2 becomes `x < 12`, growing `< -> <=` and `< -> >=`;
# check_t.py gains `assert small(11) == False`, a test line crapkit must never
# mutate (README.md:809). mutation_command never fails, so every mutant is a
# survivor and the survivors list is every mutant.

DIFF_FILES = {
    "src/t/__init__.py": "",
    "src/t/calc.py": "def small(x):\n    return x < 10\n",
    "tests/check_t.py": "from t.calc import small\n\nassert small(1)\n",
    "crapkit.toml": _toml(PASS),
}


def test_diff_touching_tests_mutates_only_source(repo_templates, tmp_path):
    """R86: before ccad6ec a diff's test file grew mutants too."""
    root = _built(repo_templates, tmp_path / "repo", DIFF_FILES)
    (root / "src/t/calc.py").write_text("def small(x):\n    return x < 12\n", encoding="utf-8")
    with (root / "tests/check_t.py").open("a", encoding="utf-8") as handle:
        handle.write("assert small(11) == False\n")

    run = _mutate(root)

    assert run.survivors() == [("src/t/calc.py", 2, "< -> <="), ("src/t/calc.py", 2, "< -> >=")]


# --- nested: a crapkit root one directory below the git top ------------------------------------

@pytest.mark.nightly
def test_nested_root_finds_targets(repo_templates, tmp_path):
    """R75: before dfb6c36 git named the diff's files from the repo top
    (app/src/n/calc.py) and mutate, reading them from the crapkit root, found
    no file to mutate."""
    files = {"app/crapkit.toml": _toml(PASS), "app/src/n/__init__.py": "",
             "app/src/n/calc.py": "def big(x):\n    return x > 10\n"}
    root = _built(repo_templates, tmp_path / "repo", files, root="app")
    (root / "src/n/calc.py").write_text("def big(x):\n    return x > 12\n", encoding="utf-8")

    run = _mutate(root)

    assert run.survivors() == [("src/n/calc.py", 2, "> -> >="), ("src/n/calc.py", 2, "> -> <=")]


# --- gen: where mutants may grow ---------------------------------------------------------------
#
# mutation_command never fails, so the survivors are every mutant. Each file has
# one real comparison, and its only mutants are that comparison's two
# (README.md:809). Everything else on the page is syntax that is not a
# comparison: a Swift half-open range, TypeScript type arguments, and Python
# text inside a docstring and a comment.

GEN_SWIFT = """\
func count(_ b: Int) -> Int {
    var n = 0
    for i in 0..<b {
        if i > 2 { n += 1 }
    }
    return n
}
"""
GEN_TS = """\
export function sizeOf(m: Map<string, number>, limit: number): boolean {
  const seen: Array<number> = [];
  return m.size > limit;
}
"""
GEN_PY = '''\
def describe(a, b):
    """True when a < b.

    The caller checks a > b on its own.
    """
    return a < b  # a > b never holds here
'''
GEN_FILES = {"src/gen/count.swift": GEN_SWIFT, "src/gen/size.ts": GEN_TS,
             "src/gen/describe.py": GEN_PY,
             "crapkit.toml": _toml(PASS, languages=("python", "typescript", "swift"))}


@pytest.fixture(scope="module")
def gen_run(repo_templates, tmp_path_factory) -> Mutated:
    root = _built(repo_templates, tmp_path_factory.mktemp("gen") / "repo", GEN_FILES)
    return _mutate(root, "--files", *sorted(path for path in GEN_FILES if path.startswith("src/")))


@pytest.mark.nightly
def test_a_swift_half_open_range_is_not_mutated(gen_run):
    """R183: before c4a6716 `0..<b` grew `0..<=b` and `0..>=b`, which do not
    compile, die on the compiler and read as kills."""
    assert gen_run.mutated_lines("src/gen/count.swift") == ["if i >= 2 { n += 1 }",
                                                            "if i <= 2 { n += 1 }"]


@pytest.mark.nightly
def test_type_argument_angles_are_not_mutated(gen_run):
    """R185: before c24e6a4 the < and > of Map<string, number> and Array<number>
    grew mutants that do not compile."""
    assert gen_run.mutated_lines("src/gen/size.ts") == ["return m.size >= limit;",
                                                        "return m.size <= limit;"]


@pytest.mark.nightly
def test_text_inside_strings_and_comments_is_not_mutated(gen_run):
    """R186: before c24e6a4 a docstring's second line and a trailing comment
    were mutated as code: survivors no test can kill."""
    assert gen_run.survivors("src/gen/describe.py") == [("src/gen/describe.py", 6, "< -> <="),
                                                        ("src/gen/describe.py", 6, "< -> >=")]


# --- uncommitted: a test the working tree holds and HEAD does not --------------------------------
#
# calc.py:2 `x > 0` grows `> -> >=` and `> -> <=`. The committed suite checks
# positive(5), which kills `<=` only. The working tree adds `not positive(0)`,
# which kills `>=` (0 >= 0 holds). mutate runs the working tree's tests, so
# both die at any worker count.

W_CHECK = ("import os\nimport sys\n\nsys.path.insert(0, os.path.join(os.path.dirname("
           "os.path.abspath(__file__)), '..', 'src'))\nfrom w.calc import positive\n\n"
           "assert positive(5)\n")
W_FILES = {"src/w/__init__.py": "", "src/w/calc.py": "def positive(x):\n    return x > 0\n",
           "tests/w_check.py": W_CHECK, "crapkit.toml": _toml("python tests/w_check.py")}


@pytest.mark.nightly
@pytest.mark.parametrize("workers", [1, 2])
def test_an_uncommitted_test_kills_at_any_worker_count(repo_templates, tmp_path, workers):
    """R187: before c24e6a4 a worker's worktree held HEAD plus the mutated file
    only, so with two workers the suite ran the committed tests and `>=`
    survived."""
    root = _built(repo_templates, tmp_path / "repo", W_FILES)
    (root / "crapkit.toml").write_text(_toml("python tests/w_check.py", workers=workers),
                                       encoding="utf-8")
    with (root / "tests/w_check.py").open("a", encoding="utf-8") as handle:
        handle.write("assert not positive(0)\n")

    run = _mutate(root, "--files", "src/w/calc.py")

    assert (run.payload["mutants"], run.survivors()) == (2, [])


# --- sound: this repo's own mutation_command ------------------------------------------------------
#
# crapkit mutate runs mutation_command once on the unmutated tree before any
# mutant, in a checkout of HEAD, and refuses to score when it fails
# (docs/configuration.md:80). The accuracy plan asks three things of the
# command crapkit.toml gives this repo: it passes on a clean tree, it runs at
# least every test tests/unit collects on its own, and it takes at most a
# third of mutation_timeout_seconds, the headroom that deadline was set with.
#
# The command runs as written, inside a container as accuracy.yml's jobs run it,
# with --maxfail after it to undo its -x: a clean tree passes either way, and a
# failing one names every failing test, so each run reads the same set.
# PYTEST_ADDOPTS adds a JUnit file, which counts what ran whatever verbosity the
# repo's addopts set; the tests the run names FAILED or ERROR come from its
# short summary, which pytest prints at any -q. A failing test no open ruling
# records (SS3, SS4) fails the check.

REPO = Path(__file__).resolve().parents[3]
_COLLECTED = re.compile(r"(\d+) tests? collected")
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def ran(junit_xml: str) -> int:
    """How many tests a run's JUnit file records, skipped ones included."""
    root = ElementTree.fromstring(junit_xml)
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    return sum(int(suite.get("tests", "0")) for suite in suites)


def collected(output: str) -> int:
    """The count `pytest --collect-only -q` ends with."""
    counts = _COLLECTED.findall(output)
    return int(counts[-1]) if counts else 0


def failing(output: str) -> str:
    """The node ids a pytest run's short summary names FAILED or ERROR, sorted, or `none`."""
    return ",".join(sorted(set(_FAILED.findall(output)))) or "none"


def test_a_junit_file_and_a_summary_read_as_what_ran():
    xdist = ('<testsuites><testsuite name="pytest" tests="3435" failures="0" errors="0" '
             'skipped="12"/></testsuites>')
    assert ran(xdist) == 3435
    assert ran('<testsuite tests="2" failures="1"/>') == 2
    assert ran("<testsuites/>") == 0
    assert collected("a::b\nc::d\n\n2 tests collected in 0.4s\n") == 2
    assert collected("1 test collected in 0.1s") == 1
    assert collected("tests/unit/a.py: 2\n") == 0
    summary = "FAILED tests/a.py::t - AssertionError: x\nERROR tests/b.py\nFAILED tests/a.py::t\n"
    assert failing(summary) == "tests/a.py::t,tests/b.py"
    assert failing("3 passed in 1s\n") == "none"


@pytest.fixture
def clean_tree(tmp_path):
    """A detached checkout of HEAD, as crapkit mutate builds for its workers."""
    tree = tmp_path / "clean"
    added = hang_guard.run(["git", "-C", str(REPO), "worktree", "add", "--detach", str(tree), "HEAD"],
                           text=True)
    assert added.returncode == 0, added.stderr
    yield tree
    hang_guard.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(tree)])


def _in_tree(argv: list[str], tree: Path, timeout: float | None, extra: dict | None = None):
    env = drive.child_env({"PYTHONDONTWRITEBYTECODE": "1", **(extra or {})})
    return hang_guard.run(argv, cwd=tree, env=env, timeout=timeout, text=True,
                          encoding="utf-8", errors="replace")


INSIDE_CONTAINER = {"CRAPKIT_INSIDE_CONTAINER": "1"}  # crapkit/lanes.py's own switch
CLEAN_RUN_RULINGS = ("SS3", "SS4")


def _open_failures() -> set[str]:
    """The tests the open clean-run rulings record as failing on an unmutated tree."""
    rows = rulings.load()
    return {test for ruling in CLEAN_RUN_RULINGS if rows[ruling].ruling == "defect"
            for test in rows[ruling].crapkit_value.split(",")}


def _pytest_in(tree: Path, targets: list[str], extra: dict | None = None):
    """pytest over `targets` in `tree`, with the killer's import paths."""
    paths = {"PYTHONPATH": os.pathsep.join([str(tree / "src"), str(tree / "tests")])}
    return _in_tree([sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider",
                     "-p", "no:randomly"], tree, None, {**paths, **(extra or {})})


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.platform("linux")
def test_mutation_command_is_sound(clean_tree, tmp_path):
    """Linux only: the jobs that run this command (accuracy.yml's mutation jobs
    and the `accuracy` label's crapkit mutate) run in the accuracy image. The run
    fails only on tests an open clean-run ruling records; SS3 and SS4 are the
    strict xfails that turn when those tests pass, and with no ruling open the
    run must pass outright."""
    config = tomllib.loads((REPO / "crapkit.toml").read_text(encoding="utf-8"))["crapkit"]
    deadline, junit = config["mutation_timeout_seconds"], tmp_path / "killer.xml"
    started = time.monotonic()
    killer = _in_tree([*shlex.split(config["mutation_command"]), "--maxfail=1000000"], clean_tree,
                      deadline, {"PYTEST_ADDOPTS": f"--junitxml={junit}", **INSIDE_CONTAINER})
    seconds = time.monotonic() - started
    unit = _in_tree([sys.executable, "-m", "pytest", "tests/unit", "-o", "addopts=",
                     "--collect-only", "-q", "-p", "no:cacheprovider"], clean_tree, deadline)
    failed = set(_FAILED.findall(killer.stdout))

    assert failed <= _open_failures(), f"no open ruling records {sorted(failed - _open_failures())}"
    assert (killer.returncode == 0) == (not failed), killer.stdout[-3000:] + killer.stderr[-3000:]
    assert ran(junit.read_text(encoding="utf-8")) >= collected(unit.stdout) > 0
    assert seconds <= deadline / 3, (
        f"the clean run took {seconds:.0f} s, over a third of mutation_timeout_seconds "
        f"({deadline}); set it to three times the clean run")


@rulings.applies("SS3")
@pytest.mark.nightly
@pytest.mark.process
def test_ss3_the_hang_bound_guard_passes_on_a_clean_tree(clean_tree):
    run = _pytest_in(clean_tree, ["tests/unit/test_one_hang_bound.py"])

    rulings.pin_ruling("SS3", crapkit=failing(run.stdout), oracle="none")


LANE_TESTS = ["tests/unit/test_lanes_infra.py", "tests/unit/test_lane_reuse_refusal.py",
              "tests/unit/test_lane_starts_through_its_launch_spec.py"]


@rulings.applies("SS4")
@pytest.mark.nightly
@pytest.mark.process
def test_ss4_the_lane_tests_pass_inside_a_container(clean_tree):
    run = _pytest_in(clean_tree, LANE_TESTS, INSIDE_CONTAINER)

    rulings.pin_ruling("SS4", crapkit=failing(run.stdout), oracle="none")
