"""The doctor and MCP fixes, through the commands a user types.

- In a container, doctor WARNs on the coverage.py lane that `crapkit coverage`
  then refuses, naming the trigger and the key; the docs' key clears both.
- A crapkit hook in .git/hooks that a global core.hooksPath sends git away
  from passes without a word; doctor names it, and the fix it names arms the
  gate again. pre-commit run in CI judges every tracked file, and doctor says
  nothing about the workflow that runs it.
- An MCP client that kept a 0.5.x tool name after upgrading gets the new name
  back instead of a bare "unknown tool".
- The 60-second start runs on CPython 3.14, the version the new classifier
  names.
"""
from __future__ import annotations

import os
from pathlib import Path

from kit import docsnip, gitmirror, repos, wheels
from kit.cells import cell
from kit.mcp_client import McpClient

from fixes_support import allow_container_lane, in_container, install_candidate

PACKET = "deploy-fixes"
WINDOWS = os.name == "nt"
REFUSAL = "runs the python suite, which is host-only"
BREACH_PY = '''def breach(score, attempts, late, bonus, strict):
    if score > 90 and not late:
        return "A"
    if score > 80:
        return "B" if attempts < 3 else "C"
    if bonus and strict:
        return "C"
    if late and attempts > 2:
        return "F"
    if strict or bonus:
        return "E"
    return "D"
'''


def crapkit(box, repo: Path, *args: str, expect: int | None = 0, env: dict | None = None):
    return box.run(["crapkit", *args], cwd=repo, expect=expect, env=env)


def commit(box, repo: Path, message: str, *, expect: int | None = 0, extra: tuple = ()):
    box.run(["git", "add", "-A"], cwd=repo, expect=0)
    return box.run(["git", "commit", "-q", *extra, "-m", message], cwd=repo, env=box.commit_env(),
                   expect=expect)


def output(step) -> str:
    return step.stdout + step.stderr


def container_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if "this is a container" in line]


# --- the container guard -------------------------------------------------------------

def _container_warn_then_refusal(box, templates, env: dict | None, marker: str, python: str):
    install_candidate(box, "crapkit[py]", python=python)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    crapkit(box, repo, "init", env=env)
    doctor = crapkit(box, repo, "doctor", env=env)
    refused = crapkit(box, repo, "coverage", expect=5, env=env)

    (warn,) = container_lines(output(doctor))
    assert f"lane 'py' runs pytest and this is a container ({marker})" in warn
    assert "set container_ok = true on the lane (docs/lanes.md#containers)" in warn
    assert REFUSAL in output(refused)

    allow_container_lane(repo)
    assert container_lines(output(crapkit(box, repo, "doctor", env=env))) == []
    crapkit(box, repo, "coverage", env=env)


@cell("lin-doctor-container-warn", channel="pip venv (crapkit[py]) on CPython 3.11",
      harness="none (sh + git)", scenario="fresh: doctor WARNs on lane 'py' (/.dockerenv exists) before "
      "coverage refuses it with exit 5; the docs/lanes.md#containers key clears both",
      use_cases="doctor, coverage, container guard", os="linux", image="core", cadence="push")
def test_doctor_warns_in_a_container_before_coverage_refuses(box, templates):
    assert Path("/.dockerenv").exists(), "this cell runs as a Linux container user"
    _container_warn_then_refusal(box, templates, None, "/.dockerenv exists", "3.11")


@cell("win-doctor-container-warn", channel="pip venv (crapkit[py]) on CPython 3.12",
      harness="none (cmd.exe)", scenario="fresh: CRAPKIT_INSIDE_CONTAINER=1 gives the same WARN and "
      "refusal; without it neither appears", use_cases="doctor, coverage, container guard",
      os="windows", image=None, cadence="push")
def test_doctor_on_windows_warns_under_the_container_variable(box, templates):
    _container_warn_then_refusal(box, templates, {"CRAPKIT_INSIDE_CONTAINER": "1"},
                                 "CRAPKIT_INSIDE_CONTAINER=1", "3.12")
    fresh = repos.checkout(box, "py-pytest", cache=templates, repo_name="fresh")
    crapkit(box, fresh, "init")

    assert container_lines(output(crapkit(box, fresh, "doctor"))) == []
    crapkit(box, fresh, "coverage")


# --- the silent passes ---------------------------------------------------------------

def adopted(box, templates) -> Path:
    install_candidate(box, "crapkit[py]")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    crapkit(box, repo, "init")
    if in_container():
        allow_container_lane(repo)
    commit(box, repo, "adopt crapkit")
    return repo


def stage_breach(repo: Path, name: str) -> None:
    (repo / "calc" / f"{name}.py").write_text(BREACH_PY, encoding="utf-8", newline="\n")


