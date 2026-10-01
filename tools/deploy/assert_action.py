"""Hold a job log to what crapkit's GitHub Action shows its user.

    python crapkit/tools/deploy/assert_action.py --cell gha-action-consumer --outcome failure
    python tools/deploy/assert_action.py --log job.log --event push --outcome failure
    python tools/deploy/assert_action.py --job deploy-action --event pull_request --outcome failure

--cell names a GitHub-runner cell and brings its checks (CELLS below); flags
given after it add to them or replace them. --event defaults to the run's
$GITHUB_EVENT_NAME.

What is read:

  --log FILE   a job log: act's output, or a log downloaded from a run
  --job NAME   that finished job's log of this run, through `gh api`: how a
               job that `needs:` the one running the action reads it, with
               `actions: read`
  neither      the action's own state directory, the newest
               $RUNNER_TEMP/crapkit.* (or --state DIR): a later step of the
               job that ran the action reads the comment and verify's code
               there, since a step cannot read its own job's log

Lines keep their text whichever runner printed them: act's
`[workflow/job]   | ` prefix and the Actions log's timestamp are dropped.

Checks, each printed as `ok`, `FAIL` or `skip`, exiting 1 when any fails:

  outcome   the action step's outcome (`steps.<id>.outcome`) is --expect-outcome
  gate      --gate on:   "gate is on: exiting with verify's code N" (N is
                         --gate-code when given)
            --gate base: "gate is on and the base run was not made (...)"
            --gate off:  "gate is off: verify's code N is in the comment, ..."
            from a state directory: verify's code in crapkit-verify.exit
  comment   the body from `<!-- crapkit-action -->` to the end of its step
            (or crapkit-comment.md) holds every --function and --comment-has
  posting   --post none:   "no pull request on this event" (any event but a
                           pull_request)
            --post denied: "posting the crapkit comment exited N", N not 0,
                           after gh's `(HTTP 403)`, and the gate line after it,
                           so the gate still decides the job
            --post posted: "posting the crapkit comment exited 0"
            from a state directory: skipped, the line is only in the log

--post defaults to denied on a pull_request, where ci.yml's deploy-action
job holds `pull-requests: read`, and to none on every other event. The checks
and the comment go to $GITHUB_STEP_SUMMARY when it is set.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from consumer import BREACH

MARKER = "<!-- crapkit-action -->"
ACT_LINE = re.compile(r"^\[[^\]]*\]\s+\|\s?(?P<text>.*)$")
ACT_MARK = re.compile(r"^\[[^\]]*\]\s")
STAMPED = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z ?(?P<text>.*)$")
GATE = {
    "on": r"gate is on: exiting with verify's code (?P<code>\d+)",
    "base": r"gate is on and the base run was not made \((?P<why>.*)\): verify judged no changed function",
    "off": r"gate is off: verify's code (?P<code>\d+) is in the comment, this check stays green",
}
POSTED = re.compile(r"posting the crapkit comment exited (?P<code>\d+)(?P<why>.*)")
NO_PULL_REQUEST = "no pull request on this event: the comment above was not posted"
FORBIDDEN = "(HTTP 403)"
STAMP_REFUSAL = "ratchet marks were recorded under [crapkit-analysis="
# The checks of each GitHub-runner cell, on consumer.py's repository with
# `gate: "true"` and `delta: "false"`. A push, or delta off on a pull request,
# makes no base run, so the marked function's regression is verify's code 7.
CELLS = {
    "gha-action-consumer": ["--gate-code", "7", "--function", BREACH],
    "gha-action-readonly-token": ["--gate-code", "7", "--function", BREACH],
    "gha-action-windows": ["--gate-code", "7", "--function", BREACH],
    "published-action-tag": ["--gate-code", "7", "--function", BREACH],
    # No container_ok on the lane: crapkit's guard refuses it inside the
    # job's container and coverage's 5 stands in for verify's code.
    "gha-action-container-job": ["--gate-code", "5", "--comment-has", "**no verdict: `crapkit coverage` exited 5"],
    # A consumer seeded under 0.7.6 (analysis 10), scored by the moved pin over
    # the store the v0.7.6 step left: the stamp refusal, naming the re-seed.
    "gha-action-tag-upgrade": ["--gate-code", "3", "--comment-has", f"{STAMP_REFUSAL}10 ",
                               "--comment-has", "`crapkit ratchet seed"],
}
STATE_FILES = "crapkit.*/crapkit-verify.exit"


@dataclass(frozen=True)
class Line:
    text: str
    output: bool


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    skipped: bool = False


# --- reading a log -----------------------------------------------------------------

def line(raw: str) -> Line:
    """One log line as its text, and whether a step printed it (output) or
    the runner did (a step header, a group marker)."""
    act = ACT_LINE.match(raw)
    if act:
        return Line(act["text"], True)
    if ACT_MARK.match(raw):
        return Line(raw, False)
    stamped = STAMPED.match(raw)
    text = stamped["text"] if stamped else raw
    return Line(text, not text.startswith("##["))


def lines(log: str) -> list[Line]:
    return [line(raw) for raw in log.lstrip("\ufeff").splitlines()]


def find(log: list[Line], pattern: str) -> tuple[int, re.Match] | None:
    """The first output line matching `pattern`, with its index."""
    compiled = re.compile(pattern)
    for index, entry in enumerate(log):
        match = compiled.search(entry.text) if entry.output else None
        if match:
            return index, match
    return None


def marker(log: list[Line]) -> int | None:
    for index, entry in enumerate(log):
        if entry.output and entry.text == MARKER:
            return index
    return None


def comment(log: list[Line]) -> str:
    """The comment body the build step printed: from the marker to its step's end."""
    start = marker(log)
    if start is None:
        return ""
    body = itertools.takewhile(lambda entry: entry.output, log[start:])
    return "\n".join(entry.text for entry in body).strip()


