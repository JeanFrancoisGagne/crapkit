"""End-to-end: `--reuse-artifacts` refuses the artifact a lane's last attempt
failed to write, and a salvage with new bytes written after that attempt reuses again.

On 0.4.15 a lane that stopped writing its artifact was refused by `coverage`
(exit 5, "wrote no artifact this run") and then scored by `coverage
--reuse-artifacts` (exit 0) and by `verify --reuse-artifacts` (ok true), which
made the dead lane's old numbers the next trusted baseline. Real git, real CLI.

The lane command is a counting wrapper so each test can say how many times
the lane really ran: reuse never reruns, and `--reuse-unchanged` reruns a lane
whose last attempt wrote nothing instead of trusting its stamp.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import run_cli

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

COUNTER = (
    "import pathlib, subprocess, sys\n"
    "with open('runs.txt', 'a', encoding='utf-8') as f:\n"
    "    f.write('run\\n')\n"
    "sys.exit(subprocess.run([sys.executable, 'make_cov.py']).returncode)\n"
)

ALIVE = 'command = "python run_counted.py"'
DEAD = "command = 'python -c \"import sys; sys.exit(1)\"'"

TOML = f"""[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[exclude]
globs = ["**/*.test.ts"]

[[lane]]
name = "unit"
{ALIVE}
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", "commit", "-q", "-m", message],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    (repo / "run_counted.py").write_text(COUNTER, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncoverage/\nruns.txt\n__pycache__/\n",
                                     encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    _commit(repo, "init")
    return repo


def _run_count(repo: Path) -> int:
    marker = repo / "runs.txt"
    if not marker.is_file():
        return 0
    return len(marker.read_text(encoding="utf-8").splitlines())


def _lane_command(repo: Path, line: str) -> None:
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")
    current = ALIVE if ALIVE in text else DEAD
    (repo / "crapkit.toml").write_text(text.replace(current, line), encoding="utf-8")


ARTIFACT = Path("coverage") / "coverage-final.json"


def _salvage(repo: Path) -> None:
    """What the shard recipe ends in: the artifact combined again by hand after
    the failed attempt, which holds other bytes than the refused file."""
    subprocess.run([sys.executable, "make_cov.py"], cwd=repo, check=True, capture_output=True)
    artifact = repo / ARTIFACT
    artifact.write_text(json.dumps(json.loads(artifact.read_text(encoding="utf-8")), indent=2),
                        encoding="utf-8")


def _touch(repo: Path) -> Path:
    artifact = repo / ARTIFACT
    later = artifact.stat().st_mtime_ns + 5_000_000_000
    os.utime(artifact, ns=(later, later))
    return repo


def _copy_checkout(repo: Path) -> Path:
    """A copy of the whole checkout that keeps no times; the copy is where
    the next command runs."""
    copy = repo.parent / "copy"
    shutil.copytree(repo, copy, copy_function=shutil.copy)
    return copy


def _same_bytes(repo: Path) -> Path:
    artifact = repo / ARTIFACT
    artifact.write_bytes(artifact.read_bytes())
    return repo


def _stamps_deleted(repo: Path) -> Path:
    (repo / ".crapkit" / "artifacts.json").unlink()
    return repo


def _measured_then_dead(repo: Path) -> None:
    assert run_cli(repo, "coverage", "--json").returncode == 0
    _lane_command(repo, DEAD)
    failed = run_cli(repo, "coverage", "--json")
    assert failed.returncode == 5, failed.stderr
    assert "wrote no artifact this run" in failed.stderr, failed.stderr


def test_reuse_refuses_the_artifact_the_last_attempt_failed_to_write(repo: Path):
    _measured_then_dead(repo)

    reused = run_cli(repo, "coverage", "--reuse-artifacts", "--json")

    assert reused.returncode == 5, reused.stderr
    assert "lane 'unit' wrote no artifact on its last attempt" in reused.stderr, reused.stderr
    assert "predates it and is the previous run's" in reused.stderr
    assert "--reuse-artifacts" in reused.stderr, "the refusal names the flag that hit it"
    assert str(repo / ".crapkit" / "lane-unit.log") in reused.stderr, "and the log of that attempt"
    assert _run_count(repo) == 1, "--reuse-artifacts never reruns"


def test_verify_refuses_the_artifact_the_last_attempt_failed_to_write(repo: Path):
    """The wider exposure: the failed `coverage` is exit 5, and the `verify
    --reuse-artifacts` that follows it was exit 0 with ok true, and its run
    became the trusted baseline. With one lane declared, the refusal is the
    every-lane-failed exit; the two-lane form, where verify says it cannot
    conclude, is driven in-process."""
    _measured_then_dead(repo)

    res = run_cli(repo, "verify", "--reuse-artifacts", "--json")

    assert res.returncode == 5, res.stderr
    assert "wrote no artifact on its last attempt" in res.stderr, res.stderr
    assert "every lane failed (1 of 1)" in res.stderr
    assert '"ok"' not in res.stdout, "no verdict is printed over a refused artifact"


def test_a_salvage_written_after_the_failed_attempt_reuses(repo: Path):
    """A coverage JSON combined by hand from the killed run's shards holds new
    bytes, so the recipe the shard hint gives still ends in a scored run.
    Rewritten: the salvage used to be the same bytes under a later time, which
    pinned a touch as a salvage."""
    _measured_then_dead(repo)
    assert run_cli(repo, "coverage", "--reuse-artifacts").returncode == 5
    _salvage(repo)

    res = run_cli(repo, "coverage", "--reuse-artifacts", "--json")

    assert res.returncode == 0, res.stderr
    assert "wrote no artifact" not in res.stderr
    assert _run_count(repo) == 1, "the salvage was read, not rerun"


# (stale-touch shape-9, boundary-10, history-3) what happens to the leftover,
# each keeping its bytes; --reuse-artifacts and --reuse-unchanged both refuse it
KEPT_BYTES = {"touch": _touch, "copy-checkout-without-times": _copy_checkout,
              "same-bytes-rewrite": _same_bytes, "stamps-file-deleted": _stamps_deleted}


@pytest.mark.parametrize("event", sorted(KEPT_BYTES))
@pytest.mark.parametrize("flag", ["--reuse-artifacts", "--reuse-unchanged"])
def test_the_leftover_stays_refused_while_it_holds_the_same_bytes(repo: Path, flag, event):
    _measured_then_dead(repo)
    if flag == "--reuse-unchanged":
        _lane_command(repo, ALIVE)
    root = KEPT_BYTES[event](repo)

    res = run_cli(root, "coverage", flag, "--json")

    assert "reusing without rerun" not in res.stderr, res.stderr
    if flag == "--reuse-artifacts":
        assert res.returncode == 5, res.stderr
        assert "lane 'unit' wrote no artifact on its last attempt" in res.stderr, res.stderr
        return
    assert res.returncode == 0, res.stderr
    assert _run_count(root) == 2, "the lane whose last attempt failed ran again"


def test_reuse_unchanged_reruns_a_lane_whose_last_attempt_wrote_nothing(repo: Path):
    """`--reuse-unchanged` judged the stamp commit alone, and the failed attempt
    never touched the stamp, so it went on reusing the dead lane's artifact.
    The attempt that failed is now part of the stamp, and the lane reruns."""
    _measured_then_dead(repo)
    _lane_command(repo, ALIVE)

    res = run_cli(repo, "coverage", "--reuse-unchanged", "--json")

    assert res.returncode == 0, res.stderr
    assert "reusing without rerun" not in res.stderr
    assert _run_count(repo) == 2, "the lane whose last attempt failed has to run again"


def _killed_attempt(repo: Path) -> None:
    """What a kill during the lane's attempt leaves: the artifact set aside under
    .crapkit/aside/ by an attempt that never ran its exit."""
    from crapkit.lane_outputs import owned

    owned(repo, "unit", (ARTIFACT.as_posix(),)).__enter__()
    assert not (repo / ARTIFACT).exists()


# (the flag, the words the lane line prints once the artifact is back)
AFTER_KILL = {"--reuse-artifacts": "reuses", "--reuse-unchanged": "reusing without rerun"}


@pytest.mark.parametrize("flag", sorted(AFTER_KILL))
def test_the_artifact_a_killed_attempt_set_aside_is_reused(repo: Path, flag):
    """A kill or a CI timeout runs no cleanup. `--reuse-artifacts` exited 5 with
    "produced no artifact" and never named the copy, and the next attempt
    removed it."""
    assert run_cli(repo, "coverage", "--json").returncode == 0
    _killed_attempt(repo)

    res = run_cli(repo, "coverage", flag, "--json")

    assert res.returncode == 0, res.stderr
    assert ("crapkit: lane 'unit': coverage/coverage-final.json is back at its path; an "
            "attempt that did not finish (a kill or a timeout) had set it aside under "
            ".crapkit/aside/") in res.stderr, res.stderr
    assert _run_count(repo) == 1, "the artifact the killed attempt set aside was reused"
    assert not (repo / ".crapkit" / "aside").exists()
