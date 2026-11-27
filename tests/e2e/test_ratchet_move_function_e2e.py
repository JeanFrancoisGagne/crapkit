"""End-to-end: `ratchet move --function` re-keys a mark after a signature edit.

A parameter added to a marked function changes its key, so the mark recorded
under the old signature keys no function. The command moves that one mark at
its recorded value, in a git repo with no `.crapkit` directory and no run, and
moving it back once the edit is undone gives the marks file back byte for byte.
"""
import hashlib
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

TOML = '[crapkit]\ntarget = 6\n\n[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n'
BEFORE = "def classify(a, b):\n    return a\n\n\ndef grade(x):\n    return x\n"
AFTER = BEFORE.replace("classify(a, b)", "classify(a, b, c=None)")
RATCHET = "crapkit-ratchet.tsv"
MARKS = ("# crapkit-analysis=13 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
         "calc/grade.py\tclassify( a , b )\t7.0000\n"
         "calc/grade.py\tgrade( x )\t31.5000\n")

run_cli = cli_runner(timeout=180, encoding="utf-8")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, check=True, capture_output=True, text=True)
    return done.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "calc").mkdir()
    (tmp_path / "calc" / "grade.py").write_text(BEFORE, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / RATCHET).write_bytes(MARKS.encode("utf-8"))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def digest(repo: Path) -> str:
    return hashlib.sha256((repo / RATCHET).read_bytes()).hexdigest()


def test_a_signature_edit_moves_its_mark_and_moving_it_back_restores_the_bytes(repo: Path):
    before = digest(repo)
    (repo / "calc" / "grade.py").write_text(AFTER, encoding="utf-8")

    res = run_cli(repo, "ratchet", "move", "--function", "calc/grade.py", "classify( a , b )",
                  "classify")

    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert res.stdout == ("crapkit-ratchet.tsv: re-keyed 1 mark in calc/grade.py from "
                          "classify( a , b ) to classify( a , b , c = None )\n")
    assert (repo / RATCHET).read_bytes() == MARKS.replace(
        "\tclassify( a , b )\t", "\tclassify( a , b , c = None )\t").encode("utf-8")
    assert git(repo, "diff", "--name-only").split() == ["calc/grade.py", RATCHET]
    assert not (repo / ".crapkit" / "crap.sqlite").exists(), "the command opens no store"

    (repo / "calc" / "grade.py").write_text(BEFORE, encoding="utf-8")
    back = run_cli(repo, "ratchet", "move", "--function", "calc/grade.py",
                   "classify( a , b , c = None )", "classify")

    assert back.returncode == 0, (back.returncode, back.stdout, back.stderr)
    assert digest(repo) == before


def test_a_refusal_exits_3_and_leaves_the_marks_file_as_it_was(repo: Path):
    before = digest(repo)
    (repo / "calc" / "grade.py").write_text(AFTER, encoding="utf-8")

    res = run_cli(repo, "ratchet", "move", "--function", "calc/grade.py", "classify( z )", "classify")

    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert res.stderr == ("crapkit: ratchet move --function: no mark named classify( z ) in "
                          'calc/grade.py; its marks are "classify( a , b )", "grade( x )"; name one '
                          "of them as OLD\n")
    assert digest(repo) == before
