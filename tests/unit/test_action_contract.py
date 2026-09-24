"""The composite action at the repo root, pinned to the parser and the README.

A workflow file is never imported, so nothing else in this suite would notice a
run step spelling a subcommand the parser dropped or a flag it never had: the
consumer finds out when the job exits 2 on their pull request. These read
`action.yml` the way `test_cli_docs_contract.py` reads README's Subcommands
table, and they read the dogfood job that runs the action on this repo.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple
from urllib.parse import parse_qs, urlsplit

import pytest

from crapkit.cli.parser import build_parser
from hang_guard import HANG_SECONDS

ROOT = Path(__file__).resolve().parent.parent.parent
ACTION = ROOT / "action.yml"
CI = ROOT / ".github" / "workflows" / "ci.yml"
BUILDER = ROOT / "tools" / "action" / "comment.py"
README_HEADING = "## The GitHub Action"

# A crapkit call and the long flags on its line. A call is what the shell
# reaches: the start of a line, or the far side of an operator. `echo "crapkit
# coverage exited $code"` is a log line and not an invocation, and reading it as
# one would make every step's own logging part of this contract.
_CALL = re.compile(r"(?:^|&&|\|\||[;|]|\$\()\s*crapkit\s+([a-z][a-z0-9-]*)([^\n]*)", re.M)
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")

# An `exit` and the word after it, where the shell would read one. The two
# guards are the strings that surround it in this file: `exited $code`, which
# every step logs its status with, and `crapkit-verify.exit`, the file the
# verdict writes that code to. Reading either as an exit would put every step
# in the list of things that can fail the job.
_EXIT = re.compile(r"(?<![\w.-])exit\b(?:\s+(\S+))?")


@lru_cache(maxsize=None)
def _action() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def _ci() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _action()["runs"]["steps"]


def _step_named(name: str) -> dict:
    step = next((s for s in _steps() if s.get("name") == name), None)
    assert step is not None, f"the action lost its {name!r} step"
    return step


def _run_bodies() -> list[str]:
    """Every `run:` block, comment lines dropped. What a step invokes is what
    the shell reaches, and a `#` line reaches nothing."""
    bodies = [step["run"] for step in _steps() if "run" in step]
    return ["\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
            for body in bodies]


def _logical_lines(body: str) -> list[str]:
    """One entry per command the shell reads. A trailing backslash continues a
    line, so a `gh api` call spread over three physical lines is one command and
    its `|| code=$?` tail sits on the last of them."""
    lines, pending = [], ""
    for line in body.splitlines():
        pending += line.rstrip()
        if pending.endswith("\\"):
            pending = pending[:-1]
            continue
        lines.append(pending.strip())
        pending = ""
    if pending:
        lines.append(pending.strip())
    return lines


def _subcommands() -> dict:
    import argparse

    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


def _flags(name: str) -> set[str]:
    return {opt for action in _subcommands()[name]._actions for opt in action.option_strings}


def _until_next_section(rest: list[str]) -> list[str]:
    """Down to the next `## `, ignoring the ones inside a fence.

    The section quotes the rendered comment, and that comment is markdown with
    its own `## crapkit` heading. A reader that stops at the first `## ` stops
    four lines in, and every assertion below it passes on an empty body.
    """
    body, fenced = [], False
    for line in rest:
        if line.startswith("```"):
            fenced = not fenced
        if line.startswith("## ") and not fenced:
            break
        body.append(line)
    return body


@lru_cache(maxsize=None)
def _readme_section() -> str:
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    assert README_HEADING in lines, f"README lost its {README_HEADING!r} heading"
    return "\n".join(_until_next_section(lines[lines.index(README_HEADING) + 1:]))


# --- the shape a runner will accept ------------------------------------------

def test_the_action_declares_the_keys_a_runner_requires():
    """`name`, `description` and `runs.using` are what GitHub reads before it
    runs anything; a file missing one fails the job with a parse error and no
    step output."""
    data = _action()

    assert data["name"]
    assert data["name"].lower() != "crapkit", (
        "the Marketplace refuses an action name that matches an existing user or "
        "organization, and a GitHub user named craPkit exists; the name has to say "
        "more than the project's")
    assert data["description"]
    assert data["runs"]["using"] == "composite"
    assert _steps(), "a composite action with no steps runs nothing"


def test_every_run_step_names_the_shell_it_runs_in():
    """A composite `run` step with no `shell` is a hard error on every runner,
    and the message names the file rather than the step."""
    for step in _steps():
        if "run" in step:
            assert step.get("shell") == "bash", f"{step.get('name')!r} declares no bash shell"


def test_the_action_lives_at_the_root_so_uses_needs_no_path():
    """`uses: JeanFrancoisGagne/crapkit@<tag>` resolves `action.yml` at the
    repository root and nowhere else."""
    assert ACTION.is_file()


# --- the inputs the README documents -----------------------------------------

def test_the_inputs_carry_the_defaults_the_readme_documents():
    inputs = _action()["inputs"]

    assert inputs["gate"]["default"] == "false"
    assert inputs["top"]["default"] == "5"
    assert inputs["python-version"]["default"] == "3.12"


def test_every_input_is_named_in_the_readme_section():
    section = _readme_section()

    for name in _action()["inputs"]:
        assert f"`{name}`" in section, f"the action takes {name} and the README never says so"


def test_every_input_the_readme_names_exists_on_the_action():
    """The other direction: a documented input a consumer sets is silently
    ignored, because `inputs.<name>` on an undeclared name is the empty
    string."""
    named = set(re.findall(r"`(gate|top|python-version|delta)`", _readme_section()))

    assert named - set(_action()["inputs"]) == set()


# --- the pull request's own delta --------------------------------------------

def test_the_delta_input_is_on_by_default():
    """The verdict a reviewer wants is the one for the commits in front of them.
    A consumer who does not want the second lane run sets `delta: "false"`."""
    assert _action()["inputs"]["delta"]["default"] == "true"


def test_the_delta_step_only_runs_on_a_pull_request():
    """A push event has no base commit to score and no pull request to comment
    on, so the second lane run would buy nothing."""
    step = _step_named("score the base commit")

    assert "pull_request" in step["if"]
    assert "inputs.delta" in step["if"]


def test_the_delta_step_scores_the_base_sha_in_a_worktree_of_its_own():
    """Scoring the base commit in place would need a checkout that throws the
    pull request's own tree away. A detached worktree holds both at once."""
    body = _step_named("score the base commit")["run"]

    assert "git worktree add --detach" in body
    assert "BASE_SHA" in body
    assert "merge-base" in body, (
        "base.sha is the base branch's tip, not the fork point: a base branch that moved "
        "after the fork leaves verify with no run at or behind the diff basis")


def test_the_delta_step_leaves_its_run_in_the_store_the_verdict_reads():
    """Two runs in one store is what `--base` picks between: the base commit's
    run is the baseline, the checkout's run is what it judges. A run left in the
    worktree's own `.crapkit/` is invisible to the step that needs it."""
    body = _step_named("score the base commit")["run"]

    assert "crap.sqlite" in body, "the base run has to reach the checkout's store"


def test_the_verdict_measures_the_diff_from_the_base_commit():
    """`--base REF` moves the diff basis to merge-base(REF, HEAD), so the gate
    judges the functions the pull request changed rather than none of them."""
    body = _step_named("the verdict")["run"]

    assert "--base" in body
    assert "--base" in _flags("verify"), "crapkit verify takes no --base"


def test_the_verdict_still_runs_without_a_base_run_behind_it():
    """A shallow clone, a fork point git does not hold, a lane that failed on the
    base commit: the delta is best effort and the comment still has to land."""
    body = _step_named("the verdict")["run"]
    calls = [m.group(2) for m in _CALL.finditer(body) if m.group(1) == "verify"]

    assert len(calls) == 2, "one verify call with --base and one without"
    assert sum("--base" in tail for tail in calls) == 1


def test_the_readme_names_the_cost_of_the_delta_run():
    """Two lane runs on a pull request is the price, and a consumer whose suite
    is slow needs to read it before the bill arrives."""
    section = _readme_section()

    assert "two lane runs" in section
    assert 'delta: "false"' in section


# --- what the steps actually invoke ------------------------------------------

def test_every_crapkit_subcommand_the_steps_invoke_exists():
    called = {m.group(1) for body in _run_bodies() for m in _CALL.finditer(body)}

    assert called, "no run step calls crapkit; this contract lost its subject"
    assert called - set(_subcommands()) == set(), "the action calls a subcommand argparse drops"


