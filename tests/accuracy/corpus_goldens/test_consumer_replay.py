"""tools/accuracy/consumer_replay.py: record a consumer's lanes once, replay them under two wheels.

- hand: the lane rewrite on hand-written configs, and the refusals.
- hand: a consumer repo whose lane copies a fixture artifact is recorded;
  the recording is then edited by hand (h fully covered becomes 1 of 3
  lines), and the replay's scored export reads the edited value, worked from
  the README formula: ccn 2 at cov 1/3 is 4 * (2/3)^3 + 2 = 3.1852 at 4 dp,
  and k, absent from the recording, reads cov 0: 9 + 3 = 12.
  So the replay scored the recording and ran no lane.
- hand: a planted candidate (CRAP + 0.5 at ccn 3) moves "CRAP score"; with
  --declared-since the move needs a CHANGES row added since the ref, worked
  on a two-commit CHANGES history; a candidate that exits 5 stops the replay
  with exit 1, while the same failure on the base side is infra (exit 3).
- hand: each plant's line appears once in the file it plants, checked on
  every push, since the replays that plant run only nightly.
"""
from fractions import Fraction
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from accuracy.corpus_goldens import releases, wheels
from accuracy.kit import exact, repos, surfaces

TOOL = Path(__file__).resolve().parents[3] / "tools" / "accuracy" / "consumer_replay.py"


def _tool():
    if "accuracy_consumer_replay" not in sys.modules:
        spec = importlib.util.spec_from_file_location("accuracy_consumer_replay", TOOL)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["accuracy_consumer_replay"]


replay = _tool()
wheel_diff = releases.wheel_diff()
LANE = ('[[lane]]\nname = "py"\ncommand = "pytest --cov"\nartifact = ".crapkit/cov/py.json"\n'
        'parser = "coveragepy"\nscopes = ["src"]\n')


# --- the lane rewrite ---------------------------------------------------------------------

def test_every_lane_command_becomes_the_copy_of_its_recording():
    config = ('[crapkit]\ntarget = 6\n\n' + LANE + 'results_artifact = ".crapkit/py.xml"\n\n'
              '[[lane]]\nname = "js"\ncommand = \'npx vitest\'\nartifact = "cov/js.json"\n'
              'parser = "istanbul"\nscopes = ["web"]\n')

    rewritten = replay.recorded_config(config)
    lines = [line for line in rewritten.split("\n") if line.startswith("command")]

    assert lines == [
        'command = "' + repos.copy_command((".crapkit/recorded/py/py.json", ".crapkit/cov/py.json"),
                                           (".crapkit/recorded/py/py.xml", ".crapkit/py.xml"))
        .replace('"', '\\"') + '"',
        'command = "' + repos.copy_command((".crapkit/recorded/js/js.json", "cov/js.json"))
        .replace('"', '\\"') + '"']
    assert rewritten.count("\n") == config.count("\n")
    assert replay.lanes(rewritten) == replay.lanes(config)


def test_a_multi_line_command_refuses():
    config = LANE.replace('command = "pytest --cov"', 'command = """\npytest --cov\n"""')

    with pytest.raises(replay.ReplayError, match="multi-line"):
        replay.recorded_config(config)


def test_a_lane_without_a_command_line_refuses():
    config = LANE.replace('command = "pytest --cov"\n', '') + '\n[[scope]]\ncommand = "x"\n'

    with pytest.raises(replay.ReplayError, match="no one-line"):
        replay.recorded_config(config)


# --- CHANGES rows since a ref -------------------------------------------------------------

HEADER = "id\tdate\tkind\tcalcs\tanalysis_version\tlizard_version\tchangelog\treason\n"


def _changes(rows: list[str]) -> str:
    return HEADER + "".join(row + "\n" for row in rows)


@pytest.mark.process
def test_declared_calcs_are_the_rows_added_since_the_ref(tmp_path):
    path = "tests/accuracy/change_control/CHANGES.tsv"
    first = "C1\t2026-09-25\tnone\t\t11\t1.24.0\t\tfirst lock"
    second = "C2\t2026-09-26\tfix\tCRAP score; nloc\t12\t1.24.0\t#c2\tD13"
    spec = repos.Spec(steps=(repos.Commit(files={"a.txt": "a"}, message="before"),
                             repos.Commit(files={path: _changes([first])}, message="C1"),
                             repos.Commit(files={path: _changes([first, second])}, message="C2")))
    root = repos.build(spec, tmp_path / "repo").root

    assert wheel_diff.declared_since("HEAD~1", root) == {"CRAP score", "nloc"}
    assert wheel_diff.declared_since("HEAD~2", root) == {"CRAP score", "nloc"}
    assert wheel_diff.declared_since("HEAD", root) == set()


# --- record and replay (nightly and release: each replay measures two wheels) -------------

@pytest.mark.parametrize("name", sorted(wheels.PLANTS))
def test_every_plant_s_line_is_in_the_file_it_plants(name):
    """The replays that plant run nightly; this check runs on every push, so a
    source edit that moves a planted line fails before the nightly."""
    file_name, line, _ = wheels.PLANTS[name]

    assert (wheels.SOURCE / file_name).read_bytes().decode("utf-8").count(line) == 1


