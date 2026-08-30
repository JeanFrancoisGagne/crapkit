"""[crapkit] max_parallel_lanes, through the real CLI over a real git repo.

The two lanes RENDEZVOUS: each drops a marker, then waits for the other's before
writing its artifact. Serial, the first lane waits alone and times out; at
max_parallel_lanes = 2 they meet. That is the difference between "the flag is
wired" and "the lanes actually overlapped".

The identity test is the point of the feature: same tree, same artifacts, same
export bytes and same JSON, however many lanes ran at once.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner

PY = sys.executable.replace("\\", "/")

APP_TS = (
    "export function dispatch(kind: string): number {\n"
    "  if (kind === 'a') {\n"
    "    return 1;\n"
    "  }\n"
    "  return 2;\n"
    "}\n"
)

# argv: lane name, peer name. Writes coverage/<lane>.json for src/<lane>.ts.
LANE_PY = (
    "import json, os, pathlib, sys, time\n"
    "name, peer = sys.argv[1], sys.argv[2]\n"
    'pathlib.Path(name + ".started").write_text("1", encoding="utf-8")\n'
    'deadline = time.monotonic() + 3.0\n'
    'peer_marker = pathlib.Path(peer + ".started")\n'
    "while time.monotonic() < deadline and not peer_marker.exists():\n"
    "    time.sleep(0.02)\n"
    'pathlib.Path(name + ".met").write_text("yes" if peer_marker.exists() else "no",\n'
    '                                       encoding="utf-8")\n'
    'src = os.path.join(os.getcwd(), "src", name + ".ts")\n'
    'artifact = {src: {"path": src,\n'
    '    "fnMap": {"0": {"name": "dispatch", "decl": {"start": {"line": 1}},\n'
    '                    "loc": {"start": {"line": 1}, "end": {"line": 6}}}},\n'
    '    "f": {"0": 2},\n'
    '    "branchMap": {"0": {"loc": {"start": {"line": 2}},\n'
    '                        "locations": [{"start": {"line": 2}}, {"start": {"line": 3}}]}},\n'
    '    "b": {"0": [1, 0]}}}\n'
    'pathlib.Path("coverage").mkdir(exist_ok=True)\n'
    'pathlib.Path("coverage", name + ".json").write_text(json.dumps(artifact), encoding="utf-8")\n'
)

CRAPKIT_TOML = f"""[crapkit]
target = 6

[[scope]]
name = "alpha"
paths = ["src/alpha.ts"]
languages = ["typescript"]

[[scope]]
name = "beta"
paths = ["src/beta.ts"]
languages = ["typescript"]

[[lane]]
name = "alpha"
command = "{PY} lane.py alpha beta"
artifact = "coverage/alpha.json"
parser = "istanbul"
scopes = ["alpha"]

[[lane]]
name = "beta"
command = "{PY} lane.py beta alpha"
artifact = "coverage/beta.json"
parser = "istanbul"
scopes = ["beta"]
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


_run_cli = cli_runner(timeout=300)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "mini"
    (root / "src").mkdir(parents=True)
    for name in ("alpha", "beta"):
        (root / "src" / f"{name}.ts").write_text(APP_TS, encoding="utf-8")
    (root / "crapkit.toml").write_text(CRAPKIT_TOML, encoding="utf-8")
    (root / "lane.py").write_text(LANE_PY, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\ncoverage/\n*.started\n*.met\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return root


def _set_parallel(repo: Path, lanes: int) -> None:
    config = repo / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8")
                      .replace("[crapkit]\n", f"[crapkit]\nmax_parallel_lanes = {lanes}\n"),
                      encoding="utf-8")


def _clear_markers(repo: Path) -> None:
    for marker in list(repo.glob("*.started")) + list(repo.glob("*.met")):
        marker.unlink()


def _met(repo: Path, lane: str) -> str:
    return (repo / f"{lane}.met").read_text(encoding="utf-8")


def test_serial_lanes_never_meet_each_other(repo: Path):
    res = _run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stderr
    assert _met(repo, "alpha") == "no", "serially, the first lane waits alone"
    assert sorted(json.loads(res.stdout)["lanes"]) == ["alpha", "beta"]


def test_two_parallel_lanes_overlap_in_time(repo: Path):
    _set_parallel(repo, 2)
    res = _run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stderr
    assert _met(repo, "alpha") == "yes" and _met(repo, "beta") == "yes"
    assert sorted(json.loads(res.stdout)["lanes"]) == ["alpha", "beta"]


def test_parallel_scores_byte_identically_to_serial(repo: Path):
    assert _run_cli(repo, "coverage").returncode == 0  # warm the analysis cache for both
    _clear_markers(repo)
    serial = _run_cli(repo, "coverage", "--json", "--export", "serial.tsv")
    assert serial.returncode == 0, serial.stderr
    _clear_markers(repo)
    _set_parallel(repo, 2)
    parallel = _run_cli(repo, "coverage", "--json", "--export", "parallel.tsv")
    assert parallel.returncode == 0, parallel.stderr

    assert (repo / "parallel.tsv").read_bytes() == (repo / "serial.tsv").read_bytes()
    first, second = json.loads(serial.stdout), json.loads(parallel.stdout)
    for summary in (first, second):
        summary.pop("run_id")
    assert second == first


def test_only_a_raised_knob_narrates_the_lanes(repo: Path):
    serial = _run_cli(repo, "coverage")
    assert "started" not in serial.stderr, "the default run says exactly what it always said"
    _clear_markers(repo)
    _set_parallel(repo, 2)
    parallel = _run_cli(repo, "coverage")
    for lane in ("alpha", "beta"):
        assert f"lane {lane!r} started" in parallel.stderr
        assert f"lane {lane!r} finished" in parallel.stderr


def test_a_lane_that_fails_in_parallel_is_still_reported_and_skipped(repo: Path):
    config = repo / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8")
                      .replace(f'"{PY} lane.py beta alpha"', f'"{PY} -c \\"import sys; sys.exit(3)\\""'),
                      encoding="utf-8")
    _set_parallel(repo, 2)
    res = _run_cli(repo, "coverage", "--json")
    assert res.returncode == 5, res.stderr
    assert "lane 'beta' FAILED" in res.stderr
    assert list(json.loads(res.stdout)["lanes"]) == ["alpha"], "the survivor still scores"
