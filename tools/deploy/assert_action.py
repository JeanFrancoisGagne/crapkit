"""Hold a job log to what crapkit's GitHub Action shows its user.

    python tools/deploy/assert_action.py --log job.log --event push --outcome failure
    python tools/deploy/assert_action.py --job deploy-action --event pull_request --outcome failure

The log is a file (act's output, or a log downloaded from a run), or --job
fetches a finished job's log of this run through `gh api`, which is how a job
that `needs:` the one running the action reads it: a step cannot read its own
job's log. Lines keep their text whichever runner printed them: act's
`[workflow/job]   | ` prefix and the Actions log's timestamp are dropped.

Checks, each printed as `ok` or `FAIL` and exiting 1 when any fails:

  outcome   the action step's outcome (`steps.<id>.outcome`) is --expect-outcome
  gate      --gate on:   "gate is on: exiting with verify's code N" (N is
                         --gate-code when given)
            --gate base: "gate is on and the base run was not made (...)"
            --gate off:  "gate is off: verify's code N is in the comment, ..."
  comment   the body from `<!-- crapkit-action -->` to the end of its step
            holds every --function and --comment-has
  posting   --post none:   "no pull request on this event" (a push)
            --post denied: "posting the crapkit comment exited N", N not 0,
                           after gh's `(HTTP 403)`, and the gate line after it,
                           so the gate still decides the job
            --post posted: "posting the crapkit comment exited 0"

--post defaults to none on a push and denied on a pull_request, the two
events ci.yml's deploy-action job runs on with `pull-requests: read`. The
checks and the comment go to $GITHUB_STEP_SUMMARY when it is set.
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


@dataclass(frozen=True)
class Line:
    text: str
    output: bool


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


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
    return "none" if event == "push" else "denied"


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
    to publish it, so a miss is retried until `wait` runs out."""
    repo, run = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_RUN_ID"]
    until = time.monotonic() + wait
    while True:
        found = job_id(repo, run, name)
        text = _log_of(repo, found) if found is not None else ""
        if text or time.monotonic() >= until:
            return text
        time.sleep(5)


def _log_of(repo: str, job: int) -> str:
    try:
        return gh_api(f"repos/{repo}/actions/jobs/{job}/logs")
    except subprocess.CalledProcessError:
        return ""


# --- output ------------------------------------------------------------------------------

def report(results: list[Check], body: str) -> str:
    rows = [f"{'ok  ' if result.ok else 'FAIL'} {result.name}: {result.detail}" for result in results]
    return "\n".join(rows + ["", "the comment:", body or "(none)"]) + "\n"


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("## crapkit Action checks\n\n```\n" + text + "```\n")


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", type=Path, help="a job log: act's output or a downloaded Actions log")
    source.add_argument("--job", help="fetch this job's log from the current run through gh api")
    parser.add_argument("--wait", type=float, default=120, help="seconds --job waits for the log (default 120)")
    parser.add_argument("--event", required=True, choices=["push", "pull_request"])
    parser.add_argument("--outcome", required=True, help="steps.<id>.outcome of the action step")
    parser.add_argument("--expect-outcome", default="failure")
    parser.add_argument("--gate", default="on", choices=sorted(GATE))
    parser.add_argument("--gate-code", help="the verify code the gate line must name")
    parser.add_argument("--post", choices=["none", "denied", "posted"])
    parser.add_argument("--function", action="append", default=[], help="a function the comment must name")
    parser.add_argument("--comment-has", action="append", default=[], help="text the comment must hold")
    return parser.parse_args(argv)


def read_log(args: argparse.Namespace) -> str:
    return args.log.read_text(encoding="utf-8") if args.log else fetch_log(args.job, args.wait)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    log = lines(read_log(args))
    results = checks(log, args)
    text = report(results, comment(log))
    sys.stdout.write(text)
    write_summary(text)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
