"""Git transport keeps the tracked spelling through every path join."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from crapkit import gitio
from crapkit.churn import parse_git_log_lines
from crapkit.coupling import change_coupling_lines
from crapkit.diffparse import changed_ranges
from crapkit.errors import GitError
from crapkit.mutate import file_mutants
from crapkit.mutate_pool import drop_pool, run_mutants


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=Path test", "-c", "user.email=path@example.test",
                           *args], cwd=root, capture_output=True, check=True).stdout


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    return tmp_path


NAMES = [" leading.py", "caf\u00e9.py", "ordinary.py", "line\u2028break.py"]
if os.name != "nt":
    NAMES += ["trailing.py ", "tab\tname.py", "new\nline.py", "cr\rname.py", "back\\slash.py"]


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("nested", [False, True])
def test_dirty_history_and_diff_keys_equal_tracked_paths(repository, name, nested):
    root = repository / "app" if nested else repository
    root.mkdir(exist_ok=True)
    target = root / name
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    (root / "other.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "initial")
    before = gitio.head_commit(root)
    target.write_text("def f():\n    return 2\n", encoding="utf-8")
    assert set(gitio.ls_files(root)) == {name, "other.py"}
    assert gitio.status_names(root) == [name]
    assert gitio.unstaged_paths(root) == {name}
    assert set(changed_ranges(gitio.diff_since(root, before))) == {name}
    lines = list(gitio.churn_log_lines(root, 12))
    assert set(parse_git_log_lines(lines)) == {name, "other.py"}
    assert change_coupling_lines(lines, min_support=1, tracked={name, "other.py"})[0]["files"] == sorted([name, "other.py"])
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "edit")
    assert gitio.diff_names_since(root, before) == [name]
    assert gitio.staged_blobs(root, [name])[name] == b"def f():\n    return 2\n"
    with gitio.staged_reads(root) as reads:
        assert reads.staged_blobs([name])[name] == b"def f():\n    return 2\n"
    assert name in gitio.index_modes(root, ".")
    before_rename = gitio.head_commit(root)
    moved = "moved " + name
    git(root, "mv", "--", name, moved)
    git(repository, "commit", "-qm", "rename")
    assert gitio.renamed_paths(root, before_rename) == {name: moved}


@pytest.mark.parametrize("workers", [1, 2])
def test_mutation_workers_receive_dirty_leading_space_dependency(repository, workers):
    source = "def enabled():\n    return True\n"
    (repository / "m.py").write_text(source, encoding="utf-8")
    (repository / " dependency.txt").write_text("old", encoding="utf-8")
    (repository / "runner.py").write_text(
        "from pathlib import Path\nimport m\n"
        "assert Path(' dependency.txt').read_text() == 'current'\n"
        "assert m.enabled()\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "initial")
    (repository / " dependency.txt").write_text("current", encoding="utf-8")
    mutant = file_mutants(source, None, "python")[0]._replace(path="m.py")
    cfg = SimpleNamespace(mutation_workers=workers, mutation_timeout_seconds=10,
                          mutation_command=f'"{sys.executable}" runner.py')
    try:
        assert run_mutants(repository, cfg, [mutant, mutant], lambda *args: None) == [True, True]
    finally:
        drop_pool(repository)


def test_path_reads_report_a_missing_git_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(GitError, match="git executable not found"):
        gitio.ls_files(tmp_path)


def test_path_reads_preserve_git_command_errors(tmp_path):
    with pytest.raises(GitError, match="git ls-files.*failed"):
        gitio.ls_files(tmp_path)
