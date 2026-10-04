"""The root Dockerfile's image, built and started with docs/agent-json.md#docker's lines.

The image serves as uid 1000, and a bind mount keeps the host's ownership, so
a reader whose uid is not 1000 serves a checkout another uid owns. The page
says the tools still answer there but the caches they write under .crapkit/
are not saved, and gives a `--user "$(id -u):$(id -g)"` form that runs the
server as the checkout's owner. The cell builds the image from the stamped
tree with the page's build line, scores a repo owned by uid 1000 and one owned
by uid 1001, and starts the page's two run lines against them through a client
that keeps stdin open. A named volume stands in for `$PWD`, and 1001:1001 for
what `$(id -u):$(id -g)` prints for that owner.

Cell of the deploy-docs packet: the doc half of lin-host-docker-product. It
needs the host's Docker daemon and the network the build pulls through.
"""
from __future__ import annotations

import shlex
import shutil
import uuid

import pytest

from docs_support import in_container
from kit import docsnip
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-docs"
DOCKER = shutil.which("docker")
PAGE, HEADING = "docs/agent-json.md", "Docker"
# The page's `-t crapkit`, renamed so a run never replaces a reader's own image.
TAG = "crapkit-deploy-docs"
OTHER_UID = "1001"
CALLS = (("get_next_item", {}), ("list_runs", {}), ("list_worklist", {"top": 2}), ("get_trend", {}),
         ("get_function_brief", {"path": "app/calc.py", "name": "f"}), ("list_coupled_files", {}),
         ("list_duplicate_functions", {}), ("get_ratchet_report", {}), ("list_claims", {}),
         ("get_function_history", {"path": "app/calc.py", "name": "f"}), ("check_config", {}),
         ("check_gate", {"path": "app/calc.py"}))
CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "app"\npaths = ["app"]\nlanguages = ["python"]\n'
          "coverage_optional = true\n")
# A scored checkout in /repo, owned by $1: what a reader has after the README start.
SCORE = f"""set -e
cd /repo
git config --global --add safe.directory '*'
git init -q -b main .
git config user.email deploy@example.invalid
git config user.name deploy
mkdir -p app
printf 'def f(x):\\n    if x:\\n        return 1\\n    return 2\\n' > app/calc.py
printf '{CONFIG.encode("unicode_escape").decode()}' > crapkit.toml
git add -A
git commit -qm adopt
crapkit coverage
chown -R "$1:$1" /repo
"""
# The churn cache by any format version, as the shell globs it: the version is
# in the file name and moves with the format; who may save it does not.
CACHE = "churn-cache-v*.json"

needs_docker = pytest.mark.skipif(DOCKER is None or in_container(),
                                  reason="needs the host's Docker daemon; the nightly host job runs it")


def page_lines() -> list[str]:
    block = docsnip.fence(PAGE, HEADING, contains="docker build")
    user_form = docsnip.fence(PAGE, HEADING, contains="--user")
    return [*docsnip.commands(block), *docsnip.commands(user_form)]


def as_run(line: str, volume: str, owner: str) -> list[str]:
    """One of the page's lines as argv, with the tag, the mount source and the
    uid a shell would substitute."""
    swaps = {"crapkit": TAG, "$PWD:/repo": f"{volume}:/repo", "$(id -u):$(id -g)": f"{owner}:{owner}"}
    return [DOCKER, *(swaps.get(word, word) for word in shlex.split(line)[1:])]


def docker(box, *argv: str, expect: int | None = 0):
    return box.run([DOCKER, *argv], expect=expect)


def scored_volume(box, owner: str) -> str:
    volume = f"crapkit-deploy-docs-{owner}-{uuid.uuid4().hex[:8]}"
    docker(box, "volume", "create", volume)
    docker(box, "run", "--rm", "--user", "0", "-v", f"{volume}:/repo", "--entrypoint", "sh", TAG, "-c", SCORE,
           "score", owner)
    return volume


def answers(box, argv: list[str]) -> dict[str, bool]:
    """Every tool, called once: name -> whether it answered without isError."""
    with McpClient.in_box(box, argv, cwd=box.root) as client:
        client.initialize()
        return {name: not client.call(name, args).get("isError") for name, args in CALLS}


def cache_owner(box, volume: str) -> str | None:
    """The uid that owns .crapkit's churn cache, or None when none was saved."""
    listed = docker(box, "run", "--rm", "--user", "0", "-v", f"{volume}:/repo", "--entrypoint", "sh", TAG, "-c",
                    f"stat -c %u /repo/.crapkit/{CACHE} 2>/dev/null || true").stdout.strip()
    return listed or None


def serve_each(box, cases: list[tuple[str, str]], volumes: dict[str, str]) -> list[tuple[list[str], str | None]]:
    """For each (owner, run line): the tools that failed, and who owns the cache after."""
    seen = []
    for owner, line in cases:
        answered = answers(box, as_run(line, volumes[owner], owner))
        seen.append(([name for name, ok in answered.items() if not ok], cache_owner(box, volumes[owner])))
    return seen


@cell("docs-docker-user", channel="root Dockerfile image, docs/agent-json.md#docker's lines",
      harness="Docker (host daemon)",
      scenario="fresh: build as the page prints; on a repo uid 1000 owns the documented run answers all 12 tools "
               "and saves its cache; on a repo uid 1001 owns it answers all 12 and saves none; the --user form "
               "answers all 12 and saves the cache as 1001",
      use_cases="Docker channel", os=("linux", "windows"), image=None, cadence="nightly", docker_host=True,
      online=True)
@needs_docker
def test_the_docker_lines_serve_a_checkout_whoever_owns_it(box, candidate):
    build, run, run_as_owner = page_lines()
    box.run(as_run(build, "", ""), cwd=candidate.staged, expect=0)
    volumes = {owner: scored_volume(box, owner) for owner in ("1000", OTHER_UID)}
    try:
        seen = serve_each(box, [("1000", run), (OTHER_UID, run), (OTHER_UID, run_as_owner)], volumes)
    finally:
        for volume in volumes.values():
            docker(box, "volume", "rm", "-f", volume, expect=None)

    box.transcript.attach("docker-answers", seen)
    assert seen == [([], "1000"), ([], None), ([], OTHER_UID)], "every tool answers; only the owner saves a cache"
