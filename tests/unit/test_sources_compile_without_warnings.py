"""Every Python file in the package, the suite and the tools compiles with no warning.

Python prints a SyntaxWarning to stderr each time it compiles a file with no
.pyc to reuse: a read-only install, PYTHONDONTWRITEBYTECODE, a git hook's first
run. An invalid escape such as "\\d" warns from 3.12 on, and a return, break or
continue that leaves a `finally` block warns from 3.14 on (PEP 765). The line
lands in whatever reads crapkit's stderr: an agent, a hook's output, a CI log.
Each CI leg compiles every file with warnings as errors, on its own interpreter.
"""
import subprocess
import sys
import warnings
from pathlib import Path

from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parents[2]
TREES = ("src/crapkit", "tests", "tools")


def _warning(path: Path) -> str | None:
    """What compiling `path` warns or raises, or None when it compiles clean."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (SyntaxError, Warning) as problem:
            return f"{path.relative_to(ROOT).as_posix()}: {problem!r}"
    return None


def test_every_file_compiles_with_warnings_as_errors():
    paths = sorted(path for tree in TREES for path in (ROOT / tree).rglob("*.py"))
    problems = [problem for problem in map(_warning, paths) if problem]

    assert len(paths) > 600, paths
    assert problems == [], "\n".join(problems)


def test_the_first_run_prints_no_warning(tmp_path):
    """`crapkit --version` compiled from source, with every warning shown."""
    done = subprocess.run([sys.executable, "-X", f"pycache_prefix={tmp_path}", "-W", "default",
                           "-m", "crapkit", "--version"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=HANG_SECONDS)

    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("crapkit "), done.stdout
    assert done.stderr == "", done.stderr
