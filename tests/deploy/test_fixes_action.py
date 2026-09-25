"""The Action's working-directory input, run by act the way a runner runs it.

A monorepo keeps crapkit.toml below the repository top (packages/api). The
Action ran every step at the top, where `crapkit coverage` finds no
crapkit.toml and exits 3, and with gate "true" that 3 failed the job. With
`working-directory: packages/api` the same job scores the package, and on a
pull request the fork-point run scores the same directory inside its worktree.

act runs the README's four-line job on this container (-self-hosted),
offline: actions/checkout copies the repository into the workspace, the Action
comes from act's cache as `JeanFrancoisGagne/crapkit@v<candidate>`, cloned from
the GitHub mirror, and actions/checkout and actions/setup-python come from the
image's pre-fetched copies, tagged with the refs the README and action.yml name.
Without the checkout step act's workspace is empty, and every Action run reads
"no crapkit.toml" whatever the repository holds.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

from kit import docsnip, gitmirror, repos
from kit.cells import cell

from fixes_support import allow_container_lane, install_candidate

PACKET = "deploy-fixes"
ACT = Path("/opt/act/bin/act")
IMAGE_ACTIONS = Path("/opt/act/cache")
IMAGE_TOOL_CACHE = Path("/opt/hostedtoolcache/Python")
# The refs the README job (`actions/checkout@v4`) and action.yml
# (`actions/setup-python@v5`) name; the image pre-fetched other refs by SHA.
REFS = {"actions-checkout": "v4", "actions-setup-python": "v5"}


def _writable(tree: Path) -> None:
    """git writes object files read-only, and act rewrites the action's copy."""
    for path in tree.rglob("*"):
        path.chmod(path.stat().st_mode | stat.S_IWUSR)


def action_cache(box, candidate) -> Path:
    """act's action cache: the image's pre-fetched actions, setup-python under
    the ref action.yml names, and crapkit at the candidate's tag, cloned from
    the mirror the README's GitHub URL resolves to."""
    cache = box.root / "act-cache"
    shutil.copytree(IMAGE_ACTIONS, cache, symlinks=True)
    for name, ref in REFS.items():
        (fetched,) = cache.glob(f"{name}@*")
        shutil.copytree(fetched, cache / f"{name}@{ref}", symlinks=True)
        box.run(["git", "tag", ref], cwd=cache / f"{name}@{ref}", expect=0)
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    crapkit = cache / f"JeanFrancoisGagne-crapkit@v{candidate.version}"
    box.run(["git", "clone", "-q", "--branch", f"v{candidate.version}",
             "https://github.com/JeanFrancoisGagne/crapkit.git", str(crapkit)], expect=0)
    # act's offline mode resolves the ref in this repository and fetches when it
    # cannot: a tag only in packed-refs sent it to github.com. A loose tag, and
    # no origin to fetch from, keep it on the copy.
    box.run(["git", "tag", "-f", f"v{candidate.version}"], cwd=crapkit, expect=0)
    box.run(["git", "remote", "remove", "origin"], cwd=crapkit, expect=0)
    _writable(cache)
    return cache


def tool_cache(box) -> Path:
    """A runner tool cache the way GitHub's image holds one: a writable CPython
    with no PEP 668 marker. The image's own copy is a read-only link to the
    uv-managed interpreter, which pip refuses to install into."""
    (version_dir,) = IMAGE_TOOL_CACHE.iterdir()
    target = box.root / "toolcache" / "Python" / version_dir.name
    shutil.copytree((version_dir / "x64").resolve(), target / "x64", symlinks=True)
    for marker in (target / "x64").glob("lib/python3*/EXTERNALLY-MANAGED"):
        marker.unlink()
    (target / "x64.complete").touch()
    return target.parent.parent


# What the README's whole job adds between the checkout and the Action: the
# team's own install of what its lanes need ("whatever your lanes need to
# run"). This consumer's lane runs pytest with pytest-cov. Its setup-python
# names the image's pinned SHA, not @v5: act copies an action into one
# directory per ref and fails with "file exists" when the Action's own
# setup-python@v5 copies it a second time in the same job.
OWN_INSTALL = ('      - uses: {setup_python}\n        with:\n          python-version: "3.12"\n'
               '      - run: pip install pytest-cov\n')


def pinned_setup_python() -> str:
    (fetched,) = IMAGE_ACTIONS.glob("actions-setup-python@*")
    return "actions/setup-python@" + fetched.name.partition("@")[2]


