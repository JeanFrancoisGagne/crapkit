"""A function's history leaves out only the runs that cannot place its twins.

A run written before occurrence was recorded holds same-line twins it cannot
tell apart. One such run used to refuse the whole history, so `explain`,
`brief` and the history tool refused a function the newest run positions
cleanly, and `runs prune` keeps that run as an identity witness, so the
refusal never cleared.
"""
import json
import subprocess
from contextlib import closing

from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')


def _row(start, occurrence, crap, name="(anonymous)"):
    return ScoredRow("web", "src/a.ts", name, start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", crap, "add-tests", 0, occurrence)


def _write(store, rows, commit="c0ffee"):
    return store.write_run(commit=commit, tool_versions={"analysis_version": "10"}, rows=rows)


def _legacy_then_fresh(store):
    """Run 1 cannot tell its line-1 twins apart; run 2 puts them on lines 1 and 5."""
    legacy = _write(store, [_row(1, 0, 12.0), _row(1, 0, 20.0), _row(9, 0, 7.0, "f")])
    fresh = _write(store, [_row(1, 1, 12.0), _row(5, 1, 20.0), _row(9, 1, 8.0, "f")])
    return legacy, fresh


def _crap_by_run(history):
    return [(h["run_id"], h["crap"]) for h in history]


def test_history_of_a_twin_leaves_out_the_run_that_cannot_place_it(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        _legacy, fresh = _legacy_then_fresh(store)
        assert _crap_by_run(store.function_history("src/a.ts", "(anonymous)#2")) == [(fresh, 20.0)]
        assert _crap_by_run(store.function_history("src/a.ts", "(anonymous)")) == [(fresh, 12.0)]


def test_a_legacy_run_keeps_its_rows_for_every_group_it_can_place(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        legacy, fresh = _legacy_then_fresh(store)
        assert _crap_by_run(store.function_history("src/a.ts", "f")) == [(legacy, 7.0), (fresh, 8.0)]


def test_positioned_same_line_twins_keep_every_run(tmp_path):
    store = SnapshotStore(tmp_path / "s.sqlite")
    with closing(store._conn):
        first = _write(store, [_row(1, 1, 12.0), _row(1, 2, 20.0)])
        second = _write(store, [_row(1, 1, 13.0), _row(1, 2, 21.0)])
        assert _crap_by_run(store.function_history("src/a.ts", "(anonymous)#2")) == [
            (first, 20.0), (second, 21.0)]


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


def _repo(root):
    _git(root, "init", "-q")
    (root / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src/a.ts").write_text("".join(f"// {n}\n" for n in range(1, 12)), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    with closing(store._conn):
        return _legacy_then_fresh(store)


def test_explain_and_brief_answer_for_a_twin_a_legacy_run_holds(tmp_path, capsys):
    _legacy, fresh = _repo(tmp_path)
    assert main(["explain", "src/a.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 0
    explained = json.loads(capsys.readouterr().out)["functions"][0]
    assert _crap_by_run(explained["history"]) == [(fresh, 20.0)]
    assert main(["brief", "src/a.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 0
    brief = json.loads(capsys.readouterr().out)
    assert brief["regrowth"]["history"] == [[fresh, 3]]