def test_every_flag_the_steps_pass_exists_on_its_subcommand():
    for body in _run_bodies():
        for match in _CALL.finditer(body):
            name, tail = match.group(1), match.group(2)
            for flag in _FLAG.findall(tail):
                assert flag in _flags(name), f"crapkit {name} takes no {flag}"


def test_the_steps_read_json_rather_than_parsing_a_table():
    """Plain output is prose for a human and is free to be reworded; the JSON
    payloads carry a `schema` field for exactly this reader."""
    for body in _run_bodies():
        for match in _CALL.finditer(body):
            assert "--json" in match.group(2), f"crapkit {match.group(1)} runs without --json"


def test_the_action_installs_crapkit_from_its_own_checkout():
    """`github.action_path` is the ref the consumer pinned in `uses:`, so the
    crapkit that scores their tree is the one they asked for. A `pip install
    crapkit` here would score every consumer with whatever released last."""
    joined = "\n".join(_run_bodies())

    assert "pip install" in joined
    assert "GITHUB_ACTION_PATH" in joined, "the install must name the action's own checkout"
    assert 'pip install -e "$GITHUB_ACTION_PATH"' in joined, (
        "the install must be editable: a regular install replaces a consumer's editable "
        "install of the same package, and when that consumer is crapkit itself the lane's "
        "--cov=crapkit then measures site-packages, which the wrong-tree refusal rejects")


# --- the sticky comment ------------------------------------------------------

def test_the_marker_the_action_greps_for_is_the_one_the_builder_writes():
    """Two spellings of the marker is a comment per push instead of one comment
    edited in place, and nothing fails: the second run simply does not find the
    first run's comment."""
    marker = re.search(r'MARKER = "([^"]+)"', BUILDER.read_text(encoding="utf-8"))

    assert marker, "the builder declares no marker"
    assert marker.group(1) in ACTION.read_text(encoding="utf-8")


def test_the_section_reader_walks_past_a_heading_inside_a_fence():
    """Guards the reader above, which otherwise passes on an empty body."""
    body = ["intro", "```markdown", "## crapkit", "```", "tail", "## Next", "gone"]

    assert _until_next_section(body)[-1] == "tail"


def _whole_job_snippet() -> str:
    """The section's second yaml block: the whole job, not the four-line one.

    `permissions:` is what tells them apart, because only the job carries one.
    """
    blocks = re.findall(r"```yaml\n(.*?)```", _readme_section(), re.S)
    jobs = [block for block in blocks if "permissions:" in block]
    assert len(jobs) == 1, f"expected one whole-job snippet, found {len(jobs)}"
    return jobs[0]


def test_the_readme_job_sets_python_up_before_the_teams_own_install():
    """The action's own first step is `actions/setup-python`, so a job that pip
    installs before it installs into whatever interpreter the runner defaulted
    to, and the action then runs the lanes on another one. The dependencies are
    on the machine and the lane still cannot import them."""
    job = _whole_job_snippet()

    assert "actions/setup-python" in job, "the snippet sets no interpreter up"
    assert job.index("actions/setup-python") < job.index("pip install"), \
        "the pip install lands in an interpreter the lanes never run on"


def test_the_readme_states_the_permission_the_comment_needs():
    """Without it the `gh api` POST is a 403 on a job whose every other step
    passed."""
    assert "pull-requests: write" in _readme_section()


def test_the_post_step_opens_by_keeping_its_own_status():
    """`shell: bash` runs with -e, so the first command that fails takes the step
    and the job with it. The scoring steps open `code=0` and record what they got;
    this one posts the action's whole output and has the most to lose."""
    body = _step_named("post the comment")["run"]

    assert _logical_lines(body)[0] == "code=0"


def test_every_gh_api_call_records_its_status_rather_than_failing_the_step():
    """A pull request from a fork carries a read-only token, so the POST is a 403
    on a job whose scoring all passed. Bare, that 403 fails the job and the
    verdict the steps above computed is never explained anywhere."""
    for body in _run_bodies():
        for line in _logical_lines(body):
            if "gh api" in line:
                assert re.search(r"\|\| [a-z_]+=\$\?$", line), f"a gh api call can fail the step: {line}"


def test_only_the_exit_code_step_exits_on_a_status_it_chose():
    """The gate's own comment says `gate: true` is the only thing that fails this
    action. Any other step that exits non-zero, or exits on whatever status it
    last got, makes that sentence false. A regression guard, not the test that
    drove the post step's change: that step's only exit was already `exit 0`.
    A bare command failing under -e is the other way out, and the two tests
    above hold the `gh api` calls; the steps that run crapkit keep `code=0`."""
    exiting = [body for body in _run_bodies()
               if any(match.group(1) != "0" for match in _EXIT.finditer(body))]

    assert len(exiting) == 1, "a step other than the gate decides this action's exit code"
    assert exiting[0] in _step_named("the exit code")["run"]


def test_the_readme_states_the_fetch_depth_verify_needs():
    """`actions/checkout` clones one commit; verify reads the diff against the
    baseline's commit out of git and exits 4 without it."""
    assert "fetch-depth: 0" in _readme_section()


# --- crapkit runs the action on crapkit --------------------------------------

def test_the_dogfood_job_runs_the_action_from_this_checkout():
    """The action's only proof is a job that runs it. `uses: ./` takes the
    working copy, so a broken step fails on the pull request that broke it."""
    job = _ci()["jobs"]["dogfood"]
    uses = [step for step in job["steps"] if step.get("uses") == "./"]

    assert uses, "the dogfood job stopped running the action"
    assert uses[0].get("with", {}).get("gate") in (False, "false"), "crapkit's own job stays advisory"


def test_the_dogfood_job_names_delta_rather_than_taking_the_default():
    """crapkit's `py` lane spells `--cov=crapkit`, which the action's editable
    install of this checkout resolves to this checkout. A base worktree run
    would measure HEAD's source, `crapkit coverage` refuses that artifact, and
    the delta run costs a suite and buys nothing. The job says so out loud, so
    the default flipping does not quietly add eight minutes to every pull
    request."""
    job = _ci()["jobs"]["dogfood"]
    step = next(s for s in job["steps"] if s.get("uses") == "./")

    assert step["with"]["delta"] in (False, "false")


def test_the_dogfood_job_can_write_the_comment():
    assert _ci()["jobs"]["dogfood"]["permissions"]["pull-requests"] == "write"


# --- the comment the builder renders -----------------------------------------
#
# The action hands it three payload files and a changed-file list, so these hand
# it the same shapes. Nothing here reaches into a shell step.

@lru_cache(maxsize=None)
def _builder():
    """`tools/` is not a package: the action calls this file by path, and
    loading it the same way keeps the test on the code the runner runs."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("crapkit_action_comment", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _worklist() -> dict:
    return {"active": [
        {"path": "calc/grade.py", "start": 67, "function": "curve( scores )",
         "ccn": 11, "risk": 3.5, "remedy": "decompose"},
        {"path": "calc/report.py", "start": 4, "function": "render( rows )",
         "ccn": 7, "risk": 0.5, "remedy": "add-tests"},
    ]}


def test_the_table_holds_only_the_changed_files():
    """A reviewer reads the rows their own diff is answerable for. The rest of
    the repository's debt is `crapkit worklist` in their own checkout."""
    picked = _builder().rows(_worklist(), ["calc/report.py"], 5)

    assert [row["path"] for row in picked] == ["calc/report.py"]


def test_no_changed_file_list_ranks_the_whole_repository():
    """A push event names no base commit and a shallow clone cannot diff against
    one. Ranking everything beats printing nothing."""
    picked = _builder().rows(_worklist(), [], 5)

    assert len(picked) == 2


def test_top_caps_the_rows_after_the_filter_not_before():
    """`crapkit worklist` ranks the repository; capping first would drop a
    changed file's own row for an untouched file that ranks above it."""
    picked = _builder().rows(_worklist(), ["calc/report.py"], 1)

    assert [row["path"] for row in picked] == ["calc/report.py"]


def _three_rows(tmp_path) -> Path:
    worklist = tmp_path / "worklist.json"
    worklist.write_text(json.dumps({"active": [
        {"path": f"calc/m{i}.py", "start": 1, "function": f"f{i}( x )", "ccn": 9 - i, "risk": 1.0,
         "remedy": "decompose"} for i in range(3)]}), encoding="utf-8")
    return worklist


# The `top` input as a workflow hands it over: a string, "" when the expression
# it names is unset. The number is the rows the table shows out of three.
_TOPS = {"5": 3, "2": 2, " 5 ": 3, "": 3, "ten": 3, "five": 3, "5.0": 3, "-1": 3, "1" * 20: 3}


