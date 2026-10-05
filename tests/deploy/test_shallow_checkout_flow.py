"""A depth-1 checkout: the clone actions/checkout makes unless the job sets fetch-depth: 0.

`ratchet report` ages each mark and counts each repayment from the marks file's git history,
`brief` reads a mark's age from the same history, and `worklist` counts churn from the
repo's commits. A depth-1 clone holds one commit. The repo here is the brownfield template
adopted with the README's 60-second start, one mark repaid 40 days after the seed, and a
later commit that adds a test the suite already fails. Its full history reads every open
mark 40 days old and one repayment; a `git clone --depth 1` of it through file:// reads 0
and 0, says so with `shallow: true` and one stderr line, and `ratchet report --enforce`
refuses to judge a debt policy there.

The stderr lines asserted are the ones tests/unit/test_shallow_history_is_named.py pins
in process, read from that file, so the in-process test and this cell assert one spelling
of each line.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from kit import docsnip, gitsurf, repos
from kit.cells import cell

PACKET = "gate-group-04a"
IN_PROCESS = Path(__file__).resolve().parents[1] / "unit" / "test_shallow_history_is_named.py"
PINNED = ("FETCH", "REPORT_LINE", "BRIEF_LINE")
MARKS = "crapkit-ratchet.tsv"
DAY = 86400
REPAID_AFTER, FAILING_AFTER = 40, 45
REPAID = "calc/legacy_4.py"
DECOMPOSED = 'def grade_4(score, attempts, late, bonus):\n    return "D"\n'
KNOWN_FAILURE = '''from calc.grade import grade


def test_a_late_third_attempt_reads_d():
    assert grade(50, 3, True, False) == "D"
'''
DEBT_KEY = "debt_max_age_months = 1"
LANES_DOC = "docs/lanes.md"


# --- the lines the in-process test pins ------------------------------------------

def _value(node: ast.expr, known: dict[str, str]) -> str:
    """A string constant, a name bound above it, or an f-string of the two."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_value(part, known) for part in node.values)
    if isinstance(node, ast.FormattedValue):
        return _value(node.value, known)
    return known[node.id]


def pinned() -> dict[str, str]:
    """FETCH, REPORT_LINE and BRIEF_LINE as the in-process test spells them. That
    module imports crapkit, which the runner's interpreter does not hold, so its
    module-level assignments are read from its source instead of imported."""
    known: dict[str, str] = {}
    for node in ast.parse(IN_PROCESS.read_text(encoding="utf-8")).body:
        names = [target.id for target in getattr(node, "targets", []) if isinstance(target, ast.Name)]
        if len(names) == 1 and names[0] in PINNED:
            known[names[0]] = _value(node.value, known)
    return {name: known[name] for name in PINNED}


def refusal_line() -> str:
    """docs/ratchet.md "The debt policy": what `--enforce` prints in a shallow clone."""
    return docsnip.fence(gitsurf.RATCHET_DOC, "The debt policy", contains="judges mark ages").text.strip()


def churn_line() -> str:
    """README "The GitHub Action": the line `worklist` prints in a shallow clone."""
    return docsnip.fence(gitsurf.README, "The GitHub Action", contains="warning: churn counts").text.strip()


def may_predate_line() -> str:
    """docs/lanes.md: verify's line when no baseline recorded which tests failed."""
    return docsnip.fence(LANES_DOC, "The test count is the second check", contains="may predate").text.strip()


# --- the repo, its history and the depth-1 clone ----------------------------------

def dated(seconds: int) -> dict[str, str]:
    stamp = f"@{seconds} +0000"
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def commit(box, repo: Path, path: str, message: str, env: dict[str, str]) -> None:
    box.run(["git", "add", path], cwd=repo, expect=0)
    box.run(["git", "commit", "-q", "-m", message], cwd=repo, env=env, expect=0)


def history(box, templates) -> Path:
    """The marks seeded by the 60-second start; grade_4 decomposed under its
    ceiling, and the marks file verify rewrote without it committed 40 days
    after the seed; then a failing test committed 5 days later."""
    gitsurf.pip_venv(box)
    repo = repos.checkout(box, "brownfield", cache=templates)
    gitsurf.adopt(box, repo)
    seeded = int(box.run(["git", "log", "-1", "--format=%ct", "--", MARKS], cwd=repo, expect=0).stdout)
    (repo / REPAID).write_text(DECOMPOSED, encoding="utf-8", newline="\n")
    commit(box, repo, REPAID, "decompose grade_4", box.commit_env())
    repaid = box.run(["crapkit", "verify"], cwd=repo, expect=0)
    assert "1 dropped" in repaid.stdout, repaid.stdout
    commit(box, repo, MARKS, "commit the mark verify dropped", dated(seeded + REPAID_AFTER * DAY))
    (repo / "tests" / "test_known_failure.py").write_text(KNOWN_FAILURE, encoding="utf-8", newline="\n")
    commit(box, repo, "tests/test_known_failure.py", "a test the suite already fails",
           dated(seeded + FAILING_AFTER * DAY))
    return repo


def depth_one(box, repo: Path) -> Path:
    """`git clone --depth 1`, the history actions/checkout fetches by default."""
    clone = box.root / "ci"
    box.run(["git", "clone", "-q", "--depth", "1", repo.as_uri(), str(clone)], expect=0)
    shallow = box.run(["git", "rev-parse", "--is-shallow-repository"], cwd=clone, expect=0)
    assert shallow.stdout.strip() == "true"
    return clone


def open_marks(repo: Path) -> list[str]:
    marks = sorted(gitsurf.marks(repo))
    assert marks and not [key for key in marks if key.startswith(REPAID)], marks
    return marks


