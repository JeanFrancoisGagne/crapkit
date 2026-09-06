"""Init cannot shadow source ownership declared by an ancestor."""
import json
from pathlib import Path

import pytest

from conftest import cli_runner, git, git_init_repo


run_cli = cli_runner(encoding="utf-8", timeout=30)


def _parent(tmp_path: Path, path: str) -> Path:
    git_init_repo(tmp_path)
    child = tmp_path / "child"
    (child / "src").mkdir(parents=True)
    (child / "src/app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    config = ('[[scope]]\nname="parent"\npaths=[' + json.dumps(path)
              + ']\nlanguages=["python"]\n')
    (tmp_path / "crapkit.toml").write_text(config, encoding="utf-8")
    git(tmp_path, "add", ".")
    return child


@pytest.mark.parametrize("scope_path", [".", "./", "child", "child/src", "child/src/app.py"])
@pytest.mark.parametrize("explicit", [False, True])
def test_init_refuses_overlapping_scope_before_writing_any_nested_state(tmp_path, scope_path, explicit):
    child = _parent(tmp_path, scope_path)
    args = ("--repo", str(child)) if explicit else ()
    result = run_cli(child, "init", *args)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "already claims child (scope 'parent')" in result.stderr
    assert not (child / "crapkit.toml").exists()
    assert not (child / ".gitignore").exists()
    assert not (child / ".crapkit").exists()


def test_a_similarly_named_sibling_scope_does_not_claim_the_child(tmp_path):
    child = _parent(tmp_path, "children")
    result = run_cli(child, "init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (child / "crapkit.toml").is_file()


def test_an_independent_nested_repository_stops_root_scope_ownership(tmp_path):
    child = _parent(tmp_path, ".")
    git_init_repo(child)
    git(child, "add", ".")
    result = run_cli(child, "init")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (child / "crapkit.toml").is_file()
