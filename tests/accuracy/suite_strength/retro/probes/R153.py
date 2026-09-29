"""R153: the reuse refusal for an artifact a failed attempt left behind was
judged from two readings of the lane log's modification time, so on a
filesystem with a coarse clock (FAT keeps 2 seconds) a lane that failed inside
one tick of the previous run's log write recorded no refusal, and
`--reuse-artifacts` scored the leftover artifact.

    <retro venv python> R153.py WORKTREE

verdict_model's check freezes the log from inside the lane, and crapkit writes
its own exit line to the log after the lane stops, so NTFS and ext4 always see
the log move and the check passes at the commit before the fix. This probe
freezes the log where a coarse clock would: it builds a committed repo with one
JavaScript function and a lane that fails on demand without writing its
artifact, measures once, then runs the failing `crapkit coverage` in its own
process with the fold (crapkit.cli.scoring._collect_lanes, which both commits
have) wrapped to set the log's time back to the first run's before it folds.
`crapkit coverage --reuse-artifacts` must then refuse the leftover artifact.
"""
# source: docs/lanes.md "The artifact a failed attempt left behind is refused": --reuse-artifacts refuses a lane whose last attempt failed without writing its artifact, until something rewrites that file; FAT's modification time has a 2-second resolution (Microsoft, "File Times")
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = """import json, sys
from pathlib import Path
if Path('tests/state.txt').read_text().strip() == 'fail':
    sys.exit(1)
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': 1},
    'statementMap': {'0': loc}, 's': {'0': 1}, 'branchMap': {}, 'b': {}}}
Path('.crapkit').mkdir(exist_ok=True)
Path('.crapkit/cov.json').write_text(json.dumps(data), encoding='utf-8')
"""
REFUSED = 5


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    command = json.dumps(f'"{sys.executable}" measure.py')
    config = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n\n'
              f'[[lane]]\nname = "b"\nparser = "istanbul"\nscopes = ["src"]\ncommand = {command}\n'
              'artifact = ".crapkit/cov.json"\n')
    files = {"src/app.js": "function f() {\n  return 1;\n}\n", "tests/state.txt": "pass\n",
             "measure.py": RUNNER, ".gitignore": ".crapkit/\n__pycache__/\n", "crapkit.toml": config}
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _spawned(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env, capture_output=True)


def _fail_inside_one_tick(repo: Path, log: Path, frozen: int) -> None:
    """The failing coverage, in process, with the log set back before the fold."""
    import crapkit.cli.scoring as scoring
    from crapkit.cli import main as crapkit_main

    fold = scoring._collect_lanes

    def folded_on_a_coarse_clock(*args, **kwargs):
        os.utime(log, ns=(frozen, frozen))
        return fold(*args, **kwargs)

    scoring._collect_lanes = folded_on_a_coarse_clock
    os.chdir(repo)
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            crapkit_main(["coverage"])
        except SystemExit:
            pass


def main(argv: list[str]) -> int:
    # crapkit's in-process store stays open until exit, which Windows will not delete under
    with tempfile.TemporaryDirectory(prefix="crapkit-r153-", ignore_cleanup_errors=True) as scratch:
        repo = _repo(Path(scratch))
        first = _spawned(repo, "coverage")
        if first.returncode != 0:
            raise RuntimeError(f"crapkit coverage exited {first.returncode}")
        log = repo / ".crapkit" / "lane-b.log"
        (repo / "tests" / "state.txt").write_bytes(b"fail\n")
        _fail_inside_one_tick(repo, log, log.stat().st_mtime_ns)
        os.chdir(scratch)
        reused = _spawned(repo, "coverage", "--reuse-artifacts")
    told = " ".join((reused.stdout + reused.stderr).decode(errors="replace").split())
    assert reused.returncode == REFUSED, f"--reuse-artifacts scored the failed lane's leftover: {told[-200:]}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
