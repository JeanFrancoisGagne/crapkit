"""Rust and shell all the way through: git ls-files, the pool, the store.

The unit tests measure both readers in this process, where they are registered
because the test module imported them. The path that actually runs a repo does
not: `analyze_jobs` hands the file list to a ProcessPoolExecutor once there are
16 or more of them, and a spawned child on Windows imports `crapkit.analyze` and
nothing else. Registration wired anywhere but that module's scope leaves the
children measuring `.rs` with the reader that counts no match arm and `.sh` with
lizard's C reader, and both answers come back shaped like right ones.

So this repo carries enough files to force the pool, and the numbers below are
hand-counted from the sources above them.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit.store import SnapshotStore

# Four arms that match a value, plus the wildcard: base 1 + 4 = 5. The stock
# reader reads 2 for this, whatever the arm count.
CLASSIFY = """fn classify(n: i32) -> &'static str {
    match n {
        0 => "zero",
        1 => "one",
        2 => "two",
        3 => "three",
        _ => "many",
    }
}
"""
CLASSIFY_CCN = 5

# base 1, + if, elif, for, &&, || = 6. Read as C this loses elif and || is all
# that keeps it from 4.
DEPLOY = """deploy() {
  if [ -z "$1" ]; then
    return 1
  elif [ "$1" = "all" ]; then
    for host in $HOSTS; do
      ping "$host" && echo up || echo down
    done
  fi
}
"""
DEPLOY_CCN = 6

# base 1, + if = 2. The `function name { }` spelling, which lizard's C reader
# does not report at all.
RELOAD = """function reload {
  if [ -f "$1" ]; then
    . "$1"
  fi
}
"""
RELOAD_CCN = 2

TOML = ('[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "rs"\npaths = ["src"]\nlanguages = ["rust"]\n'
        'coverage_optional = true\n\n'
        '[[scope]]\nname = "sh"\npaths = ["scripts"]\nlanguages = ["shell"]\n'
        'coverage_optional = true\n')

# analyze.analyze_jobs pools at 16 jobs. 18 Rust files plus 2 shell ones clears
# it with room for the threshold to move a little.
RUST_FILES = 18


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=300, env=dict(os.environ))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def polyglot_repo(tmp_path: Path) -> Path:
    write(tmp_path / "crapkit.toml", TOML)
    for i in range(RUST_FILES):
        write(tmp_path / "src" / f"m{i:02d}.rs", CLASSIFY)
    write(tmp_path / "scripts" / "deploy.sh", DEPLOY)
    write(tmp_path / "scripts" / "reload.bash", RELOAD)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.email=t@t", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"]):
        subprocess.run(cmd, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def store_rows(repo: Path, run_id: int) -> dict[str, int]:
    """path -> ccn, straight out of the SQLite the run wrote."""
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    return {row.path: row.ccn for row in store.read_rows(run_id)}


def test_rust_and_shell_land_in_the_store_with_the_hand_counted_ccn(polyglot_repo: Path):
    res = run_cli(polyglot_repo, "inventory", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    summary = json.loads(res.stdout)

    assert summary["files"] == RUST_FILES + 2
    assert summary["functions"] == RUST_FILES + 2
    by_path = store_rows(polyglot_repo, summary["run_id"])
    assert by_path["src/m00.rs"] == CLASSIFY_CCN
    assert by_path["scripts/deploy.sh"] == DEPLOY_CCN
    assert by_path["scripts/reload.bash"] == RELOAD_CCN


def test_every_pool_worker_measured_rust_the_same_way(polyglot_repo: Path):
    """18 identical Rust files, spread across the pool by chunks. A child that
    skipped registration reports 2 for the files it handled and 5 for none, so a
    single distinct value is the assertion that catches it."""
    res = run_cli(polyglot_repo, "inventory", "--json")
    assert res.returncode == 0, res.stdout + res.stderr

    by_path = store_rows(polyglot_repo, json.loads(res.stdout)["run_id"])
    rust = {ccn for path, ccn in by_path.items() if path.endswith(".rs")}

    assert rust == {CLASSIFY_CCN}


def test_the_scopes_claim_their_own_files(polyglot_repo: Path):
    res = run_cli(polyglot_repo, "inventory", "--json")
    assert res.returncode == 0, res.stdout + res.stderr

    store = SnapshotStore(polyglot_repo / ".crapkit" / "crap.sqlite")
    scopes = {row.scope for row in store.read_rows(json.loads(res.stdout)["run_id"])}

    assert scopes == {"rs", "sh"}


def test_doctor_passes_on_a_cc_only_rust_and_shell_repo(polyglot_repo: Path):
    """Neither language has a coverage parser, so both scopes declare
    `coverage_optional`. Without it a lane-less scope is a doctor FAIL, which is
    the whole reason the flag exists."""
    run_cli(polyglot_repo, "inventory", "--json")

    res = run_cli(polyglot_repo, "doctor", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
