"""verify over a store an older crapkit release wrote, with that release as the writer.

An upgrade keeps `.crapkit/crap.sqlite`, and the next verify takes the newest
trusted run in it as its baseline, whichever release stored that run. The unit
tests build such runs by hand from the fields a release is known to write. Here
the release writes them itself: its source comes out of this repository's
history through `git archive <tag>`, and it runs from there with PYTHONPATH, so
a row is exactly what that release stores.

0.8.0 began naming a failure that passed its flake retry under
`retried_passes`. A 0.7.6 verify kept that id in its `failures` and named it
nowhere else, so read as a baseline it forgave a later real failure of the same
test, exit 0, and every baseline after it forgave it too. A coverage run holds
no retry, so the failure list an older coverage run stored still forgives.

A 0.8.0 verify over a reused junit it could not find passed and stored a
trusted run with no failure list and no test count. This tree's verify refuses
that case with exit 5 and stores nothing, but a store 0.8.0 wrote can still hold
such a run, and every reader walks past it to the run behind it.

The tags come from the git history, which CI's `fetch-depth: 0` checkout
carries. A shallow or tagless clone skips these tests and says why.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import hang_guard
import process_table
from conftest import child_env, cli_runner, git_commit_all, git_init_repo
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable.replace("\\", "/")
THIS_TREE = "this tree"
# 0.7.6's verify stops its flake retry with killpg, which Darwin refuses with EPERM
# for a group whose only member is its unreaped leader: that release fails there
# with `[Errno 1] Operation not permitted` and writes no store. 0.8.1 fixed it.
OLD_RETEST_STOP = pytest.mark.skipif(
    sys.platform == "darwin", reason="crapkit 0.7.6's verify fails its retest stop on macOS (EPERM)")

run_cli = cli_runner(encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})

SRC = "def hot(n):\n    if n:\n        return 1\n    return 0\n"

# The retest writes one testcase, t::c0, passing or failing as retest.txt says.
RETEST = (
    "import pathlib\n"
    "passed = pathlib.Path('retest.txt').read_text().strip() == 'pass'\n"
    "body = '' if passed else '<failure message=\"boom\"/>'\n"
    "pathlib.Path('junit.xml').write_text('<testsuite name=\"t\" tests=\"1\">'\n"
    "    '<testcase classname=\"t\" name=\"c0\">' + body + '</testcase></testsuite>')\n"
)

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -c pass"
artifact = "cov.json"
parser = "istanbul"
scopes = ["src"]
results_artifact = "junit.xml"
"""

_RELEASES: dict[str, Path] = {}


def release_src(tag: str, tmp_path_factory) -> Path:
    """The release's `src`, taken once per worker from the git history."""
    if tag not in _RELEASES:
        _RELEASES[tag] = _archive(tag, tmp_path_factory.mktemp(tag.replace(".", "_")))
    return _RELEASES[tag]


def _archive(tag: str, into: Path) -> Path:
    archived = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", tag, "src"],
                              capture_output=True)
    if archived.returncode:
        pytest.skip(f"the crapkit history at {ROOT} holds no tag {tag} (a shallow or tagless "
                    "clone), and this test runs that release from it")
    zipfile.ZipFile(io.BytesIO(archived.stdout)).extractall(into)
    return into / "src"


def write_as(writer: str, repo: Path, tmp_path_factory, *args: str) -> None:
    """One command under the named release, or under this tree."""
    if writer == THIS_TREE:
        done = run_cli(repo, *args)
    else:
        env = child_env({"PYTHONPATH": str(release_src(writer, tmp_path_factory)),
                         "CRAPKIT_OVERRIDE_REASON": None})
        with process_table.hold(naming=False):
            done = hang_guard.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env,
                                  text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, f"{writer}: crapkit {' '.join(args)}\n{done.stdout}{done.stderr}"


