"""R136: git reads a lane's `inputs` as literal paths, so `inputs =
["src/*.js"]` loaded without error, matched no file, and `--reuse-unchanged`
reused the lane after an edit to src/app.js, scoring the tree with a stale
artifact. (The same fix gave `./src/` and a backslash the scope-path spelling;
git on Windows already read `./src/` as src and a backslash as a slash, so the
glob is the spelling that shows the bug on every platform.)

    <retro venv python> R136.py WORKTREE

verdict_model's check reads coverage --json's rerun_reason, which neither the
commit before the fix nor the fix writes. This probe asks the question through
the CLI both commits have. It builds a committed repo with one JavaScript
function and a lane that declares `inputs = ["src/*.js"]` and counts its own
runs in a file under .crapkit/, runs `crapkit coverage`, edits src/app.js
without committing, and runs `crapkit coverage --reuse-unchanged`: the lane
must run again. A crapkit that refuses the glob at load scores nothing stale,
so that refusal passes; any other refusal means the probe can tell nothing
and stops with a RuntimeError.
"""
# source: docs/configuration.md, the lane `inputs` row: reuse holds only while "no committed, staged, unstaged or untracked change touches these paths", and "Entries are literal paths, no globs: an entry holding `*` or `?` ... is a config error"; src/app.js is the file src/*.js names, so an edit to it must rerun the lane or the glob must be refused
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = """import json
from pathlib import Path
folder = Path('.crapkit')
folder.mkdir(exist_ok=True)
count = folder / 'counter.txt'
count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))
loc = {'start': {'line': 1, 'column': 0}, 'end': {'line': 3, 'column': 1}}
data = {'src/app.js': {'path': 'src/app.js',
    'fnMap': {'0': {'name': 'f', 'decl': loc, 'loc': loc}}, 'f': {'0': 1},
    'statementMap': {'0': loc}, 's': {'0': 1}, 'branchMap': {}, 'b': {}}}
(folder / 'cov.json').write_text(json.dumps(data), encoding='utf-8')
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    command = json.dumps(f'"{sys.executable}" measure.py')
    config = ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n\n'
              f'[[lane]]\nname = "js"\nparser = "istanbul"\nscopes = ["src"]\ncommand = {command}\n'
              'artifact = ".crapkit/cov.json"\ninputs = ["src/*.js"]\n')
    files = {"src/app.js": "function f() {\n  return 1;\n}\n", "measure.py": RUNNER,
             ".gitignore": ".crapkit/\n__pycache__/\n", "crapkit.toml": config}
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


def _coverage(repo: Path, *flags: str) -> tuple[int, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run([sys.executable, "-m", "crapkit", "coverage", *flags], cwd=repo, env=env,
                          capture_output=True)
    return done.returncode, (done.stdout + done.stderr).decode(errors="replace")


def _measured(repo: Path, *flags: str) -> None:
    code, told = _coverage(repo, *flags)
    if code != 0:
        raise RuntimeError(f"crapkit coverage {' '.join(flags)} exited {code}: {told[-400:]}")


def _runs(repo: Path) -> int:
    return int((repo / ".crapkit" / "counter.txt").read_text())


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r136-") as scratch:
        repo = _repo(Path(scratch))
        code, told = _coverage(repo)
        if code != 0 and "literal paths" in told:
            return 0
        if code != 0:
            raise RuntimeError(f"crapkit coverage exited {code}: {told[-400:]}")
        edited = repo / "src" / "app.js"
        edited.write_bytes(edited.read_bytes() + b"// edited\n")
        _measured(repo, "--reuse-unchanged")
        runs = _runs(repo)
    assert runs == 2, f"the lane ran {runs} time(s): an uncommitted edit under inputs src/*.js was reused"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