@pytest.mark.parametrize("top", list(_TOPS), ids=[repr(top) for top in _TOPS])
def test_the_top_input_renders_rows_whatever_string_the_workflow_hands_over(tmp_path, top, capsys):
    """`--top` was `type=int`: "", "ten" and "5.0" exited 2 in "build the
    comment", the composite stopped there, and the job failed with the gate off
    and no comment. "-1" sliced the last row off without a word."""
    out = tmp_path / "comment.md"

    code = _builder().main(["--worklist", str(_three_rows(tmp_path)), "--top", top, "--out", str(out)])

    assert code == 0
    assert out.read_text(encoding="utf-8").count("| `calc/m") == _TOPS[top]


@pytest.mark.parametrize("top", ["ten", "5.0", "-1"])
def test_a_top_that_is_not_a_row_count_is_named_in_a_warning(tmp_path, top, capsys):
    _builder().main(["--worklist", str(_three_rows(tmp_path)), "--top", top, "--out", str(tmp_path / "c.md")])

    assert capsys.readouterr().out == (
        f"::warning title=crapkit::input top is {top!r}, not a whole number of rows; the comment "
        f"shows 5. Set top to a number such as \"10\", or leave it out for 5.\n")


@pytest.mark.parametrize("top", ["5", "", " 5 "])
def test_a_row_count_or_an_empty_top_warns_about_nothing(tmp_path, top, capsys):
    _builder().main(["--worklist", str(_three_rows(tmp_path)), "--top", top, "--out", str(tmp_path / "c.md")])

    assert capsys.readouterr().out == ""


def test_a_changed_file_with_no_ranked_function_says_so():
    body = _builder().table([])

    assert "No ranked function" in body


def test_the_verdict_line_carries_the_exit_code_and_the_counts():
    verify = {"ok": False, "run_id": 9, "baseline_run": 8, "changed_files": 1,
              "gate_violations": [{"path": "calc/grade.py"}], "ratchet_regressions": [],
              "new_failures": [], "diff_uncovered_count": 0}

    line = _builder().verdict_line(verify, 6)

    assert "exit 6" in line
    assert "1 gate violation," in line
    assert "0 ratchet regressions" in line


def test_a_verify_that_wrote_no_verdict_reports_its_exit_code():
    """A read command with no run behind it exits 1 and writes nothing to the
    redirect. The comment says which command failed and stays a comment."""
    line = _builder().verdict_line(None, 1)

    assert "exited 1" in line
    assert "tooling" in line


def test_the_body_leads_with_the_marker():
    """A body GitHub truncates still has to be findable on the next push."""
    text = _builder().body(None, None, 1, None, [], 5)

    assert text.startswith(_builder().MARKER)


def test_the_request_body_is_json_so_no_shell_quotes_the_comment(tmp_path):
    """`gh api --input` takes a file. Building it here means backticks, quotes
    and newlines in a function's own name are json.dumps' problem."""
    import json

    out, body = tmp_path / "c.md", tmp_path / "c.json"
    _builder().main(["--out", str(out), "--json-out", str(body)])

    assert json.loads(body.read_text(encoding="utf-8"))["body"] == out.read_text(encoding="utf-8")


# --- a verdict with no base run is not a pass (spec item 4, decision 7) --------
#
# On a depth-1 clone the base step made no run, verify judged the checkout against
# its own run (an empty diff), and the comment said "verify passed" over a pull
# request that exits 6 at full depth. The base step now records why, the comment
# says what was judged, and gate: true fails the check when the base run was
# attempted and failed.

def test_the_base_step_records_a_reason_on_every_failure_path():
    """A shallow clone, a fork point older than crapkit.toml, a lane that fails
    there: each used to leave `crapkit base scoring exited N` in the log and
    nothing the comment could quote."""
    body = _step_named("score the base commit")["run"]

    assert "crapkit-base.reason" in body
    assert "is-shallow-repository" in body, "a depth-1 clone must be told apart from a rewrite"
    assert "shallow clone" in body and "fetch-depth: 0" in body
    assert "crapkit.toml at the fork point" in body
    assert "lane failed at the fork point" in body


def test_the_exit_step_fails_only_a_pull_request_whose_base_run_was_attempted_and_failed():
    """With `gate: true`, exit 1 when the base step ran on a pull request and
    made no run; a push or `delta: "false"` keeps verify's own code, because
    those are documented opt-ins and the comment renders the honest line."""
    step = _step_named("the exit code")
    env = " ".join(str(v) for v in step.get("env", {}).values())

    assert "pull_request" in env and "inputs.delta" in env, (
        "the step must know whether the base run was attempted")
    assert "crapkit-base.sha" in step["run"], "an attempted base run leaves its sha behind"
    assert re.search(r"(?<![\w.-])exit 1\b", step["run"])


def test_the_exit_step_asks_whether_the_base_run_was_attempted_in_the_base_steps_own_words():
    """ATTEMPTED is the base step's `if:` typed a second time. Edited alone, the
    gate exits 1 on a pull request whose base step never ran, or never exits 1
    on one that did."""
    assert _step_named("the exit code")["env"]["ATTEMPTED"] == _step_named("score the base commit")["if"]


_REASON = "shallow clone does not hold the fork point of 1234abc; set fetch-depth: 0 on the checkout"


def _bash() -> str:
    """The bash a runner's `shell: bash` step runs under, or a skip. On Windows
    the `bash` on PATH can be the WSL launcher under System32, which cannot
    read the files this test writes."""
    bash = shutil.which("bash")
    if os.name == "nt" and (bash is None or "system32" in bash.lower()):
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else Path()
        bash = str(candidate) if candidate.is_file() else None
    if bash is None or "system32" in bash.lower():
        pytest.skip("no bash on PATH to run the step under")
    return bash


def _run_exit_step(tmp_path, gate: str, attempted: str, verify_exit: int, base_sha):
    """The exit step's body under `bash --noprofile --norc -eo pipefail`, which
    is what `shell: bash` means on a runner, over the files the earlier steps
    leave in RUNNER_TEMP: verify's exit always, the base step's sha when it
    made a run, its reason when it did not."""
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    (runner_temp / "crapkit-verify.exit").write_text(f"{verify_exit}\n", encoding="utf-8")
    (runner_temp / "crapkit-base.reason").write_text(_REASON + "\n", encoding="utf-8")
    if base_sha is not None:
        (runner_temp / "crapkit-base.sha").write_text(base_sha + "\n", encoding="utf-8")
    script = tmp_path / "exit-step.sh"
    script.write_text(_step_named("the exit code")["run"], encoding="utf-8", newline="\n")
    env = {**os.environ, "GATE": gate, "ATTEMPTED": attempted, "CRAPKIT_STATE": runner_temp.as_posix()}
    return subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                          env=env, capture_output=True, text=True)


@pytest.mark.parametrize("gate, attempted, verify_exit, base_sha, expected", [
    ("true", "true", 0, None, 1),
    ("true", "true", 5, None, 1),
    ("true", "true", 0, "1234abc", 0),
    ("true", "true", 6, "1234abc", 6),
    ("true", "false", 0, None, 0),
    ("true", "false", 7, None, 7),
    ("false", "true", 0, None, 0),
])
def test_the_exit_step_run_under_bash_fails_only_an_attempted_base_run_that_was_not_made(
        tmp_path, gate, attempted, verify_exit, base_sha, expected):
    """Decision 7, run rather than read: exit 1 only when the base run was
    attempted (a pull request with delta on) and left no sha, whatever verify
    said; an attempted base run that was made, a push, `delta: "false"` and a
    gate that is off all keep the code verify wrote."""
    result = _run_exit_step(tmp_path, gate, attempted, verify_exit, base_sha)

    assert result.returncode == expected, result.stdout + result.stderr


def test_the_exit_steps_failure_prints_the_reason_the_base_step_wrote(tmp_path):
    result = _run_exit_step(tmp_path, "true", "true", 0, None)

    assert _REASON in result.stdout


def _two_commit_repo(repo: Path, fork_files: dict) -> str:
    """A repo whose first commit, the fork point, holds `fork_files`; HEAD is a
    second commit on top. Returns the fork point's sha."""
    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
                              cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    repo.mkdir()
    git("init", "-q")
    for rel, text in {"README.md": "fork\n", **fork_files}.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git("add", "-A")
    git("commit", "-qm", "fork point")
    fork = git("rev-parse", "HEAD")
    (repo / "README.md").write_text("head\n", encoding="utf-8")
    git("commit", "-qam", "head")
    return fork


