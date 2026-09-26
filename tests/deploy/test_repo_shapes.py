"""The first line a user acts on, in each repo shape the start meets.

A submodule, a repo with no commit yet, a directory that is not a repo, a
brownfield repo whose owner armed the gate before anything else, a monorepo
whose crapkit root sits below the git top, and a linked worktree. Each cell
runs the README's lines in that shape and holds crapkit to one thing: the
line it prints next says what to do.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from kit import gitsurf, repos
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-git"


def start(box, repo: Path) -> dict[str, object]:
    """The 60-second start in `repo` through its commit and verify, the fix for
    the container guard applied after init; each line's step by its line."""
    steps = {}
    for line in gitsurf.start_lines():
        steps[line] = box.script(line, cwd=repo, env=box.commit_env() if line.startswith("git commit") else None)
        if line == "crapkit init" and steps[line].exit == 0:
            gitsurf.keyed_in_container(repo)
    return steps


# --- a submodule ----------------------------------------------------------------------

@cell("lin-submodule", channel="pip venv", harness="git 2.47",
      scenario="fresh: first line the user acts on in a repo with a submodule", use_cases="init, commit gate",
      os="linux", image="cells", cadence="nightly")
def test_a_submodule_stays_out_of_the_scopes_and_its_bump_commits(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "submodule", cache=templates)
    gitsurf.adopt(box, repo)
    gitsurf.route1(box, repo)

    scopes = tomllib.loads((repo / "crapkit.toml").read_text(encoding="utf-8"))["scope"]
    assert [path for scope in scopes for path in scope["paths"] if path.startswith("vendor")] == []
    library = repo / "vendor" / "lib"
    (library / "scripts" / "deploy.sh").write_text("#!/bin/sh\necho bumped\n", encoding="utf-8", newline="\n")
    box.run(["git", "commit", "-q", "-am", "bump"], cwd=library, env=box.commit_env(), expect=0)
    gitsurf.assert_accepted(gitsurf.commit(box, repo, stage="vendor/lib"))
    gitsurf.refused_then_accepted(box, repo)


# --- no commit yet --------------------------------------------------------------------

@cell("lin-zero-commit", channel="pip venv", harness="git 2.47",
      scenario="fresh: first line the user acts on in a repo with no commit", use_cases="init, commit gate",
      os="linux", image="cells", cadence="nightly")
def test_a_repo_with_no_commit_starts_once_its_first_commit_lands(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "zero-commit", cache=templates)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "first"], cwd=repo, env=box.commit_env(), expect=0)

    steps = start(box, repo)
    assert [line for line, step in steps.items() if step.exit != 0] == []


@cell("lin-zero-commit", channel="pip venv", harness="git 2.47",
      scenario="fresh: first line the user acts on in a repo with no commit", use_cases="init, commit gate",
      os="linux", image="cells", cadence="nightly")
def test_coverage_before_the_first_commit_names_it(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "zero-commit", cache=templates)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)

    coverage = start(box, repo)["crapkit coverage"]
    assert coverage.exit == 0 or re.search(r"no commit|first commit", coverage.stderr, re.I), coverage.stderr


# --- not a repo -----------------------------------------------------------------------

@cell("lin-not-git", channel="pip venv", harness="git 2.47",
      scenario="fresh: first line the user acts on in a directory that is not a repo", use_cases="init",
      os="linux", image="cells", cadence="nightly")
def test_init_outside_a_repo_says_so_and_starts_after_git_init(box, templates):
    gitsurf.pip_venv(box)
    folder = repos.checkout(box, "not-git", cache=templates)

    refused = box.script("crapkit init", cwd=folder)
    assert refused.exit != 0 and "not a git repository" in refused.stderr, refused.stderr
    box.run(["git", "init", "-q", "-b", "main"], cwd=folder, expect=0)
    box.run(["git", "add", "-A"], cwd=folder, expect=0)
    box.run(["git", "commit", "-q", "-m", "first"], cwd=folder, env=box.commit_env(), expect=0)
    assert [line for line, step in start(box, folder).items() if step.exit != 0] == []


# --- brownfield, gate armed first --------------------------------------------------------

@cell("lin-brownfield-arm-first", channel="pip venv", harness="git 2.47",
      scenario="fresh: gate armed before init; init, seed, then an edit to signed debt", use_cases="init, commit gate",
      os="linux", image="cells", cadence="nightly")