# --- reading what the commands print -----------------------------------------------

def report(box, root: Path, *flags: str, expect: int = 0):
    """`ratchet report --json`: the step and its parsed stdout."""
    step = box.run(["crapkit", "ratchet", "report", "--json", *flags], cwd=root, expect=expect)
    return step, json.loads(step.stdout)


def ages(body: dict) -> list[int]:
    return [row["age_days"] for row in body["oldest"]]


def burn_down(body: dict) -> tuple:
    return body["shallow"], ages(body), body["dropped_total"], body["dropped_last_30d"]


def with_policy(box, root: Path, *flags: str, expect: int):
    """`ratchet report --enforce` with an age limit of one month in the working
    tree's crapkit.toml; the config is read from disk, the history from git."""
    config = root / "crapkit.toml"
    kept = config.read_text(encoding="utf-8")
    config.write_text(kept.replace("[crapkit]\n", f"[crapkit]\n{DEBT_KEY}\n", 1), encoding="utf-8", newline="\n")
    try:
        return box.run(["crapkit", "ratchet", "report", "--enforce", *flags], cwd=root, expect=expect)
    finally:
        config.write_text(kept, encoding="utf-8", newline="\n")


def brief(box, root: Path, *flags: str):
    return box.run(["crapkit", "brief", "calc/legacy_1.py", "grade_1", *flags], cwd=root, expect=0)


@cell("lin-depth1-checkout", channel="pip venv", harness="git 2.47",
      scenario="fresh: marks committed across dated revisions; file:// --depth 1 clone; ratchet report, "
               "--enforce, brief and worklist in the clone; verify exit 8 may-predate; verify --base refusal",
      use_cases="ratchet report, brief, worklist, verify", os="linux", image="cells", cadence="nightly")
def test_a_depth_one_clone_reads_one_commit_of_the_marks_history_and_says_so(box, templates):
    lines = pinned()
    repo = history(box, templates)
    marks = open_marks(repo)

    full, body = report(box, repo)
    assert burn_down(body) == (False, [REPAID_AFTER] * len(marks), 1, 1), body
    assert lines["REPORT_LINE"] not in full.stderr
    with_policy(box, repo, expect=1)

    clone = depth_one(box, repo)
    shallow, body = report(box, clone)
    assert burn_down(body) == (True, [0] * len(marks), 0, 0), body
    assert shallow.stderr.splitlines() == [lines["REPORT_LINE"]]
    plain = box.run(["crapkit", "ratchet", "report"], cwd=clone, expect=0)
    assert plain.stdout.startswith(f"ratchet burn-down: {len(marks)} open mark(s), 0 repaid"), plain.stdout
    assert plain.stderr.splitlines() == [lines["REPORT_LINE"]]

    refused = with_policy(box, clone, expect=4)
    assert (refused.stdout, refused.stderr.strip()) == ("", refusal_line())
    assert refusal_line().endswith(f"; {lines['FETCH']}")
    error = json.loads(with_policy(box, clone, "--json", expect=4).stdout)["error"]
    assert (error["exit"], error["kind"]) == (4, "git") and error["message"].endswith(lines["FETCH"])


@cell("lin-depth1-checkout", channel="pip venv", harness="git 2.47",
      scenario="fresh: marks committed across dated revisions; file:// --depth 1 clone; ratchet report, "
               "--enforce, brief and worklist in the clone; verify exit 8 may-predate; verify --base refusal",
      use_cases="ratchet report, brief, worklist, verify", os="linux", image="cells", cadence="nightly")
def test_brief_worklist_and_verify_in_a_depth_one_clone(box, templates):
    """The clone's baseline runs with results_artifact taken off the lane, so no
    run recorded which tests fail. Put back, the lane declares it, and the
    failure the suite already carried counts as new: exit 8, with the line that
    says it may predate the change. `verify --base` names a fork point the clone
    does not hold, and its refusal names the fetch."""
    lines = pinned()
    repo = history(box, templates)
    clone = depth_one(box, repo)
    config = clone / "crapkit.toml"
    declared = config.read_text(encoding="utf-8")
    undeclared = re.sub(r"^results_artifact = .*\n", "", declared, flags=re.MULTILINE)
    assert undeclared != declared, declared
    config.write_text(undeclared, encoding="utf-8", newline="\n")
    box.run(["crapkit", "coverage"], cwd=clone, expect=0)

    full = brief(box, repo, "--json")
    assert json.loads(full.stdout)["gate_rule"]["mark_age_days"] == REPAID_AFTER
    assert lines["BRIEF_LINE"] not in full.stderr
    packet = json.loads(brief(box, clone, "--json").stdout)
    assert (packet["shallow"], packet["gate_rule"]["mark_age_days"]) == (True, 0), packet
    assert brief(box, clone).stderr.count(lines["BRIEF_LINE"]) == 1
    ranked = box.run(["crapkit", "worklist", "--json"], cwd=clone, expect=0)
    assert json.loads(ranked.stdout)["shallow"] is True
    assert ranked.stderr.count(churn_line()) == 1 and churn_line().endswith(lines["FETCH"])

    config.write_text(declared, encoding="utf-8", newline="\n")
    verdict = box.run(["crapkit", "verify"], cwd=clone, expect=8)
    assert may_predate_line() in verdict.stderr, verdict.stderr
    fork = box.run(["git", "rev-parse", "HEAD~1"], cwd=repo, expect=0).stdout.strip()
    based = box.run(["crapkit", "verify", "--base", fork], cwd=clone, expect=4)
    said = based.stderr.strip()
    assert said.startswith(f"crapkit: git merge-base {fork} HEAD failed in ") and said.endswith(
        f"; {lines['FETCH']}"), said
