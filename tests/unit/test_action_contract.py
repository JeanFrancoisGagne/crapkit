"""The composite action at the repo root, pinned to the parser and the README.

A workflow file is never imported, so nothing else in this suite would notice a
run step spelling a subcommand the parser dropped or a flag it never had: the
consumer finds out when the job exits 2 on their pull request. These read
`action.yml` the way `test_cli_docs_contract.py` reads README's Subcommands
table, and they read the dogfood job that runs the action on this repo.
"""
import re
from functools import lru_cache
from pathlib import Path

import pytest

from crapkit.cli import build_parser

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
    coverage = {"error": {"exit": 5, "kind": "tool", "message": "every lane failed (1 of 1); the errors are above\n"}, "schema": 1}

    line = _builder().no_verdict_line(coverage, 5)

    assert "(every lane failed (1 of 1); the errors are above)" in line


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
    assert "exit 7: ratchet regression" in line(_failing_verify(), 7)
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