def _global_hooks_path_skips_route_1(box, templates):
    repo = adopted(box, templates)
    route_1 = docsnip.fence("README.md", "Route 1: `.git/hooks/pre-commit` (local, not committed)")
    box.script(route_1.text, shell="sh", cwd=repo, expect=0)
    stage_breach(repo, "first")
    refused = commit(box, repo, "a breach the gate sees", expect=1)
    assert "crapkit" in output(refused), "Route 1 armed the gate"

    elsewhere = box.home / ".githooks"
    elsewhere.mkdir()
    box.run(["git", "config", "--global", "core.hooksPath", elsewhere.as_posix()], cwd=repo, expect=0)
    commit(box, repo, "the same breach, ungated")
    doctor = crapkit(box, repo, "doctor")
    (warn,) = [line for line in output(doctor).splitlines() if "skips the gate without a word" in line]
    assert "core.hooksPath (global config: " in warn

    fix = warn.split("`")[1]
    box.script(fix, shell="sh", cwd=repo, expect=0)
    stage_breach(repo, "second")
    assert commit(box, repo, "a breach after the fix", expect=1).exit == 1
    assert "skips the gate" not in output(crapkit(box, repo, "doctor"))


@cell("lin-doctor-global-hookspath", channel="Route 1 sh with a global core.hooksPath",
      harness="git (pinned)", scenario="fresh: Route 1 refuses a breach; a global core.hooksPath lets the "
      "next breach in silently; doctor names it; its fix refuses the next breach",
      use_cases="commit gate, doctor", os="linux", image="core", cadence="nightly")
def test_doctor_names_a_route_1_hook_a_global_hooks_path_skips(box, templates):
    _global_hooks_path_skips_route_1(box, templates)


@cell("win-doctor-global-hookspath", channel="Route 1 sh with a global core.hooksPath",
      harness="PortableGit (pinned)", scenario="fresh: the Linux cell on Windows, the README heredoc in Git Bash",
      use_cases="commit gate, doctor", os="windows", image=None, cadence="nightly")
def test_doctor_on_windows_names_a_route_1_hook_a_global_hooks_path_skips(box, templates):
    _global_hooks_path_skips_route_1(box, templates)


CI_WORKFLOW = """on: [push, pull_request]
jobs:
  pre-commit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
      - run: pip install pre-commit
      - run: pre-commit run --all-files
"""


@cell("lin-doctor-precommit-ci", channel="pre-commit (README Route 3 via the mirror)",
      harness="pre-commit (wheelhouse)", scenario="fresh: `pre-commit run --all-files` on a clean index refuses "
      "a breach committed past the hook; doctor raises no WARN about the CI workflow that runs it",
      use_cases="commit gate, doctor", os="linux", image="core", cadence="nightly")
def test_pre_commit_in_ci_refuses_a_committed_breach_and_doctor_says_nothing_of_it(box, templates, candidate):
    repo = adopted(box, templates)
    gitmirror.make(box).publish(candidate.staged, candidate.version)
    config = docsnip.fence("README.md", "Route 3: the pre-commit framework", index=0)
    (repo / ".pre-commit-config.yaml").write_text(config.text + "\n", encoding="utf-8")
    for line in docsnip.commands(docsnip.fence("README.md", "Route 3: the pre-commit framework", index=1)):
        box.script(line, shell="sh", cwd=repo, expect=0)
    commit(box, repo, "arm pre-commit")
    stage_breach(repo, "first")
    assert commit(box, repo, "a breach the hook sees", expect=1).exit == 1
    commit(box, repo, "the breach, committed past the hook", extra=("--no-verify",))

    ci = box.run(["pre-commit", "run", "--all-files"], cwd=repo, expect=1)
    assert "every tracked file was judged" in ci.stdout and "calc/first.py" in ci.stdout, ci.stdout

    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "lint.yml").write_text(CI_WORKFLOW, encoding="utf-8")
    doctor = output(crapkit(box, repo, "doctor"))
    assert [line for line in doctor.splitlines() if "lint.yml" in line] == [], doctor


# --- MCP tool names ---------------------------------------------------------------------

OLD_NAMES = {"next_item": "get_next_item", "worklist": "list_worklist", "runs": "list_runs",
             "brief": "get_function_brief", "explain": "get_function_history", "doctor": "check_config",
             "coupling": "list_coupled_files", "duplication": "list_duplicate_functions",
             "ratchet_report": "get_ratchet_report", "gate": "check_gate"}


def _old_names_answer_with_new_ones(box):
    install_candidate(box, "crapkit")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=box.root) as client:
        client.initialize()
        answers = {old: client.call(old) for old in OLD_NAMES}

    for old, new in OLD_NAMES.items():
        assert answers[old]["isError"] is True
        assert answers[old]["content"][0]["text"].startswith(
            f"unknown tool '{old}': renamed {new} in 0.6.0"), answers[old]


