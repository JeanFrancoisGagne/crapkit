"""The commit gate honours the ratchet marks `rescore --gate` already honours.

A repo that seeded its debt carries over-ceiling functions with recorded marks.
Touching one — a comment, a renamed local, a line moved — made `hook-precommit`
exit 6 while `rescore --gate` on the same working tree exited 0, because the
flag reads the marks file and the hook did not. A whole session of green
advisories then ended at a commit wall on debt the repo had already signed for.

The hook exempts on EXISTENCE of a (path, long_name) mark, not on the numeric
`crap > mark` rule the flag uses: a staged blob carries no coverage, so it has
no CRAP to compare against a mark. `verify` keeps the numeric worsening check,
which is what catches a mark that actually rose.

These run against the real subcommand, spawned, because the gate's answer is
the process exit code a git commit reads.
"""
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""

CEILING = "exceed the complexity ceiling"
DECOMPOSE = "decompose before committing"
EXEMPT = "carry a ratchet mark"


run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace",
                     env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, encoding="utf-8")
    return res.stdout


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def tangled(name: str, decisions: int = 7) -> str:
    """`decisions` decisions: ccn is one more, so 7 is ccn 8 over the target of 6."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, decisions + 1))
    return f"def {name}(n):\n{body}    return n\n"


def commented(name: str, decisions: int = 7) -> str:
    """The same function with one comment line added inside it. Same ccn, same
    long_name, and a changed range the gate cannot help but see."""
    head, rest = tangled(name, decisions).split("\n", 1)
    return f"{head}\n    # explains the branch below\n{rest}"


def marks(*entries: tuple[str, str, float]) -> str:
    """A committed marks file. No stamp check runs here — the gate reads
    existence, and a stamp mismatch is `verify`'s refusal, not the hook's."""
    lines = ["# crapkit-analysis=1 lizard=1.17.31", "path\tlong_name\tcrap"]
    lines += [f"{path}\t{name}\t{crap:.4f}" for path, name, crap in entries]
    return "\n".join(lines) + "\n"


def seeded_repo(tmp_path: Path, name: str, ratchet: str | None) -> Path:
    """Committed ccn-8 `legacy` beside ccn-8 `newer`, with `ratchet` as the
    marks file (None writes none). Nothing is staged yet."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n")
    write(repo, "crapkit.toml", CONFIG)
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("newer"))
    if ratchet is not None:
        write(repo, "crapkit-ratchet.tsv", ratchet)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return repo


LEGACY_MARK = marks(("src/mod.py", "legacy( n )", 63.6))


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    return seeded_repo(tmp_path, "seeded", LEGACY_MARK)


def stage_comment_in_legacy(repo: Path) -> None:
    write(repo, "src/mod.py", commented("legacy") + "\n" + tangled("newer"))
    git(repo, "add", "src/mod.py")


# --- the exemption -----------------------------------------------------------

