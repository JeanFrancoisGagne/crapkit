"""R39: under lizard's stock Python reader, the net that refuses a def read no
further than its signature let a PEP 695 def through cut off after its first
line, scored at ccn 1 under the name `:( int , str )`.

    <retro venv python> R39.py WORKTREE

analysis_oracles' check compares only the cuts ast rejects, and until a later
fix (R42) a file that ends inside a signature keeps the rows before the cut, so
the check holds a row at both commits. The shape it misses is a whole, valid
file whose def the stock reader cuts off. This probe runs `crapkit inventory
--export` with crapkit's own reader registration stubbed out (the reader crapkit
falls back to once crapkit.lizardpython retires) and the analysis kept serial,
so no pool worker registers it again. No row may cover part of f: a row that
starts on f's line must end where ast says f ends. A refused file has no row.
"""
# source: Python's ast module parses the file below and puts f at lines 5 to 9 (FunctionDef.lineno, end_lineno); src/crapkit/analyze.py at the fix: a def read no further than its signature refuses its file
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CUT = ("def whole(x):\n    return x\n\n\n"
       "def f[T: (int, str), U: int](a, b=(),\n"
       "                              c=1):\n"
       "    if a:\n        return b\n    return c\n")
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "all"\npaths = ["pep695_cut.py"]\n'
          'languages = ["python"]\ncoverage_optional = true\n')
STOCK = "; ".join((
    "import contextlib, sys",
    "import crapkit.lizardpython as reader",
    "reader.register = lambda: None",
    "import crapkit.analyze as analyze",
    "analyze._pool_for = lambda *args, **kwargs: contextlib.nullcontext(None)",
    "from crapkit.cli import main",
    "sys.exit(main(sys.argv[2:]))"))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir()
    (repo / "pep695_cut.py").write_bytes(CUT.encode())
    (repo / "crapkit.toml").write_bytes(CONFIG.encode())
    (repo / ".gitignore").write_bytes(b".crapkit/\n")
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


def _export(repo: Path, out: Path) -> list[dict]:
    argv = [sys.executable, "-c", STOCK, "crapkit", "inventory", "--export", str(out)]
    done = subprocess.run(argv, cwd=repo, env=_env(), capture_output=True)
    if not out.is_file():
        raise RuntimeError(f"crapkit inventory exited {done.returncode} and wrote no export: "
                           f"{done.stderr.decode(errors='replace')[-400:]}")
    return _rows(out.read_bytes().decode())


def _cut(rows: list[dict], first: int, last: int) -> list[tuple]:
    """Rows that start on f's first line and end anywhere but its last."""
    spans = [(row["long_name"], int(row["start"]), int(row["end"])) for row in rows]
    return [span for span in spans if span[1] == first and span[2] != last]


def main(argv: list[str]) -> int:
    (f,) = [node for node in ast.parse(CUT).body if getattr(node, "name", "") == "f"]
    with tempfile.TemporaryDirectory(prefix="crapkit-r39-") as scratch:
        rows = _export(_repo(Path(scratch)), Path(scratch) / "inventory.tsv")
    cut = _cut(rows, f.lineno, f.end_lineno)
    assert cut == [], f"f spans lines {f.lineno} to {f.end_lineno}, crapkit scored {cut}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
