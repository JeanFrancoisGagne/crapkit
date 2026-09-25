"""doctor names a commit gate that is set up and never judges anything.

Two setups passed every commit or every CI run without a word:

- Route 1 writes crapkit's hook to .git/hooks/pre-commit. A core.hooksPath set
  anywhere else (a global one, husky's .husky/_) sends git to another
  directory, and git runs no hook from .git/hooks at all.
- Route 3 names crapkit-gate in .pre-commit-config.yaml, and CI runs
  `pre-commit run --all-files`. The hook judges the staged index, which a CI
  checkout leaves empty, so pre-commit reports it Passed on any branch.

WARN, never FAIL: the commit and the CI run still succeed; they just were not
gated.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crapkit.cli import admin
from crapkit.doctor import HookRoute, ci_precommit_passes, skipped_hook

ROUTE_1 = "#!/bin/sh\nexec python -m crapkit hook-precommit\n"
GATE_CONFIG = ("repos:\n  - repo: https://github.com/JeanFrancoisGagne/crapkit\n    rev: v0.8.0\n"
               "    hooks:\n      - id: crapkit-gate\n")


def route(**changes) -> HookRoute:
    base = dict(default="/r/.git/hooks/pre-commit", default_text=ROUTE_1,
                effective="/home/u/.githooks/pre-commit", effective_text="",
                scope="global", value="/home/u/.githooks")
    return HookRoute(**{**base, **changes})


# --- the hook git is sent away from ---------------------------------------------

def test_a_crapkit_hook_git_never_runs_is_one_warn_naming_both_paths_and_both_fixes():
    (finding,) = skipped_hook(route())

    assert finding.level == "WARN"
    assert finding.text == (
        "/r/.git/hooks/pre-commit runs crapkit's gate, but core.hooksPath (global config: "
        "/home/u/.githooks) sends git to /home/u/.githooks/pre-commit, so every commit here "
        "skips the gate without a word; run `git config --local core.hooksPath "
        "/r/.git/hooks` in this repo, or call crapkit hook-precommit from "
        "/home/u/.githooks/pre-commit")


@pytest.mark.parametrize("changes", [
    {"effective": "/r/.git/hooks/pre-commit", "scope": "", "value": ""},
    {"effective_text": "#!/bin/sh\nnpx lint-staged\ncrapkit hook-precommit\n"},
    {"default_text": ""},
    {"default_text": "#!/bin/sh\nnpx lint-staged\n"},
], ids=["no-hooks-path", "hooks-path-calls-crapkit", "no-local-hook", "local-hook-not-crapkit"])
def test_a_hook_git_does_run_or_one_that_is_not_crapkit_says_nothing(changes):
    assert skipped_hook(route(**changes)) == ()


# --- pre-commit in CI -----------------------------------------------------------

@pytest.mark.parametrize("step", [
    "      - run: pre-commit run --all-files\n",
    "      - run: pre-commit run --all-files --show-diff-on-failure\n",
    "      - uses: pre-commit/action@v3.0.1\n",
    "      - run: prek run --all-files\n",
])
def test_ci_running_pre_commit_over_the_gate_is_named(step):
    ci = {".github/workflows/ci.yml": "jobs:\n  lint:\n    steps:\n" + step}

    (finding,) = ci_precommit_passes(GATE_CONFIG, ci)

    assert finding.level == "WARN"
    assert finding.text == (
        ".github/workflows/ci.yml runs pre-commit, and the crapkit-gate hook judges the staged "
        "index, which a CI checkout leaves empty: it passes every run whatever the branch "
        "holds; gate CI with `crapkit verify` instead (README, Route 4: CI)")


def test_ci_that_runs_crapkit_verify_or_a_config_without_the_gate_says_nothing():
    verify = {".github/workflows/ci.yml": "      - run: crapkit verify --baseline-tsv b.tsv\n"}
    precommit = {".gitlab-ci.yml": "lint:\n  script: pre-commit run --all-files\n"}

    assert ci_precommit_passes(GATE_CONFIG, verify) == ()
    assert ci_precommit_passes("repos: []\n", precommit) == ()
    assert ci_precommit_passes("", precommit) == ()


def test_each_ci_file_is_named_once_in_path_order():
    ci = {"z.yml": "pre-commit run", ".gitlab-ci.yml": "pre-commit run -a", "a.yml": "echo"}

    assert [f.text.split()[0] for f in ci_precommit_passes(GATE_CONFIG, ci)] == [
        ".gitlab-ci.yml", "z.yml"]


# --- through git ------------------------------------------------------------------

def _git(root: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with Route 1 written, and a global git config this test owns."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(ROUTE_1, encoding="utf-8")
    return root


def test_route_1_under_a_global_hooks_path_warns_and_the_named_fix_clears_it(repo, tmp_path):
    global_hooks = tmp_path / "home" / ".githooks"
    global_hooks.mkdir()
    _git(repo, "config", "--global", "core.hooksPath", global_hooks.as_posix())

    (finding,) = admin._doctor_silent_gates(repo)
    assert "(global config: " in finding.text
    assert f"sends git to {(global_hooks / 'pre-commit').resolve().as_posix()}" in finding.text

    fix = finding.text.split("`")[1]
    _git(repo, *fix.split()[1:])
    assert admin._doctor_silent_gates(repo) == []
    assert Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-path",
                     "hooks/pre-commit")).resolve() == (repo / ".git/hooks/pre-commit").resolve()


def test_route_1_with_no_hooks_path_says_nothing(repo):
    assert admin._doctor_silent_gates(repo) == []


def test_pre_commit_in_a_workflow_over_the_gate_is_named(repo):
    (repo / ".pre-commit-config.yaml").write_text(GATE_CONFIG, encoding="utf-8")
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "lint.yaml").write_text("steps:\n  - run: pre-commit run --all-files\n",
                                         encoding="utf-8")

    (finding,) = admin._doctor_silent_gates(repo)
    assert finding.text.startswith(".github/workflows/lint.yaml runs pre-commit")


def test_outside_a_repository_nothing_is_read(tmp_path):
    assert admin._doctor_silent_gates(tmp_path) == []
