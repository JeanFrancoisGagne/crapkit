"""The root Dockerfile as its header says to use it, on a Docker host.

The header (and docs/agent-json.md#docker) gives three lines: build the image
from a checkout, `docker run -i --rm -v "$PWD:/repo" -w /repo crapkit` from
the repo to serve, and that run with `--user "$(id -u):$(id -g)"` for a
checkout whose owner is not uid 1000. The image serves as uid 1000, and a bind
mount keeps the host's ownership, so the cell scores a repo on the host the way
the README start does, then serves it three ways: owned by uid 1000 and owned
by uid 1001 (a GitHub runner's user) with the run line, and owned by 1001 with
the --user line. A named volume holding that ownership stands in for `$PWD`,
so the same cell runs against Docker Desktop's Linux engine, and 1001:1001
stands in for what `$(id -u):$(id -g)` prints for that owner.

It needs the host's Docker daemon, and the build pulls python:3.12-slim and
installs from PyPI, so it runs in the nightly host job, never in a kit image.
"""
from __future__ import annotations

import re
import shlex
import shutil
import uuid
from pathlib import Path

import pytest

from kit import docsnip, installers, repos
from kit.cells import cell
from kit.mcp_client import McpClient

PACKET = "deploy-channels"
HEADER = re.compile(r"^#\s{2,}(docker .+)$", re.M)
# The header's `-t crapkit`, renamed so a run never replaces a reader's own image.
TAG = "crapkit-deploy-channels"
# The churn cache by any format version, as the shell globs it: the version is
# in the file name and moves with the format; who may save it does not.
CACHE = "/repo/.crapkit/churn-cache-v*.json"


def header_lines() -> list[str]:
    """The build, run and run-as-owner lines of the stamped Dockerfile's header comment."""
    return HEADER.findall((docsnip.root() / "Dockerfile").read_text(encoding="utf-8"))


def as_argv(docker: str, line: str, volume: str = "", owner: str = "") -> list[str]:
    """One header line as argv: the renamed tag, the volume for $PWD, and the
    uid a shell would substitute for the owner."""
    swaps = {"crapkit": TAG, "$PWD:/repo": f"{volume}:/repo", "$(id -u):$(id -g)": f"{owner}:{owner}"}
    return [docker, *(swaps.get(word, word) for word in shlex.split(line)[1:])]


def daemon() -> str:
    """The host's docker CLI, or a skip naming the job that has one."""
    docker = shutil.which("docker")
    if docker is None or installers.in_container():
        pytest.skip("needs the host's Docker daemon: the nightly host job runs this cell")
    return docker


def as_root(box, docker: str, volume: str, text: str, expect: int | None = 0):
    return box.run([docker, "run", "--rm", "--user", "0", "--entrypoint", "sh", "-v", f"{volume}:/repo", TAG,
                    "-c", text], expect=expect)


def measured(box, templates) -> Path:
    """A repo after the README start on the host: init, coverage, seed, committed."""
    installers.pip_extra(box, "3.12")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    for step in (["init"], ["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    installers.commit(box, repo, "adopt crapkit")
    return repo


def volume_of(box, docker: str, repo: Path, owner: str) -> str:
    """A named volume holding the repo as a Linux checkout owned by `owner`
    has it: the owner's uid and gid, no group or other write bit."""
    volume = f"{TAG}-{owner}-{uuid.uuid4().hex[:8]}"
    box.run([docker, "volume", "create", volume], expect=0)
    holder = f"{volume}-cp"
    box.run([docker, "create", "--name", holder, "-v", f"{volume}:/repo", TAG], expect=0)
    try:
        box.run([docker, "cp", f"{repo.as_posix()}/.", f"{holder}:/repo"], expect=0)
    finally:
        box.run([docker, "rm", "-f", holder])
    as_root(box, docker, volume, f"chown -R {owner}:{owner} /repo && chmod -R go-w /repo")
    return volume


def served(box, argv: list[str]) -> dict:
    """What a client that starts the server with `argv` gets: initialize and get_next_item."""
    with McpClient.in_box(box, argv, cwd=box.root) as client:
        info = client.initialize()
        item = client.call("get_next_item", {})
        stderr = client.stderr_text()
        client.close()
    return {"info": info, "item": item, "stderr": stderr}


def cache_owner(box, docker: str, volume: str) -> str | None:
    """The uid owning the churn cache get_next_item writes, or None when none was saved."""
    listed = as_root(box, docker, volume, f"stat -c %u {CACHE} 2>/dev/null || true").stdout.strip()
    return listed or None


def serve_each(box, docker: str, repo: Path, cases: list[tuple[str, str]]) -> list[dict]:
    seen = []
    for owner, line in cases:
        volume = volume_of(box, docker, repo, owner)
        try:
            session = served(box, as_argv(docker, line, volume, owner))
            seen.append({**session, "owner": owner, "cache": cache_owner(box, docker, volume)})
        finally:
            box.run([docker, "volume", "rm", "-f", volume])
    return seen


def next_path(session: dict) -> str | None:
    item = session["item"]
    return None if item["isError"] else item["structuredContent"]["item"]["path"]


@cell("lin-host-docker-product", channel="root Dockerfile image", harness="Docker-launching client",
      scenario="fresh: build as the header says; serve a repo owned by uid 1000, one owned by uid 1001, and the "
               "1001 repo with --user \"$(id -u):$(id -g)\"; what get_next_item answers and which caches are saved",
      use_cases="Docker channel", os="linux", image=None, cadence="nightly", docker_host=True, online=True)
def test_the_documented_image_serves_a_repo_whoever_owns_it(box, templates, candidate):
    docker = daemon()
    build, run, run_as_owner = header_lines()
    box.run(as_argv(docker, build), cwd=candidate.staged, expect=0, note="the Dockerfile header's build line")
    repo = measured(box, templates)
    seen = serve_each(box, docker, repo, [("1000", run), ("1001", run), ("1001", run_as_owner)])
    box.transcript.attach("docker-sessions", seen)

    assert docsnip.commands(docsnip.fence("docs/agent-json.md", "Docker")) == [build, run]
    assert [session["info"]["serverInfo"]["version"] for session in seen] == [candidate.version] * 3
    assert [next_path(session) for session in seen] == ["calc/grade.py"] * 3, seen
    assert [session["cache"] for session in seen] == ["1000", None, "1001"], "only the repo's owner saves a cache"
    assert not any("Traceback" in session["stderr"] for session in seen)
