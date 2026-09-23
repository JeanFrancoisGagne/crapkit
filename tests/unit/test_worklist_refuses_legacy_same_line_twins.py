"""`worklist` refuses a run whose same-line twins carry no recorded position.

A run scored before crapkit recorded each function's order within its line
stores occurrence 0 on every row, so two callbacks declared on one line share
one location. The worklist keys every verdict by location, and a verdict it
cannot place is refused, never handed to the other twin. The refusal covers the
whole run: the twins below sit under the floor, where the list never prints them.
"""
import json

from hand_scored_repo import make_repo, run, scored, write_run, write_toml

BIG = scored("big( )", 1, 10, ccn=9, cov=0.0, crap=90.0, remedy="decompose")
CALLBACK = scored("cb( )", 5, 5, ccn=1, cov=1.0, crap=1.0, remedy="ok", path="src/legacy.py",
                  occurrence=0)


def test_worklist_refuses_twins_no_run_placed_on_their_line(tmp_path, capsys):
    root = make_repo(tmp_path / "repo", files={"src/app.py": "x\n" * 40,
                                               "src/legacy.py": "y\n" * 40})
    write_toml(root, 6, floor=3)
    write_run(root, [BIG, CALLBACK, CALLBACK])

    code, out, _ = run(root, capsys, "worklist", "--json")

    assert code == 5
    assert json.loads(out)["error"] == {
        "exit": 5, "kind": "tool",
        "message": "ambiguous legacy function identity in src/legacy.py: cb( ) in run 1; "
                   "refresh analysis before selecting or comparing these functions"}
