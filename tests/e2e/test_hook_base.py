"""CI gates the committed change against its base with the current index, a
marked function whose ccn passed its mark included, and reads a straddling
function's base ccn at the merge base `--base` names."""
from conftest import git, git_commit_all, git_init_repo, run_cli


def test_committed_complexity_is_gated_against_the_named_base(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=2\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "logic.py"
    source.write_text("def decide(a, b):\n    return a\n", encoding="utf-8")
    git_commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    source.write_text("def decide(a, b):\n    if a:\n        return 1\n"
                      "    if b:\n        return 2\n    return 3\n", encoding="utf-8")
    git_commit_all(tmp_path, "change")

    in_a_commit = run_cli(tmp_path, "hook-precommit", env_extra={"GIT_INDEX_FILE": ".git/index"})
    changed = run_cli(tmp_path, "hook-precommit", "--base", "base")

    assert in_a_commit.returncode == 0, in_a_commit.stdout + in_a_commit.stderr
    assert changed.returncode == 6, changed.stdout + changed.stderr
    assert "decide" in changed.stdout


def test_outside_a_commit_nothing_staged_judges_every_tracked_file(tmp_path):
    """`pre-commit run --all-files` in CI stages nothing and starts no commit;
    the plain hook then judges the tracked files, so a committed breach fails."""
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=2\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "logic.py").write_text(
        "def decide(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 3\n",
        encoding="utf-8")
    git_commit_all(tmp_path, "a breach nobody gated")

    result = run_cli(tmp_path, "hook-precommit")

    assert result.returncode == 6, result.stdout + result.stderr
    assert "1 tracked function(s) exceed the complexity ceiling of 2" in result.stdout
    assert "every tracked file was judged" in result.stderr


CC_ONLY_AT_2 = ('[crapkit]\ntarget=2\n[[scope]]\nname="src"\npaths=["src"]\n'
                'languages=["python"]\ncoverage_optional=true\n')
DECIDE_3 = "def decide(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 3\n"
DECIDE_4 = DECIDE_3.replace("    return 3\n", "    if a and b:\n        return 4\n    return 3\n")


def test_a_committed_marked_rise_is_refused_against_the_base_and_outside_a_commit(tmp_path):
    """decide is marked at 3 in a cc-only scope, where CRAP is ccn, and grows
    to ccn 5. A CI step running `hook-precommit --base` refuses it, and so
    does the hook run by hand with nothing staged, which judges every tracked
    file."""
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(CC_ONLY_AT_2, encoding="utf-8")
    (tmp_path / "crapkit-ratchet.tsv").write_text("path\tlong_name\tcrap\nsrc/logic.py\tdecide( a , b )\t3.0000\n",
                                                  encoding="utf-8", newline="\n")
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "logic.py"
    source.write_text(DECIDE_3, encoding="utf-8")
    git_commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    source.write_text(DECIDE_4, encoding="utf-8")
    git_commit_all(tmp_path, "change")

    changed = run_cli(tmp_path, "hook-precommit", "--base", "base")
    tracked = run_cli(tmp_path, "hook-precommit")

    assert changed.returncode == 6, changed.stdout + changed.stderr
    assert "crapkit gate: 1 staged function(s) rose past their ratchet mark:" in changed.stdout
    assert "  ccn   5  src/logic.py:1  decide( a , b )  mark 3.0000" in changed.stdout
    assert tracked.returncode == 6, tracked.stdout + tracked.stderr
    assert "crapkit gate: 1 tracked function(s) rose past their ratchet mark:" in tracked.stdout


MEASURED_AT_6 = '[crapkit]\ntarget=6\n[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'


def tangled(decisions: int) -> str:
    """`legacy(n)` with `decisions` ifs: ccn is one more."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, decisions + 1))
    return f"def legacy(n):\n{body}    return n\n"


def test_base_main_in_a_ci_shaped_clone_warns_against_the_merge_base_blob(tmp_path):
    """legacy is marked 63.6 at ccn 8. The branch takes it to ccn 10 and main,
    after the fork, to ccn 12. The range 10 to 110 straddles the mark either
    way: against the merge base legacy rose from 8, against main's tip it fell
    from 12. `--base main` reads the merge base and warns."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git_init_repo(origin)
    (origin / "crapkit.toml").write_text(MEASURED_AT_6, encoding="utf-8")
    (origin / "crapkit-ratchet.tsv").write_text("path\tlong_name\tcrap\nsrc/logic.py\tlegacy( n )\t63.6000\n",
                                                encoding="utf-8", newline="\n")
    (origin / "src").mkdir()
    (origin / "src" / "logic.py").write_text(tangled(7), encoding="utf-8", newline="\n")
    git_commit_all(origin, "base")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "-c", "core.autocrlf=false", str(origin), str(clone))
    git(clone, "checkout", "-q", "-b", "change")
    (clone / "src" / "logic.py").write_text(tangled(9), encoding="utf-8", newline="\n")
    git_commit_all(clone, "legacy grows on the branch")
    (origin / "src" / "logic.py").write_text(tangled(11), encoding="utf-8", newline="\n")
    git_commit_all(origin, "legacy grows on main")
    git(clone, "fetch", "-q", "origin", "main:main")

    based = run_cli(clone, "hook-precommit", "--base", "main")

    assert based.returncode == 0, based.stdout + based.stderr
    assert "  ccn 8 -> 10  src/logic.py:1  legacy( n )  mark 63.6000" in based.stderr.splitlines(), based.stderr


def test_an_unknown_gate_base_refuses_instead_of_judging_nothing(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    git_commit_all(tmp_path, "base")

    result = run_cli(tmp_path, "hook-precommit", "--base", "missing-ref")

    assert result.returncode == 4, result.stdout + result.stderr
