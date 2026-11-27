"""A repo holding a planted crapkit.py never runs it, through crapkit or through
a line crapkit's pages or output give a reader to run there.

`python -m crapkit` puts the working directory first on sys.path, so a
`crapkit.py` there ran in place of crapkit. The MCP server ran each tool that
way from the root of the repo it serves, and the call answered empty with
isError false. README's hook fallback and merge driver, which git runs from the
worktree root, and the next step crapkit prints when no console script is on
PATH spelled the same `python -m crapkit`, so the planted file ran there too.
Each now starts the interpreter with `-P`.

`python -m pip` reads sys.path the same way, so a planted pip.py ran in place
of pip through the pip lines crapkit prints and through the Action's install
step. Those start the interpreter with `-P` too.

The MCP server itself starts from another directory here, the way an MCP client
starts `crapkit mcp --repo <path>`, so only the tool's child stands in the
repo and the planted file can only run there.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import zipfile
from pathlib import Path

import hang_guard
import test_readme_gate_routes_e2e as pages
import yaml
from conftest import cli_runner, git_commit_all, git_init_repo

# The MCP server is a stdio process, and this file tests it as one.
run_cli = cli_runner(spawn=True)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
MARKER = "planted-ran.txt"
# A module that records its own file name in MARKER beside it and does nothing
# else, so it exits 0 wherever it runs in place of the real one.
PLANTED = ("import os\n"
           "here, name = os.path.split(os.path.abspath(__file__))\n"
           f"with open(os.path.join(here, {MARKER!r}), 'a') as ran:\n"
           "    ran.write(name + '\\n')\n")
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


def plant(repo: Path, env: dict, name: str = "crapkit.py") -> None:
    """PLANTED as the repo's own root module `name`, committed with whatever
    else the test wrote."""
    (repo / name).write_text(PLANTED, encoding="utf-8")
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


# --- the pip lines crapkit prints, and the one the Action runs ---------------------------
#
# Each pip here is a venv's own, made offline by ensurepip, and installs from a
# directory of wheels this file writes, so no line reaches an index.

def venv(where: Path) -> Path:
    """A venv holding pip; its python."""
    done = hang_guard.run([sys.executable, "-m", "venv", str(where)], text=True)
    assert done.returncode == 0, done.stderr
    return where / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def wheels(where: Path, name: str, version: str) -> Path:
    """A directory holding a wheel of `name` with one empty package in it."""
    package = name.replace("-", "_")
    info = f"{package}-{version}.dist-info"
    files = {f"{package}/__init__.py": "",
             f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
             f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    files[f"{info}/RECORD"] = "".join(f"{path},,\n" for path in [*files, f"{info}/RECORD"])
    where.mkdir()
    with zipfile.ZipFile(where / f"{package}-{version}-py3-none-any.whl", "w") as built:
        for path, text in files.items():
            built.writestr(path, text)
    return where


def offline(env: dict, found: Path | None = None) -> dict:
    """`env` with pip reading no config and no index, only the wheels in `found`."""
    held = {"PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    return {**env, **held, **({"PIP_FIND_LINKS": str(found)} if found else {})}


def pasted_pip(line: str, python: Path, repo: Path, env: dict, found: Path):
    """`line`, which has to run pip through `python`, pasted in the repo root:
    into cmd.exe on Windows, whose next steps keep a path's backslashes, and
    into sh elsewhere."""
    first = shlex.split(line, posix=os.name != "nt")[0].strip('"')
    assert os.path.samefile(first, python), line
    shell = ["cmd", "/d", "/c"] if os.name == "nt" else [pages.sh(), "-c"]
    return pages.run([*shell, line], repo, offline(env, found))


def test_the_pytest_cov_install_init_prints_runs_pip_from_a_repo_holding_a_planted_pip_py(tmp_path: Path):
    """init binds the lane to the repo's own venv, which holds a pytest and no
    pytest-cov, and prints the install bound to that venv's python."""
    env = pages.machine(tmp_path, tmp_path / "bin")
    repo = tmp_path / "repo"
    (repo / "pylib").mkdir(parents=True)
    (repo / "pylib" / "mod.py").write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname = "pyrepo"\n', encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    python = venv(repo / ".venv")
    (site,) = (repo / ".venv").glob("**/site-packages")
    (site / "pytest.py").write_text("", encoding="utf-8")
    for argv in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "init"]):
        git(repo, env, *argv)
    note = run_cli(repo, "init").stderr
    printed = re.search(r"run `([^`]+)` in the environment", note)
    assert printed and printed.group(1).endswith(" -m pip install pytest-cov"), note
    plant(repo, env, "pip.py")

    done = pasted_pip(printed.group(1), python, repo, env, wheels(tmp_path / "wheels", "pytest-cov", "99.0"))

    assert not (repo / MARKER).exists(), "the repo's pip.py ran in place of pip"
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Successfully installed pytest-cov-99.0" in done.stdout, done.stdout


