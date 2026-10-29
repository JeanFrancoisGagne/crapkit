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

When a straddling function's ccn rose above its ccn at the diff base, or the
base holds no function under its key, the hook still passes it, but on a warn
block on stderr instead of the count line: the rise may have broken the mark,
and `verify` says whether it did.

These run against the real subcommand through `run_cli`, because the gate's
answer is the exit code a git commit reads.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import child_env, cli_runner

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
COUNT_LINE = ("crapkit gate: {n} staged function(s) carry a ratchet mark and were not gated - "
              "`crapkit verify` fails a mark that rises")
WARN_HEAD = ("crapkit gate: {n} staged marked function(s) rose in ccn and may pass their ratchet mark; "
             "`crapkit verify` judges them with coverage:")
WARN_WORDS = "rose in ccn"


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
    marks are in the committed TSV and `crapkit ratchet report` reads them. A
    comment edit leaves legacy's ccn where the base had it, so no warn."""
    stage_comment_in_legacy(seeded)

    res = run_cli(seeded, "hook-precommit")

    assert res.stderr == COUNT_LINE.format(n=1) + "\n", res.stderr


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


# --- the rise only coverage can see: warned, and caught by verify -------------

LANE_CONFIG = CONFIG + """
[exclude]
globs = ["tests/**"]

[[lane]]
name = "py"
command = "python -m pytest tests -p no:cacheprovider -p no:randomly --cov=src --cov-branch --cov-report=json:.crapkit/cov/py.json"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["src"]
full_suite = false
env = { COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }
"""
WARN_ROW = re.compile(r"^  ccn (\S+) -> (\d+)  (\S+):(\d+)  (.+?)  mark (\d+\.\d{4})$", re.M)


def warn_rows(stderr: str) -> list[tuple[str, ...]]:
    return WARN_ROW.findall(stderr)


