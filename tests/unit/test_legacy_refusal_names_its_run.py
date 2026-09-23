"""The legacy-identity refusal names the stored run it read.

The sentence used to name only the file and the function. A store can hold a
fresh run beside the legacy one, so a refusal that does not say which run it
read sends the user to rerun coverage for nothing. A caller that knows why the
run is pinned passes its own advice in place of the default.
"""
import subprocess
from contextlib import closing

import pytest

from crapkit.cli import main
from crapkit.errors import ToolError
from crapkit.keys import refuse_ambiguous, require_unambiguous
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')


def _row(start, occurrence, path="src/a.ts"):
    return ScoredRow("web", path, "(anonymous)", start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, occurrence)


def _write(store, rows):
    return store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"}, rows=rows)


def test_a_stored_read_names_the_run_that_holds_the_legacy_twins(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        _write(store, [_row(1, 1), _row(5, 1)])
        legacy = _write(store, [_row(1, 0), _row(1, 0)])
        with pytest.raises(ToolError) as refused:
            store.function_span(legacy, "src/a.ts", "(anonymous)")
    assert str(refused.value) == (
        f"ambiguous legacy function identity in src/a.ts: (anonymous) in run {legacy}; "
        "refresh analysis before selecting or comparing these functions")


def test_explain_names_the_run_it_read(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        legacy = _write(store, [_row(1, 0), _row(1, 0)])
    assert main(["explain", "src/a.ts", "(anonymous)", "--repo", str(tmp_path)]) == 5
    assert f"src/a.ts: (anonymous) in run {legacy};" in capsys.readouterr().err


def test_every_run_holding_a_group_is_named_and_advice_replaces_the_default():
    with pytest.raises(ToolError) as refused:
        refuse_ambiguous({("a.ts", "f"): (7, 3), ("b.ts", "g"): ()},
                         advice="failed verify run 9 pins run 3")
    assert str(refused.value) == ("ambiguous legacy function identity in a.ts: f in runs 3, 7; "
                                  "b.ts: g; failed verify run 9 pins run 3")


def test_groups_with_no_runs_keep_the_default_sentence():
    with pytest.raises(ToolError) as refused:
        refuse_ambiguous({("a.ts", "f")})
    assert str(refused.value) == ("ambiguous legacy function identity in a.ts: f; "
                                  "refresh analysis before selecting or comparing these functions")
    refuse_ambiguous({})


def test_rows_read_from_a_named_run_carry_it_into_the_refusal():
    rows = [_row(1, 0), _row(1, 0)]
    with pytest.raises(ToolError, match=r"src/a\.ts: \(anonymous\) in run 4; see run 2$"):
        require_unambiguous(rows, run_id=4, advice="see run 2")
    with pytest.raises(ToolError, match=r"src/a\.ts: \(anonymous\); refresh analysis"):
        require_unambiguous(rows)
