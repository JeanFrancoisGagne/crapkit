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
