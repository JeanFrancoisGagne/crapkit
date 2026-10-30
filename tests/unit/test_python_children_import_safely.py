"""crapkit's own Python children never import a module from their working directory.

`python -m <module>` and `python -c <code>` put the working directory first on
sys.path, so a module file a repo holds there shadows crapkit's own modules and
the standard library. Four children did that through 0.8.1: the MCP server's
tool calls and the watch's rescore (`-m crapkit`), the measurement owner
(`-c`), and the analysis pool's spawned workers, which multiprocessing starts
as `-c` with only the parent's own flags. Each now runs with `-P` or with
PYTHONSAFEPATH=1 in its environment.

Each test plants, in the child's working directory, the module that child
imports first, as a file that records its own run, then checks the record
stays empty and the child did its work. The record is read before the work's
outcome, so with the bug in place a test fails on the planted run and not on
whatever the broken child left behind.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import runpy  # noqa: F401 - loaded here before a planted runpy.py stands in the cwd
import subprocess
from pathlib import Path

import pytest

from crapkit import _analysis_pool, mcp_server, resources
from crapkit._process_owner import own_processes
from crapkit.cli import admin, main

MARKER = "planted-ran.txt"
# A module that appends its own file name to MARKER beside it. Every child has
# loaded os before it imports anything from its working directory.
PLANTED = ("import os\n"
           "here = os.path.dirname(os.path.abspath(__file__))\n"
           f"with open(os.path.join(here, {MARKER!r}), 'a') as ran:\n"
           "    ran.write(os.path.basename(__file__) + '\\n')\n")
CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""


def _plant(directory: Path, *modules: str) -> Path:
    """Each module as a planted file in `directory`; answers the record."""
    directory.mkdir(parents=True, exist_ok=True)
    for module in modules:
        (directory / f"{module}.py").write_text(PLANTED, encoding="utf-8")
    return directory / MARKER


def _ran(marker: Path) -> list[str]:
    return marker.read_text(encoding="utf-8").split() if marker.exists() else []


def _repo(root: Path) -> Path:
    """A committed repo whose one scope holds one function, `watched`."""
    (root / "src").mkdir(parents=True)
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (root / "src" / "mod.py").write_text("def watched(n):\n    return n + 1\n", encoding="utf-8")
    for args in (("init", "-q", "-b", "main"), ("add", "-A"),
                 ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def test_an_mcp_tool_call_runs_crapkit_and_never_the_repos_crapkit_py(tmp_path):
    """The server runs each tool as `python -m crapkit` from the root it serves."""
    repo = _repo(tmp_path / "repo")
    marker = _plant(repo, "crapkit")
    tool = next(t for t in mcp_server.TOOLS if t["name"] == "check_config")

    answer = mcp_server._run_cli(tool, {}, str(repo))

    assert _ran(marker) == []
    assert answer["isError"] is False, answer
    assert json.loads(answer["content"][0]["text"])["schema"] == 1, answer


def test_the_watch_rescores_with_crapkit_and_never_the_repos_crapkit_py(tmp_path, monkeypatch,
                                                                         capfd):
    """The rescore child inherits the watch's working directory, the repo root."""
    repo = _repo(tmp_path / "repo")
    assert main(["coverage", "--repo", str(repo)]) == 0
    marker = _plant(repo, "crapkit")
    monkeypatch.chdir(repo)
    capfd.readouterr()

    admin._watch_rescore(repo, ["src/mod.py"])

    assert _ran(marker) == []
    assert "watched" in capfd.readouterr().out


def test_the_measurement_owner_never_imports_from_its_working_directory(tmp_path, monkeypatch):
    """The guardian starts as `python -c`: runpy is its first import, json its
    module's first one the interpreter has not loaded."""
    marker = _plant(tmp_path / "cwd", "runpy", "json")
    monkeypatch.chdir(tmp_path / "cwd")

    try:
        with own_processes([tmp_path / ".crapkit" / "probe.lock"], label="probe") as owner:
            started = (owner.held, owner.process is not None)
    finally:
        assert _ran(marker) == []
    assert started == (True, True)


@pytest.fixture()
def spawned_pool(tmp_path, monkeypatch):
    """The analysis pool on the spawn start method, the default on Windows and
    macOS, with four CPUs and a private slot directory so it never falls back
    to the serial path."""
    spawn = multiprocessing.get_context("spawn")
    monkeypatch.setattr(multiprocessing, "get_context", lambda method=None: spawn)
    monkeypatch.setattr(resources, "available_cpus", lambda: (4, "cpu_count"))
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", str(tmp_path / "slots"))
    monkeypatch.delenv("CRAPKIT_ANALYSIS_WORKERS", raising=False)
    monkeypatch.delenv("CRAPKIT_ANALYSIS_MEMORY_MB", raising=False)
    return _analysis_pool.analysis_pool


@pytest.mark.parametrize("prior", [None, ""], ids=["unset", "empty"])
def test_a_spawned_analysis_worker_never_imports_from_its_working_directory(
        tmp_path, monkeypatch, spawned_pool, prior):
    """The workers' first import is multiprocessing. An empty PYTHONSAFEPATH
    leaves the working directory on sys.path as an unset one does, and both
    come back as they were: a lane started after the pool keeps them."""
    if prior is None:
        monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONSAFEPATH", prior)
    marker = _plant(tmp_path / "cwd", "multiprocessing")
    monkeypatch.chdir(tmp_path / "cwd")

    try:
        with spawned_pool(workers=2, worker_budget=2) as pool:
            done = list(pool.map(abs, [-1, -2], chunksize=1))
    finally:
        assert _ran(marker) == []
    assert done == [1, 2]
    assert os.environ.get("PYTHONSAFEPATH") == prior
