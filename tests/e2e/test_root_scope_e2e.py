"""Root scopes must produce real rows through inventory and coverage commands."""
import json

import pytest

from conftest import git_commit_all, git_init_repo, run_cli


@pytest.mark.parametrize("command", ["inventory", "coverage"])
def test_root_scope_command_scores_tracked_root_source(tmp_path, command):
    git_init_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="root"\npaths=["."]\n'
        'languages=["typescript"]\ncoverage_optional=true\n', encoding="utf-8")
    (tmp_path / "app.ts").write_text("function rootFunction(x) { return x ? 1 : 0; }\n", encoding="utf-8")
    git_commit_all(tmp_path, "root source")
    result = run_cli(tmp_path, command, "--export", "result.tsv", "--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["functions"] == 1, result.stdout
    exported = (tmp_path / "result.tsv").read_text(encoding="utf-8")
    assert "app.ts" in exported and "rootFunction" in exported
