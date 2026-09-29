"""R33: `--reuse-unchanged` reused a lane after a tracked file outside the
lane's scope changed, although the lane's tests read that file, so a stale
artifact scored the tree.

    <retro venv python> R33.py WORKTREE

verdict_model's check reads coverage --json's rerun_reason, which neither the
commit before the fix nor the fix writes. This probe asks the question through
the CLI both commits have. It builds a committed repo with one JavaScript
function and a lane whose command counts its own runs in a file under
.crapkit/ and writes an istanbul artifact whose hit depends on
tests/state.txt, runs `crapkit coverage`, edits tests/state.txt (tracked,
outside the scope's src/) without committing, and runs `crapkit coverage
--reuse-unchanged`: the lane must run again.
"""
# source: docs/lanes.md, the --reuse-unchanged row of the options table: "A lane without `inputs` needs the same clean HEAD"; a tree with an uncommitted edit is not a clean HEAD, so the lane runs a second time
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = """import json
from pathlib import Path
hit = int(Path('tests/state.txt').read_text().strip() == 'pass')
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
count = folder / 'counter.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': hit},
    'statementMap': {'0': loc}, 's': {'0': hit}, 'branchMap': {}, 'b': {}}}
(folder / 'cov.json').write_text(json.dumps(data), encoding='utf-8')
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    command = json.dumps(f'"{sys.executable}" measure.py')
    config = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n\n'
              f'[[lane]]\nname = "js"\nparser = "istanbul"\nscopes = ["src"]\ncommand = {command}\n'
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


def _coverage(repo: Path, *flags: str) -> None:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run([sys.executable, "-m", "crapkit", "coverage", *flags], cwd=repo, env=env,
                          capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"crapkit coverage {' '.join(flags)} exited {done.returncode}: "
                           f"{(done.stdout + done.stderr).decode(errors='replace')[-400:]}")


def _runs(repo: Path) -> int:
    return int((repo / ".crapkit" / "counter.txt").read_text())


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r33-") as scratch:
        repo = _repo(Path(scratch))
        _coverage(repo)
        (repo / "tests" / "state.txt").write_bytes(b"fail\n")
        _coverage(repo, "--reuse-unchanged")
        runs = _runs(repo)
    assert runs == 2, f"the lane ran {runs} time(s): an uncommitted edit to tests/state.txt was reused"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
