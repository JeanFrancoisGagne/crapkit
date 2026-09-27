"""Installs from git, where a new commit can carry the same version string.

lin-up-pipgit installs the README's `pip install git+https://...` line (the
mirror answers for GitHub), then the mirror's main moves from commit A to
commit B without a version bump. lin-up-local installs a clone with the
README's `pip install .`, pulls B, and also takes the guide's route for a
source checkout (README Development, `pip install -e ".[dev]"`). Each cell
hashes the crapkit package the venv's Python imports after every step, so the
transcript says which commit's code a user runs, and holds the docs to a line
that reaches B.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from kit import docsnip, gitmirror, state
from kit.cells import cell

PACKET = "deploy-upgrade"
HASH_SCRIPT = (
    "import crapkit, hashlib, pathlib\n"
    "root = pathlib.Path(crapkit.__file__).parent\n"
    "digest = hashlib.sha256()\n"
    "for path in sorted(root.rglob('*.py')):\n"
    "    digest.update(path.relative_to(root).as_posix().encode() + b'|' + path.read_bytes())\n"
    "print(digest.hexdigest())\n")
LATER = "\n# a commit after the release, under the same version string\n"


def tree_hash(tree: Path) -> str:
    root = tree / "src" / "crapkit"
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode() + b"|" + path.read_bytes())
    return digest.hexdigest()


def later_tree(box, candidate) -> Path:
    """Commit B: the candidate's tree with one more line, the version unchanged."""
    tree = box.root / "tree-b"
    shutil.copytree(candidate.staged, tree)
    init = tree / "src" / "crapkit" / "__init__.py"
    init.write_bytes(init.read_bytes() + LATER.encode("utf-8"))
    return tree


def running(box, commits: dict[str, str]) -> str:
    """Which commit's crapkit the venv's Python imports: "A", "B" or the hash."""
    digest = box.run(["python", "-c", HASH_SCRIPT], cwd=box.root, expect=0).stdout.strip()
    return commits.get(digest, digest)


def install_line(fragment: str) -> str:
    return next(line for line in docsnip.commands(docsnip.fence("README.md", "Install", index=1)) if fragment in line)


def documented(*needles: str) -> bool:
    pages = [(docsnip.root() / page).read_text(encoding="utf-8") for page in ("README.md", state.GUIDE)]
    return any(all(needle in line for needle in needles) for page in pages for line in page.splitlines())


@cell("lin-up-pipgit", channel="pip git (README URL via mirror)", harness="none",
      scenario="upgrade: mirror commit A to B, same version string; which code runs (source hash)",
      use_cases="install refresh", os="linux", image="core", cadence="nightly")
def test_lin_up_pipgit(box, candidate):
    gaps = state.Gaps()
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    tree_b = later_tree(box, candidate)
    commits = {tree_hash(candidate.staged): "A", tree_hash(tree_b): "B"}
    state.pip_venv(box)
    line = install_line("git+https://")
    url = line.split()[-1]

    state.run_line(box, box.root, line)
    seen = {"README line": running(box, commits)}
    mirror.publish(tree_b, candidate.version)
    seen.update(after_commit_b(box, commits, line, url))
    box.transcript.attach("code-run-after-each-step", seen)

    assert seen["README line"] == "A" and seen["--force-reinstall"] == "B", seen
    refreshing = next(label for label in REFRESHES if seen[label] == "B")
    gaps.check(refreshing == "README line again" or documented("git+https", refreshing),
               f"README.md > Install: after a new commit under the same version, rerunning `{line}` runs "
               f"commit {seen['README line again']}; `{refreshing}` reaches B, and no doc says so")
    gaps.raise_any()


REFRESHES = ("README line again", "--upgrade", "--force-reinstall")


def after_commit_b(box, commits: dict[str, str], line: str, url: str) -> dict[str, str]:
    """Which commit runs after each way a user might refresh a git install."""
    commands = {"README line again": line, "--upgrade": f"python -m pip install --upgrade {url}",
                "--force-reinstall": f"python -m pip install --force-reinstall --no-deps {url}"}
    seen = {}
    for label in REFRESHES:
        state.run_line(box, box.root, commands[label], note=f"after commit B: {label}")
        seen[label] = running(box, commits)
    return seen


@cell("lin-up-local", channel="pip . and -e .[dev]", harness="none",
      scenario="upgrade: clone pulls commit B, same version string; which code `pip install .` and the "
               "Development editable install run",
      use_cases="install refresh", os="linux", image="core", cadence="nightly")
def test_lin_up_local(box, candidate):
    mirror = gitmirror.make(box)
    mirror.publish(candidate.staged, candidate.version)
    tree_b = later_tree(box, candidate)
    commits = {tree_hash(candidate.staged): "A", tree_hash(tree_b): "B"}
    clone = box.root / "crapkit"
    box.run(["git", "clone", "-q", install_line("git+https://").split("git+")[-1], str(clone)], expect=0)
    state.pip_venv(box)
    line = install_line("pip install .")

    state.run_line(box, clone, line)
    seen = {"pip install .": running(box, commits)}
    mirror.publish(tree_b, candidate.version)
    box.run(["git", "pull", "-q"], cwd=clone, expect=0)
    state.run_line(box, clone, line, note="after pulling commit B: the same line again")
    seen["pip install . again"] = running(box, commits)
    development = docsnip.commands(docsnip.fence("README.md", "Development"))[0]
    state.run_line(box, clone, development, note="the guide's route for a source checkout: README Development")
    seen["Development editable"] = running(box, commits)
    box.transcript.attach("code-run-after-each-step", seen)

    assert seen == {"pip install .": "A", "pip install . again": "B", "Development editable": "B"}, seen
