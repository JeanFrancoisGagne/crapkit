"""A new failure that passes its flake retry, followed through everything verify says.

A lane that declares retest_command reruns just the newly failed ids, and an id
that passes the rerun leaves new_failures. These tests follow that id into the
--json payload, the OK line, and the run verify stores, which is the baseline a
later verify measures against. Each is driven through `main`, the entry point
`python -m crapkit` uses, over a tmp repo whose lanes read canned artifacts.
"""
import json

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit.cli import main
from crapkit.store import SnapshotStore
from crapkit.verify import evaluate, settle_verdict

TEST_FILE = "src/app.test.ts"
FLAKY = f"{TEST_FILE}::renders"
OLD = f"{TEST_FILE}::old"


def _junit(repo, *failing: str) -> None:
    """A full run's report: `renders` and `old` both ran, the named ids failed."""
    cases = "".join(
        f'<testcase classname="{TEST_FILE}" name="{name}">'
        f'{"<failure>boom</failure>" if f"{TEST_FILE}::{name}" in failing else ""}</testcase>'
        for name in ("renders", "old"))
    (repo / "junit.xml").write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")


def _rerun(repo, *, passes: bool) -> None:
    """repair.py is the lane's retest_command: it rewrites the junit with the
    rerun's result for `renders`, the only id a retry reruns here."""
    body = "" if passes else "<failure>boom</failure>"
    report = (f'<testsuite><testcase classname="{TEST_FILE}" name="renders">{body}'
              "</testcase></testsuite>")
    (repo / "repair.py").write_text(
        f"import pathlib\npathlib.Path('junit.xml').write_text({report!r}, encoding='utf-8')\n",
        encoding="utf-8")


def _declare_retest(repo) -> None:
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(text.replace(
        'artifact = "coverage/unit.json"',
        'artifact = "coverage/unit.json"\nresults_artifact = "junit.xml"\n'
        'retest_command = "python repair.py {tests}"'), encoding="utf-8")


@pytest.fixture()
def retry_repo(repo, capsys):
    """The `unit` lane reports junit and declares a retest, and one coverage run
    measured it with `old` already failing: the baseline carries that failure."""
    _declare_retest(repo)
    _junit(repo, OLD)
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def verify(repo, capsys, *flags: str) -> tuple[int, str, str]:
    code = main(["verify", "--reuse-artifacts", *flags, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


# --- dirty attribution after the retry -----------------------------------------

def test_a_dirty_failure_that_passed_its_retry_is_not_named_dirty(retry_repo, capsys):
    """dirty_failures is the subset of new_failures whose test file has
    uncommitted edits, so a failure the retry cleared leaves both lists."""
    (retry_repo / TEST_FILE).write_text("export const edited = 1;\n", encoding="utf-8")
    _junit(retry_repo, FLAKY)
    _rerun(retry_repo, passes=True)

    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 0, err
    assert payload["new_failures"] == []
    assert payload["dirty_failures"] == []
    assert payload["dirty_findings"] == 0


def test_a_dirty_failure_that_failed_its_retry_stays_named_dirty(retry_repo, capsys):
    (retry_repo / TEST_FILE).write_text("export const edited = 1;\n", encoding="utf-8")
    _junit(retry_repo, FLAKY)
    _rerun(retry_repo, passes=False)

    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 8, err
    assert payload["new_failures"] == [FLAKY]
    assert payload["dirty_failures"] == [FLAKY]


def test_settling_keeps_the_dirty_failures_that_are_still_new():
    """Two dirty new failures, one of which a retry cleared: the survivor keeps
    its dirty tag and the cleared one loses it."""
    found = evaluate(fresh=[], changed_ranges={}, ratchet=[], baseline_failures=set(),
                     fresh_failures={"tests/a.py::x", "tests/b.py::y"}, target=6,
                     dirty_paths={"tests/a.py", "tests/b.py"})

    settled = settle_verdict(found._replace(new_failures=["tests/b.py::y"]))

    assert settled.dirty_failures == ["tests/b.py::y"]
    assert settled.ok is False


# --- the OK line and the payload name the retried pass apart -----------------

def test_the_ok_line_calls_a_retried_pass_by_its_own_name(retry_repo, capsys):
    """The baseline failed `old`; this run failed `old` and `renders`, and
    `renders` passed its rerun. Only `old` is an unchanged failure: the
    baseline never failed `renders`."""
    _junit(retry_repo, OLD, FLAKY)
    _rerun(retry_repo, passes=True)

    code, out, err = verify(retry_repo, capsys)

    ok_line = out.splitlines()[0]
    assert code == 0, err
    assert ok_line.startswith("verify OK @ "), ok_line
    assert "(1 unchanged failure forgiven, first src/app.test.ts::old)" in ok_line, ok_line
    assert "(1 new failure passed on rerun, first src/app.test.ts::renders)" in ok_line, ok_line


def test_the_json_verdict_keeps_forgiven_and_retried_apart(retry_repo, capsys):
    _junit(retry_repo, OLD, FLAKY)
    _rerun(retry_repo, passes=True)

    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 0, err
    assert payload["forgiven_failures"] == [OLD]
    assert payload["retried_passes"] == [FLAKY]
    assert payload["new_failures"] == []


def test_a_run_with_no_retry_names_no_retried_pass(retry_repo, capsys):
    _junit(retry_repo, OLD)

    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 0, err
    assert payload["forgiven_failures"] == [OLD]
    assert payload["retried_passes"] == []


# --- the run verify stores, and the baseline it becomes ----------------------

def test_a_retried_pass_is_new_again_when_it_fails_against_that_run(retry_repo, capsys):
    """Verify A: `renders` failed, passed its rerun, and A passed, so A is the
    next baseline. Verify B: `renders` fails and fails its rerun too. A never
    counted `renders` as failing, so B's failure is new, not forgiven."""
    _junit(retry_repo, FLAKY)
    _rerun(retry_repo, passes=True)
    first, out, err = verify(retry_repo, capsys, "--json")
    assert first == 0, err
    stored_run = json.loads(out)["run_id"]

    _junit(retry_repo, FLAKY)
    _rerun(retry_repo, passes=False)
    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 8, out + err
    assert payload["baseline_run"] == stored_run, "B must measure against A, the retried run"
    assert payload["new_failures"] == [FLAKY]
    assert payload["forgiven_failures"] == []


def test_a_failure_the_stored_run_carried_is_still_forgiven(retry_repo, capsys):
    """The same stored run keeps forgiving `old`, which it failed without a retry."""
    _junit(retry_repo, OLD, FLAKY)
    _rerun(retry_repo, passes=True)
    first, out, err = verify(retry_repo, capsys, "--json")
    assert first == 0, err
    stored_run = json.loads(out)["run_id"]

    _junit(retry_repo, OLD)
    code, out, err = verify(retry_repo, capsys, "--json")

    payload = json.loads(out)
    assert code == 0, err
    assert payload["baseline_run"] == stored_run
    assert payload["forgiven_failures"] == [OLD]


def test_the_stored_run_keeps_the_first_attempt_and_names_the_retried_pass(retry_repo, capsys):
    """`failures` stays the lane's own report, so the release guard still sees a
    lane that failed a test; `retried_passes` says which of them passed on rerun."""
    _junit(retry_repo, OLD, FLAKY)
    _rerun(retry_repo, passes=True)
    assert verify(retry_repo, capsys)[0] == 0

    lane = SnapshotStore(retry_repo / ".crapkit" / "crap.sqlite").list_runs()[-1]["lanes"]["unit"]

    assert lane["failures"] == [OLD, FLAKY]
    assert lane["retried_passes"] == [FLAKY]
