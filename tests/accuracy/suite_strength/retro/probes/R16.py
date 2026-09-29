"""R16: the analyzer opened source files in the machine's locale encoding, so on a
Windows machine whose code page is cp1252 a UTF-8 file's non-ASCII function name
read as mojibake: at the fix's parent, `def café(x)` read back as
`©( x )`.

    <retro venv python> R16.py WORKTREE

analysis_oracles' decode check compares crapkit's rows across encodings with
the PowerShell reader the fix added, and the commit before the fix refuses any
config that names PowerShell, so it cannot run there. This probe asks the
decoding question alone. It builds a committed repo with one UTF-8 Python file
whose function is named café, runs `crapkit inventory --export` in a child whose
locale encoding is cp1252 (PYTHONUTF8=0, which Windows gives a cp1252 machine),
and reads the function's name back.
"""
# source: PEP 3120 and the Python language reference, "Encoding declarations": Python source is UTF-8 unless it declares otherwise, so `def café(x):` below names café; the file holds the UTF-8 bytes c3 a9 for é
from __future__ import annotations

import locale
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = "def café(x):\n    if x:\n        return 1\n    return 0\n"
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "py"\npaths = ["pylib"]\n'
          'languages = ["python"]\ncoverage_optional = true\n')


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_bytes(SOURCE.encode("utf-8"))
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
    """This environment without PYTHONPATH, in the locale's encoding, not UTF-8 mode."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHONPATH")}
    return {**env, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "0"}


def _long_names(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line[:1] not in ("", "#")]
    column = lines[0].split("\t").index("long_name")
    return [line.split("\t")[column] for line in lines[1:]]


def _names(repo: Path, out: Path) -> list[str]:
    done = subprocess.run([sys.executable, "-m", "crapkit", "inventory", "--export", str(out)],
                          cwd=repo, env=_env(), capture_output=True)
    if done.returncode != 0:
        raise RuntimeError(f"crapkit inventory exited {done.returncode}")
    return _long_names(out.read_bytes().decode("utf-8"))


def main(argv: list[str]) -> int:
    if locale.getpreferredencoding(False).lower() not in ("cp1252", "mbcs"):
        raise RuntimeError(f"the locale encoding here is {locale.getpreferredencoding(False)}, "
                           "not cp1252, so the probe can tell nothing")
    with tempfile.TemporaryDirectory(prefix="crapkit-r16-") as scratch:
        names = _names(_repo(Path(scratch)), Path(scratch) / "inventory.tsv")
    assert names == ["café( x )"], f"the UTF-8 function {ascii('café')} reads as {names!a}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