def build(tmp_path: Path, *, retest: bool) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text(SRC, encoding="utf-8", newline="\n")
    lane_retest = f'retest_command = \'"{PY}" retest.py {{tests}}\'\n' if retest else ""
    (repo / "crapkit.toml").write_text(TOML + lane_retest, encoding="utf-8", newline="\n")
    (repo / "retest.py").write_text(RETEST, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\njunit.xml\nretest.txt\n",
                                     encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    cov(repo)
    return repo


def cov(repo: Path) -> None:
    """The istanbul artifact every run reuses: `hot` measured, both lines run."""
    key = str(repo / "src" / "a.py")
    body = {"path": key,
            "fnMap": {"0": {"name": "hot", "decl": {"start": {"line": 1}},
                            "loc": {"start": {"line": 1}, "end": {"line": 4}}}},
            "f": {"0": 1}, "branchMap": {}, "b": {},
            "statementMap": {"1": {"start": {"line": 2}, "end": {"line": 2}}},
            "s": {"1": 1}}
    (repo / "cov.json").write_text(json.dumps({key: body}), encoding="utf-8")


BOOM = '<failure message="boom"/>'


def junit(repo: Path, *failing: str) -> None:
    """A finished report of t::c0, t::c1 and t::c2, the named ids failing."""
    cases = "".join(f'<testcase classname="t" name="c{i}">{BOOM if f"t::c{i}" in failing else ""}'
                    "</testcase>" for i in range(3))
    (repo / "junit.xml").write_text(f'<testsuite name="t" tests="3">{cases}</testsuite>',
                                    encoding="utf-8")


def passing_junit(repo: Path, tests: int) -> None:
    """A finished report of `tests` passing cases."""
    cases = "".join(f'<testcase classname="t" name="c{i}"/>' for i in range(tests))
    (repo / "junit.xml").write_text(f'<testsuite name="t" tests="{tests}">{cases}</testsuite>',
                                    encoding="utf-8")


def runs(repo: Path) -> list[dict]:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()


def newest_run(repo: Path) -> dict:
    return runs(repo)[-1]


def verify_over_a_deleted_junit_as_0_8_0(repo: Path, tmp_path_factory) -> dict:
    """0.8.0's verify reuses a junit that is gone, passes, and stores a trusted
    run whose lane holds neither a failure list nor a test count."""
    (repo / "junit.xml").unlink()
    write_as("v0.8.0", repo, tmp_path_factory, "verify", "--reuse-artifacts", "--no-tighten")
    stored = newest_run(repo)
    assert stored["kind"] == "verify", stored
    assert not {"failures", "tests_total"} & set(stored["lanes"]["py"]), stored["lanes"]
    return stored


def version_of(writer: str) -> str:
    from crapkit import __version__

    return __version__ if writer == THIS_TREE else writer.lstrip("v")


@pytest.mark.parametrize("writer", [pytest.param("v0.7.6", marks=OLD_RETEST_STOP), THIS_TREE])
def test_a_failure_a_passing_verify_retried_is_new_when_it_fails_its_retry(
        tmp_path, tmp_path_factory, writer):
    """The writer's verify sees t::c0 fail and then pass its flake retry, and
    passes. This tree's verify then sees t::c0 fail its retry too: no run ever
    recorded it failing for good, so it is new, exit 8, whichever release
    stored the baseline."""
    repo = build(tmp_path, retest=True)
    junit(repo)
    write_as(writer, repo, tmp_path_factory, "coverage", "--reuse-artifacts")
    junit(repo, "t::c0")
    (repo / "retest.txt").write_text("pass", encoding="utf-8")
    write_as(writer, repo, tmp_path_factory, "verify", "--reuse-artifacts", "--no-tighten")
    baseline = newest_run(repo)
    assert (baseline["kind"], baseline["tool_versions"]["crapkit"]) == ("verify", version_of(writer))
    assert baseline["lanes"]["py"]["failures"] == ["t::c0"]
    junit(repo, "t::c0")
    (repo / "retest.txt").write_text("fail", encoding="utf-8")

    res = run_cli(repo, "verify", "--reuse-artifacts", "--no-tighten", "--json")

    assert res.returncode == 8, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["baseline_run"] == baseline["id"]
    assert (payload["new_failures"], payload["forgiven_failures"]) == (["t::c0"], [])


@OLD_RETEST_STOP
def test_the_verify_0_7_6_wrote_is_named_as_the_reason_its_list_is_passed_over(
        tmp_path, tmp_path_factory):
    repo = build(tmp_path, retest=True)
    junit(repo)
    write_as("v0.7.6", repo, tmp_path_factory, "coverage", "--reuse-artifacts")
    junit(repo, "t::c0")
    (repo / "retest.txt").write_text("pass", encoding="utf-8")
    write_as("v0.7.6", repo, tmp_path_factory, "verify", "--reuse-artifacts", "--no-tighten")
    assert "retried_passes" not in newest_run(repo)["lanes"]["py"], "0.7.6 wrote no such key"
    junit(repo, "t::c0")
    (repo / "retest.txt").write_text("fail", encoding="utf-8")

    res = run_cli(repo, "verify", "--reuse-artifacts", "--no-tighten")

    assert res.returncode == 8, res.stdout + res.stderr
    assert ("warning: lane 'py': baseline run 2 was written by crapkit 0.7.6, which kept a "
            "failure that passed its flake retry in its failure list, so its failures are "
            "compared with run 1's") in res.stderr, res.stderr


@pytest.mark.parametrize("writer", ["v0.4.9", "v0.7.6"])
def test_a_failure_an_older_coverage_run_recorded_is_still_forgiven(
        tmp_path, tmp_path_factory, writer):
    """Only verify runs a flake retry, so a coverage run's failure list means
    what it says under every release."""
    repo = build(tmp_path, retest=False)
    junit(repo, "t::c0")
    write_as(writer, repo, tmp_path_factory, "coverage", "--reuse-artifacts")
    baseline = newest_run(repo)
    assert (baseline["kind"], baseline["tool_versions"]["crapkit"]) == ("coverage", version_of(writer))

    res = run_cli(repo, "verify", "--reuse-artifacts", "--no-tighten", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["forgiven_failures"] == ["t::c0"]


def test_a_failure_behind_a_0_8_0_verify_over_a_deleted_junit_is_still_forgiven(
        tmp_path, tmp_path_factory):
    """The 0.8.0 verify is the newest trusted run and recorded no failure list,
    so this tree reads the list off the coverage run behind it, where t::c0
    already failed. Read as 'nothing failed', t::c0 was new, exit 8."""
    repo = build(tmp_path, retest=False)
    junit(repo, "t::c0")
    write_as("v0.8.0", repo, tmp_path_factory, "coverage", "--reuse-artifacts")
    baseline = verify_over_a_deleted_junit_as_0_8_0(repo, tmp_path_factory)
    junit(repo, "t::c0")

    res = run_cli(repo, "verify", "--reuse-artifacts", "--no-tighten", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert (payload["baseline_run"], payload["new_failures"]) == (baseline["id"], [])
    assert payload["forgiven_failures"] == ["t::c0"]


@pytest.mark.parametrize(("argv", "line"), [
    pytest.param(("coverage", "--reuse-artifacts"),
                 "lane 'py' ran 12 tests, 8 fewer than run {counted}'s 20 (the last trusted "
                 "run, run {gap}, recorded no test count for it)", id="coverage"),
    pytest.param(("verify", "--reuse-artifacts", "--no-tighten"),
                 "warning: lane 'py' runs 8 fewer tests than run {counted} (baseline run {gap} "
                 "recorded no test count for it)", id="verify"),
])
def test_a_drop_behind_a_0_8_0_verify_over_a_deleted_junit_is_still_named(
        tmp_path, tmp_path_factory, argv, line):
    """Both suite-size checks compare 12 tests with the 20 the coverage run
    counted, and name that run and the one that counted nothing. Read as the
    newest trusted run's count, the missing count hid the drop."""
    repo = build(tmp_path, retest=False)
    passing_junit(repo, 20)
    write_as("v0.8.0", repo, tmp_path_factory, "coverage", "--reuse-artifacts")
    gap = verify_over_a_deleted_junit_as_0_8_0(repo, tmp_path_factory)["id"]
    passing_junit(repo, 12)

    res = run_cli(repo, *argv)

    assert res.returncode == 0, res.stdout + res.stderr
    assert line.format(counted=runs(repo)[0]["id"], gap=gap) in res.stderr, res.stderr
