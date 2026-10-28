"""The commit gate honours the ratchet marks `rescore --gate` already honours.

A repo that seeded its debt carries over-ceiling functions with recorded marks.
Touching one (a comment, a renamed local, a line moved) made `hook-precommit`
exit 6 while `rescore --gate` on the same working tree exited 0, because the
flag reads the marks file and the hook did not. A whole session of green
advisories then ended at a commit wall on debt the repo had already signed for.

A staged blob carries no coverage, so the hook knows a function's CRAP only as
a range: its ccn, at full coverage, up to its CRAP at none. It passes a marked
function while that ccn sits at or under the mark, with one count line, since
coverage could still hold the CRAP there; `verify` judges the exact number. A
ccn above the mark puts the CRAP above it at any coverage, so the hook refuses
that marked rise with exit 6 and a block naming the function and its mark.

These run against the real subcommand through `run_cli`, because the gate's
answer is the exit code a git commit reads.
"""
import json
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
    """A committed marks file. No stamp check runs here: the hook reads the
    marks' values, and a stamp mismatch is `verify`'s refusal, not the hook's."""
    lines = ["# crapkit-analysis=1 lizard=1.17.31", "path\tlong_name\tcrap"]
    lines += [f"{path}\t{name}\t{crap:.4f}" for path, name, crap in entries]
    return "\n".join(lines) + "\n"


def seeded_repo(tmp_path: Path, name: str, ratchet: str | None, config: str = CONFIG,
                source: str | None = None) -> Path:
    """Committed ccn-8 `legacy` beside ccn-8 `newer` (or `source`), with
    `ratchet` as the marks file (None writes none). Nothing is staged yet."""
    repo = tmp_path / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n")
    write(repo, "crapkit.toml", config)
    write(repo, "src/mod.py", source or tangled("legacy") + "\n" + tangled("newer"))
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
                     "gated - `crapkit verify` fails a mark that rises"], res.stderr


def test_two_marked_functions_are_one_line_not_two(tmp_path: Path):
    repo = seeded_repo(tmp_path, "both", marks(("src/mod.py", "legacy( n )", 63.6),
                                               ("src/mod.py", "newer( n )", 63.6)))
    write(repo, "src/mod.py", commented("legacy") + "\n" + commented("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert [ln for ln in res.stderr.splitlines() if EXEMPT in ln] == [
        "crapkit gate: 2 staged function(s) carry a ratchet mark and were not "
        "gated - `crapkit verify` fails a mark that rises"], res.stderr


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


# --- the rise only coverage can see, and where it is caught -------------------

def test_a_marked_function_that_got_worse_is_verifys_problem_not_the_gates(seeded: Path):
    """The cost this rule accepts. ccn 12 sits under the mark of 63.6, so the
    staged blob, with no coverage, cannot say whether the CRAP passed it: the
    range 12 to 156 straddles the mark. `verify` compares the numbers against
    the mark, which is the check that stays."""
    write(seeded, "src/mod.py", tangled("legacy", 11) + "\n" + tangled("newer"))
    git(seeded, "add", "src/mod.py")

    res = run_cli(seeded, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "1 staged function(s) carry a ratchet mark" in res.stderr, res.stderr


# --- the rise the ccn alone proves ------------------------------------------------

RISE = "rose past their ratchet mark"
LEGACY_AT_9 = marks(("src/mod.py", "legacy( n )", 9.0))
LEGACY_ROW = "  ccn  10  src/mod.py:1  legacy( n )  mark 9.0000"
COMMIT = ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q")


def stage_legacy_at_ccn_10(repo: Path, newer: str | None = None) -> None:
    write(repo, "src/mod.py", tangled("legacy", 9) + "\n" + (newer or tangled("newer")))
    git(repo, "add", "src/mod.py")


def rise_rows(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("  ccn ") and " mark " in line]


def test_a_mark_the_staged_ccn_passed_refuses_the_commit_naming_it(tmp_path: Path):
    """legacy marked 9.0000 and staged at ccn 10: its CRAP is at least 10.
    `legacy( n )` and its mark appear on the block's one row."""
    repo = seeded_repo(tmp_path, "rise", LEGACY_AT_9)
    stage_legacy_at_ccn_10(repo)

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "crapkit gate: 1 staged function(s) rose past their ratchet mark:" in res.stdout, res.stdout
    assert rise_rows(res.stdout) == [LEGACY_ROW], res.stdout
    assert res.stdout.isascii() and CEILING not in res.stdout, res.stdout


def test_base_main_in_a_ci_shaped_clone_gives_the_same_verdict(tmp_path: Path):
    """CI checks the committed change out in a clone and runs the hook with
    `--base`: the same function, the same mark, the same lines."""
    repo = seeded_repo(tmp_path, "origin", LEGACY_AT_9)
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "-c", "core.autocrlf=false", str(repo), str(clone))
    stage_legacy_at_ccn_10(repo)
    git(clone, "checkout", "-q", "-b", "change")
    stage_legacy_at_ccn_10(clone)
    git(clone, *COMMIT, "-m", "legacy grows")

    staged = run_cli(repo, "hook-precommit")
    based = run_cli(clone, "hook-precommit", "--base", "main")

    assert based.returncode == staged.returncode == 6, based.stdout + based.stderr
    assert based.stdout == staged.stdout, (based.stdout, staged.stdout)
    assert rise_rows(based.stdout) == [LEGACY_ROW], based.stdout


def test_a_rise_beside_an_unmarked_breach_prints_both_blocks_the_unmarked_first(tmp_path: Path):
    repo = seeded_repo(tmp_path, "both", LEGACY_AT_9)
    stage_legacy_at_ccn_10(repo, newer=commented("newer"))

    res = run_cli(repo, "hook-precommit")

    lines = res.stdout.splitlines()
    assert res.returncode == 6, res.stdout + res.stderr
    ceiling = next(i for i, line in enumerate(lines) if CEILING in line)
    rise = next(i for i, line in enumerate(lines) if RISE in line)
    assert ceiling < rise, res.stdout
    assert "newer( n )" in lines[ceiling + 1] and rise_rows(res.stdout) == [LEGACY_ROW], res.stdout
    assert DECOMPOSE in res.stdout, res.stdout


UNREAD_CONFIG = CONFIG.replace('languages = ["python"]', 'languages = ["python", "typescript"]')
REFUSED_ARROW = "export const handlers = {\n  pick: ({ x }) => new Set<string>([x]).has(x),\n};\n"


def test_a_rise_beside_an_unread_staged_file_prints_both(tmp_path: Path):
    """The unread file is refused whatever else the commit holds, and the rise
    is still named: the developer fixes both before the next attempt."""
    repo = seeded_repo(tmp_path, "unread", LEGACY_AT_9, UNREAD_CONFIG)
    write(repo, "src/handlers.ts", REFUSED_ARROW)
    git(repo, "add", "src/handlers.ts")
    stage_legacy_at_ccn_10(repo)

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "UNREAD  src/handlers.ts" in res.stdout, res.stdout
    assert rise_rows(res.stdout) == [LEGACY_ROW], res.stdout


def test_only_the_second_twin_past_its_mark_is_refused_under_its_handle(tmp_path: Path):
    """Two `legacy` defs: the first marked 63.6, the second, `#2`, marked 9.
    The second grows to ccn 10 and the refusal names its handle."""
    twins = tangled("legacy") + "\n" + tangled("legacy")
    repo = seeded_repo(tmp_path, "twins", marks(("src/mod.py", "legacy( n )", 63.6),
                                                ("src/mod.py", "legacy( n )#2", 9.0)), source=twins)
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("legacy", 9))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert rise_rows(res.stdout) == ["  ccn  10  src/mod.py:18  legacy( n )#2  mark 9.0000"], res.stdout
    assert EXEMPT not in res.stderr, res.stderr


