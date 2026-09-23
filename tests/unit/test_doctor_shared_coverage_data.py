"""doctor names coveragepy lanes that write one data file when the config lets
them run at once.

Two pytest-cov lanes started from one directory both write `.coverage` there.
Under max_parallel_lanes = 2 one of them intermittently died with
`sqlite3.OperationalError: table coverage_schema already exists` and the run
came back partial, exit 5, with nothing in doctor's report to say why.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main


def _coveragepy_lanes(root, parallel: int) -> None:
    toml = root / "crapkit.toml"
    text = toml.read_text(encoding="utf-8").replace('parser = "istanbul"', 'parser = "coveragepy"')
    toml.write_text(text.replace("target = 6", f"target = 6\nmax_parallel_lanes = {parallel}"),
                    encoding="utf-8")


def _shared_warns(root, capsys) -> list[str]:
    assert main(["doctor", "--json", "--repo", str(root)]) in (0, 1)
    warnings = json.loads(capsys.readouterr().out)["warnings"]
    return [w for w in warnings if "one coverage.py data file" in w]


def test_parallel_lanes_writing_one_data_file_warn(repo, capsys):
    _coveragepy_lanes(repo, 2)

    [warn] = _shared_warns(repo, capsys)

    assert warn == ("lanes 'unit', 'ui' write one coverage.py data file, and "
                    "max_parallel_lanes = 2 can start them together, which can lose one "
                    "to sqlite3.OperationalError and leave the run partial; give each its "
                    "own, for example env = { COVERAGE_FILE = \".coverage.unit\" }"), warn


def test_serial_lanes_writing_one_data_file_say_nothing(repo, capsys):
    _coveragepy_lanes(repo, 1)

    assert _shared_warns(repo, capsys) == []


def test_tune_holds_the_lane_slots_at_one_and_says_which_lanes(repo, capsys):
    _coveragepy_lanes(repo, 1)

    assert main(["doctor", "--tune", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()

    assert out[2] == "max_parallel_lanes = 1", out
    assert out[3].startswith("# held at 1: lanes 'unit', 'ui' write one coverage.py data file"), out