# --- the checks ----------------------------------------------------------------------

def check_outcome(actual: str, expected: str) -> Check:
    return Check("outcome", actual == expected, f"the action step's outcome is {actual!r}, expected {expected!r}")


def check_gate(log: list[Line], gate: str, code: str | None) -> Check:
    found = find(log, GATE[gate])
    if found is None:
        return Check("gate", False, f"no line matches {GATE[gate]!r}")
    printed = found[1].groupdict().get("code")
    ok = code is None or printed == code
    return Check("gate", ok, f"{found[1].group(0)!r}" + ("" if ok else f", expected code {code}"))


def check_comment(body: str, wanted: list[str]) -> Check:
    missing = [text for text in wanted if text not in body]
    if not body:
        return Check("comment", False, f"no {MARKER} line in the log")
    return Check("comment", not missing, f"missing {missing}" if missing else f"{len(body.splitlines())} lines")


def check_no_post(log: list[Line]) -> Check:
    return Check("posting", find(log, re.escape(NO_PULL_REQUEST)) is not None, f"expects {NO_PULL_REQUEST!r}")


def check_posted(log: list[Line]) -> Check:
    found = find(log, POSTED.pattern)
    ok = found is not None and found[1]["code"] == "0"
    return Check("posting", ok, found[1].group(0) if found else "no 'posting the crapkit comment exited' line")


def check_denied(log: list[Line], gate: str) -> Check:
    """A refused post, with gh's 403 before it and the gate line after it."""
    posted, forbidden, decided = find(log, POSTED.pattern), find(log, re.escape(FORBIDDEN)), find(log, GATE[gate])
    if posted is None or posted[1]["code"] == "0":
        return Check("posting", False, "no refused 'posting the crapkit comment exited N' line")
    order = forbidden is not None and decided is not None and forbidden[0] < posted[0] < decided[0]
    return Check("posting", order, f"{posted[1].group(0)!r}; 403 before it and the gate line after it: {order}")


def check_post(log: list[Line], post: str, gate: str) -> Check:
    if post == "none":
        return check_no_post(log)
    return check_posted(log) if post == "posted" else check_denied(log, gate)


def checks(log: list[Line], args: argparse.Namespace) -> list[Check]:
    return [check_outcome(args.outcome, args.expect_outcome), check_gate(log, args.gate, args.gate_code),
            check_comment(comment(log), args.function + args.comment_has),
            check_post(log, args.post or default_post(args.event), args.gate)]


def default_post(event: str) -> str:
    return "denied" if event == "pull_request" else "none"


# --- the action's state directory, from a later step of the same job ---------------

LOG_ONLY = Check("posting", True, "not read: the gate and posting lines are only in the job's log, which "
                 "`--job <this job>` reads from a job that needs it", skipped=True)


def newest_state(temp: Path) -> Path | None:
    """The newest crapkit.* directory under RUNNER_TEMP: the last action step's."""
    found = [path.parent for path in temp.glob(STATE_FILES)]
    return max(found, key=lambda path: (path / "crapkit-verify.exit").stat().st_mtime, default=None)


def check_code(code: str, expected: str | None) -> Check:
    ok = expected is None or code == expected
    return Check("gate", ok, f"verify's code in crapkit-verify.exit is {code}" + ("" if ok else f", expected {expected}"))


def state_checks(state: Path, args: argparse.Namespace) -> tuple[list[Check], str]:
    code = (state / "crapkit-verify.exit").read_text(encoding="utf-8").strip()
    comment_file = state / "crapkit-comment.md"
    body = comment_file.read_text(encoding="utf-8").strip() if comment_file.exists() else ""
    return [check_outcome(args.outcome, args.expect_outcome), check_code(code, args.gate_code),
            check_comment(body, args.function + args.comment_has), LOG_ONLY], body


