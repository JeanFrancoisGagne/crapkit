"""Install channels paired with the git consumers that call crapkit.

The README says `uv tool install crapkit` or `pipx install crapkit` "puts a
`crapkit` command on PATH once, which is what the commit gate ... call[s]",
and `uvx crapkit` "runs the same commands". The hooks Routes 1 and 2 write
run `python -m crapkit`, and the merge driver line runs `crapkit`. Each cell
installs through one channel, wires one consumer from the docs, and commits
or merges through git: the gate must refuse a breach, the driver must merge.

The pairs, as MAP.toml [pairs] lists them for the git consumers:
  pipx     x Route 1       uv tool x Route 2
  uvx      x merge driver  python3 only (pip --user) x Route 1
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kit import docsnip, gitsurf, repos
from kit.cells import cell

PACKET = "deploy-git"
NOT_PYTHON = "A repo that is not Python"
UVX_DRIVER = ("deploy-bug deploy-git-9: docs/ratchet.md's driver line runs `crapkit`, which a uvx user does "
              "not have: git prints `crapkit: not found`, reports a conflict and leaves ours with no markers")
RELEASE_SH = '''#!/bin/sh
stage() {
    case "$1" in
        a) echo 1 ;;
        b) echo 2 ;;
        c) echo 3 ;;
        d) echo 4 ;;
        e) echo 5 ;;
        f) echo 6 ;;
        g) echo 7 ;;
        *) echo 0 ;;
    esac
}

ship() {
    case "$1" in
        a) echo 1 ;;
        b) echo 2 ;;
        c) echo 3 ;;
        d) echo 4 ;;
        e) echo 5 ;;
        f) echo 6 ;;
        g) echo 7 ;;
        *) echo 0 ;;
    esac
}
'''


# --- channels ---------------------------------------------------------------------

def pipx(box) -> None:
    box.script(gitsurf.inline(gitsurf.README, NOT_PYTHON, "pipx install"), expect=0)
    box.prepend_path(box.run(["pipx", "environment", "--value", "PIPX_BIN_DIR"], expect=0).stdout.strip())


def uv_tool(box) -> None:
    box.script(gitsurf.inline(gitsurf.README, NOT_PYTHON, "uv tool install"), expect=0)
    box.prepend_path(box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip())


def python3_user(box) -> None:
    """The README's `pip install crapkit` with the system pip, refused under
    PEP 668, then the override that refusal names, as a user install."""
    install = docsnip.commands(docsnip.fence(gitsurf.README, "Install"))[0]
    refused = box.script(install, expect=1)
    assert "--break-system-packages" in refused.stderr
    box.script(f"{install} --user --break-system-packages", expect=0)
    box.prepend_path(box.home / ".local" / "bin")


# --- consumers --------------------------------------------------------------------

def gated(box, templates, route) -> Path:
    """crapkit init and its config committed, then the route from the README."""
    repo = repos.checkout(box, "py-pytest", cache=templates)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    route(box, repo)
    return repo


@cell("lin-pair-pipx-route1", channel="pipx x Route 1", harness="git 2.47",
      scenario="fresh: the message git shows when the hook cannot reach crapkit", use_cases="commit gate",
      os="linux", image="cells", cadence="nightly")
def test_pipx_install_feeds_the_route1_gate(box, templates):
    pipx(box)
    repo = gated(box, templates, gitsurf.route1)

    gitsurf.refused_then_accepted(box, repo)


@cell("lin-pair-uvtool-route2", channel="uv tool x Route 2", harness="git 2.47",
      scenario="fresh: the message git shows when the hook cannot reach crapkit", use_cases="commit gate",
      os="linux", image="cells", cadence="nightly")
def test_uv_tool_install_feeds_the_route2_gate(box, templates):
    uv_tool(box)
    repo = gated(box, templates, gitsurf.route2)

    gitsurf.refused_then_accepted(box, repo)


@cell("lin-pair-python3-only", channel="pip --user on a python3-only system x Route 1", harness="git 2.47",
      scenario="fresh: the message git shows when the hook cannot reach crapkit", use_cases="commit gate",
      os="linux", image="cells", cadence="nightly")
def test_a_python3_only_install_feeds_the_route1_gate(box, templates):
    python3_user(box)
    assert box.which("python") is None
    repo = gated(box, templates, gitsurf.route1)

    gitsurf.refused_then_accepted(box, repo)


# --- uvx and the merge driver ----------------------------------------------------------

def uvx(box, repo: Path, *args: str):
    return box.run(["uvx", "crapkit", *args], cwd=repo, expect=0)


def burn_down(box, repo: Path, function: str) -> None:
    """Drop one arm of `function`, then remeasure and let seed tighten its mark."""
    path = repo / "scripts" / "release.sh"
    text = path.read_text(encoding="utf-8")
    start = text.index(f"{function}() {{")
    arm = text.index("        g) echo 7 ;;\n", start)
    path.write_text(text[:arm] + text[arm + len("        g) echo 7 ;;\n"):], encoding="utf-8", newline="\n")
    box.run(["git", "commit", "-q", "-am", f"{function}: one arm fewer"], cwd=repo, env=box.commit_env(), expect=0)
    uvx(box, repo, "coverage")
    assert "tightened 1" in uvx(box, repo, "ratchet", "seed").stdout
    box.run(["git", "commit", "-q", "-am", f"{function}: tighten its mark"], cwd=repo, env=box.commit_env(),
            expect=0)


def uvx_repo(box, templates) -> Path:
    """A repo that is not Python, adopted with the uvx lines the README prints,
    two shell functions over the ceiling marked, and the driver installed."""
    repo = repos.checkout(box, "go-rust-shell", cache=templates)
    (repo / "scripts" / "release.sh").write_text(RELEASE_SH, encoding="utf-8", newline="\n")
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    for args in (["init"], ["coverage"], ["ratchet", "seed"]):
        uvx(box, repo, *args)
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", "adopt crapkit"], cwd=repo, env=box.commit_env(), expect=0)
    gitsurf.commit_attribute(box, repo)
    gitsurf.configure_driver(box, repo)
    return repo


@pytest.mark.xfail(strict=True, reason=UVX_DRIVER)
@cell("lin-pair-uvx-merge-driver", channel="uvx x merge driver", harness="git 2.47",
      scenario="fresh: the message git shows when the driver cannot reach crapkit", use_cases="ratchet merge",
      os="linux", image="cells", cadence="nightly")
def test_the_driver_merges_for_a_uvx_user(box, templates):
    repo = uvx_repo(box, templates)
    assert box.which("crapkit") is None
    box.run(["git", "checkout", "-q", "-b", "feature"], cwd=repo, expect=0)
    burn_down(box, repo, "ship")
    feature = gitsurf.marks(repo)
    box.run(["git", "checkout", "-q", "main"], cwd=repo, expect=0)
    burn_down(box, repo, "stage")
    expected = {key: min(value, feature[key]) for key, value in gitsurf.marks(repo).items()}

    gitsurf.assert_driver_merged(gitsurf.merge(box, repo, "feature"), repo, expected)