def replayed(test):
    return pytest.mark.nightly(pytest.mark.release(pytest.mark.process(test)))


# h (ccn 2) is in the recording; k (ccn 3) is not, so it scores cov 0: CRAP 12.
SOURCE = ("def h(v):\n    if v > 1:\n        return v\n    return 0\n\n\n"
          "def k(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 0\n")


def _coverage(executed: list[int], missing: list[int]) -> str:
    summary = {"covered_lines": len(executed), "num_statements": len(executed) + len(missing),
               "missing_lines": len(missing), "excluded_lines": 0, "covered_branches": 0,
               "num_branches": 0, "missing_branches": 0, "num_partial_branches": 0,
               "percent_covered": 0.0}
    region = {"executed_lines": executed, "missing_lines": missing, "excluded_lines": [],
              "executed_branches": [], "missing_branches": [], "summary": summary}
    return json.dumps({"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True,
                                "show_contexts": False, "timestamp": "2026-09-24T00:00:00"},
                       "files": {"src/h.py": {**region, "classes": {},
                                              "functions": {"h": {**region, "start_line": 1}}}},
                       "totals": summary})


def _consumer(tmp_path: Path) -> Path:
    config = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
              'languages = ["python"]\n\n'
              + repos.lane_toml("py", ".crapkit/cov/py.json", "coveragepy", ["src"], "fixture.json"))
    files = {"crapkit.toml": config, "src/h.py": SOURCE, "fixture.json": _coverage([2, 3, 4], [])}
    return repos.build(repos.Spec(steps=(repos.Commit(files=files),)), tmp_path / "consumer").root


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    work = tmp_path_factory.mktemp("consumer")
    consumer = _consumer(work)
    artifacts = work / "artifacts"
    code = replay.main(["record", "--repo", str(consumer), "--commit", "HEAD",
                        "--artifacts", str(artifacts)])
    assert code == 0
    (artifacts / "py" / "py.json").write_bytes(_coverage([2], [3, 4]).encode("utf-8"))
    return consumer, artifacts, work


def _replay(recorded, tmp_path, base: Path, candidate: Path, *extra: str) -> int:
    consumer, artifacts, _ = recorded
    return replay.main(["replay", "--repo", str(consumer), "--artifacts", str(artifacts),
                        "--base-wheel", str(base), "--candidate-wheel", str(candidate),
                        "--out", str(tmp_path / "out"), *extra])


@replayed
def test_the_recording_names_its_commit_and_lanes(recorded):
    consumer, artifacts, _ = recorded
    manifest = json.loads((artifacts / "manifest.json").read_text(encoding="utf-8"))

    assert manifest == {"commit": repos.git(consumer, "rev-parse", "HEAD").strip(),
                        "lanes": ["py"]}
    assert (artifacts / "py" / "py.json").is_file()


@replayed
def test_the_replay_scores_the_recording_and_runs_no_lane(recorded, tmp_path, capsys):
    wheel = wheels.zipped(tmp_path / "w.whl")

    code = _replay(recorded, tmp_path, wheel, wheel)
    scored = surfaces.read_tsv((tmp_path / "out" / "candidate" / "scored.tsv")
                               .read_text(encoding="utf-8"))[1]

    assert (code, capsys.readouterr().out.splitlines()[0]) == (
        0, "0 value(s) moved on the consumer repository")
    assert [(row["long_name"], row["ccn"], exact.half_even(Fraction(row["crap"]), 4))
            for row in scored] == [
        ("h( v )", "2", exact.half_even(exact.crap(2, Fraction(1, 3)), 4)),
        ("k( a , b )", "3", exact.half_even(exact.crap(3, Fraction(0)), 4))]


@replayed
def test_an_undeclared_move_fails_and_a_declared_one_passes(recorded, tmp_path, monkeypatch,
                                                            capsys):
    base = wheels.zipped(tmp_path / "base.whl")
    planted = wheels.zipped(tmp_path / "planted.whl", wheels.COGNITIVE_PLANT)
    declared = {"HEAD": set(), "v-declared": {"Cognitive complexity"}}
    monkeypatch.setattr(wheel_diff, "declared_since", declared.__getitem__)

    undeclared = _replay(recorded, tmp_path / "a", base, planted, "--declared-since", "HEAD")
    printed = capsys.readouterr().out.splitlines()
    passed = _replay(recorded, tmp_path / "b", base, planted, "--declared-since", "v-declared")

    assert (undeclared, passed) == (1, 0)
    moved = "  src/h.py k( a , b ) cognitive: 2 -> 3"  # in inventory.tsv and scored.tsv
    assert printed[:4] == ["2 value(s) moved on the consumer repository",
                           "  Cognitive complexity: 2", moved, moved]
    assert printed[-1] == ("no CHANGES row since the previous tag names "
                           "['Cognitive complexity']")


@replayed
def test_a_candidate_that_stops_fails_and_a_base_that_stops_is_infra(recorded, tmp_path):
    good = wheels.zipped(tmp_path / "good.whl")
    stops = wheels.zipped(tmp_path / "stops.whl", wheels.STOP_PLANT)

    assert _replay(recorded, tmp_path / "a", good, stops) == 1
    assert _replay(recorded, tmp_path / "b", stops, good) == 3