def gather(args: argparse.Namespace) -> tuple[list[Check], str]:
    """The checks and the comment, from a log or from the action's state directory."""
    if args.log or args.job:
        log = lines(read_log(args))
        return checks(log, args), comment(log)
    state = args.state or newest_state(Path(os.environ.get("RUNNER_TEMP", ".")))
    if state is None:
        return [Check("state", False, f"no {STATE_FILES} under $RUNNER_TEMP: the action never reached its "
                                      "verdict step")], ""
    return state_checks(state, args)


# --- fetching a job's log -------------------------------------------------------------

def gh_api(path: str) -> str:
    """`gh api PATH`'s output. gh is looked up the way a shell would, so a gh.cmd
    on Windows is found where CreateProcess alone looks for gh.exe."""
    gh = shutil.which("gh") or "gh"
    return subprocess.run([gh, "api", path], check=True, capture_output=True, encoding="utf-8").stdout


def job_id(repo: str, run: str, name: str) -> int | None:
    jobs = json.loads(gh_api(f"repos/{repo}/actions/runs/{run}/jobs?per_page=100"))["jobs"]
    done = [job["id"] for job in jobs if job["name"] == name and job.get("status") == "completed"]
    return done[0] if done else None


def fetch_log(name: str, wait: float) -> str:
    """The finished job's log. A job that just completed can take a few seconds
    to publish it, so a miss is retried until `wait` runs out. Then the last
    reason ends the run: an empty log failed every line check with no word of
    why, on each of 0.8.1's first two pushes to main."""
    repo, run = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    until = time.monotonic() + wait
    while True:
        found = job_id(repo, run, name)
        text, why = _log_of(repo, found) if found is not None else ("", f"run {run} holds no completed job {name!r}")
        if text:
            return text
        if time.monotonic() >= until:
            raise SystemExit(f"assert_action: {name}'s log could not be read: {why}")
        time.sleep(5)


def _log_of(repo: str, job: int) -> tuple[str, str]:
    """The log, or "" and why there is none: gh's own error, or an empty answer."""
    try:
        text = gh_api(f"repos/{repo}/actions/jobs/{job}/logs")
    except subprocess.CalledProcessError as failed:
        return "", (failed.stderr or "").strip() or f"gh exited {failed.returncode}"
    return text, "" if text else f"gh answered job {job}'s log with nothing"


# --- output ------------------------------------------------------------------------------

def mark(result: Check) -> str:
    return "skip" if result.skipped else ("ok  " if result.ok else "FAIL")


def report(results: list[Check], body: str) -> str:
    rows = [f"{mark(result)} {result.name}: {result.detail}" for result in results]
    return "\n".join(rows + ["", "the comment:", body or "(none)"]) + "\n"


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("## crapkit Action checks\n\n```\n" + text + "```\n")


def parser_for() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cell", choices=sorted(CELLS), help="a GitHub-runner cell: its checks from CELLS")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--log", type=Path, help="a job log: act's output or a downloaded Actions log")
    source.add_argument("--job", help="fetch this job's log from the current run through gh api")
    source.add_argument("--state", type=Path, help="the action's state directory (default: newest under $RUNNER_TEMP)")
    parser.add_argument("--wait", type=float, default=120, help="seconds --job waits for the log (default 120)")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME"),
                        help="the event the job ran on (default: $GITHUB_EVENT_NAME)")
    parser.add_argument("--outcome", required=True, help="steps.<id>.outcome of the action step")
    parser.add_argument("--expect-outcome", default="failure")
    parser.add_argument("--gate", default="on", choices=sorted(GATE))
    parser.add_argument("--gate-code", help="the verify code the gate line must name")
    parser.add_argument("--post", choices=["none", "denied", "posted"])
    parser.add_argument("--function", action="append", default=[], help="a function the comment must name")
    parser.add_argument("--comment-has", action="append", default=[], help="text the comment must hold")
    return parser


def parse(argv: list[str] | None) -> argparse.Namespace:
    """The arguments, with --cell's checks in front of the ones given."""
    parser, given = parser_for(), list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(given)
    args = parser.parse_args(CELLS[args.cell] + given) if args.cell else args
    if not args.event:
        parser.error("--event is needed outside a GitHub Actions job ($GITHUB_EVENT_NAME is not set)")
    return args


def read_log(args: argparse.Namespace) -> str:
    return args.log.read_text(encoding="utf-8") if args.log else fetch_log(args.job, args.wait)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    # A Windows runner's console encoding cannot print every character a comment holds.
    getattr(sys.stdout, "reconfigure", lambda **_: None)(errors="replace")
    results, body = gather(args)
    text = report(results, body)
    sys.stdout.write(text)
    write_summary(text)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
