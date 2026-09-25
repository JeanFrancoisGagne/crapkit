"""A shallow clone holds part of the history, and the readers that count its
commits say so.

`ratchet report` ages every mark and counts every repayment from the ratchet
file's git history, and `brief` reads a mark's age from the same history. In a
depth-1 clone, the default `actions/checkout` depth, that history is one
commit: every mark read 0 days old and nothing was ever repaid. `--enforce` then
passed an age limit a full clone fails and failed a repayment quota a full
clone passes, and nothing on stderr said the history was cut.

Now `--enforce` refuses to judge the debt policy in a shallow clone (exit 4,
naming `fetch-depth: 0`), and the report and the packet carry `shallow` and
print one stderr line. The ages and counts keep the values and meanings they
had. A ratchet file renamed with `git mv` starts its history again at the
rename, since the log walks no renames; the report names that commit.

Real git in tmp_path, the clone made through `file://` at depth 1.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from hand_scored_repo import run

from crapkit import mcp_server
from crapkit.gitio import is_shallow
from crapkit.ratchet import KEY_VERSION, RatchetEntry, dump_ratchet, metric_version
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]
OLD, MID, NEW = ("2025-06-01T12:00:00+00:00", "2026-09-01T12:00:00+00:00",
                 "2026-09-20T12:00:00+00:00")
KNOT = "def {n}(a, b):\n    if a:\n        return 1\n    if b:\n        return 2\n    return 0\n"
OLD_DEBT, REPAID = "old_debt( a , b )", "repaid( a , b )"
TOML = """[crapkit]
target = 6
{policy}

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -c pass"
artifact = ".crapkit/cov/py.json"
parser = "coveragepy"
scopes = ["src"]
"""
POLICIES = {"max-age": "debt_max_age_months = 6", "repayment": "repayment_min_per_30d = 1",
            "none": ""}
FETCH = ("this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout "
         "or run git fetch --unshallow")
REPORT_LINE = f"warning: mark ages and repayments read only the commits this clone holds; {FETCH}"
BRIEF_LINE = f"warning: churn counts and mark ages read only the commits this clone holds; {FETCH}"


def git(root: Path, *args: str, date: str | None = None) -> str:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", *args], cwd=root, check=True,
                          capture_output=True, text=True, encoding="utf-8", env=env).stdout


def commit(root: Path, message: str, date: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message, date=date)


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def marks(*names: str) -> str:
    paths = {OLD_DEBT: "src/a.py", REPAID: "src/b.py"}
    entries = [RatchetEntry(paths[n], n, 12.0) for n in names]
    return dump_ratchet(entries, stamp=metric_version(), key_version=KEY_VERSION)


def set_policy(root: Path, policy: str, ratchet_file: str | None = None) -> None:
    """The debt knobs in the working tree's crapkit.toml. An uncommitted edit:
    the config is read from disk, the history from git."""
    extra = POLICIES[policy] + (f'\nratchet_file = "{ratchet_file}"' if ratchet_file else "")
    write(root, "crapkit.toml", TOML.format(policy=extra))


def build(root: Path) -> Path:
    """Two marks entered in 2025, one repaid on 2026-09-01, a later commit on
    2026-09-20 that leaves the marks alone. A full clone reads old_debt as 457
    days old and one repayment in the last 30 days."""
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    write(root, "src/a.py", KNOT.format(n="old_debt"))
    write(root, "src/b.py", KNOT.format(n="repaid"))
    write(root, ".gitignore", ".crapkit/\n")
    set_policy(root, "none")
    commit(root, "init", OLD)
    write(root, "crapkit-ratchet.tsv", marks(OLD_DEBT, REPAID))
    commit(root, "seed marks", OLD)
    write(root, "crapkit-ratchet.tsv", marks(OLD_DEBT))
    commit(root, "repay one mark", MID)
    write(root, "NOTES", "later work\n")
    commit(root, "later", NEW)
    return root


def scored_run(root: Path) -> None:
    """The run brief reads, at this checkout's HEAD: old_debt over its ceiling."""
    row = ScoredRow("src", "src/a.py", OLD_DEBT, 1, 6, 3, 3, 3, 6, 2, 1, 0.0, "untested", 12.0,
                    "add-tests", 0, 1)
    (root / ".crapkit").mkdir(exist_ok=True)
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        store.write_run(commit=git(root, "rev-parse", "HEAD").strip(),
                        tool_versions={"crapkit": "0", "lizard": "0"}, rows=[row],
                        lanes={"py": {"scopes": ["src"]}}, kind="coverage")


@pytest.fixture(scope="module")
def checkouts(tmp_path_factory) -> dict[str, Path]:
    """The full history and its depth-1 clone, each with a scored run."""
    base = tmp_path_factory.mktemp("history")
    full = build(base / "full")
    shallow = base / "shallow"
    git(base, "clone", "-q", "--depth", "1", full.as_uri(), str(shallow))
    assert is_shallow(shallow) and not is_shallow(full)
    for root in (full, shallow):
        scored_run(root)
    return {"full": full, "shallow": shallow}


