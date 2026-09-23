"""When seed or prune walk back past a failed verify, the line names what they left.

The line already named the failed verify. It did not name the newer run the rule
passed over, so a reader saw an older run id and no way to reach the newer one.
Now it names both ids and the flag that reads the newer run (#75).
"""
import argparse
from pathlib import Path

import lizard
import pytest

from crapkit.analyze import ANALYSIS_VERSION
from crapkit.cli.ratchet_cmds import _skip_note, cmd_ratchet
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parent.parent.parent
MEASURED = {"analysis_version": str(ANALYSIS_VERSION), "lizard": lizard.version}
CONFIG = ('[crapkit]\ntarget = 6\n\n'
          '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n')


def scored() -> ScoredRow:
    return ScoredRow("src", "src/a.py", "hot( n )", 1, 9, 8, 8, 8, 5, 1, 1,
                     0.0, "measured", 72.0, "decompose")


def repo_with_store(tmp_path: Path) -> tuple[Path, SnapshotStore]:
    (tmp_path / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    return tmp_path, SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")


def write(store: SnapshotStore, *, kind: str = "coverage", ok: bool | None = None) -> int:
    run_id = store.write_run(commit="a" * 40, tool_versions=MEASURED, rows=[scored()],
                             kind=kind, lanes={"unit": {}})
    if ok is not None:
        store.set_verdict_ok(run_id, ok, findings=0 if ok else 1)
    return run_id


@pytest.mark.parametrize("action", ["seed", "prune"])
def test_the_line_names_the_failed_verify_and_the_newer_run_behind_it(tmp_path, capsys, action):
    repo, store = repo_with_store(tmp_path)
    old = write(store)
    failed = write(store, kind="verify", ok=False)
    new = write(store)

    assert cmd_ratchet(argparse.Namespace(action=action, repo=str(repo), baseline=None)) == 0

    line = capsys.readouterr().out.strip()
    assert f"vs run {old} (" in line, line
    assert line.endswith(f", skipped failed verify run {failed} and the newer run {new} "
                         f"(pass `--baseline {new}` to read it)"), line


def test_a_failure_with_no_newer_run_behind_it_keeps_the_short_clause(tmp_path, capsys):
    repo, store = repo_with_store(tmp_path)
    write(store)
    failed = write(store, kind="verify", ok=False)

    assert cmd_ratchet(argparse.Namespace(action="seed", repo=str(repo), baseline=None)) == 0

    assert capsys.readouterr().out.strip().endswith(f", skipped failed verify run {failed}")


def test_the_ratchet_page_prints_the_clause_the_seed_line_appends():
    page = (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8")

    assert _skip_note([{"id": 2}], {"id": 3}) in page
