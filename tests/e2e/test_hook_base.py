"""CI gates the committed change against its base with the current index."""
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

    ordinary = run_cli(tmp_path, "hook-precommit")
    changed = run_cli(tmp_path, "hook-precommit", "--base", "base")

    assert ordinary.returncode == 0, ordinary.stdout + ordinary.stderr
    assert changed.returncode == 6, changed.stdout + changed.stderr
    assert "decide" in changed.stdout


def test_an_unknown_gate_base_refuses_instead_of_judging_nothing(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    git_commit_all(tmp_path, "base")

    result = run_cli(tmp_path, "hook-precommit", "--base", "missing-ref")

    assert result.returncode == 4, result.stdout + result.stderr