@cell("lin-mcp-renamed-tools", channel="pip venv `crapkit mcp`", harness="spec client 2025-06-18",
      scenario="fresh: each 0.5.x tool name answers with its 0.6.0 name", use_cases="MCP renames",
      os="linux", image="core", cadence="push", real_cli=False)
def test_every_old_tool_name_answers_with_the_new_one(box):
    _old_names_answer_with_new_ones(box)


@cell("win-mcp-renamed-tools", channel="pip venv `crapkit mcp`", harness="spec client 2025-06-18",
      scenario="fresh: the Linux cell on Windows", use_cases="MCP renames",
      os="windows", image=None, cadence="push", real_cli=False)
def test_on_windows_every_old_tool_name_answers_with_the_new_one(box):
    _old_names_answer_with_new_ones(box)


def _upgrade_from_0_5_1(box, templates):
    old = "0.5.1"
    assert old in wheels.releases()
    install_candidate(box, f"crapkit[py]=={old}")
    repo = repos.checkout(box, "py-pytest", cache=templates)
    crapkit(box, repo, "init")
    if in_container():
        allow_container_lane(repo)
    crapkit(box, repo, "coverage")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        before = client.initialize()["serverInfo"]["version"]
        served = client.call("worklist")
    box.run(["python", "-m", "pip", "install", "-q", "--upgrade", "crapkit[py]"], expect=0)
    crapkit(box, repo, "coverage")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        after = client.initialize()["serverInfo"]["version"]
        renamed, listed = client.call("worklist"), client.call("list_worklist")

    assert before == old and not served.get("isError"), served
    assert after != old
    assert renamed["isError"] and renamed["content"][0]["text"].startswith(
        "unknown tool 'worklist': renamed list_worklist in 0.6.0")
    assert not listed.get("isError"), listed
    assert "calc/grade.py" in listed["content"][0]["text"]


@cell("lin-up-pyextra-0.5.1-renames", channel="pip [py], upgraded from 0.5.1",
      harness="spec client with 0.5.x names", scenario="upgrade: `worklist` served by 0.5.1 answers "
      "with its new name after the upgrade; list_worklist returns the repo's rows",
      use_cases="MCP renames, upgrade", os="linux", image="core", cadence="nightly", real_cli=False)
def test_a_0_5_1_client_is_told_the_new_name_after_the_upgrade(box, templates):
    _upgrade_from_0_5_1(box, templates)


@cell("win-up-pyextra-0.5.1-renames", channel="pip [py], upgraded from 0.5.1",
      harness="spec client with 0.5.x names", scenario="upgrade: the Linux cell on Windows",
      use_cases="MCP renames, upgrade", os="windows", image=None, cadence="nightly", real_cli=False)
def test_on_windows_a_0_5_1_client_is_told_the_new_name_after_the_upgrade(box, templates):
    _upgrade_from_0_5_1(box, templates)


# --- CPython 3.14 ---------------------------------------------------------------------------

def _sixty_second_start(box, templates, python: str):
    """The README's 60-second start after its own coverage-plugin line
    (`pip install "crapkit[py]"`), on `python`."""
    install_candidate(box, "crapkit[py]", python=python)
    repo = repos.checkout(box, "py-pytest", cache=templates)
    steps = docsnip.commands(docsnip.fence("README.md", "The 60-second start"))
    assert steps[:2] == ["pip install crapkit", "cd your-repo"], steps
    for line in steps[2:]:
        if line == "crapkit coverage" and in_container():
            allow_container_lane(repo)
        box.script(line, shell="sh", cwd=repo, expect=0)
    version = box.run(["python", "-c", "import platform; print(platform.python_version())"], expect=0)

    assert version.stdout.startswith(python + ".")
    assert (repo / "crapkit-ratchet.tsv").exists()


@cell("lin-pyextra-start-py314", channel="pip [py] extra", harness="none",
      scenario='fresh: the 60-second start on CPython 3.14 after `pip install "crapkit[py]"`; '
               "lin-pip-start-py314 runs it after plain `pip install crapkit`",
      use_cases="60-second start", os="linux", image="core", cadence="push")
def test_the_60_second_start_runs_on_python_3_14(box, templates):
    _sixty_second_start(box, templates, "3.14")


@cell("win-pip-start-py314", channel="pip venv", harness="none (Git Bash)",
      scenario="fresh: the 60-second start on CPython 3.14 on Windows", use_cases="60-second start",
      os="windows", image=None, cadence="nightly")
def test_the_60_second_start_runs_on_python_3_14_on_windows(box, templates):
    _sixty_second_start(box, templates, "3.14")
