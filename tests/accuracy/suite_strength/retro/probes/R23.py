"""R23: two arrows on one line shared their coverage counts, so the arrow a test
called read 0.5 measured, its idle sibling's count mixed into its own.

    <retro venv python> R23.py WORKTREE

coverage_oracles' check expects both arrows at the untested floor, a rule a
later commit (3b60211) set; the fix itself ends the coverage run on a shared
line instead, so the check fails there on a refusal that shows no wrong number.
This probe asks only this bug's question. It builds a committed repo with one
JavaScript line holding two arrows and a lane that copies a hand-written
istanbul artifact (the first arrow ran both arms, the second never ran), runs
`crapkit coverage --export`, and requires that neither line-1 arrow reads a
measured coverage: no artifact can say which count on that line is whose. A run
that refuses the shared line scores no number, so it passes; any other refusal
means the probe can tell nothing and stops with a RuntimeError.
"""
# source: istanbul's coverage object keys counts by statement and branch location, not by function, so two functions whose statements share line 1 cannot be told apart (the first arrow's statement map at 1:15-1:30 and the second's at 1:34-1:49 carry counts 2 and 0); README.md#remedy-what-to-do-about-it: a function on a line span another one shares scores uncovered and untested
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

JS = "const pair = [(x) => (x ? 1 : 2), (y) => (y ? 3 : 4)];\nmodule.exports = { pair };\n"
# (name, first column, last column, calls, arm hits) of the two arrows on line 1.
ARROWS = [("(anonymous_0)", 14, 31, 2, [1, 1]), ("(anonymous_1)", 33, 50, 0, [0, 0])]
COPY = ("import os,shutil,sys; os.makedirs(os.path.dirname(sys.argv[2]), exist_ok=True); "
        "shutil.copyfile(*sys.argv[1:3])")
CONFIG = """[crapkit]
target = 2

[exclude]
globs = ["recorded/**"]

[[scope]]
name = "js"
paths = ["js"]
languages = ["javascript"]

[[lane]]
name = "js"
command = '"{python}" -c "{copy}" recorded/js.json .crapkit/cov/js.json'
artifact = ".crapkit/cov/js.json"
parser = "istanbul"
scopes = ["js"]
"""


def _span(first: int, last: int) -> dict:
    return {"start": {"line": 1, "column": first}, "end": {"line": 1, "column": last}}


def _istanbul() -> str:
    entry = {"path": "js/shared.js", "fnMap": {}, "f": {}, "statementMap": {}, "s": {},
             "branchMap": {}, "b": {}}
    for key, (name, first, last, calls, arms) in enumerate(ARROWS):
        key = str(key)
        entry["fnMap"][key] = {"name": name, "decl": _span(first, first + 1),
                               "loc": _span(first, last), "line": 1}
        entry["f"][key] = entry["s"][key] = calls
        entry["statementMap"][key] = _span(first + 1, last - 1)
        entry["branchMap"][key] = {"type": "cond-expr", "line": 1, "loc": _span(first + 1, last - 1),
                                   "locations": [_span(first + 2, first + 3)] * 2}
        entry["b"][key] = arms
    return json.dumps({"js/shared.js": entry})


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    for path, text in (("js/shared.js", JS), ("recorded/js.json", _istanbul()), (".gitignore", ".crapkit/\n"),
                       ("crapkit.toml", CONFIG.format(python=sys.executable, copy=COPY))):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _env() -> dict:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    return {**env, "PYTHONDONTWRITEBYTECODE": "1"}


def _rows(text: str) -> list[dict]:
    """An export's rows by its header; comment lines start with #."""
    lines = [line for line in text.splitlines() if line[:1] not in ("", "#")]
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def _line_one(repo: Path) -> list[tuple[str, str]]:
    """The line-1 arrows' (cov, flag); none when crapkit refuses the shared line."""
    done = subprocess.run([sys.executable, "-m", "crapkit", "coverage", "--export", "scored.tsv"],
                          cwd=repo, env=_env(), capture_output=True)
    told = (done.stdout + done.stderr).decode(errors="replace")
    if done.returncode == 0:
        return [(row["cov"], row["flag"]) for row in _rows((repo / "scored.tsv").read_bytes().decode())
                if row["start"] == "1"]
    if "same line span" in told:
        return []
    raise RuntimeError(f"crapkit coverage exited {done.returncode}: {told[-400:]}")


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r23-") as scratch:
        arrows = _line_one(_repo(Path(scratch)))
    measured = [cov for cov, flag in arrows if flag == "measured"]
    assert measured == [], f"the line-1 arrows read {arrows}: a count on a shared line belongs to neither"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
