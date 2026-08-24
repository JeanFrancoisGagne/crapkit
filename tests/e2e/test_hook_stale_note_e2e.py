"""The gate's re-stage note fires on a real difference, never on a line-ending filter.

`core.autocrlf=true` is git-for-windows' installer default: the blob holds LF,
the checkout holds CRLF, and git reads the two as the same file. Comparing the
staged blob to the working tree's raw bytes called every file on such a checkout
different, so the first message a Windows user ever got from the gate ended in
an instruction that does nothing.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""

NOTE = "differs from the working tree"


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CRAPKIT_OVERRIDE_REASON", None)
    return subprocess.run([sys.executable, "-m", "crapkit", *args], cwd=repo,
                          capture_output=True, text=True, timeout=180, env=env)


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout


def tangled(name: str) -> str:
    """Seven decisions: ccn 8, over the target of 6 whatever its coverage."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def clean(name: str) -> str:
    return f"def {name}(n):\n    if n > 1:\n        n = n + 1\n    return n\n"


def write_crlf(repo: Path, rel: str, text: str) -> None:
    """What a Windows checkout holds: the same content, CRLF on disk."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))


@pytest.fixture()
def crlf_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "crlf"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "true")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    write_crlf(repo, "src/mod.py", clean("alpha"))
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return repo


def test_a_crlf_checkout_is_not_a_stale_staging(crlf_repo: Path):
    write_crlf(crlf_repo, "src/route.py", tangled("route"))
    git(crlf_repo, "add", "src/route.py")
    assert git(crlf_repo, "diff", "--name-only") == "", "git itself sees no difference"

    res = run_cli(crlf_repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "route" in res.stdout, "the gate still refuses the ccn-8 function"
    assert NOTE not in res.stdout, res.stdout


def test_a_fix_left_unstaged_still_gets_the_note(crlf_repo: Path):
    """The note exists for exactly this: the fix is on disk, the commit is not."""
    write_crlf(crlf_repo, "src/route.py", tangled("route"))
    git(crlf_repo, "add", "src/route.py")
    write_crlf(crlf_repo, "src/route.py", clean("route"))

    res = run_cli(crlf_repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert NOTE in res.stdout, res.stdout
    assert "src/route.py" in res.stdout