def _run_base_step(tmp_path, shim_body: str, fork_files=None, env_extra=None) -> str:
    """The base step under bash with a `crapkit` on PATH running `shim_body`,
    in a repo whose fork point holds `fork_files`. Returns the reason it wrote."""
    base_sha = _two_commit_repo(tmp_path / "repo", fork_files or {})
    state, shim = tmp_path / "state", tmp_path / "bin"
    state.mkdir()
    shim.mkdir()
    (shim / "crapkit").write_text("#!/bin/sh\n" + shim_body, encoding="utf-8", newline="\n")
    (shim / "crapkit").chmod(0o755)
    script = tmp_path / "base-step.sh"
    script.write_text(_step_named("score the base commit")["run"], encoding="utf-8", newline="\n")
    env = {**os.environ, **(env_extra or {}), "CRAPKIT_STATE": state.as_posix(), "BASE_SHA": base_sha,
           "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                   cwd=tmp_path / "repo", env=env, capture_output=True, text=True, timeout=HANG_SECONDS)
    return (state / "crapkit-base.reason").read_text(encoding="utf-8").strip()


# What `crapkit coverage` at the fork point printed on stderr and exited with,
# and the words the reason must carry. A warning can come first: coverage.py's
# no-branch-data line is the first stderr line of the default CI lane, and the
# reason quoted it for a run whose other lane failed.
_BASE_FAILURES = {
    "warning-lane-then-failing-lane": (
        "crapkit: lane 'a-py': coverage.py report carries no branch data, so the coverage term is "
        "statement-based\ncrapkit: lane 'b-js' FAILED: lane 'b-js' produced no artifact at "
        "coverage/coverage-final.json (command exit 1)\n", 5, "lane 'b-js' FAILED"),
    "killed-with-no-stderr": ("", 137, "crapkit coverage exited 137 and printed nothing"),
    "no-crapkit-toml-at-the-fork-point": (
        "crapkit: no crapkit.toml at or above /tmp/base\n", 3, "no crapkit.toml at or above"),
    "lane-exits-1-with-no-artifact": (
        "crapkit: lane 'unit' FAILED: lane 'unit' produced no artifact at coverage/coverage-final.json "
        "(command exit 1)\n", 5, "lane 'unit' FAILED"),
    "an-empty-istanbul-artifact": (
        "crapkit: lane 'unit' FAILED: istanbul artifact is empty (zero files)\n", 5, "istanbul artifact is empty"),
    "a-key-of-the-wrong-type": (
        "crapkit: crapkit.toml: scope 'src'.languages must be array\n", 3, ".languages must be array"),
}


@pytest.mark.parametrize("name", list(_BASE_FAILURES))
def test_the_base_reason_quotes_the_line_that_names_the_failure(tmp_path, name):
    """The step quoted `head -n 1` of crapkit's stderr: a warning a passing lane
    printed first, or nothing at all when crapkit died silent. The comment and
    the failing gate then named the wrong lane, or no cause."""
    stderr, code, needle = _BASE_FAILURES[name]
    (tmp_path / "stderr.txt").write_text(stderr, encoding="utf-8", newline="\n")

    reason = _run_base_step(tmp_path, f"cat '{(tmp_path / 'stderr.txt').as_posix()}' >&2\nexit {code}\n")

    assert needle in reason, reason


_A_PY_LANE = """
[[scope]]
name = "py"
paths = ["pylib"]
languages = ["python"]

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "a-py"
command = "python py.py"
artifact = "cov-py.json"
parser = "coveragepy"
scopes = ["py"]

[[lane]]
name = "b-js"
command = "python fail.py"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
"""

_NO_BRANCH_REPORT = (
    "import json\njson.dump({'meta': {'branch_coverage': False}, 'files': {'pylib/a.py': "
    "{'executed_lines': [1, 2], 'missing_lines': [], 'summary': {'num_statements': 2, "
    "'covered_lines': 2}, 'functions': {'f': {'executed_lines': [2], 'missing_lines': [], "
    "'summary': {'num_statements': 1, 'covered_lines': 1}, 'start_line': 1}}}}}, "
    "open('cov-py.json', 'w'))\n")


def test_the_base_reason_names_the_failed_lane_under_the_real_cli(tmp_path):
    """The same run through crapkit itself: lane a-py passes and warns about
    branch data, lane b-js fails. The reason names b-js."""
    files = {"crapkit.toml": _A_PY_LANE, "pylib/a.py": "def f():\n    return 1\n",
             "src/app.ts": "export function f(x: number) { return x > 0 ? 1 : 0; }\n",
             "fail.py": "import sys\nsys.exit(1)\n", "py.py": _NO_BRANCH_REPORT}
    python = Path(sys.executable).as_posix()

    reason = _run_base_step(tmp_path, f'exec "{python}" -m crapkit "$@"\n', files,
                            {"PYTHONPATH": str(ROOT / "src")})

    assert "lane 'b-js' FAILED" in reason, reason


def test_the_comment_step_hands_the_builder_the_base_files():
    """The renderer stays git-free: the sha and the reason reach it as files
    the base step wrote, the way the three payloads do."""
    body = _step_named("build the comment")["run"]
    line = next(ln for ln in _logical_lines(body) if "comment.py" in ln)

    assert "--base-sha" in line and "--base-reason" in line
    args = _builder()._parse(["--out", "x", "--base-sha", "s", "--base-reason", "r"])
    assert (args.base_sha, args.base_reason) == ("s", "r")


def _passing_verify() -> dict:
    return {"ok": True, "run_id": 3, "baseline_run": 3, "changed_files": 0,
            "gate_violations": [], "ratchet_regressions": [], "new_failures": [],
            "diff_uncovered_count": 0}


def test_a_passing_verdict_with_no_base_run_says_it_judged_no_changed_function():
    reason = "shallow clone does not hold the fork point of abc123; set fetch-depth: 0 on the checkout"

    line = _builder().verdict_line(_passing_verify(), 0, base_reason=reason)

    assert "verify judged no changed function" in line
    assert f"the base run was not made ({reason})" in line
    assert "verify passed" not in line


def test_a_passing_verdict_with_a_base_run_still_passes():
    line = _builder().verdict_line(_passing_verify(), 0, base_reason=None)

    assert line.startswith("**verify passed.**")


def test_a_failed_verdict_keeps_its_findings_whatever_the_base_reason():
    """The ratchet runs without a base, so exit 7 there is a finding and not a
    judgement of nothing."""
    verify = {**_passing_verify(), "ok": False, "ratchet_regressions": [
        {"path": "app/calc.py", "long_name": "f( )", "recorded": 10.0, "fresh_crap": 20.0}]}

    line = _builder().verdict_line(verify, 7, base_reason="no base commit")

    assert "verify failed" in line
    assert "judged no changed function" not in line


def _render(tmp_path, verify: dict, **files) -> str:
    """main() over a verify payload and the base files the action leaves;
    `sha=None` leaves that flag off, `sha=""` names a missing file."""
    import json

    payload = tmp_path / "verify.json"
    payload.write_text(json.dumps(verify), encoding="utf-8")
    argv = ["--verify", str(payload), "--out", str(tmp_path / "c.md")]
    for flag, text in files.items():
        path = tmp_path / f"base.{flag}"
        if text:
            path.write_text(text, encoding="utf-8")
        argv += [f"--base-{flag}", str(path)]
    _builder().main(argv)
    return (tmp_path / "c.md").read_text(encoding="utf-8")


def test_main_reads_the_reason_the_base_step_wrote(tmp_path):
    text = _render(tmp_path, _passing_verify(), sha="", reason="lane failed at the fork point 1234abc: crapkit: lane 'py' FAILED: exited 1")

    assert "the base run was not made (lane failed at the fork point 1234abc: crapkit: lane 'py' FAILED: exited 1)" in text


def test_a_push_event_leaves_no_base_files_and_the_comment_says_no_base_commit(tmp_path):
    """The base step is skipped on a push and under delta: "false", so neither
    file exists; the honest line still names why nothing was judged."""
    text = _render(tmp_path, _passing_verify(), sha="", reason="")

    assert "the base run was not made (no base commit)" in text


def test_a_base_sha_on_disk_keeps_the_pass(tmp_path):
    text = _render(tmp_path, _passing_verify(), sha="1234abc" + chr(10), reason="")

    assert "**verify passed.**" in text


def test_a_render_without_the_base_flags_keeps_the_pass(tmp_path):
    """Three saved payloads and no base files is the README's own render route."""
    text = _render(tmp_path, _passing_verify())

    assert "**verify passed.**" in text


def test_the_readme_gate_row_names_the_base_run_precondition():
    """`gate: "true"` now fails a pull request whose base run was attempted and
    not made; a consumer reading the inputs table learns it there."""
    section = _readme_section()
    gate_row = next(ln for ln in section.splitlines() if ln.startswith("| `gate` |"))

    assert "base run" in gate_row
    assert "judged no changed function" in section


# --- no verdict over a failed measurement (spec item 7, Action half) -----------
#
# The verdict step ran verify whatever coverage had exited. On a persistent
# runner with `clean: false`, a lane that stopped writing its artifact was
# refused by coverage (exit 5) and then verify --reuse-artifacts read the old
# artifact, passed, and became the trusted baseline.

def test_the_checkout_step_records_coverages_exit_for_the_verdict_step():
    body = _step_named("score the checkout")["run"]

    assert "crapkit-coverage.exit" in body


def test_the_verdict_step_reads_coverages_exit_before_calling_verify():
    """Coverage's exit is the first thing the step reads; when it is non-zero
    that code becomes the verdict's and neither verify call runs."""
    body = _step_named("the verdict")["run"]
    lines = _logical_lines(body)
    first_read = next(i for i, ln in enumerate(lines) if "crapkit-coverage.exit" in ln)
    first_verify = next(i for i, ln in enumerate(lines) if _CALL.search(ln) and "verify" in ln)

    assert first_read < first_verify, "verify must not run before coverage's exit is read"
    assert "crapkit-verify.exit" in body


def test_the_comment_step_hands_the_builder_coverages_exit():
    body = _step_named("build the comment")["run"]
    line = next(ln for ln in _logical_lines(body) if "comment.py" in ln)

    assert "--coverage-exit" in line
    assert _builder()._parse(["--out", "x", "--coverage-exit", "5"]).coverage_exit == 5


def test_a_failed_coverage_yields_no_verdict_and_quotes_the_lane_failures_first_line():
    coverage = {"functions": 4, "files": 2, "lane_failures": {
        "py": "lane 'py' wrote no artifact on its last attempt; the .crapkit/cov/py.json on disk predates it\nsecond line"}}

    line = _builder().no_verdict_line(coverage, 5)

    assert line.startswith("**no verdict: `crapkit coverage` exited 5 (")
    assert "lane 'py' failed: lane 'py' wrote no artifact on its last attempt" in line
    assert "second line" not in line
    assert "verify did not run" in line


def test_no_verdict_falls_back_to_the_job_log_when_coverage_printed_no_summary():
    """When every lane fails, coverage raises before any summary and the
    redirect target is empty; the lane names are only in the job log."""
    line = _builder().no_verdict_line(None, 5)

    assert "exited 5" in line
    assert "job log" in line


def test_no_verdict_quotes_the_error_object_when_coverage_printed_one():
    """0.5.0's --json prints one error object when a crapkit error escapes."""
    coverage = {"error": {"exit": 5, "kind": "tool", "message": "lizard is not importable\n"},
                "schema": 1}

    line = _builder().no_verdict_line(coverage, 5)

    assert "(lizard is not importable)" in line


def test_every_lane_failing_points_at_the_job_log_not_above():
    """The CLI's `the errors are above` means stderr; nothing sits above the line
    in a pull request comment, and the lane errors are in the job log."""
    coverage = {"error": {"exit": 5, "kind": "tool",
                          "message": "every lane failed (1 of 1); the errors are above\n"},
                "schema": 1}

    line = _builder().no_verdict_line(coverage, 5)

    assert "(every lane failed (1 of 1); the lane errors are in the job log)" in line, line
    assert "above" not in line, line
    assert f"`({_builder().coverage_failure(coverage)})`" in " ".join(_readme_section().split()), \
        "the README quotes the line the builder prints"


def test_the_body_renders_no_verdict_in_place_of_the_verify_line_when_coverage_failed():
    coverage = {"lane_failures": {"py": "lane 'py' wrote no artifact on its last attempt"}}

    text = _builder().body(coverage, None, 1, None, [], 5, coverage_exit=5)

    assert "**no verdict: `crapkit coverage` exited 5" in text
    assert "wrote no verdict" not in text


def test_main_reads_coverages_exit_and_says_no_verdict(tmp_path):
    import json

    cov = tmp_path / "cov.json"
    cov.write_text(json.dumps({"lane_failures": {"py": "lane 'py' wrote no artifact on its last attempt"}}), encoding="utf-8")
    out = tmp_path / "c.md"
    _builder().main(["--coverage", str(cov), "--coverage-exit", "5", "--verify-exit", "5", "--out", str(out)])

    assert "no verdict: `crapkit coverage` exited 5 (lane 'py' failed:" in out.read_text(encoding="utf-8")


def test_the_readme_says_verify_is_skipped_when_coverage_fails():
    section = _readme_section()

    assert "no verdict" in section
    assert "verify did not run" in section


# --- the comment names the finding (spec item 10, comment side) ---------------
#
# On exit 6 the comment read "1 gate violation" over two identical-looking rows,
# the ratchet-marked legacy_router and the pull request's own route, while the
# verify payload already carried the path, function, ccn, cov and remedy; on
# exit 9 the ceiling and the uncovered lines were only in the job log.

def _failing_verify(**over) -> dict:
    base = {"ok": False, "run_id": 3, "baseline_run": 1, "changed_files": 1,
            "gate_violations": [], "ratchet_regressions": [], "new_failures": [],
            "diff_uncovered": [], "diff_uncovered_count": 0, "diff_uncovered_max": None}
    return {**base, **over}


def _violation() -> dict:
    return {"path": "app/calc.py", "start": 34, "long_name": "route( a , b , c , d )",
            "ccn": 8, "cov": 0.1, "crap": 54.656, "remedy": "decompose"}


def test_the_verdict_names_the_rule_each_exit_code_stands_for():
    line = _builder().verdict_line

    assert "exit 6: complexity gate" in line(_failing_verify(gate_violations=[_violation()]), 6)
    assert "exit 7: ratchet regressions" in line(_failing_verify(), 7)
    assert "exit 8: new test failures" in line(_failing_verify(), 8)
    assert "exit 9: diff-coverage ceiling 3" in line(_failing_verify(diff_uncovered_max=3), 9)


def test_the_verdict_prints_one_bullet_per_gate_violation():
    line = _builder().verdict_line(_failing_verify(gate_violations=[_violation()]), 6)

    assert "- gate: `app/calc.py:34` `route( a , b , c , d )` ccn 8, cov 10%, crap 54.7 -> decompose" in line


def test_the_verdict_prints_one_bullet_per_ratchet_regression():
    verify = _failing_verify(ratchet_regressions=[
        {"path": "app/calc.py", "long_name": "legacy_router( a , b , c , d , e )",
         "recorded": 72.0, "fresh_crap": 80.5}])

    line = _builder().verdict_line(verify, 7)

    assert "- ratchet: `app/calc.py` `legacy_router( a , b , c , d , e )` 72.0 -> 80.5 (recorded -> fresh)" in line


def test_the_verdict_prints_one_bullet_per_new_test_failure():
    line = _builder().verdict_line(_failing_verify(new_failures=["tests/test_calc.py::test_route"]), 8)

    assert "- new test failure: `tests/test_calc.py::test_route`" in line


_GITHUB_COMMENT_LIMIT = 65536


def _failures(count: int) -> list:
    return [f"tests/unit/test_module_{i}.py::test_a_case_whose_name_runs_long_{i}" for i in range(count)]


def _violations(count: int) -> list:
    return [{**_violation(), "start": i, "long_name": f"route_{i}( a , b , c , d )"} for i in range(count)]


def _regressions(count: int) -> list:
    return [{"path": f"app/m{i}.py", "long_name": f"f{i}( x )", "recorded": 8.0, "fresh_crap": 9.0}
            for i in range(count)]


# A finding kind with more entries than one comment can carry: the entries the
# verdict prints, and the line that counts the rest.
_FLOODS = {
    "10-new-failures": (dict(new_failures=_failures(10)), 8, 10, None),
    "1500-new-failures": (dict(new_failures=_failures(1500)), 8, 50,
                          "- and 1450 more new test failures; `crapkit verify` lists them all"),
    "1000-gate-violations": (dict(gate_violations=_violations(1000)), 6, 50,
                             "- and 950 more gate violations; `crapkit verify` lists them all"),
    "1000-ratchet-regressions": (dict(ratchet_regressions=_regressions(1000)), 7, 50,
                                 "- and 950 more ratchet regressions; `crapkit verify` lists them all"),
}


@pytest.mark.parametrize("name", list(_FLOODS))
def test_a_flood_of_findings_prints_fifty_of_a_kind_and_counts_the_rest(name):
    """Every entry was a bullet, and GitHub refuses a comment body over 65,536
    characters: 1,500 new failures made a 152,245-character body, the POST came
    back 422, and the pull request got no comment at all."""
    over, code, shown, rest = _FLOODS[name]

    text = _builder().body(None, _failing_verify(**over), code, None, [], 5)

    assert len(text) <= _GITHUB_COMMENT_LIMIT
    assert len([ln for ln in text.splitlines() if ln.startswith("- ") and " more " not in ln]) == shown
    assert rest is None or rest in text


def test_a_body_over_githubs_limit_is_cut_below_it_and_says_so(tmp_path):
    """Long names and a large `top` still make a table GitHub refuses. The
    request body is cut at a line under the limit, in UTF-8 bytes so no count
    of a multibyte character can tip it over; the marker stays first, so the
    next push still edits this comment, and the markdown the step prints to
    the job log keeps every row."""
    worklist = tmp_path / "worklist.json"
    worklist.write_text(json.dumps({"active": [
        {"path": f"app/m{i}.py", "start": 1, "function": "é" * 300, "ccn": 9, "risk": 1.0,
         "remedy": "decompose"} for i in range(1000)]}), encoding="utf-8")
    out, request = tmp_path / "c.md", tmp_path / "c.json"

    _builder().main(["--worklist", str(worklist), "--top", str(10 ** 20), "--out", str(out),
                     "--json-out", str(request)])

    sent = json.loads(request.read_text(encoding="utf-8"))["body"]
    assert len(sent.encode("utf-8")) <= _GITHUB_COMMENT_LIMIT
    assert sent.startswith(_builder().MARKER)
    assert sent.endswith("the comment stopped at GitHub's 65,536-character limit; the job log above "
                         "holds the whole text.\n")
    assert out.read_text(encoding="utf-8").count("| `app/m") == 1000


def test_a_flood_of_new_failures_posts_one_comment_github_accepts(tmp_path):
    """The whole route: main() writes the request body, the post step sends it,
    and the local API refuses a body over the limit with 422 as GitHub does."""
    verify = tmp_path / "verify.json"
    verify.write_text(json.dumps(_failing_verify(new_failures=_failures(1500))), encoding="utf-8")
    request = tmp_path / "request.json"
    _builder().main(["--verify", str(verify), "--verify-exit", "8", "--out", str(tmp_path / "c.md"),
                     "--json-out", str(request)])

    result, writes = _post_under_real_gh(tmp_path, [], request=request)

    assert writes == _POST_FRESH
    assert "posting the crapkit comment exited 0" in result.stdout, result.stdout + result.stderr


def test_the_verdict_lists_the_first_twenty_uncovered_changed_lines_grouped_per_file():
    uncovered = [{"path": "a.py", "line": n} for n in range(1, 16)] + \
                [{"path": "b.py", "line": n} for n in range(1, 11)]
    verify = _failing_verify(diff_uncovered=uncovered, diff_uncovered_count=25, diff_uncovered_max=3)

    line = _builder().verdict_line(verify, 9)

    assert "- uncovered lines in `a.py`: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15" in line
    assert "- uncovered lines in `b.py`: 1, 2, 3, 4, 5" in line
    assert "- uncovered lines in `b.py`: 1, 2, 3, 4, 5, 6" not in line
    assert "- and 5 more uncovered changed lines" in line


def test_the_counts_line_stays_verbatim_below_the_bullets():
    verify = _failing_verify(gate_violations=[_violation()],
                             diff_uncovered=[{"path": "app/calc.py", "line": 35}], diff_uncovered_count=1)

    lines = _builder().verdict_line(verify, 6).splitlines()

    assert lines[0] == "**verify failed, exit 6: complexity gate.**"
    assert lines[-1] == ("Run 3 against baseline 1, 1 changed file: 1 gate violation, "
                         "0 ratchet regressions, 0 new test failures, 1 uncovered changed line.")
    assert [ln for ln in lines if ln.startswith("- ")] == [
        "- gate: `app/calc.py:34` `route( a , b , c , d )` ccn 8, cov 10%, crap 54.7 -> decompose",
        "- uncovered lines in `app/calc.py`: 35"]


def test_a_gate_violation_with_no_coverage_prints_a_dash():
    """An untested function's cov is null in the payload."""
    line = _builder().verdict_line(_failing_verify(gate_violations=[{**_violation(), "cov": None}]), 6)

    assert "cov -," in line


def test_a_marked_row_is_labelled_accepted_debt():
    marked = {"path": "app/calc.py", "start": 19, "function": "legacy_router( a , b , c , d , e )",
              "ccn": 8, "risk": 4.0, "remedy": "decompose", "ratchet_mark": 72.0}
    fresh = {**marked, "start": 34, "function": "route( a , b , c , d )", "ratchet_mark": None}

    text = _builder().table([marked, fresh])

    assert "| `legacy_router( a , b , c , d , e )` | 8 | 4.0 | decompose (accepted debt) |" in text
    assert "| `route( a , b , c , d )` | 8 | 4.0 | decompose |" in text


def test_a_row_without_the_mark_field_renders_as_before():
    """A 0.4.x worklist payload carries no ratchet_mark."""
    text = _builder().table(_worklist()["active"][:1])

    assert "accepted debt" not in text


def test_rows_named_by_a_finding_come_first_and_survive_the_cap():
    worklist = {"active": [
        {"path": "app/calc.py", "start": 19, "function": "legacy_router( a , b , c , d , e )"},
        {"path": "app/calc.py", "start": 34, "function": "route( a , b , c , d )"}]}
    named = {("app/calc.py", "route( a , b , c , d )")}

    picked = _builder().rows(worklist, ["app/calc.py"], 1, named)

    assert [row["function"] for row in picked] == ["route( a , b , c , d )"]


def test_the_findings_name_the_rows_the_table_lists_first():
    verify = _failing_verify(
        gate_violations=[_violation()],
        ratchet_regressions=[{"path": "app/calc.py", "long_name": "legacy_router( a , b , c , d , e )",
                              "recorded": 72.0, "fresh_crap": 80.5}],
        overridden=[{"path": "app/other.py", "long_name": "f( )"}])

    assert _builder().named_by_findings(verify) == {
        ("app/calc.py", "route( a , b , c , d )"),
        ("app/calc.py", "legacy_router( a , b , c , d , e )"),
        ("app/other.py", "f( )")}
    assert _builder().named_by_findings(None) == set()


FIXTURES = ROOT / "tests" / "fixtures" / "action_comment"


def test_the_readme_comment_is_the_render_of_the_recorded_payloads(tmp_path):
    """comment.py promises the README fence is the byte-identical render of
    three saved payloads; the payloads live beside this test, so the fence is
    regenerated from a failing example and not written by hand."""
    out = tmp_path / "comment.md"
    _builder().main(["--coverage", str(FIXTURES / "coverage.json"), "--coverage-exit", "0",
                     "--verify", str(FIXTURES / "verify.json"), "--verify-exit", "6",
                     "--worklist", str(FIXTURES / "worklist.json"),
                     "--changed", str(FIXTURES / "changed.txt"), "--top", "5", "--out", str(out)])
    fence = re.search(r"```markdown\n(<!-- crapkit-action -->\n.*?)```", _readme_section(), re.S)

    assert fence, "the README section lost its rendered comment"
    assert fence.group(1) == out.read_text(encoding="utf-8")


# --- the scored line quotes the ceilings and a lane failure (spec item 11) -----

def _coverage(**over) -> dict:
    base = {"functions": 12, "files": 3, "over_target": 2, "crap_load": 45.2, "grade": "B",
            "lane_failures": {}}
    return {**base, **over}


def test_the_scored_line_quotes_the_ceiling_the_count_is_judged_against():
    line = _builder().scored_line

    assert "2 over ceiling 6," in line(_coverage(ceilings={"default": 6}))
    assert "2 over their ceilings (6; reports 12, util 4)," in line(
        _coverage(ceilings={"default": 6, "reports": 12, "util": 4}))


def test_a_payload_without_ceilings_names_no_number():
    """A 0.4.x `coverage --json` carries `over_target` and no `ceilings`."""
    line = _builder().scored_line(_coverage())

    assert "2 over the ceiling," in line
    assert "target" not in line


def test_the_scored_line_quotes_the_first_line_of_a_lane_failure():
    coverage = _coverage(lane_failures={"js": "lane 'js' wrote no artifact on its last attempt\n  full log: x"})

    line = _builder().scored_line(coverage)

    assert line.endswith("grade B; lane 'js' failed: lane 'js' wrote no artifact on its last attempt.")
    assert "full log" not in line


def test_the_scored_line_quotes_the_error_message_when_coverage_died_under_json():
    """0.5.0's --json prints one error object on stdout when a crapkit error
    escapes, so the sentence that names the fix reaches the comment."""
    coverage = {"error": {"exit": 5, "kind": "tool", "message": "lane 'py' cannot import pytest-cov; pip install pytest-cov\n"},
                "schema": 1}

    line = _builder().scored_line(coverage)

    assert line == "`crapkit coverage` exited 5: lane 'py' cannot import pytest-cov; pip install pytest-cov."


def test_a_verify_error_object_is_quoted_and_never_counted():
    """0.5.0's `verify --json` prints one error object when a crapkit error
    escapes (a missing baseline commit, exit 4). Read as a verdict it would
    count nothing over "Run None against baseline None"."""
    message = ("baseline commit a74260f321f is not an ancestor of HEAD in this shallow clone, "
               "which does not hold it; set fetch-depth: 0 on the checkout or run git fetch --unshallow")
    verify = {"error": {"exit": 4, "kind": "git", "message": message + "\n"}, "schema": 1}

    line = _builder().verdict_line(verify, 4)

    assert line == f"**`crapkit verify` exited 4 and wrote no verdict: {message}.**"


# --- the pin the README hands the consumer ------------------------------------

_USES_PIN = re.compile(r"JeanFrancoisGagne/crapkit@v([0-9]+[.][0-9]+[.][0-9]+)")


def test_the_readme_pins_uses_to_the_release_it_documents():
    """`uses:` resolves action.yml at the tag it names, so a README that kept an
    older pin hands every new consumer an older action. The 0.4.9 and 0.4.10
    READMEs both said `@v0.4.8`: the release bump touched `crapkit X.Y.Z` and
    `rev: vX.Y.Z` and nothing else, and no test read the third pin."""
    from crapkit import __version__

    pins = set(_USES_PIN.findall((ROOT / "README.md").read_text(encoding="utf-8")))

    assert pins, "the README no longer shows a uses: pin"
    assert pins == {__version__}, f"README pins {sorted(pins)}, this release is {__version__}"


def _run_post_step(tmp_path, head_repo: str, gh_exit: int):
    """The post step under bash, with a `gh` on PATH that prints an error and
    exits `gh_exit`, the way a bad token or a missing permission answers."""
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "crapkit-comment.json").write_text("{}", encoding="utf-8")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "gh").write_text(f"#!/bin/sh\necho 'gh: Bad credentials (HTTP 401)' >&2\nexit {gh_exit}\n",
                             encoding="utf-8", newline="\n")
    (shim / "gh").chmod(0o755)
    script = tmp_path / "post-step.sh"
    script.write_text(_step_named("post the comment")["run"], encoding="utf-8", newline="\n")
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ['PATH']}",
           "CRAPKIT_STATE": state.as_posix(), "GH_TOKEN": "x", "PR": "7",
           "REPO": "owner/repo", "HEAD_REPO": head_repo}
    return subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                          env=env, capture_output=True, text=True)


