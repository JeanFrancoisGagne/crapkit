"""A def whose signature runs past its first ')' is gated on its whole body (#72).

lizard 1.24.0 read the def below as two lines at ccn 1, so `rescore --gate` and
the commit hook passed it at any complexity. crapkit.lizardpython reads the
signature to its colon: the function scores ccn 8 over its full span and the
ceiling applies. The line break after `bases=()` is what black and ruff write
for a signature too long for one line.
"""
import json

from conftest import git, git_commit_all, git_init_repo, run_cli

TOML = ('[crapkit]\ntarget=6\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n')

BASE = "def decide(a, bases=()):\n    return a\n"

# ccn 8: base 1 and seven `if`s. No coverage, so crap = 8^2 + 8 = 72 > 6.
BRANCHES = "".join(f"    if a > {i}:\n        return {i}\n" for i in range(7))
FORMATTED = "def decide(a, *, bases=(),\n           slots=False):\n" + BRANCHES + "    return -1\n"

ISSUE = ("def decide(value: int) -> tuple[\n    int, int\n]:\n    if value > 0:\n        return 1, 1\n"
         "    if value < 0:\n        return -1, -1\n    for _ in range(3):\n        value += 1\n"
         "    return 0, 0\n")


def _scored_repo(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "logic.py").write_text(BASE, encoding="utf-8")
    git_commit_all(tmp_path, "base")
    scored = run_cli(tmp_path, "coverage")
    assert scored.returncode == 0, scored.stdout + scored.stderr
    return tmp_path


def test_rescore_gate_fails_a_formatted_signature_over_the_ceiling_and_names_it(tmp_path):
    repo = _scored_repo(tmp_path)
    (repo / "src" / "logic.py").write_text(FORMATTED, encoding="utf-8")

    result = run_cli(repo, "rescore", "src/logic.py", "--gate")

    assert result.returncode == 6, result.stdout + result.stderr
    assert "src/logic.py:1" in result.stdout, result.stdout


def test_the_commit_hook_fails_the_same_staged_def(tmp_path):
    repo = _scored_repo(tmp_path)
    (repo / "src" / "logic.py").write_text(FORMATTED, encoding="utf-8")
    git(repo, "add", "-A")

    result = run_cli(repo, "hook-precommit")

    assert result.returncode == 6, result.stdout + result.stderr
    assert "src/logic.py:1" in result.stdout, result.stdout


def test_rescore_reads_the_issue_def_over_its_whole_span(tmp_path):
    repo = _scored_repo(tmp_path)
    (repo / "src" / "logic.py").write_text(ISSUE, encoding="utf-8")

    result = run_cli(repo, "rescore", "src/logic.py", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    rows = json.loads(result.stdout)["functions"]
    assert [(row["function"], row["start"], row["end"], row["ccn"]) for row in rows] == [
        ("decide( value : int )", 1, 10, 4)]


def test_a_signature_cut_off_at_end_of_file_is_named_and_not_scored(tmp_path):
    """An unfinished def takes the road of any unreadable file: named on stderr,
    scored as zero functions, and the run goes on (0.7.1 ended runs over one file)."""
    repo = _scored_repo(tmp_path)
    (repo / "src" / "logic.py").write_text("def outer(a):\n    def inner(a, b=(),\n", encoding="utf-8")

    result = run_cli(repo, "rescore", "src/logic.py", "--gate")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "could not be tokenized" in result.stderr, result.stderr
    assert "src/logic.py:2 outer.inner( a , b = ( )" in result.stderr, result.stderr
    assert "0 changed function(s) judged" in result.stdout, result.stdout
