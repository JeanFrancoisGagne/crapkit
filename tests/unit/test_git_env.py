"""No test's git reaches the repo that ran the suite (tests/git_env.py)."""
import os

import git_env


def test_the_suite_runs_with_no_variable_that_points_git_at_a_repo():
    """Run from a hook or `git bisect run`, the suite inherits GIT_DIR, and a
    test that runs git in a temp dir works in the repo that ran the suite
    instead: test_churn_author_bytes wrote 8 commits into it. tests/conftest.py
    drops those variables before any test runs."""
    leaked = sorted(set(git_env.repo_env_names()) & set(os.environ))
    assert leaked == []


def test_git_names_the_variables_that_locate_a_repo():
    assert {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} <= set(git_env.repo_env_names())