def test_a_failed_post_blames_the_fork_token_only_on_a_fork(tmp_path):
    """Every nonzero gh exit read `a fork pull request's token cannot write
    comments`: bad credentials, a missing gh and a same-repo job without
    pull-requests: write were all told they came from a fork."""
    fork = _run_post_step(tmp_path / "fork", "someone/repo", 1)
    same = _run_post_step(tmp_path / "same", "owner/repo", 1)

    assert "a fork pull request's token cannot write comments" in fork.stdout, fork.stdout
    assert "fork" not in same.stdout, same.stdout
    assert "posting the crapkit comment exited 1: gh's own error is above" in same.stdout, \
        same.stdout


def test_the_post_step_reads_the_head_repository_off_the_event():
    env = _step_named("post the comment")["env"]

    assert env["HEAD_REPO"] == "${{ github.event.pull_request.head.repo.full_name }}"


# --- the lookup, run under gh's own jq ----------------------------------------

_MARK = "<!-- crapkit-action -->\n## crapkit\nthe previous push"


def _c(ident: int, body="looks good") -> dict:
    return {"id": ident, "user": {"login": "reviewer"}, "body": body}


_MARKED = _c(2, _MARK)
_POST_FRESH = ["POST /repos/owner/repo/issues/7/comments"]