def test_a_gate_armed_first_passes_signed_debt_once_seeded(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.route1(box, repo)
    gitsurf.adopt(box, repo)

    legacy = repo / "calc" / "legacy_1.py"
    legacy.write_text(legacy.read_text(encoding="utf-8").replace('return "D"', 'return "E"'), encoding="utf-8",
                      newline="\n")
    step = gitsurf.commit(box, repo)
    gitsurf.assert_accepted(step)
    assert "carry a ratchet mark and were not gated" in step.stderr


@cell("lin-brownfield-arm-first", channel="pip venv", harness="git 2.47",
      scenario="fresh: gate armed before init; the first commit's line", use_cases="init, commit gate",
      os="linux", image="cells", cadence="nightly")
def test_a_gate_armed_before_init_names_init(box, templates):
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.route1(box, repo)
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8", newline="\n")

    step = gitsurf.commit(box, repo)
    assert step.exit == 0 or "crapkit init" in step.stderr, step.stderr


# --- a monorepo: the crapkit root below the git top ----------------------------------------

def monorepo(box, templates) -> tuple[Path, Path]:
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "subdir-root", cache=templates)
    api = repo / "packages" / "api"
    gitsurf.adopt(box, api)
    return repo, api


@cell("lin-monorepo", channel="pip venv", harness="git 2.47",
      scenario="fresh: subdir root; MCP from the git top; the edit advisory; next-item from a sibling with --repo",
      use_cases="subdir root", os="linux", image="cells", cadence="nightly")
def test_a_root_below_the_git_top_answers_mcp_and_the_advisory(box, templates):
    repo, api = monorepo(box, templates)
    edited = api / gitsurf.breach(api)

    sibling = box.run(["crapkit", "next-item", "--repo", "../api"], cwd=repo / "packages" / "web", expect=0)
    assert '"path": "calc/grade.py"' in sibling.stdout, sibling.stdout
    gitsurf.assert_advised(gitsurf.advise(box, repo, edited), "calc/route.py")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        error, text = gitsurf.next_item_text(client, {})
        assert error and "repo" in text, text
        error, text = gitsurf.next_item_text(client, {"repo": "packages/api"})
        assert not error and "calc/grade.py" in text, text


@cell("lin-monorepo", channel="pip venv", harness="git 2.47",
      scenario="fresh: subdir root; Route 2 at git top; commit in packages/api", use_cases="subdir root",
      os="linux", image="cells", cadence="nightly")
def test_route2_at_the_git_top_gates_a_commit_in_the_package(box, templates):
    repo, api = monorepo(box, templates)
    gitsurf.route2(box, repo)

    gitsurf.refused_then_accepted(box, repo, where="packages/api/calc")


@cell("lin-monorepo", channel="pip venv", harness="git 2.47",
      scenario="fresh: subdir root; next-item from a sibling package", use_cases="subdir root",
      os="linux", image="cells", cadence="nightly")
def test_next_item_from_a_sibling_package_names_repo(box, templates):
    repo, _ = monorepo(box, templates)

    step = box.run(["crapkit", "next-item"], cwd=repo / "packages" / "web", expect=3)
    assert "--repo" in step.stderr, step.stderr


# --- a linked worktree ------------------------------------------------------------------

def worktree(box, templates) -> tuple[Path, Path]:
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    gitsurf.adopt(box, repo)
    tree = box.root / "worktree"
    box.run(["git", "worktree", "add", "-q", "-b", "work", str(tree)], cwd=repo, expect=0)
    return repo, tree


@cell("lin-worktree", channel="pip venv", harness="git 2.47",
      scenario="fresh: from a linked worktree: coverage, next-item, MCP with no --repo, the edit advisory",
      use_cases="coverage, MCP, commit gate", os="linux", image="cells", cadence="push")
def test_a_linked_worktree_measures_ranks_serves_and_advises(box, templates):
    _, tree = worktree(box, templates)

    box.run(["crapkit", "coverage"], cwd=tree, expect=0)
    assert "calc/grade.py" in box.run(["crapkit", "next-item"], cwd=tree, expect=0).stdout
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=tree) as client:
        client.initialize()
        error, text = gitsurf.next_item_text(client, {})
        assert not error and "calc/grade.py" in text, text
    gitsurf.assert_advised(gitsurf.advise(box, tree, tree / gitsurf.breach(tree)), "calc/route.py")


@cell("lin-worktree", channel="pip venv", harness="git 2.47",
      scenario="fresh: README Route 1 from a linked worktree", use_cases="commit gate", os="linux", image="cells",
      cadence="push")
def test_route1_from_a_linked_worktree_arms_the_gate(box, templates):
    _, tree = worktree(box, templates)
    gitsurf.route1(box, tree, expect=None)

    gitsurf.refused_then_accepted(box, tree)