def report(root: Path, capsys, *flags: str) -> tuple[int, dict, str]:
    code, out, err = run(root, capsys, "ratchet", "report", "--json", *flags)
    return code, json.loads(out), err


# --- the readers ask .git, not a git process ------------------------------------

def test_the_file_read_gives_git_s_own_answer(checkouts, tmp_path):
    """rev-parse answers from the `shallow` file in the common git directory, so
    a linked worktree of a shallow clone is shallow too, a root below the top
    reads the top's answer, and `fetch --unshallow` clears it."""
    from crapkit.gitio import shallow_checkout

    worktree = tmp_path / "linked"
    git(checkouts["shallow"], "worktree", "add", "-q", "--detach", str(worktree))
    deep = tmp_path / "deep"
    git(tmp_path, "clone", "-q", "--depth", "1", checkouts["full"].as_uri(), str(deep))
    readers = {"full": checkouts["full"], "shallow": checkouts["shallow"],
               "linked worktree": worktree, "root below the top": checkouts["shallow"] / "src"}
    got = {name: (shallow_checkout(root), is_shallow(root)) for name, root in readers.items()}
    git(deep, "fetch", "-q", "--unshallow")

    assert got == {"full": (False, False), "shallow": (True, True),
                   "linked worktree": (True, True), "root below the top": (True, True)}
    assert (shallow_checkout(deep), is_shallow(deep)) == (False, False)


def test_no_git_directory_above_the_root_asks_git(tmp_path, monkeypatch):
    from crapkit import gitio

    asked = []
    monkeypatch.setattr(gitio, "_git_dir", lambda root: None)
    monkeypatch.setattr(gitio, "is_shallow", lambda root: asked.append(root) or True)

    assert gitio.shallow_checkout(tmp_path) is True and asked == [tmp_path]


def ages(body: dict) -> list[int]:
    return [e["age_days"] for e in body["oldest"]]


# --- the debt policy is judged only on a history that is whole -----------------

@pytest.mark.parametrize("policy, code, violations", [
    ("max-age", 1, [f"mark {OLD_DEBT} in src/a.py is 457d old (limit 180d)"]),
    ("repayment", 0, []),
])
def test_a_full_clone_judges_the_policy_on_the_whole_history(checkouts, capsys, policy, code,
                                                              violations):
    set_policy(checkouts["full"], policy)

    got, body, err = report(checkouts["full"], capsys, "--enforce")

    assert (got, body["policy_violations"], ages(body)) == (code, violations, [457])
    assert body["shallow"] is False and body["dropped_last_30d"] == 1
    assert "shallow" not in err


@pytest.mark.parametrize("policy", ["max-age", "repayment"])
def test_a_shallow_clone_refuses_to_judge_the_policy(checkouts, capsys, policy):
    """Exit 0 on the age limit and exit 1 on the quota were both verdicts on a
    history the clone did not hold."""
    set_policy(checkouts["shallow"], policy)

    code, out, err = run(checkouts["shallow"], capsys, "ratchet", "report", "--enforce")

    assert code == 4, out + err
    assert out == ""
    assert "crapkit-ratchet.tsv" in err and "set fetch-depth: 0 on the checkout" in err


def test_the_refusal_is_the_json_error_object_under_json(checkouts, capsys):
    set_policy(checkouts["shallow"], "max-age")

    code, out, _ = run(checkouts["shallow"], capsys, "ratchet", "report", "--enforce", "--json")

    error = json.loads(out)["error"]
    assert (code, error["exit"], error["kind"]) == (4, 4, "git")
    assert "fetch-depth: 0" in error["message"]


def test_enforce_with_no_knobs_judges_nothing_and_so_refuses_nothing(checkouts, capsys):
    set_policy(checkouts["shallow"], "none")

    code, body, err = report(checkouts["shallow"], capsys, "--enforce")

    assert (code, body["policy_violations"], body["shallow"]) == (0, None, True)
    assert err.count(REPORT_LINE) == 1


# --- the report and the packet say the history is cut -------------------------

@pytest.mark.parametrize("checkout, shallow, age, repaid", [
    ("full", False, 457, 1),
    ("shallow", True, 0, 0),
])
def test_the_report_carries_shallow_beside_numbers_that_keep_their_meaning(
        checkouts, capsys, checkout, shallow, age, repaid):
    set_policy(checkouts[checkout], "none")

    code, body, err = report(checkouts[checkout], capsys)

    assert (code, body["shallow"], ages(body), body["dropped_last_30d"]) == (0, shallow, [age],
                                                                              repaid)
    assert err.count(REPORT_LINE) == (1 if shallow else 0)


def test_the_plain_report_prints_the_same_line(checkouts, capsys):
    set_policy(checkouts["shallow"], "none")

    code, out, err = run(checkouts["shallow"], capsys, "ratchet", "report")

    assert code == 0 and out.startswith("ratchet burn-down: 1 open mark(s)")
    assert err.splitlines() == [REPORT_LINE]