def _edit(ident: int) -> list:
    return [f"PATCH /repos/owner/repo/issues/comments/{ident}"]


_EDIT_IN_PLACE = _edit(2)


class _FailsOnce(NamedTuple):
    """A page that answers `status` to its first request and `comments` to
    every request after it: a transient 502 or rate limit."""
    status: int
    comments: list


class _CommentsApi(BaseHTTPRequestHandler):
    """The two endpoints the post step calls: the pull request's comment list,
    served one page per request from `server.pages` after `server.delay`
    seconds, and the writes, recorded in `server.writes` as `METHOD path`. A
    page is a list of comments, the HTTP status it fails with every time, or a
    `_FailsOnce`. Each GET's page number goes to `server.gets`, so the page-1
    count is the number of times the step listed the thread."""

    def do_GET(self):
        threading.Event().wait(self.server.delay)
        url = urlsplit(self.path)
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        self.server.gets.append(page)
        answer = self.server.pages[page - 1]
        if isinstance(answer, _FailsOnce):
            answer = answer.status if self.server.gets.count(page) == 1 else answer.comments
        if isinstance(answer, int):
            self._answer(answer, {"message": f"fake {answer}", "documentation_url": "https://docs.github.com/rest"})
            return
        self._answer(200, answer, self._next(url.path, page))

    def _next(self, path: str, page: int) -> dict:
        if page >= len(self.server.pages):
            return {}
        return {"Link": f'<http://api.github.localhost{path}?page={page + 1}>; rel="next"'}

    def do_PATCH(self):
        self._write(200)

    def do_POST(self):
        self._write(201)

    def _write(self, status: int):
        sent = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if len(sent.get("body", "")) > 65536:
            self._answer(422, {"message": "Validation Failed", "errors": [{"code": "too_long"}]})
            return
        self.server.writes.append(f"{self.command} {urlsplit(self.path).path}")
        self._answer(status, {"id": 99})

    def _answer(self, status: int, payload, headers=None):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _env_routing_gh_to(port: int) -> dict:
    """gh sends a `github.localhost` host's calls over plain http, so
    HTTP_PROXY routes them to the local API. Every other proxy variable goes:
    a `no_proxy=localhost` on the machine would send them past it."""
    env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
    env.update(HTTP_PROXY=f"http://127.0.0.1:{port}", GH_HOST="github.localhost", GH_TOKEN="x")
    return env