def readme_steps(candidate) -> str:
    """The README's four lines (checkout with the whole history, then the
    Action), with the consumer's own install before the Action."""
    steps = docsnip.fence("README.md", "The GitHub Action", index=0).text.rstrip()
    uses = f"      - uses: JeanFrancoisGagne/crapkit@v{candidate.version}"
    assert steps.endswith(uses), steps
    return steps[:-len(uses)] + OWN_INSTALL.format(setup_python=pinned_setup_python()) + uses


def workflow(candidate, event: str, inputs: dict) -> str:
    with_block = "".join(f"\n          {key}: \"{value}\"" for key, value in inputs.items())
    return (f"on: {event}\njobs:\n  crapkit:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"{readme_steps(candidate)}\n        with:{with_block}\n")


def monorepo(box, templates) -> Path:
    """subdir-root adopted in packages/api, the container key applied (act runs
    the job in this container), committed on main."""
    install_candidate(box, "crapkit[py]")
    repo = repos.checkout(box, "subdir-root", cache=templates)
    api = repo / "packages" / "api"
    box.run(["crapkit", "init"], cwd=api, expect=0)
    allow_container_lane(api)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit in packages/api"], cwd=repo,
            env=box.commit_env(), expect=0)
    return repo


def act(box, repo: Path, cache: Path, tools: Path, name: str, text: str, event: dict | None = None):
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    for old in workflows.iterdir():
        old.unlink()
    (workflows / f"{name}.yml").write_text(text, encoding="utf-8")
    argv = [str(ACT), "pull_request" if event else "push", "-W", f".github/workflows/{name}.yml",
            "-P", "ubuntu-latest=-self-hosted", "--action-offline-mode", "--action-cache-path", str(cache),
            "--no-cache-server", "--env", f"PIP_CONFIG_FILE={box.env['PIP_CONFIG_FILE']}",
            "--env", f"RUNNER_TOOL_CACHE={tools}", "--env", f"AGENT_TOOLSDIRECTORY={tools}"]
    if event:
        path = box.tmp / f"{name}-event.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        argv += ["-e", str(path)]
    step = box.run(argv, cwd=repo)
    return step.exit, step.stdout + step.stderr


@cell("gha-act-monorepo-workdir", channel="the Action under act, `uses:` line from the README",
      harness="act (pinned), GitHub runner semantics", scenario="fresh: monorepo, crapkit.toml in "
      "packages/api; no input: coverage exits 3 and the gate fails with 3; working-directory "
      "packages/api: coverage exits 0; pull_request with delta: the fork-point run scores packages/api",
      use_cases="Action verdict, monorepo", os="linux", image="ci", cadence="nightly")
def test_the_working_directory_input_scores_a_package_below_the_top(box, templates, candidate):
    assert ACT.exists(), "the :ci image carries act"
    repo = monorepo(box, templates)
    cache, tools = action_cache(box, candidate), tool_cache(box)

    top_exit, top = act(box, repo, cache, tools, "top", workflow(candidate, "push",
                                                                {"gate": "true", "delta": "false"}))
    assert top_exit != 0
    assert "crapkit coverage exited 3" in top and "gate is on: exiting with verify's code 3" in top

    below_exit, below = act(box, repo, cache, tools, "below", workflow(
        candidate, "push", {"gate": "true", "delta": "false", "working-directory": "packages/api"}))
    assert "crapkit coverage exited 0" in below, below[-3000:]
    assert "gate is on: exiting with verify's code 0" in below and below_exit == 0

    base = box.run(["git", "rev-parse", "HEAD"], cwd=repo, expect=0).stdout.strip()
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    grade = repo / "packages" / "api" / "calc" / "grade.py"
    grade.write_text(grade.read_text(encoding="utf-8") + "\n\ndef bonus(x):\n    return x + 1\n",
                     encoding="utf-8")
    box.run(["git", "commit", "-qam", "a small change in packages/api"], cwd=repo,
            env=box.commit_env(), expect=0)
    event = {"pull_request": {"number": 7, "base": {"sha": base},
                              "head": {"repo": {"full_name": "example/monorepo"}}},
             "repository": {"full_name": "example/monorepo"}}
    _, pull = act(box, repo, cache, tools, "pull", workflow(
        candidate, "pull_request", {"gate": "true", "working-directory": "packages/api"}), event)
    assert "crapkit base scoring exited 0" in pull, pull[-3000:]
    assert f"the verdict covers the diff from {base}" in pull
    assert "1 changed file(s)" in pull
