"""The committed fixture lanes start no machinery their tests never measure.

Every e2e run that measures mini_repo started its py lane as `pytest -n 2`: two
xdist workers for a two-test package, 0.77 s of wall against 0.33 s with no
workers. Under the suite's own coverage run each lane's Python child also
started coverage, 76-81 ms apiece, measuring code no test reads. One test is
about xdist fragments combining, and it alone keeps two workers, in its own
fixture file.
"""
from pathlib import Path
import tomllib

from crapkit.config import shell_words

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MINI = FIXTURES / "mini_repo" / "crapkit.toml"
XDIST = FIXTURES / "mini_repo_xdist" / "crapkit.toml"


def _lanes(config):
    return {lane["name"]: lane for lane in tomllib.loads(config.read_text(encoding="utf-8"))["lane"]}


def _workers(command):
    words = shell_words(command)
    return words[words.index("-n") + 1]


def test_the_mini_repo_py_lane_runs_pytest_without_xdist_workers():
    assert _workers(_lanes(MINI)["py"]["command"]) == "0"


def test_the_xdist_combine_fixture_is_the_mini_repo_config_with_two_workers():
    mini = MINI.read_text(encoding="utf-8")

    assert _workers(_lanes(XDIST)["py"]["command"]) == "2"
    assert XDIST.read_text(encoding="utf-8") == mini.replace(" -n 0 ", " -n 2 ")


def test_every_fixture_lane_opts_its_children_out_of_subprocess_coverage():
    lanes = [lane for config in sorted(FIXTURES.rglob("crapkit.toml"))
             for lane in _lanes(config).values()]

    assert lanes, "no committed fixture lane; this contract lost its subject"
    assert [lane.get("env", {}).get("COVERAGE_PROCESS_CONFIG") for lane in lanes] == [""] * len(lanes)