def measured_repo(tmp_path: Path) -> Path:
    """The seeded repo under a pytest lane whose one test reaches neither
    function, scored, with legacy's mark set to 63.6 under the stamp seed
    wrote and committed."""
    repo = seeded_repo(tmp_path, "measured", None, LANE_CONFIG)
    write(repo, ".gitignore", ".crapkit/\n__pycache__/\n.coverage\n")
    write(repo, "tests/test_nothing.py", "def test_nothing():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, *COMMIT, "-m", "a test")
    for step in (("coverage",), ("ratchet", "seed")):
        done = run_cli(repo, *step)
        assert done.returncode == 0, done.stdout + done.stderr
    marks_file = repo / "crapkit-ratchet.tsv"
    text = marks_file.read_bytes().decode("utf-8").replace("legacy( n )\t72.0000", "legacy( n )\t63.6000")
    marks_file.write_bytes(text.encode("utf-8"))
    git(repo, "add", "-A")
    git(repo, *COMMIT, "-m", "marks")
    return repo


def test_a_marked_function_that_got_worse_is_verifys_problem_not_the_gates(tmp_path: Path):
    """ccn 12 sits under the mark of 63.6, so the staged blob, with no
    coverage, cannot say whether the CRAP passed it: the range 12 to 156
    straddles the mark. Its ccn rose from 8, so the hook warns and passes.
    `verify` compares the numbers on the committed tree and reports the rise."""
    repo = measured_repo(tmp_path)
    write(repo, "src/mod.py", tangled("legacy", 11) + "\n" + tangled("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")
    git(repo, *COMMIT, "--no-verify", "-m", "legacy grows")
    verified = run_cli(repo, "verify", "--json")
    regressions = {item["long_name"]: item for item in json.loads(verified.stdout)["findings"]
                   if item["kind"] == "ratchet_regression"}

    assert res.returncode == 0, res.stdout + res.stderr
    assert warn_rows(res.stderr) == [("8", "12", "src/mod.py", "1", "legacy( n )", "63.6000")], res.stderr
    assert "ccn 8 -> 12" in res.stderr and "mark 63.6000" in res.stderr, res.stderr
    assert verified.returncode == 6, verified.stdout + verified.stderr
    assert regressions["legacy( n )"]["recorded"] == 63.6, regressions


def test_the_warn_block_is_ascii_on_stderr_and_takes_the_function_off_the_count(seeded: Path):
    """legacy rose from 8 to 12 under its mark of 63.6; newer is not touched.
    The head line, one row, exit 0, nothing on stdout and no count line."""
    write(seeded, "src/mod.py", tangled("legacy", 11) + "\n" + tangled("newer"))
    git(seeded, "add", "src/mod.py")

    res = run_cli(seeded, "hook-precommit")

    assert (res.returncode, res.stdout) == (0, ""), res.stdout + res.stderr
    assert res.stderr == (WARN_HEAD.format(n=1) + "\n"
                          "  ccn 8 -> 12  src/mod.py:1  legacy( n )  mark 63.6000\n"), res.stderr
    assert res.stderr.isascii()


def test_a_comment_edit_beside_a_rise_keeps_its_place_on_the_count_line(tmp_path: Path):
    repo = seeded_repo(tmp_path, "both", marks(("src/mod.py", "legacy( n )", 63.6),
                                               ("src/mod.py", "newer( n )", 63.6)))
    write(repo, "src/mod.py", tangled("legacy", 11) + "\n" + commented("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stderr.splitlines() == [COUNT_LINE.format(n=1), WARN_HEAD.format(n=1),
                                       "  ccn 8 -> 12  src/mod.py:1  legacy( n )  mark 63.6000"], res.stderr


def test_a_ccn_that_fell_under_the_mark_stays_on_the_count_line(tmp_path: Path):
    """legacy at ccn 9 in the base falls to ccn 8, a comment line added: the
    range 8 to 72 still straddles the mark of 63.6, and nothing rose."""
    repo = seeded_repo(tmp_path, "fell", LEGACY_MARK, source=tangled("legacy", 8) + "\n" + tangled("newer"))
    write(repo, "src/mod.py", commented("legacy") + "\n" + tangled("newer"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stderr == COUNT_LINE.format(n=1) + "\n", res.stderr


def test_a_marked_function_added_in_the_commit_warns(tmp_path: Path):
    """The marks file names `added`, which the base holds nowhere: no base ccn
    to compare, so the hook warns."""
    repo = seeded_repo(tmp_path, "added", marks(("src/mod.py", "added( n )", 63.6)))
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("newer") + "\n" + tangled("added"))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert warn_rows(res.stderr) == [("new", "8", "src/mod.py", "35", "added( n )", "63.6000")], res.stderr


def test_a_first_commit_that_carries_a_marks_file_warns_for_each_straddling_function(tmp_path: Path):
    """No commit yet: HEAD is unborn and answers no ccn for any key."""
    repo = tmp_path / "first"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "crapkit.toml", CONFIG)
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("newer"))
    write(repo, "crapkit-ratchet.tsv", marks(("src/mod.py", "legacy( n )", 63.6), ("src/mod.py", "newer( n )", 63.6)))
    git(repo, "add", "-A")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert warn_rows(res.stderr) == [("new", "8", "src/mod.py", "1", "legacy( n )", "63.6000"),
                                     ("new", "8", "src/mod.py", "18", "newer( n )", "63.6000")], res.stderr
    assert EXEMPT not in res.stderr, res.stderr


def test_a_twin_rise_compares_with_the_bases_own_twin(tmp_path: Path):
    """Two `legacy` defs at ccn 8, both marked 63.6. The second grows to ccn
    9: the warn names `#2` against the base's `#2`, and the first, untouched,
    is not judged. The twin note prints once, for the staged file alone."""
    twins = tangled("legacy") + "\n" + tangled("legacy")
    repo = seeded_repo(tmp_path, "twins", marks(("src/mod.py", "legacy( n )", 63.6),
                                                ("src/mod.py", "legacy( n )#2", 63.6)), source=twins)
    write(repo, "src/mod.py", tangled("legacy") + "\n" + tangled("legacy", 8))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert warn_rows(res.stderr) == [("8", "9", "src/mod.py", "18", "legacy( n )#2", "63.6000")], res.stderr
    assert res.stderr.count("defines legacy( n ) more than once") == 1, res.stderr


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16-le-bom", "crlf"])
def test_a_base_blob_in_any_encoding_gives_the_same_base_ccn(tmp_path: Path, encoding: str):
    encode = ENCODINGS[encoding]
    repo = seeded_repo(tmp_path, encoding, LEGACY_MARK)
    (repo / "src" / "mod.py").write_bytes(encode(tangled("legacy") + "\n" + tangled("newer")))
    git(repo, "add", "-A")
    git(repo, *COMMIT, "--allow-empty", "-m", f"as {encoding}")
    (repo / "src" / "mod.py").write_bytes(encode(tangled("legacy", 11) + "\n" + tangled("newer")))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 0, res.stdout + res.stderr
    assert warn_rows(res.stderr) == [("8", "12", "src/mod.py", "1", "legacy( n )", "63.6000")], res.stderr


class BlobReads:
    """A spy on gitio.blobs_at: the paths of each call, then the real read."""

    def __init__(self, monkeypatch):
        from crapkit import gitio

        self.calls, real = [], gitio.blobs_at

        def spy(root, revision, paths):
            paths = list(paths)
            self.calls.append(paths)
            return real(root, revision, paths)
        monkeypatch.setattr(gitio, "blobs_at", spy)


MARKED_BOTH = marks(("src/mod.py", "legacy( n )", 63.6), ("src/other.py", "other( n )", 63.6))
LAZY = {
    # name: (marks, staged src/mod.py, staged src/other.py, exit, the paths of each blobs_at call)
    "clean": (MARKED_BOTH, tangled("legacy") + "\n" + tangled("newer") + "\nX = 1\n", tangled("other"), 0, []),
    "all-refused": (marks(("src/mod.py", "legacy( n )", 9.0)), tangled("legacy", 9) + "\n" + tangled("newer"),
                    tangled("other"), 6, []),
    "all-pardoned-by-the-high-end": (marks(("src/mod.py", "legacy( n )", 72.0)),
                                     commented("legacy") + "\n" + tangled("newer"), tangled("other"), 0, []),
    "straddling": (MARKED_BOTH, tangled("legacy", 11) + "\n" + tangled("newer"), tangled("other", 11), 0,
                   [["src/mod.py", "src/other.py"]]),
}


@pytest.mark.parametrize("case", sorted(LAZY))
def test_the_hook_reads_base_blobs_only_for_a_straddling_function_and_in_one_call(tmp_path: Path, monkeypatch,
                                                                                    case: str):
    ratchet, mod, other, code, reads = LAZY[case]
    repo = seeded_repo(tmp_path, case, ratchet)
    write(repo, "src/other.py", tangled("other"))
    git(repo, "add", "-A")
    git(repo, *COMMIT, "-m", "other")
    write(repo, "src/mod.py", mod)
    write(repo, "src/other.py", other)
    git(repo, "add", "-A")
    spy = BlobReads(monkeypatch)

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == code, res.stdout + res.stderr
    assert spy.calls == reads, spy.calls


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


def test_a_warn_beside_an_unread_staged_file_exits_6_and_prints_both(tmp_path: Path):
    """The unread file refuses the commit; the warn about legacy still prints."""
    repo = seeded_repo(tmp_path, "unread-warn", LEGACY_MARK, UNREAD_CONFIG)
    write(repo, "src/handlers.ts", REFUSED_ARROW)
    write(repo, "src/mod.py", tangled("legacy", 11) + "\n" + tangled("newer"))
    git(repo, "add", "-A")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "UNREAD  src/handlers.ts" in res.stdout, res.stdout
    assert warn_rows(res.stderr) == [("8", "12", "src/mod.py", "1", "legacy( n )", "63.6000")], res.stderr


def test_a_refused_rise_beside_a_warned_one_refuses_on_stdout_and_warns_on_stderr(tmp_path: Path):
    """legacy passed its mark of 9 (ccn 10): refused. newer rose from 8 to 12
    under its mark of 63.6: warned. Exit 6 for the refusal alone."""
    repo = seeded_repo(tmp_path, "rise-and-warn", marks(("src/mod.py", "legacy( n )", 9.0),
                                                        ("src/mod.py", "newer( n )", 63.6)))
    write(repo, "src/mod.py", tangled("legacy", 9) + "\n" + tangled("newer", 11))
    git(repo, "add", "src/mod.py")

    res = run_cli(repo, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert rise_rows(res.stdout) == [LEGACY_ROW], res.stdout
    assert WARN_WORDS not in res.stdout, res.stdout
    assert warn_rows(res.stderr) == [("8", "12", "src/mod.py", "22", "newer( n )", "63.6000")], res.stderr


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


KEPT_LEGACY = "crapkit: 1 ratchet mark kept as it was (a mark only falls): src/mod.py legacy( n ) 9.0000"


def test_git_commit_with_a_reason_passes_a_marked_rise_and_keeps_its_mark(tmp_path: Path):
    """CRAPKIT_OVERRIDE_REASON on `git commit`: legacy, marked 9.0000, commits
    at ccn 10 with one alert line, the receipt names the kept mark, and the
    marks file keeps its bytes and stays out of the commit, though it has no
    key-format line a write would add. git hands a hook's stdout on through
    its own stderr."""
    alert, log = tmp_path / "alert.py", tmp_path / "alerts.log"
    alert.write_bytes(b"import sys\nopen(sys.argv[1], 'ab').write(sys.stdin.buffer.read())\n")
    command = f"'\"{sys.executable}\" \"{alert}\" \"{log}\"'"
    config = CONFIG.replace("[crapkit]\n", f"[crapkit]\nalert_command = {command}\n")
    repo = seeded_repo(tmp_path, "granted", LEGACY_AT_9, config)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_bytes(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -m crapkit hook-precommit\n'.encode())
    hook.chmod(0o755)
    before = hashlib.sha256((repo / "crapkit-ratchet.tsv").read_bytes()).hexdigest()
    stage_legacy_at_ccn_10(repo)

    committed = subprocess.run(["git", *COMMIT, "-m", "hotfix"], cwd=repo, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               env=child_env({"CRAPKIT_OVERRIDE_REASON": "hotfix 412"}))

    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert KEPT_LEGACY in committed.stderr.splitlines(), committed.stderr
    assert git(repo, "show", "--name-only", "--format=", "HEAD").split() == ["src/mod.py"]
    assert hashlib.sha256((repo / "crapkit-ratchet.tsv").read_bytes()).hexdigest() == before
    assert len(log.read_bytes().splitlines()) == 1 and b"legacy( n )" in log.read_bytes()


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
