"""An empty queue does not read the lane artifacts or ask git about them.

`next-item` loaded every artifact's dark lines before it knew whether it had an
item to print, and an empty queue never prints them. On a repo whose lane was
fresh that was 0.985 s of a 1.083 s call: the artifact parse plus five `git`
spawns asking whether the lane's sources moved.
"""
import json
import subprocess

from hand_scored_repo import git, make_repo, run, scored, write_run

DONE = scored("done( a )", 1, 10, ccn=5, cov=1.0, crap=5.0, remedy="ok")
# ccn 3 at cov 0: CRAP 3^2 + 3 = 12 over the ceiling of 6, ccn under it.
OWED = scored("owed( a )", 12, 20, ccn=3, cov=0.0, crap=12.0, remedy="add-tests")
ARTIFACT = {"meta": {"branch_coverage": True},
            "files": {"src/app.py": {"executed_lines": [12, 13], "missing_lines": [14, 15]}}}


def _fresh_lane_repo(tmp_path, rows):
    """A repo whose lane artifact is stamped at HEAD, so reading its dark lines
    means asking git whether the lane's sources moved since."""
    root = make_repo(tmp_path / "repo")
    head = git(root, "rev-parse", "HEAD")
    (root / ".crapkit" / "cov").mkdir(parents=True)
    (root / ".crapkit" / "cov" / "py.json").write_text(json.dumps(ARTIFACT), encoding="utf-8")
    (root / ".crapkit" / "artifacts.json").write_text(
        json.dumps({".crapkit/cov/py.json": {"commit": head, "lane": "py", "seconds": 1.0}}),
        encoding="utf-8")
    write_run(root, rows)
    return root, head


def _record(spawned: list, argv) -> None:
    if isinstance(argv, (list, tuple)) and argv and argv[0] == "git":
        spawned.append(list(argv))


def _run_spy(real, spawned: list):
    def spy(argv, *args, **kwargs):
        _record(spawned, argv)
        return real(argv, *args, **kwargs)
    return spy


def _popen_spy(real, spawned: list):
    class PopenSpy(real):
        def __init__(self, argv, *args, **kwargs):
            _record(spawned, argv)
            super().__init__(argv, *args, **kwargs)
    return PopenSpy


def test_an_empty_queue_prints_its_reasons_and_starts_no_git_process(tmp_path, capsys,
                                                                     monkeypatch):
    root, head = _fresh_lane_repo(tmp_path, [DONE])
    run(root, capsys, "next-item")  # caches the churn window for the call below
    spawned: list = []
    monkeypatch.setattr(subprocess, "run", _run_spy(subprocess.run, spawned))
    monkeypatch.setattr(subprocess, "Popen", _popen_spy(subprocess.Popen, spawned))

    code, out, err = run(root, capsys, "next-item")

    assert code == 0, err
    assert json.loads(out) == {
        "commit": head, "empty": True, "run_id": 1, "schema": 1, "skipped_no_lane": 0,
        "stale": False,
        "reasons": {"all_remaining_at_or_under_target": 1, "below_floor": 0,
                    "churn_window_months": 12, "excluded_by_flag": 0,
                    "no_churn_in_window": 0, "no_lane": 0, "no_lane_over_target": 0}}
    assert spawned == [], "nothing on an empty queue reads the lane artifacts"


def test_an_item_still_names_its_dark_lines(tmp_path, capsys):
    root, _ = _fresh_lane_repo(tmp_path, [DONE, OWED])

    code, out, err = run(root, capsys, "next-item")

    assert code == 0, err
    item = json.loads(out)["item"]
    assert item["function"] == "owed( a )"
    assert item["uncovered_lines"] == [14, 15], "read off the artifact, as before"