def test_the_upgrade_doctor_prints_runs_pip_from_a_repo_holding_a_planted_pip_py(tmp_path: Path):
    """The plugin is newer than the crapkit on PATH, a launcher in a venv pip
    made, so doctor names that venv's python and its pip."""
    python = venv(tmp_path / "venv")
    launcher = python.parent / ("crapkit.bat" if os.name == "nt" else "crapkit")
    launcher.write_text("@echo crapkit 0.0.1\n" if os.name == "nt" else '#!/bin/sh\necho "crapkit 0.0.1"\n',
                        encoding="utf-8")
    launcher.chmod(0o755)
    plugin = tmp_path / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name": "crapkit", "version": "99.0.0"}',
                                                           encoding="utf-8")
    env = pages.machine(tmp_path, python.parent)
    repo = pages.adopted(tmp_path, env)
    said = run_cli(repo, "doctor", "--plugin-root", str(plugin), env_extra={"PATH": env["PATH"]}).stdout
    printed = re.search(r"upgrade it with `([^`]+)`", said)
    assert printed and printed.group(1).endswith(" -m pip install --upgrade crapkit"), said
    plant(repo, env, "pip.py")

    done = pasted_pip(printed.group(1), python, repo, env, wheels(tmp_path / "wheels", "crapkit", "99.0.0"))

    assert not (repo / MARKER).exists(), "the repo's pip.py ran in place of pip"
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Successfully installed crapkit-99.0.0" in done.stdout, done.stdout


def test_the_action_s_install_step_runs_pip_in_a_checkout_holding_a_planted_pip_py(tmp_path: Path):
    """The step as action.yml has it, run in the consumer's checkout with the
    action's own tree as GITHUB_ACTION_PATH, under bash as `shell: bash` runs
    it. Offline, pip stops at the build backend it cannot fetch, after it read
    the action's tree; the planted pip.py exited 0 and read nothing."""
    action = yaml.safe_load((pages.ROOT / "action.yml").read_text(encoding="utf-8"))
    (step,) = [step for step in action["runs"]["steps"] if "pip install" in step.get("run", "")]
    env = pages.machine(tmp_path, venv(tmp_path / "venv").parent)
    repo = pages.adopted(tmp_path, env)
    plant(repo, env, "pip.py")
    script = tmp_path / "install.sh"
    script.write_text(step["run"], encoding="utf-8", newline="\n")
    bash = shutil.which("bash", path=env["PATH"])
    assert bash, env["PATH"]

    done = pages.run([bash, "--noprofile", "--norc", "-eo", "pipefail", str(script)], repo,
                     offline({**env, "GITHUB_ACTION_PATH": pages.ROOT.as_posix()}))

    assert not (repo / MARKER).exists(), "the checkout's pip.py ran in place of pip"
    assert "Obtaining file:" in done.stdout, done.stdout + done.stderr