ENCODINGS = {
    "utf-8": lambda text: text.encode("utf-8"),
    "utf-16-le-bom": lambda text: b"\xff\xfe" + text.encode("utf-16-le"),
    "crlf": lambda text: text.replace("\n", "\r\n").encode("utf-8"),
}


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_a_rise_in_any_encoding_gets_the_same_verdict_and_line(tmp_path: Path, encoding: str):
    encode = ENCODINGS[encoding]
    repo = seeded_repo(tmp_path, encoding, LEGACY_AT_9)
    (repo / "src" / "mod.py").write_bytes(encode(tangled("legacy") + "\n" + tangled("newer")))
    git(repo, "add", "-A")
    git(repo, *COMMIT, "--allow-empty", "-m", f"as {encoding}")
    (repo / "src" / "mod.py").write_bytes(encode(tangled("legacy", 9) + "\n" + tangled("newer")))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert rise_rows(res.stdout) == [LEGACY_ROW], res.stdout


# --- the hook against verify, on the committed tree ---------------------------------

CC_ONLY = CONFIG + "coverage_optional = true\n"


def test_every_refused_rise_is_a_regression_and_no_pardon_is_one(tmp_path: Path):
    """A cc-only scope scores CRAP as ccn, so seed marks both functions at 8.
    legacy staged at ccn 10 is refused, newer under a comment is pardoned (its
    range is the one point 8). Committed past the hook, verify reports the
    first as a ratchet regression and not the second."""
    repo = seeded_repo(tmp_path, "cc", None, CC_ONLY)
    for step in (("coverage",), ("ratchet", "seed")):
        assert run_cli(repo, *step).returncode == 0
    git(repo, "add", "-A")
    git(repo, *COMMIT, "-m", "marks")
    stage_legacy_at_ccn_10(repo, newer=commented("newer"))

    hook = run_cli(repo, "hook-precommit")
    git(repo, *COMMIT, "--no-verify", "-m", "past the hook")
    verified = run_cli(repo, "verify", "--json")
    regressions = {item["long_name"]: item for item in json.loads(verified.stdout)["findings"]
                   if item["kind"] == "ratchet_regression"}

    assert hook.returncode == 6, hook.stdout + hook.stderr
    assert rise_rows(hook.stdout) == ["  ccn  10  src/mod.py:1  legacy( n )  mark 8.0000"], hook.stdout
    assert "crapkit gate: 1 staged function(s) carry a ratchet mark" in hook.stderr, hook.stderr
    assert verified.returncode == 6, verified.stdout + verified.stderr
    assert (regressions["legacy( n )"]["recorded"], regressions["legacy( n )"]["fresh_crap"]) == (8.0, 10.0)
    assert "newer( n )" not in regressions, regressions
