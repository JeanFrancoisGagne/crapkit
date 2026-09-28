"""kit.drive: one CLI call in process or spawned, the MCP session, the clock and the store."""
from pathlib import Path
import sqlite3
import sys

import pytest

from accuracy.kit import drive, repos

SEED = Path(__file__).resolve().parent / "fixtures" / "seed"
YEAR = 365 * 86_400


@pytest.fixture
def seeded(make_repo):
    return make_repo(repos.tree_spec(SEED))


def _rows(driver, run_id):
    return driver.store("select start, end, ccn_std, ccn_mod, nloc, params, cognitive "
                        "from functions where run_id = ? order by identity_id, start", (run_id,))


@pytest.mark.nightly
@pytest.mark.process
def test_in_process_and_spawned_calls_answer_alike(seeded):
    inline = drive.Driver(seeded.root)
    spawned = drive.Driver(seeded.root, spawn=True)

    assert inline.run("inventory").code == 0
    assert spawned.run("inventory").code == 0

    assert _rows(inline, 1) == _rows(spawned, 2)
    assert len(_rows(inline, 1)) == 9


@pytest.mark.process
@pytest.mark.parametrize("argv", [("coverage", "--no-such-flag"), ("frobnicate",)])
def test_a_usage_error_or_unknown_command_is_unsupported(seeded, argv):
    with pytest.raises(drive.DriveUnsupported, match=argv[0]):
        drive.Driver(seeded.root).run(*argv)


@pytest.mark.nightly
@pytest.mark.process
def test_a_refusal_is_an_answer_not_a_usage_error(seeded):
    result = drive.Driver(seeded.root).run("worklist")

    assert result.code == 1
    assert "no snapshot" in result.stderr or "no run" in result.stderr


def test_json_from_prose_fails_naming_the_command():
    result = drive.Result(("digest",), 0, "nothing changed\n", "")

    with pytest.raises(AssertionError, match="digest printed no JSON"):
        result.json()


def _churned(repo_root, now):
    driver = drive.Driver(repo_root, date_now=now)
    assert driver.run("coverage").code == 0
    listing = driver.json("worklist")
    return len(listing["active"]), listing["dormant_count"]


@pytest.mark.nightly
@pytest.mark.process
def test_date_now_is_the_clock_the_churn_window_reads(make_repo):
    spec = repos.tree_spec(SEED)

    assert _churned(make_repo(spec).root, repos.EPOCH + 86_400) == (1, 0)
    assert _churned(make_repo(spec).root, repos.EPOCH + 3 * YEAR) == (0, 1)


@pytest.mark.nightly
@pytest.mark.process
def test_one_mcp_session_answers_every_call_in_order(seeded):
    driver = drive.Driver(seeded.root)
    assert driver.run("coverage").code == 0

    results = driver.mcp([("list_worklist", {}), ("get_trend", {}), ("no_such_tool", {})])

    assert [result["isError"] for result in results] == [False, False, True]
    assert "active" in results[0]["structuredContent"]
    assert "no_such_tool" in results[2]["content"][0]["text"]


@pytest.mark.nightly
@pytest.mark.process
def test_the_store_is_read_with_sqlite3_and_never_written(seeded):
    driver = drive.Driver(seeded.root)
    assert driver.run("coverage").code == 0

    assert driver.store("select count(*) as n from functions") == [{"n": 9}]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        driver.store("delete from functions")


def _holding_a_crapkit_package(root: Path) -> Path:
    """A tree like crapkit's own source measured as a corpus: a crapkit/ package
    at its top, here one that stops any interpreter that imports it."""
    package = root / "crapkit"
    package.mkdir()
    for name in ("__init__.py", "__main__.py"):
        (package / name).write_text("raise SystemExit('the tree copy ran')\n", encoding="utf-8")
    return root


@pytest.mark.nightly
@pytest.mark.process
def test_a_spawned_call_runs_the_crapkit_under_test_not_the_tree_s_copy(tmp_path):
    result = drive.Driver(_holding_a_crapkit_package(tmp_path), spawn=True).run("--version")

    assert (result.code, "the tree copy ran" in result.stderr) == (0, False), result.stderr


@pytest.mark.nightly
@pytest.mark.process
def test_an_mcp_session_runs_the_crapkit_under_test_not_the_tree_s_copy(tmp_path):
    [result] = drive.Driver(_holding_a_crapkit_package(tmp_path)).mcp([("no_such_tool", {})])

    assert "no_such_tool" in result["content"][0]["text"]


def test_every_spawn_keeps_the_working_directory_off_the_import_path():
    """`-P` (Python 3.11+): without it `-m` puts the call's cwd first on sys.path."""
    assert drive.crapkit_argv("py", ("-m",), ("inventory",)) == [
        "py", "-P", "-m", "crapkit", "inventory"]


def test_the_driver_refuses_a_test_without_the_process_marker(tmp_path):
    with pytest.raises(AssertionError, match="spawns the crapkit CLI: mark it"):
        drive.Driver(tmp_path).run("--version")


def test_a_named_interpreter_forces_a_spawn(monkeypatch, tmp_path):
    monkeypatch.setenv(drive.PYTHON_ENV, sys.executable)

    driver = drive.Driver(tmp_path, date_now=12)

    assert driver.spawn is True
    assert driver.python == sys.executable
    assert driver.env["GIT_TEST_DATE_NOW"] == "12"


def test_a_strategy_tuple_becomes_the_production_type():
    fn = drive.to_crapkit("fn_coverage", ("f", 1, 4, True, 4, 1))

    assert type(fn).__name__ == "FnCoverage"
    assert fn.coverage == 0.25
