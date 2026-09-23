"""doctor --tune finds a lane's recorded duration after its artifact moved.

Stamps are filed under the artifact path, and every stamp names the lane that
wrote it. The scheduler falls back to that name when the path finds nothing, so
a renamed artifact keeps its lane first in the start order. doctor --tune read
the path alone and printed "no durations recorded yet" for the same lane.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main


def _stamps(root, stamps: dict) -> None:
    path = root / ".crapkit" / "artifacts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stamps), encoding="utf-8")


def _cost_line(root, capsys) -> str:
    code = main(["doctor", "--tune", "--repo", str(root)])
    out = capsys.readouterr().out
    assert code == 0, out
    return out.splitlines()[-1]


def test_a_stamp_under_the_old_artifact_path_still_costs_its_lane(repo, capsys):
    _stamps(repo, {"coverage/old-unit.json": {"commit": "abc", "lane": "unit", "seconds": 300.0}})

    assert _cost_line(repo, capsys).startswith("# lane cost: 300.0s serial -> ~300.0s")


def test_the_stamp_at_the_declared_path_wins_over_an_older_one(repo, capsys):
    _stamps(repo, {"coverage/old-unit.json": {"commit": "abc", "lane": "unit", "seconds": 300.0},
                   "coverage/unit.json": {"commit": "def", "lane": "unit", "seconds": 5.0}})

    assert _cost_line(repo, capsys).startswith("# lane cost: 5.0s serial -> ~5.0s")


def test_a_stamp_another_lane_wrote_costs_nothing_here(repo, capsys):
    _stamps(repo, {"coverage/old-api.json": {"commit": "abc", "lane": "api", "seconds": 300.0}})

    assert _cost_line(repo, capsys) == ("# lane cost: no durations recorded yet — "
                                        "suggested from the cpu count alone")