def _post_under_real_gh(tmp_path, comments: list, *, pages=None, delay: float = 0.0, request=None,
                        gets=None) -> tuple:
    """The post step under bash with the `gh` on PATH, whose built-in jq runs the
    lookup, against a local API that lists `comments` on pull request 7 (or
    serves `pages` one per request, the way GitHub pages a long thread), and
    refuses a body over 65,536 characters with a 422 as GitHub does. The step
    sends `request`, or a marker-only body. Returns the step's result and the
    writes the API accepted; `gets`, when given, receives each GET's page."""
    if shutil.which("gh") is None:
        pytest.skip("needs gh on PATH, which the ubuntu-latest and windows-latest runners carry")
    state = tmp_path / "state"
    state.mkdir()
    sent = request.read_text(encoding="utf-8") if request else '{"body": "<!-- crapkit-action -->"}'
    (state / "crapkit-comment.json").write_text(sent, encoding="utf-8")
    script = tmp_path / "post-step.sh"
    script.write_text(_step_named("post the comment")["run"], encoding="utf-8", newline="\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CommentsApi)
    server.pages, server.writes, server.delay = pages or [comments], [], delay
    server.gets = [] if gets is None else gets
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {**_env_routing_gh_to(server.server_address[1]), "GH_CONFIG_DIR": str(tmp_path / "gh"),
           "CRAPKIT_STATE": state.as_posix(), "PR": "7", "REPO": "owner/repo", "HEAD_REPO": "owner/repo"}
    try:
        result = subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                                env=env, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=HANG_SECONDS)
    finally:
        server.shutdown()
        server.server_close()
    return result, server.writes


def test_the_lookup_edits_the_marked_comment_in_place(tmp_path):
    result, writes = _post_under_real_gh(tmp_path, [{"id": 1, "body": "looks good"}, _MARKED])

    assert writes == _EDIT_IN_PLACE, result.stdout + result.stderr


@pytest.mark.parametrize("comments", [
    [{"id": 1, "body": None}, _MARKED],
    [{"id": 1}, _MARKED],
    [_MARKED, {"id": 3, "body": None}],
], ids=["null-body-before", "no-body-key", "null-body-after"])
def test_a_comment_with_no_body_does_not_hide_the_marked_one(tmp_path, comments):
    """The API schema does not require an issue comment's `body`. `contains` on
    null is an error in gh's jq as in jq 1.7, so one such comment anywhere in the
    list made gh exit 1, the lookup came back empty, and the step posted a second
    crapkit comment instead of editing the first."""
    result, writes = _post_under_real_gh(tmp_path, comments)

    assert writes == _EDIT_IN_PLACE, result.stdout + result.stderr


