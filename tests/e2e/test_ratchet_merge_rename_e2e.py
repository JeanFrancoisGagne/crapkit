"""A real `git merge` through the installed marks merge driver keeps a tighten
made on one branch when the other branch re-keyed or moved the same mark.

main tightens `classify( a , b )` from 7.0000 to 6.0000. The feature branch
either re-keys the mark after a signature change (`ratchet move --function`)
or moves the file (`git mv` plus `ratchet move`). Merging the feature branch
into main leaves one line, under the feature branch's key, at 6.0000.
"""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import sys

from conftest import child_env, cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8")

RATCHET = "crapkit-ratchet.tsv"
CONFIG = '[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n'
HEAD = "# crapkit-analysis=13 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
GRADE, GRADING = "calc/grade.py", "calc/grading.py"
OLD, NEW = "classify( a , b )", "classify( a , b , c = None )"


def marks(path: str, name: str, crap: str) -> str:
    return f"{HEAD}{path}\t{name}\t{crap}\n"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          env=child_env(), capture_output=True, text=True, encoding="utf-8")


def write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    (repo / rel).write_text(text, encoding="utf-8", newline="\n")


def driven_repo(tmp_path: Path) -> Path:
    """main holding `classify` marked at 7.0000, with the driver installed as
    docs/ratchet.md says, run by this interpreter, then main's tighten to 6.0000
    on top and a feature branch cut from before it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git_init_repo(repo)
    write(repo, "crapkit.toml", CONFIG)
    write(repo, GRADE, "def classify(a, b):\n    return a\n")
    write(repo, ".gitattributes", "crapkit-ratchet.tsv merge=crapkit-ratchet\n")
    write(repo, RATCHET, marks(GRADE, OLD, "7.0000"))
    git_commit_all(repo, "base marks")
    driver = f"{shlex.quote(Path(sys.executable).as_posix())} -m crapkit ratchet merge %O %A %B"
    assert git(repo, "config", "merge.crapkit-ratchet.driver", driver).returncode == 0
    assert git(repo, "branch", "feature").returncode == 0
    write(repo, RATCHET, marks(GRADE, OLD, "6.0000"))
    git_commit_all(repo, "a verify tightened classify")
    assert git(repo, "checkout", "-q", "feature").returncode == 0
    return repo


def merged_into_main(repo: Path) -> subprocess.CompletedProcess:
    git_commit_all(repo, "the feature branch's change")
    assert git(repo, "checkout", "-q", "main").returncode == 0
    return git(repo, "merge", "feature", "-m", "merge feature")


def test_a_re_key_branch_merged_into_a_tighten_branch_keeps_the_tighten(tmp_path: Path):
    repo = driven_repo(tmp_path)
    write(repo, GRADE, "def classify(a, b, c=None):\n    return a\n")
    moved = run_cli(repo, "ratchet", "move", "--function", GRADE, OLD, NEW)
    assert moved.returncode == 0, moved.stdout + moved.stderr

    done = merged_into_main(repo)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "ratchet merge: 1 mark(s)" in done.stdout + done.stderr
    assert (repo / RATCHET).read_text(encoding="utf-8") == marks(GRADE, NEW, "6.0000")


def test_a_git_mv_and_ratchet_move_branch_merged_into_a_tighten_branch_keeps_the_tighten(tmp_path: Path):
    repo = driven_repo(tmp_path)
    assert git(repo, "mv", GRADE, GRADING).returncode == 0
    moved = run_cli(repo, "ratchet", "move", GRADE, GRADING)
    assert moved.returncode == 0, moved.stdout + moved.stderr

    done = merged_into_main(repo)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "ratchet merge: 1 mark(s)" in done.stdout + done.stderr
    assert (repo / RATCHET).read_text(encoding="utf-8") == marks(GRADING, OLD, "6.0000")
    assert (repo / GRADING).is_file() and not (repo / GRADE).exists()
