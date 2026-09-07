"""A literal POSIX backslash survives measurement and commands that consume it."""
import json
import os

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="literal backslashes require a POSIX filesystem")
def test_git_filename_survives_inventory_rescore_and_claim_release(tmp_path):
    git_init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    path = "src/a\\b.py"
    (tmp_path / path).write_text("def f(x):\n" + "".join(
        f"    if x == {i}: return {i}\n" for i in range(7)) + "    return 0\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\nanalysis_workers=1\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    git_commit_all(tmp_path, "literal filename")
    measured = run_cli(tmp_path, "coverage", "--json")
    assert measured.returncode == 0, measured.stderr
    assert json.loads(measured.stdout)["functions"] == 1
    claim = run_cli(tmp_path, "next-item", "--claim")
    assert claim.returncode == 0, claim.stderr
    assert json.loads(claim.stdout)["item"]["path"] == path
    rescored = run_cli(tmp_path, "rescore", path, "--json")
    assert rescored.returncode == 0, rescored.stderr
    assert [row["path"] for row in json.loads(rescored.stdout)["functions"]] == [path]
    released = run_cli(tmp_path / "src", "claims", "release", "a\\b.py", "f", "--json")
    assert released.returncode == 0, released.stderr
    assert json.loads(released.stdout)["released"] == 1
