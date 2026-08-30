"""config_value answers what the REPOSITORY's config says.

Every other spawn in this module carries `-c diff.relative=true`, and
command-line config is config: `git -c diff.relative=true config --get
diff.relative` prints `true` in a repo that never set it, and prints `true`
again in one that set it to `false`. A helper whose whole job is reading the
repo's settings must not be reading back the flags crapkit itself passed.

Real git processes here, because the bug lives in the argv this module builds.
"""
import subprocess

import pytest

from crapkit.gitio import config_value


def git(repo, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def test_a_key_this_repo_never_set_reads_as_unset(repo):
    assert config_value(repo, "diff.relative") == ""


def test_the_repo_own_value_is_the_answer(repo):
    git(repo, "config", "diff.relative", "false")

    assert config_value(repo, "diff.relative") == "false"


def test_a_key_crapkit_passes_nothing_for_still_reads(repo):
    """The regression guard cuts both ways: no flag was injected for
    core.hooksPath, and its one caller must keep getting the value."""
    git(repo, "config", "core.hooksPath", "git-hooks")

    assert config_value(repo, "core.hooksPath") == "git-hooks"