def test_a_comment_edit_inside_a_marked_function_commits(seeded: Path):
    """The wall this ticket removes: the edit adds no decision and the function
    is already recorded debt, so the commit is not the moment to argue about it."""
    stage_comment_in_legacy(seeded)

    res = run_cli(seeded, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert CEILING not in res.stdout, res.stdout
    assert DECOMPOSE not in res.stdout, res.stdout


def test_the_exemption_says_how_many_marks_it_honoured(seeded: Path):
    """One line, on stderr, naming the count. Transparency without a list: the
    marks are in the committed TSV and `crapkit ratchet report` reads them."""
    stage_comment_in_legacy(seeded)

    res = run_cli(seeded, "hook-precommit")

    lines = [ln for ln in res.stderr.splitlines() if EXEMPT in ln]
    assert lines == ["crapkit gate: 1 staged function(s) carry a ratchet mark and were not "
                     "gated — `crapkit verify` fails a mark that rises"], res.stderr


def test_two_marked_functions_are_one_line_not_two(tmp_path: Path):
    repo = seeded_repo(tmp_path, "both", marks(("src/mod.py", "legacy( n )", 63.6),
                                               ("src/mod.py", "newer( n )", 63.6)))
    write(repo, "src/mod.py", commented("legacy") + "\n" + commented("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert [ln for ln in res.stderr.splitlines() if EXEMPT in ln] == [
        "crapkit gate: 2 staged function(s) carry a ratchet mark and were not "
        "gated — `crapkit verify` fails a mark that rises"], res.stderr


# --- what the exemption does not reach ---------------------------------------

def test_an_unmarked_function_over_the_ceiling_is_still_refused(tmp_path: Path):
    """The gate's whole job. No marks file at all, so nothing is exempt."""
    repo = seeded_repo(tmp_path, "unmarked", None)
    write(repo, "src/mod.py", commented("legacy") + "\n" + tangled("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert CEILING in res.stdout and DECOMPOSE in res.stdout, res.stdout
    assert "legacy( n )" in res.stdout, res.stdout
    assert EXEMPT not in res.stderr, res.stderr


def test_a_mark_on_one_function_does_not_carry_its_neighbour(seeded: Path):
    """`legacy` is marked, `newer` is not; editing both leaves exactly one
    breach, and the summary line still reports the one it exempted."""
    write(seeded, "src/mod.py", commented("legacy") + "\n" + commented("newer"))
    git(seeded, "add", "src/mod.py")

    res = run_cli(seeded, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    breaches = [ln for ln in res.stdout.splitlines() if ln.startswith("  ccn ")]
    assert len(breaches) == 1 and "newer( n )" in breaches[0], res.stdout
    assert "1 staged function(s) carry a ratchet mark" in res.stderr, res.stderr


def test_a_mark_recorded_for_another_file_exempts_nothing(tmp_path: Path):
    repo = seeded_repo(tmp_path, "otherfile", marks(("src/other.py", "legacy( n )", 63.6)))
    stage_comment_in_legacy(repo)

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert EXEMPT not in res.stderr, res.stderr


def test_a_clean_commit_stays_silent(seeded: Path):
    """No breach, no marks read, no line: the summary must not become noise on
    every commit that had nothing to exempt."""
    write(seeded, "src/mod.py", tangled("legacy") + "\n" + tangled("newer") + "\nX = 1\n")
    git(seeded, "add", "src/mod.py")

    res = run_cli(seeded, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stderr == "", res.stderr


def test_a_clean_commit_never_opens_the_marks_file(tmp_path: Path):
    """The gate runs at every commit and most commits breach nothing. An
    unreadable marks file proves the read did not happen: it would refuse."""
    repo = seeded_repo(tmp_path, "unread", "not\ta\tvalid\tratchet\tline\n")
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("newer") + "\nX = 1\n")
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr


def test_an_unparseable_marks_file_names_itself_instead_of_crashing(tmp_path: Path):
    """A `git commit` must never end in a traceback. Exit 3 with the file named
    is the refusal; the raw ValueError from the parser is not."""
    repo = seeded_repo(tmp_path, "corrupt", "not\ta\tvalid\tratchet\tline\n")
    stage_comment_in_legacy(repo)

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 3, res.stdout + res.stderr
    assert "unreadable ratchet file crapkit-ratchet.tsv" in res.stderr, res.stderr
    assert "Traceback" not in res.stderr, res.stderr


# --- the loosening, and where it is caught -----------------------------------

def test_a_marked_function_that_got_worse_is_verifys_problem_not_the_gates(seeded: Path):
    """The cost this decision accepts. Existence exemption cannot see a rise:
    the staged blob has no coverage and therefore no CRAP. `verify` compares the
    numbers against the mark and exits 7, which is the check that stays."""
    write(seeded, "src/mod.py", tangled("legacy", 11) + "\n" + tangled("newer"))
    git(seeded, "add", "src/mod.py")

    res = run_cli(seeded, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 staged function(s) carry a ratchet mark" in res.stderr, res.stderr
