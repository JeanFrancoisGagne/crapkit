"""R168: git quotes a non-ASCII path in status and diff output unless
core.quotePath is off, and crapkit compared those quoted names with the plain
names ls-files gives, so an uncommitted edit to src/bêta.js read as no change
and `--reuse-unchanged` reused the lane's stale artifact.

    <retro venv python> R168.py WORKTREE

verdict_model's check reads coverage --json's rerun_reason, which neither the
commit before the fix nor the fix writes. This probe asks the question through
the CLI both commits have. It builds a committed repo (core.quotePath=true, git's
default, pinned) with one JavaScript function in src/bêta.js and a lane whose
command counts its own runs in a file under .crapkit/, runs `crapkit coverage`,
edits src/bêta.js without committing, and runs `crapkit coverage
--reuse-unchanged`: the lane must run again.
"""
# source: git-config(1), core.quotePath: "Commands that output paths ... will quote \"unusual\" characters", bytes above 0x80 included, when it is true, the default; docs/lanes.md, the --reuse-unchanged row of the options table: an edited file under the lane's scope is a change, so the lane runs a second time
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = """import json
from pathlib import Path
hit = 1
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
count = folder / 'counter.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/b\u00eata.js': {'path': 'src/b\u00eata.js',
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
    files = {"src/bêta.js": "function f() {\n  return 1;\n}\n", "measure.py": RUNNER, ".gitignore": ".crapkit/\n__pycache__/\n", "crapkit.toml": config}
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false"),
                       ("core.quotePath", "true")):
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
    with tempfile.TemporaryDirectory(prefix="crapkit-r168-") as scratch:
        repo = _repo(Path(scratch))
        _coverage(repo)
        edited = repo / "src" / "bêta.js"
        edited.write_bytes(edited.read_bytes() + b"// edited\n")
        _coverage(repo, "--reuse-unchanged")
        runs = _runs(repo)
    assert runs == 2, f"the lane ran {runs} time(s): an uncommitted edit to {ascii('src/bêta.js')} was reused"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
