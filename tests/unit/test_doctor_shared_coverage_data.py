"""doctor names coveragepy lanes whose data files one of them deletes and
combines when the config lets them run at once.

Two pytest-cov lanes started from one directory both write `.coverage` there.
Under max_parallel_lanes = 2 one of them intermittently died with
`sqlite3.OperationalError: table coverage_schema already exists` and the run
came back partial, exit 5, with nothing in doctor's report to say why. A lane
left on `.coverage` beside one on `.coverage.b` failed the same way, with
WinError 32, after doctor had learned only the first case.
"""
import json

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main


def _coveragepy_lanes(root, parallel: int, **files: str) -> None:
    """Both template lanes on coveragepy; `files` maps a lane name to the
    COVERAGE_FILE its env sets."""
    toml = root / "crapkit.toml"
    text = toml.read_text(encoding="utf-8").replace('parser = "istanbul"', 'parser = "coveragepy"')
    for name, data_file in files.items():
        text = text.replace(f'artifact = "coverage/{name}.json"',
                            f'artifact = "coverage/{name}.json"\n'
                            f'env = {{ COVERAGE_FILE = "{data_file}" }}')
    toml.write_text(text.replace("target = 6", f"target = 6\nmax_parallel_lanes = {parallel}"),
                    encoding="utf-8")


def _shared_warns(root, capsys) -> list[str]:
    assert main(["doctor", "--json", "--repo", str(root)]) in (0, 1)
    warnings = json.loads(capsys.readouterr().out)["warnings"]
    return [w for w in warnings if "coverage.py data files" in w]


_WARN = ("lanes 'unit', 'ui' write coverage.py data files that one of them deletes and "
         "combines, and max_parallel_lanes = 2 can start them together, which can fail one "
         "lane and leave the run partial; give each lane its own COVERAGE_FILE, for example "
         "env = { COVERAGE_FILE = \".coverage.unit\" } in lane 'unit' and "
         "env = { COVERAGE_FILE = \".coverage.ui\" } in lane 'ui'")


def test_parallel_lanes_writing_one_data_file_warn(repo, capsys):
    _coveragepy_lanes(repo, 2)

    [warn] = _shared_warns(repo, capsys)

    assert warn == _WARN, warn


def test_a_lane_left_on_the_default_file_warns_beside_one_on_a_dotted_name(repo, capsys):
    """Lane 'unit' writes .coverage, and pytest-cov deletes and combines every
    .coverage.* beside it, lane 'ui''s .coverage.ui and its in-flight pieces
    among them. Run at once, one lane failed with WinError 32 on such a piece
    and the run came back partial, while doctor said nothing."""
    _coveragepy_lanes(repo, 2, ui=".coverage.ui")

    assert _shared_warns(repo, capsys) == [_WARN]


def test_lanes_that_each_set_their_own_file_say_nothing(repo, capsys):
    _coveragepy_lanes(repo, 2, unit=".coverage.unit", ui=".coverage.ui")

    assert _shared_warns(repo, capsys) == []


def test_serial_lanes_writing_one_data_file_say_nothing(repo, capsys):
    _coveragepy_lanes(repo, 1)

    assert _shared_warns(repo, capsys) == []


def test_tune_holds_the_lane_slots_at_one_and_says_which_lanes(repo, capsys):
    _coveragepy_lanes(repo, 1)

    assert main(["doctor", "--tune", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()

    assert out[2] == "max_parallel_lanes = 1", out
    assert out[3].startswith("# held at 1: lanes 'unit', 'ui' write coverage.py data files"), out


def test_tune_holds_the_slots_for_a_lane_left_on_the_default_file(repo, capsys):
    _coveragepy_lanes(repo, 2, ui=".coverage.ui")

    assert main(["doctor", "--tune", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out.splitlines()

    assert out[2] == "max_parallel_lanes = 1", out
