"""A repo holding a planted crapkit.py never runs it, through crapkit or through
a line crapkit's pages or output give a reader to run there.

`python -m crapkit` puts the working directory first on sys.path, so a
`crapkit.py` there ran in place of crapkit. The MCP server ran each tool that
way from the root of the repo it serves, and the call answered empty with
isError false. README's hook fallback and merge driver, which git runs from the
worktree root, and the next step crapkit prints when no console script is on
PATH spelled the same `python -m crapkit`, so the planted file ran there too.
Each now starts the interpreter with `-P`.

The MCP server itself starts from another directory here, the way an MCP client
starts `crapkit mcp --repo <path>`, so only the tool's child stands in the
repo and the planted file can only run there.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import test_readme_gate_routes_e2e as pages
from conftest import cli_runner, git_commit_all, git_init_repo

# The MCP server is a stdio process, and this file tests it as one.
run_cli = cli_runner(spawn=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MARKER = "planted-ran.txt"
# A crapkit.py that records its run in MARKER beside it and does nothing else.
PLANTED = ("import os\n"
           "here = os.path.dirname(os.path.abspath(__file__))\n"
           f"with open(os.path.join(here, {MARKER!r}), 'a') as ran:\n"
           "    ran.write('crapkit.py\\n')\n")
MARKS = "path\tlong_name\tcrap\ncalc/a.py\tf( )\t{crap}\n"


def _rpc(msg_id, method, params=None):
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def test_an_mcp_call_in_a_repo_holding_a_planted_crapkit_py_answers_from_crapkit(tmp_path: Path):
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    (repo / "crapkit.py").write_text(PLANTED, encoding="utf-8")
    elsewhere = tmp_path / "client"
    elsewhere.mkdir()
    frames = [_rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
              json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
              _rpc(2, "tools/call", {"name": "check_config", "arguments": {}})]

    proc = run_cli(elsewhere, "mcp", "--repo", str(repo), stdin="\n".join(frames) + "\n")

    assert not (repo / MARKER).exists(), "the repo's crapkit.py ran in place of crapkit"
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    replies = {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}
    call = replies[2]["result"]
    assert call["isError"] is False, call
    report = json.loads(call["content"][0]["text"])
    assert (report["schema"], report["problems"]) == (1, []), report


# --- the lines a reader runs in the repo root --------------------------------------------

def reader_repo(tmp_path: Path) -> tuple[dict, Path]:
    """A committed repo on a PATH with no `crapkit` command and no `uvx`, where
    `python` imports this crapkit: the reader who runs crapkit as `python -m
    crapkit`, and a hook whose PATH reaches only the body's last line."""
    env = pages.machine(tmp_path, pages.shims(tmp_path, crapkit=False, python="crapkit"))
    return env, pages.adopted(tmp_path, env)


def git(repo: Path, env: dict, *args: str) -> None:
    done = pages.run(["git", *args], repo, env)
    assert done.returncode == 0, (args, done.stderr)


def plant(repo: Path, env: dict) -> None:
    """PLANTED as the repo's own root crapkit.py, committed with whatever else
    the test wrote."""
    (repo / "crapkit.py").write_text(PLANTED, encoding="utf-8")
    git(repo, env, "add", "-A")
    git(repo, env, "commit", "-qm", "plant")


def readme_driver_line() -> str:
    """The merge-driver line of README's Development block."""
    text = (pages.ROOT / "README.md").read_text(encoding="utf-8")
    development = re.search(r"^## Development\n.*?^```\n(.*?)^```", text, re.M | re.S).group(1)
    (line,) = [line for line in development.splitlines() if "merge.crapkit-ratchet.driver" in line]
    return line


def lower(repo: Path, env: dict, crap: str) -> None:
    (repo / "crapkit-ratchet.tsv").write_text(MARKS.format(crap=crap), encoding="utf-8", newline="\n")
    git(repo, env, "commit", "-qam", f"lower the mark to {crap}")


def test_readme_s_hook_fallback_in_a_repo_holding_a_planted_crapkit_py_runs_the_gate(tmp_path: Path):
    """git runs the hook from the worktree root, and with neither `crapkit` nor
    `uvx` on its PATH the body's last line, the `python` one, runs."""
    env, repo = reader_repo(tmp_path)
    plant(repo, env)
    pasted = pages.paste([pages.sh()], pages.readme_fence(pages.ROUTE_ONE, "sh"), repo, env)

    committed = pages.commit_breach(repo, env)

    assert not (repo / MARKER).exists(), "the repo's crapkit.py ran in place of the gate"
    assert committed.returncode != 0, f"the breach committed; the paste said: {pasted.stderr}"
    assert "crapkit gate:" in committed.stderr, committed.stderr


def test_readme_s_merge_driver_in_a_repo_holding_a_planted_crapkit_py_merges_through_crapkit(tmp_path: Path):
    """git runs the driver from the worktree root. Both sides lowered one mark:
    crapkit's merge keeps the lower one, where the planted file exited 0 and
    left ours as it was."""
    env, repo = reader_repo(tmp_path)
    (repo / ".gitattributes").write_text("crapkit-ratchet.tsv merge=crapkit-ratchet\n", encoding="utf-8")
    (repo / "crapkit-ratchet.tsv").write_text(MARKS.format(crap="50.0000"), encoding="utf-8", newline="\n")
    plant(repo, env)
    driver = pages.run([pages.sh(), "-c", readme_driver_line()], repo, env)
    assert driver.returncode == 0, driver.stderr
    git(repo, env, "checkout", "-qb", "theirs")
    lower(repo, env, "20.0000")
    git(repo, env, "checkout", "-q", "main")
    lower(repo, env, "30.0000")

    merged = pages.run(["git", "merge", "-q", "--no-edit", "theirs"], repo, env)

    assert not (repo / MARKER).exists(), "the repo's crapkit.py ran in place of the merge driver"
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert "\t20.0000" in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


def test_the_next_step_crapkit_prints_runs_crapkit_from_a_repo_holding_a_planted_crapkit_py(tmp_path: Path):
    """With no console script on PATH a next step names the interpreter
    (crapkit.invocation), and the reader pastes it in the repo root. worklist
    before any run names coverage."""
    env, repo = reader_repo(tmp_path)
    refused = pages.run([sys.executable, "-m", "crapkit", "worklist"], repo, env)
    printed = re.search(r"run `([^`]+)` first", refused.stderr)
    assert printed and printed.group(1).endswith(" -m crapkit coverage"), refused.stderr
    plant(repo, env)

    pasted = pages.run([pages.sh(), "-c", printed.group(1)], repo, env)

    assert not (repo / MARKER).exists(), "the repo's crapkit.py ran in place of crapkit"
    assert pasted.returncode == 0, pasted.stdout + pasted.stderr
