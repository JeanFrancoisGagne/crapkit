"""R22: two anonymous callbacks that open on the same line were not addressed
apart by every reader: worklist gave one callback the other's CRAP.

    <retro venv python> R22.py WORKTREE

verdict_model's check reads the `occurrence` field of brief's JSON, which the
fix added, so it cannot run on the commit before. This probe asks the question
through the CLI both commits have. It builds a committed repo with one
TypeScript line that opens f, a map callback and a filter callback, in a
cc-only scope, runs `crapkit coverage`, and reads the two callbacks' (ccn, crap)
pairs from `worklist --json` (active rows) and from `brief --json`
(file_functions). Each reader must list both callbacks with their own numbers.
"""
# source: McCabe 1976 (IEEE TSE SE-2(4), section II): ccn is one plus the decision points, so `x => x > 1 ? 1 : x > 0 ? 2 : 3` (two conditional operators) reads 3 and `y => y > 2 || y < 0` (one short-circuit ||) reads 2, the counts ESLint's `complexity` rule gives for `?:` and `||`; README Languages section: a cc-only scope declares `coverage_optional = true` and scores `crap = ccn`, so the callbacks read CRAP 3.0 and 2.0
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

APP = ("export const f = (a: number) => [a].map(x => x > 1 ? 1 : x > 0 ? 2 : 3)"
       ".filter(y => y > 2 || y < 0);\n\n\n\n"
       "function g(x: number) {\n  if (x > 1) { return 1; }\n  return 2;\n}\n")
CONFIG = ('[crapkit]\ntarget = 1\n\n[[scope]]\nname = "web"\npaths = ["web"]\n'
          'languages = ["typescript"]\ncoverage_optional = true\n')
# (ccn, crap) of the map and filter callbacks, sorted: worked above, not read from crapkit.
CALLBACKS = [(2, 2.0), (3, 3.0)]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "app.ts").write_bytes(APP.encode())
    (repo / "crapkit.toml").write_bytes(CONFIG.encode())
    (repo / ".gitignore").write_bytes(b".crapkit/\n")
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _crapkit(repo: Path, *args: str) -> str:
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    done = subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo, env=env,
                          capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"crapkit {' '.join(args)} exited {done.returncode}: "
                           f"{done.stderr.decode(errors='replace')[-400:]}")
    return done.stdout.decode("utf-8")


def _callbacks(rows: list[dict]) -> list[tuple[int, float]]:
    return sorted((row["ccn"], row["crap"]) for row in rows
                  if row["start"] == 1 and row["function"] == "(anonymous)")


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r22-") as scratch:
        repo = _repo(Path(scratch))
        _crapkit(repo, "coverage")
        worklist = _callbacks(json.loads(_crapkit(repo, "worklist", "--json"))["active"])
        brief = json.loads(_crapkit(repo, "brief", "web/app.ts", "(anonymous)", "--json"))
        assert worklist == CALLBACKS, f"worklist gives the line-1 callbacks {worklist}, not {CALLBACKS}"
        assert _callbacks(brief["file_functions"]) == CALLBACKS, \
            f"brief gives the line-1 callbacks {_callbacks(brief['file_functions'])}, not {CALLBACKS}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
