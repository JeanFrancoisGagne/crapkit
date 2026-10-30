"""doctor over a crapkit.toml that crapkit 0.7.6's init wrote, run first by 0.7.6 and then by this tree.

An upgrade keeps the user's config. This tree's doctor names each lane's
runner, so the config an older init wrote must get that line too, with
doctor's exit code what 0.7.6's doctor gave for the same repo. 0.7.6 runs
from its own source, taken out of this repository's history with
`git archive v0.7.6`, as tests/e2e/test_verify_reads_stores_older_crapkits_wrote_e2e.py
runs it. A shallow or tagless clone skips these tests and says why. On macOS
0.7.6 cannot finish a command it starts (OLD_GROUP_PROBE_FAILS), so there the
test drops only the exit-code comparison with 0.7.6's doctor.

The deploy cells lin-up-pip-0.7.6, win-up-pip-0.7.6 and
lin-up-claude-plugin-0.7.6 run the real upgrade at the slot close.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import hang_guard
import process_table
from conftest import child_env, cli_runner, git_commit_all, git_init_repo

ROOT = Path(__file__).resolve().parents[2]
OLD = "v0.7.6"
# 0.7.6's process owner asks killpg(pid, 0) whether a finished command's group
# is gone and takes only ESRCH for gone. Darwin answers EPERM for a group whose
# only member is its unreaped leader, so there 0.7.6's init (at the pytest-cov
# probe, after it wrote crapkit.toml) and doctor (at the start probe) exit 5
# with `[Errno 1] Operation not permitted`. 0.8.1 fixed it. On macOS the test
# keeps every check of this tree's doctor and drops only the exit-code
# comparison with 0.7.6's doctor, which has no code to compare there.
OLD_GROUP_PROBE_FAILS = sys.platform == "darwin"

run_cli = cli_runner(encoding="utf-8", errors="replace")

# Each repo shape 0.7.6's init writes one lane for, and the runner line this
# tree's doctor prints for that lane.
SHAPES = {
    "py": {
        "files": {"pylib/mod.py": "def g(x):\n    return x or 0\n",
                  "pyproject.toml": '[project]\nname = "pyrepo"\n'},
        "line": "ok   lane 'py': runs pytest (named in its command)",
        "toolchain": {"name": "pytest", "source": "command"},
    },
    "js": {
        "files": {"src/app.ts": "export function f(a: number) { return a ? 1 : 2; }\n",
                  "package.json": json.dumps({"scripts": {"test": "vitest run"},
                                              "devDependencies": {"vitest": "^2.0.0"}})},
        "line": "ok   lane 'js': runs vitest (named in package.json script \"test\")",
        "toolchain": {"name": "vitest", "source": "script"},
    },
}


@pytest.fixture(scope="module")
def old_src(tmp_path_factory) -> Path:
    """0.7.6's `src`, taken once per worker from the git history."""
    archived = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=zip", OLD, "src"],
                              capture_output=True)
    if archived.returncode:
        pytest.skip(f"the crapkit history at {ROOT} holds no tag {OLD} (a shallow or tagless "
                    "clone), and this test runs that release from it")
    into = tmp_path_factory.mktemp("v0_7_6")
    zipfile.ZipFile(io.BytesIO(archived.stdout)).extractall(into)
    return into / "src"


def run_old(old_src: Path, repo: Path, *args: str) -> subprocess.CompletedProcess:
    """One command under crapkit 0.7.6."""
    env = child_env({"PYTHONPATH": str(old_src), "CRAPKIT_OVERRIDE_REASON": None})
    with process_table.hold(naming=False):
        return hang_guard.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env,
                              text=True, encoding="utf-8", errors="replace")


def repo_of(tmp_path: Path, shape: str) -> Path:
    (tmp_path / shape).mkdir()
    repo = git_init_repo(tmp_path / shape)
    for name, text in SHAPES[shape]["files"].items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8", newline="\n")
    git_commit_all(repo, "init")
    return repo


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_doctor_names_the_runner_of_the_lane_0_7_6_init_wrote(tmp_path, old_src, shape):
    repo = repo_of(tmp_path, shape)
    init = run_old(old_src, repo, "init")
    assert init.returncode in ((0, 5) if OLD_GROUP_PROBE_FAILS else (0,)), init.stdout + init.stderr
    assert (repo / "crapkit.toml").is_file(), init.stdout + init.stderr
    old = run_old(old_src, repo, "doctor")
    assert "runs " not in old.stdout, "0.7.6's doctor printed no runner line"

    new = run_cli(repo, "doctor")
    report = json.loads(run_cli(repo, "doctor", "--json").stdout)

    assert SHAPES[shape]["line"] in new.stdout.splitlines(), new.stdout
    assert OLD_GROUP_PROBE_FAILS or new.returncode == old.returncode, (old.stdout + old.stderr, new.stdout + new.stderr)
    assert [lane["toolchain"] for lane in report["lanes"]] == [SHAPES[shape]["toolchain"]]
