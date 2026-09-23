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
