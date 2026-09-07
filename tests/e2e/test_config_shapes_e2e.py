"""Inventory refuses malformed corpus configuration without recording an empty run."""
import json

import pytest

from conftest import cli_runner, git_init_repo, git_commit_all


run_cli = cli_runner()


@pytest.mark.parametrize("scope,extra,field", [
    ('paths="src"\nlanguages=["python"]', "", "paths"),
    ('paths=["src"]\nlanguages="python"', "", "languages"),
    ('paths=["src"]\nlanguages=["python"]', '[exclude]\nglobs="tests/**"', "globs"),
])
def test_inventory_rejects_invalid_shapes_before_writing_snapshot(tmp_path, scope, extra, field):
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def value():\n    return 1\n")
    (tmp_path / "crapkit.toml").write_text('[[scope]]\nname="src"\n' + scope + "\n" + extra + "\n")
    git_commit_all(tmp_path, "fixture")
    result = run_cli(tmp_path, "inventory", "--json")
    assert result.returncode == 3, result.stdout + result.stderr
    assert field in json.loads(result.stdout)["error"]["message"]
    assert not (tmp_path / ".crapkit/crap.sqlite").exists()