def test_the_line_is_the_one_gitio_builds_around_the_merge_base_fix():
    from crapkit import gitio

    assert gitio.shallow_warning("mark ages and repayments") == REPORT_LINE
    assert FETCH == gitio._SHALLOW_FIX, "one sentence names the fetch everywhere"


@pytest.mark.parametrize("checkout, shallow, age", [("full", False, 457), ("shallow", True, 0)])
def test_brief_carries_shallow_beside_the_mark_age(checkouts, capsys, checkout, shallow, age):
    set_policy(checkouts[checkout], "none")

    code, out, err = run(checkouts[checkout], capsys, "brief", "src/a.py", "old_debt", "--json")

    packet = json.loads(out)
    assert code == 0
    assert (packet["shallow"], packet["gate_rule"]["mark_age_days"]) == (shallow, age)
    assert err.count(BRIEF_LINE) == (1 if shallow else 0)


def test_a_brief_batch_says_it_once_and_carries_it_on_every_packet(checkouts, capsys):
    set_policy(checkouts["shallow"], "none")

    code, out, err = run(checkouts["shallow"], capsys, "brief", "--batch", "2", "--json")

    batch = json.loads(out)
    assert code == 0 and batch["shallow"] is True
    assert [p["shallow"] for p in batch["packets"]] == [True]
    assert err.count(BRIEF_LINE) == 1


@pytest.mark.parametrize("tool, arguments", [
    ("get_ratchet_report", {}),
    ("get_function_brief", {"path": "src/a.py", "name": "old_debt"}),
    ("list_worklist", {}),
    ("get_next_item", {}),
])
@pytest.mark.parametrize("checkout, shallow", [("full", False), ("shallow", True)])
def test_the_mcp_tools_carry_shallow(checkouts, tool, arguments, checkout, shallow):
    """An MCP client reads stdout alone, so the field is the only place it learns."""
    set_policy(checkouts[checkout], "none")

    result = mcp_server._call_tool(checkouts[checkout], tool, arguments)

    assert result["isError"] is False, result["content"][0]["text"][-400:]
    assert result["structuredContent"]["shallow"] is shallow


# --- a renamed marks file restarts its history at the rename -------------------

@pytest.fixture()
def renamed(checkouts, tmp_path) -> Path:
    root = tmp_path / "renamed"
    shutil.copytree(checkouts["full"], root)
    git(root, "mv", "crapkit-ratchet.tsv", "debt.tsv")
    set_policy(root, "max-age", ratchet_file="debt.tsv")
    commit(root, "rename the marks file", "2026-09-21T12:00:00+00:00")
    return root


def test_a_renamed_ratchet_file_names_the_commit_its_history_starts_at(renamed, capsys):
    """The log walks no renames (a --follow cost 0.6 s of a 1.14 s report), so
    the ages restart at the rename. The report now says where, and the age and
    the policy verdict keep what the visible history gives them."""
    code, body, err = report(renamed, capsys, "--enforce")
    rename = git(renamed, "rev-parse", "HEAD").strip()[:11]

    assert (code, ages(body), body["policy_violations"]) == (0, [0], [])
    assert err.splitlines() == [
        f"warning: debt.tsv's history starts at {rename}, the commit that renamed it from "
        "crapkit-ratchet.tsv, so mark ages and repayments count from there"]


def test_brief_prints_the_rename_line_ratchet_report_prints(renamed, capsys):
    """brief's mark age counts from the rename as well, and said nothing: a
    457-day mark read 0 days old with no line. Both commands read the history
    through one reader and print its one line."""
    _, _, report_err = report(renamed, capsys)

    code, out, err = run(renamed, capsys, "brief", "src/a.py", "old_debt", "--json")

    (line,) = [ln for ln in report_err.splitlines() if "renamed it from" in ln]
    assert code == 0, err
    assert json.loads(out)["gate_rule"]["mark_age_days"] == 0
    assert err.splitlines().count(line) == 1, err


def test_a_ratchet_file_that_was_never_renamed_names_no_rename(checkouts, capsys):
    set_policy(checkouts["full"], "none")

    _, _, err = report(checkouts["full"], capsys)

    assert "renamed" not in err


def test_the_ratchet_page_says_a_rename_restarts_the_clock():
    page = (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8")

    assert "the commit that renamed it from" in page
    assert "restart" in page.split("## The debt policy", 1)[0].split("## Reporting the burn-down")[1]


# --- a mark no commit carries yet is documented at 0d --------------------------

def test_a_mark_no_commit_carries_yet_reads_0d_open_and_uncommitted(tmp_path, capsys):
    """The green variation: a seed on disk before its first commit."""
    root = tmp_path / "fresh"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/a.py", KNOT.format(n="old_debt"))
    set_policy(root, "max-age")
    commit(root, "init", OLD)
    write(root, "crapkit-ratchet.tsv", marks(OLD_DEBT))

    code, body, err = report(root, capsys, "--enforce")

    assert (code, body["open"], body["uncommitted"], ages(body)) == (0, 1, 1, [0])
    assert body["shallow"] is False and err == ""