# Every shape a comment thread takes, one page per list: the body's state, the
# list's state, where the marked comment sits and which page fails. The write
# is the contract: PATCH the comment the marker opens, POST when there is none,
# and never a URL built from anything but a comment id. A listing that fails
# before it reaches the marked comment is _LISTING_FAILS below.
_THREADS = {
    "body-number": ([[_c(1, 5), _MARKED]], _EDIT_IN_PLACE),
    "body-empty-string": ([[_c(1, ""), _MARKED]], _EDIT_IN_PLACE),
    "body-non-ascii": ([[_c(1, "café 世界 \U0001f600"), _MARKED]], _EDIT_IN_PLACE),
    "body-300-kB": ([[_c(1, "x" * 300_000), _MARKED]], _EDIT_IN_PLACE),
    "no-comment": ([[]], _POST_FRESH),
    "one-item-the-marked-one": ([[_MARKED]], _EDIT_IN_PLACE),
    "one-item-another-comment": ([[_c(1)]], _POST_FRESH),
    "no-marker-on-the-page": ([[_c(1), _c(3)]], _POST_FRESH),
    "marker-on-page-2": ([[_c(1), _c(3)], [_c(4), _c(5, _MARK)]], _edit(5)),
    "marker-on-page-3-of-3": ([[_c(1)], [_c(3)], [_c(9, _MARK)]], _edit(9)),
    "null-body-on-page-1-marker-on-page-2": ([[_c(1, None), _c(3)], [_c(5, _MARK)]], _edit(5)),
    "absent-body-on-page-1-marker-on-page-2": ([[{"id": 1}, _c(3)], [_c(5, _MARK)]], _edit(5)),
    "marker-on-page-1-null-body-on-page-2": ([[_MARKED, _c(3)], [_c(4, None)]], _EDIT_IN_PLACE),
    "a-quote-of-the-marker-before-the-marked-comment": ([[_c(1, "> " + _MARK + "\nwhy red?"), _MARKED]],
                                                        _EDIT_IN_PLACE),
    "marker-on-page-1-and-page-2-fails": ([[_MARKED], 502], _EDIT_IN_PLACE),
}

# A listing that fails before it reaches the marked comment, one page per list,
# the failing page as a _FailsOnce, and the write the step owes once a second
# listing gets through.
_LISTING_FAILS = {
    "page-1-fails-502": ([_FailsOnce(502, [_c(1)]), [_MARKED]], _EDIT_IN_PLACE),
    "page-1-rate-limited-403": ([_FailsOnce(403, [_c(1)]), [_MARKED]], _EDIT_IN_PLACE),
    "the-only-page-403": ([_FailsOnce(403, [])], _POST_FRESH),
    "marker-on-page-2-and-page-2-fails": ([[_c(1)], _FailsOnce(502, [_c(5, _MARK)])], _edit(5)),
    "page-2-fails-marker-on-page-3": ([[_c(1)], _FailsOnce(502, [_c(3)]), [_c(30, _MARK)]], _edit(30)),
}


def _failing_every_time(pages: list) -> list:
    return [page.status if isinstance(page, _FailsOnce) else page for page in pages]


@pytest.mark.parametrize("name", list(_THREADS))
def test_every_thread_shape_gets_one_write_to_the_right_place(tmp_path, name):
    """gh's jq errors on a string operation over a body that is not a string,
    and on an error page gh prints GitHub's JSON on stdout and exits 1: the step
    took that JSON as the comment id and PATCHed
    `issues/comments/{"message": ...}`. A lookup that failed before it saw the
    marked comment posts a fresh one, which is what its log line says."""
    pages, expected = _THREADS[name]

    result, writes = _post_under_real_gh(tmp_path, [], pages=pages)

    assert writes == expected, result.stdout + result.stderr


@pytest.mark.parametrize("name", [name for name, (_, writes) in _THREADS.items() if writes != _POST_FRESH])
def test_the_log_never_says_posting_a_fresh_one_before_an_edit(tmp_path, name):
    """A lookup that found the comment and then failed on a later page said
    `posting a fresh one` and PATCHed: the log contradicted the write."""
    pages, _ = _THREADS[name]

    result, _ = _post_under_real_gh(tmp_path, [], pages=pages)

    assert "posting a fresh one" not in result.stdout, result.stdout


@pytest.mark.parametrize("name", list(_LISTING_FAILS))
def test_a_listing_that_fails_once_is_listed_again_before_anything_is_posted(tmp_path, name):
    """A lookup that failed before it saw the marked comment posted a fresh one
    at once, so one transient 502 left the pull request with two crapkit
    comments. The step now lists the thread a second time and edits the
    comment that listing finds."""
    pages, expected = _LISTING_FAILS[name]
    gets = []

    result, writes = _post_under_real_gh(tmp_path, [], pages=pages, gets=gets)

    assert (gets.count(1), writes) == (2, expected), result.stdout + result.stderr
    assert "failed twice" not in result.stdout, result.stdout


@pytest.mark.parametrize("name", list(_LISTING_FAILS))
def test_a_listing_that_fails_twice_posts_a_fresh_comment_and_says_so(tmp_path, name):
    """A lasting error still puts this push's verdict on the pull request: a
    stale verdict misleads a reviewer more than a second comment does."""
    gets = []

    result, writes = _post_under_real_gh(tmp_path, [], pages=_failing_every_time(_LISTING_FAILS[name][0]),
                                         gets=gets)

    assert (gets.count(1), writes) == (2, _POST_FRESH), result.stdout + result.stderr
    assert "listing the comments failed twice before it found a crapkit comment" in result.stdout, \
        result.stdout


def test_a_listing_that_fails_after_the_marked_comment_edits_it_without_listing_again(tmp_path):
    gets = []

    result, writes = _post_under_real_gh(tmp_path, [], pages=_THREADS["marker-on-page-1-and-page-2-fails"][0],
                                         gets=gets)

    assert (gets.count(1), writes) == (1, _EDIT_IN_PLACE), result.stdout + result.stderr
    assert "after it found comment 2: editing that one" in result.stdout, result.stdout


def test_marked_comments_on_two_pages_edit_the_first_and_log_no_failure(tmp_path):
    """The reported bug left threads with a crapkit comment on two pages. `head
    -n 1` closed the pipe after the first id, gh died writing the second (SIGPIPE
    on Linux, a closed pipe on Windows), and the log said the lookup exited 141
    and a fresh comment was coming before the step PATCHed. Each page answers
    after a quarter second, the latency that made it fail every time."""
    result, writes = _post_under_real_gh(tmp_path, [], pages=[[_MARKED], [_c(7, _MARK)]], delay=0.25)

    assert writes == _EDIT_IN_PLACE, result.stdout + result.stderr
    assert "looking the existing comment up exited" not in result.stdout, result.stdout


def _lookup_filter() -> str:
    """The --jq program the post step hands gh, as the step spells it."""
    found = re.search(r"--jq '([^']+)'", _step_named("post the comment")["run"])
    assert found, "the post step no longer passes gh a --jq filter"
    return found.group(1)


_ENGINE_PAGES = {
    "body-null": ([{"id": 1, "body": None}, _MARKED], "2"),
    "body-absent": ([{"id": 1}, _MARKED], "2"),
    "body-number": ([{"id": 1, "body": 5}, _MARKED], "2"),
    "body-empty": ([{"id": 1, "body": ""}, _MARKED], "2"),
    "list-empty": ([], ""),
    "quoted-marker": ([{"id": 1, "body": "> " + _MARK}], ""),
}


@pytest.mark.parametrize("engine", ["jq", "gojq", "jq-1.6"])
@pytest.mark.parametrize("page", list(_ENGINE_PAGES))
def test_the_lookup_filter_holds_under_every_jq_engine(engine, page):
    """gh runs the filter in its built-in gojq; jq 1.6 and 1.7.1 fail `contains`
    on null the same way, so the filter is held to all three. Each engine runs
    where it is on PATH: jq 1.7.1 on both CI runner images, gojq and jq 1.6
    where a machine installed them."""
    binary = shutil.which(engine)
    if binary is None:
        pytest.skip(f"needs {engine} on PATH; the CI runner images carry jq only")
    comments, expected = _ENGINE_PAGES[page]

    done = subprocess.run([binary, "-r", _lookup_filter()], input=json.dumps(comments),
                          capture_output=True, text=True, timeout=HANG_SECONDS)

    assert (done.returncode, done.stdout.strip()) == (0, expected), done.stderr
