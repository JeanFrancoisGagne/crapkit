"""A git question that fails is named, and the reader takes its safe action.

Three readers turned a failed git read into an answer. `ratchet prune` read a
missing anchor commit as "nothing was renamed" and deleted a renamed file's
debt marks as repaid. verify read "this clone does not hold the baseline
commit" as "a rebase or amend rewrote history" and sent the reader after a
fresh baseline. The Action read a failed base diff as an empty change list and
logged "0 changed file(s)". Each case here builds the clone that makes git
fail and asserts what the reader says and does instead.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""

run_cli = cli_runner(env_extra={"CRAPKIT_OVERRIDE_REASON": None})


def git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "gc.auto=0",
                          *args], cwd=repo, check=True, capture_output=True, text=True,
                         encoding="utf-8")
    return res.stdout


def tangled(name: str) -> str:
    """Seven decisions: ccn 8, over the ceiling of 6 in a cc-only scope."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def cc_only_repo(repo: Path, files: dict[str, str]) -> Path:
    """One cc-only Python scope, committed on `main`, crapkit's state ignored."""
    repo.mkdir(parents=True)
    git_init_repo(repo)
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8", newline="\n")
    git_commit_all(repo, "init")
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def clone_with_store(src: Path, dst: Path, *clone_args: str) -> Path:
    """A clone with the source's .crapkit copied in, the way a CI cache restore
    or a store carried over from another checkout arrives."""
    subprocess.run(["git", "clone", "-q", *clone_args, src.as_uri(), str(dst)],
                   check=True, capture_output=True)
    shutil.copytree(src / ".crapkit", dst / ".crapkit")
    return dst


# --- verify: a baseline commit this clone does not hold -------------------------

def test_a_baseline_commit_this_clone_never_fetched_is_named_as_missing(tmp_path: Path):
    """The store came from a checkout that measured a side branch; this clone
    fetched main alone. Nothing rewrote history, so the fix is a fetch, and the
    refusal names the commit and the command that brings it. Same exit 4."""
    origin = cc_only_repo(tmp_path / "origin", {"src/app.py": "def f(n):\n    return n\n"})
    git(origin, "checkout", "-q", "-b", "side")
    (origin / "src" / "side.py").write_text("def g(n):\n    return n\n", encoding="utf-8")
    git_commit_all(origin, "side work")
    assert run_cli(origin, "coverage").returncode == 0
    missing = head(origin)
    git(origin, "checkout", "-q", "main")
    clone = clone_with_store(origin, tmp_path / "clone", "--single-branch", "--branch", "main")

    res = run_cli(clone, "verify", "--no-tighten")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"baseline commit {missing[:11]} is not in this clone" in res.stderr, res.stderr
    assert f"git fetch origin {missing}" in res.stderr, res.stderr
    assert "rewrote history" not in res.stderr, res.stderr
    assert "shallow" not in res.stderr, res.stderr


# --- ratchet prune: renames are followed from a commit this clone holds ------------
#
# prune follows renames with one tree diff from the store's first run to HEAD. When
# that commit is gone (a rebase and gc, a squash-merged branch gc collected, a
# depth-1 CI clone with .crapkit restored) the diff failed, the failure read as "no
# renames", and a renamed file's marks were deleted as repaid debt with no word.

MARKS = "crapkit-ratchet.tsv"


def seeded_then_renamed(tmp_path: Path) -> Path:
    """Run 1 and a seed on src/old.py's ccn 8 function, then a committed
    `git mv src/old.py src/new.py`."""
    repo = cc_only_repo(tmp_path / "repo", {"src/old.py": tangled("tangle"),
                                            "src/keep.py": "def keep(n):\n    return n\n"})
    assert run_cli(repo, "coverage").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    git_commit_all(repo, "seed marks")
    git(repo, "mv", "src/old.py", "src/new.py")
    git_commit_all(repo, "rename old -> new")
    return repo


def marks_on(repo: Path) -> list[str]:
    return [line.split("\t")[0] for line in (repo / MARKS).read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]


def first_run_commit(repo: Path) -> str:
    import json

    return json.loads(run_cli(repo, "runs", "--json").stdout)["runs"][0]["commit"]


def collect(repo: Path) -> None:
    """What 90 days of reflog expiry and a gc do, at once."""
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "-q", "--prune=now")


def rewrite_every_commit(repo: Path, gone: str) -> None:
    """`rebase -f --root`, then the gc that collects what it replaced.

    A replay in the same second as the commits it replays reproduces their ids,
    so the rebase takes a committer date of its own; the check after the gc
    proves the commit really left the clone."""
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "rebase", "-q", "-f",
                    "--root"], cwd=repo, check=True, capture_output=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+0000"})
    collect(repo)
    probe = subprocess.run(["git", "cat-file", "-e", f"{gone}^{{commit}}"], cwd=repo,
                           capture_output=True)
    assert probe.returncode != 0, f"{gone} survived the rewrite"


def test_prune_with_the_anchor_present_follows_the_rename_and_names_it(tmp_path: Path):
    repo = seeded_then_renamed(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "pruned 0, followed 1 rename(s) (src/old.py -> src/new.py) - 1 mark(s)" in res.stdout, \
        res.stdout
    assert marks_on(repo) == ["path", "src/new.py"]


def test_prune_after_a_rewrite_collected_the_anchor_refuses_before_writing(tmp_path: Path):
    """`rebase -f --root` rewrites every commit and gc collects the old ones.
    The refusal names the commit, the file whose fate git cannot tell, the
    fetch, and `ratchet move` for when no remote holds the commit."""
    repo = seeded_then_renamed(tmp_path)
    anchor = first_run_commit(repo)
    rewrite_every_commit(repo, anchor)
    assert run_cli(repo, "coverage").returncode == 0
    before = (repo / MARKS).read_bytes()

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"run 1's commit {anchor[:11]} is not in this clone" in res.stderr, res.stderr
    assert "src/old.py" in res.stderr, res.stderr
    assert f"git fetch origin {anchor}" in res.stderr, res.stderr
    assert "ratchet move OLD NEW" in res.stderr, res.stderr
    assert "nothing was written" in res.stderr, res.stderr
    assert (repo / MARKS).read_bytes() == before


def test_prune_in_a_shallow_clone_with_the_store_restored_names_the_unshallow_fetch(
        tmp_path: Path):
    """The CI shape: actions/checkout at depth 1 and .crapkit from a cache."""
    src = seeded_then_renamed(tmp_path)
    assert run_cli(src, "coverage").returncode == 0
    anchor = first_run_commit(src)
    clone = clone_with_store(src, tmp_path / "shallow", "--depth", "1")
    before = (clone / MARKS).read_bytes()

    res = run_cli(clone, "ratchet", "prune")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"run 1's commit {anchor[:11]} is not in this clone" in res.stderr, res.stderr
    assert "git fetch --unshallow" in res.stderr and "fetch-depth: 0" in res.stderr, res.stderr
    assert (clone / MARKS).read_bytes() == before


def squashed_feature(tmp_path: Path, *, rename_on_feature: bool) -> tuple[Path, str]:
    """Run 1 on a feature branch that is squash-merged, deleted and collected.
    With `rename_on_feature` the seed and the rename happen on the branch;
    without it they happen on main after the merge, behind run 2."""
    repo = cc_only_repo(tmp_path / "repo", {"src/old.py": tangled("tangle")})
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "NOTES.md").write_text("feature\n", encoding="utf-8")
    git_commit_all(repo, "feature start")
    assert run_cli(repo, "coverage").returncode == 0
    anchor = first_run_commit(repo)
    if rename_on_feature:
        seed_and_rename(repo)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feature")
    git_commit_all(repo, "feature (squashed)")
    git(repo, "branch", "-q", "-D", "feature")
    collect(repo)
    assert run_cli(repo, "coverage").returncode == 0
    if not rename_on_feature:
        seed_and_rename(repo)
        assert run_cli(repo, "coverage").returncode == 0
    return repo, anchor


def seed_and_rename(repo: Path) -> None:
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    git_commit_all(repo, "seed marks")
    git(repo, "mv", "src/old.py", "src/new.py")
    git_commit_all(repo, "rename")


def test_prune_after_gc_collected_a_squashed_branchs_first_run_refuses(tmp_path: Path):
    """Run 2 on main is held, but the rename happened before it on the branch,
    so the diff from run 2 cannot see it either."""
    repo, anchor = squashed_feature(tmp_path, rename_on_feature=True)
    before = (repo / MARKS).read_bytes()

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"run 1's commit {anchor[:11]} is not in this clone" in res.stderr, res.stderr
    assert (repo / MARKS).read_bytes() == before


def test_a_rename_after_the_oldest_held_run_is_followed_when_the_anchor_is_gone(tmp_path: Path):
    """Run 1's commit is gone and run 2's is held. A rename after run 2 shows
    in the diff from run 2, so prune follows it and says which run the renames
    were read from."""
    repo, anchor = squashed_feature(tmp_path, rename_on_feature=False)

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "followed 1 rename(s) (src/old.py -> src/new.py)" in res.stdout, res.stdout
    assert (f"run 1's commit {anchor[:11]} is not in this clone, so renames were followed "
            "from run 2") in res.stderr, res.stderr
    assert marks_on(repo) == ["path", "src/new.py"]


def test_a_missing_anchor_refuses_nothing_when_no_marked_file_left_the_checkout(tmp_path: Path):
    """A dropped mark whose file is still there cannot be a rename, so a gone
    anchor refuses nothing; the line still says the commit is missing."""
    repo = cc_only_repo(tmp_path / "repo",
                        {"src/app.py": tangled("tangle") + "\n\n" + tangled("gone")})
    assert run_cli(repo, "coverage").returncode == 0
    anchor = first_run_commit(repo)
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    git_commit_all(repo, "seed marks")
    (repo / "src" / "app.py").write_text(tangled("tangle"), encoding="utf-8", newline="\n")
    git_commit_all(repo, "drop gone()")
    rewrite_every_commit(repo, anchor)
    assert run_cli(repo, "coverage").returncode == 0

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "pruned 1, followed 0 rename(s)" in res.stdout, res.stdout
    assert f"run 1's commit {anchor[:11]} is not in this clone" in res.stderr, res.stderr


def test_a_clone_holding_no_runs_commit_says_so_when_nothing_needed_a_rename(tmp_path: Path):
    """Two runs at run 1's commit, a later commit with no run, and a depth-1
    clone of it: no run's commit is in the clone. No marked file left, so prune
    goes on and the note says no rename was followed."""
    repo = cc_only_repo(tmp_path / "repo",
                        {"src/app.py": tangled("tangle") + "\n\n" + tangled("gone")})
    assert run_cli(repo, "coverage").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    (repo / "src" / "app.py").write_text(tangled("tangle"), encoding="utf-8", newline="\n")
    assert run_cli(repo, "coverage").returncode == 0
    anchor = first_run_commit(repo)
    git_commit_all(repo, "seed marks, drop gone()")
    clone = clone_with_store(repo, tmp_path / "shallow", "--depth", "1")

    res = run_cli(clone, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "pruned 1, followed 0 rename(s)" in res.stdout, res.stdout
    assert (f"run 1's commit {anchor[:11]} is not in this clone, and no run's commit is, "
            "so no rename was followed") in res.stderr, res.stderr


def test_a_store_outside_any_git_work_tree_prunes_and_says_no_rename_was_followed(
        tmp_path: Path):
    """A checkout copied out without its .git holds no history a file could
    have been renamed in. prune drops what the run lacks and says why it
    followed nothing."""
    repo = cc_only_repo(tmp_path / "repo", {"src/old.py": tangled("tangle"),
                                            "src/keep.py": "def keep(n):\n    return n\n"})
    assert run_cli(repo, "coverage").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    (repo / "src" / "old.py").unlink()
    git_commit_all(repo, "old.py left")
    assert run_cli(repo, "coverage").returncode == 0
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))

    res = run_cli(copy, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert "pruned 1, followed 0 rename(s)" in res.stdout, res.stdout
    assert "is not a git work tree, so prune followed no renames" in res.stderr, res.stderr


def corrupt(repo: Path, sha: str) -> None:
    """Overwrite a commit's loose object with bytes zlib refuses: git holds the
    commit and cannot read it."""
    loose = repo / ".git" / "objects" / sha[:2] / sha[2:]
    loose.chmod(0o644)
    loose.write_bytes(b"garbage")


def test_prune_names_gits_error_when_git_cannot_read_run_1s_commit(tmp_path: Path):
    """The commit is in the clone and its object is corrupt. has_commit read
    git's failure as "not held", so prune said the commit was missing and sent
    the reader after a fetch that could not help."""
    repo = seeded_then_renamed(tmp_path)
    assert run_cli(repo, "coverage").returncode == 0
    anchor = first_run_commit(repo)
    corrupt(repo, anchor)
    before = (repo / MARKS).read_bytes()

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 4, res.stdout + res.stderr
    assert f"git cannot say whether run 1's commit {anchor[:11]} is in this clone" in res.stderr, \
        res.stderr
    assert "corrupt" in res.stderr, res.stderr
    assert "fix what git reports, then run `" in res.stderr, res.stderr
    assert "nothing was written" in res.stderr, res.stderr
    assert "is not in this clone" not in res.stderr and "git fetch" not in res.stderr, res.stderr
    assert (repo / MARKS).read_bytes() == before


def test_explain_history_outside_a_git_work_tree_names_gits_error(tmp_path: Path):
    """has_commit read "not a git repository" as "no commit yet", so explain
    said the function held only uncommitted lines."""
    repo = cc_only_repo(tmp_path / "repo", {"src/app.py": tangled("tangle")})
    assert run_cli(repo, "coverage").returncode == 0
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))

    res = run_cli(copy, "explain", "src/app.py", "tangle", "--history", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    fn = json.loads(res.stdout)["functions"][0]
    assert fn["commits"] is None, fn
    assert fn["commits_note"].startswith("git cannot read the history of src/app.py:1-"), fn
    assert "not a git repository" in fn["commits_note"], fn
    assert "uncommitted" not in fn["commits_note"], fn


def test_the_prune_line_names_three_renames_and_counts_the_rest(tmp_path: Path):
    repo = cc_only_repo(tmp_path / "repo", {f"src/m{i}.py": tangled(f"t{i}") for i in range(4)})
    assert run_cli(repo, "coverage").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    git_commit_all(repo, "seed marks")
    for i in range(4):
        git(repo, "mv", f"src/m{i}.py", f"src/n{i}.py")
    git_commit_all(repo, "rename all four")
    assert run_cli(repo, "coverage").returncode == 0

    res = run_cli(repo, "ratchet", "prune")

    assert res.returncode == 0, res.stdout + res.stderr
    assert ("followed 4 rename(s) (src/m0.py -> src/n0.py, src/m1.py -> src/n1.py, "
            "src/m2.py -> src/n2.py and 1 more)") in res.stdout, res.stdout


# --- the Action: a base diff git refuses -------------------------------------------
#
# "the changed files" step ran `git diff "$BASE_SHA...HEAD" || : > changed`, so on
# the actions/checkout default (a depth-1 clone, which lacks the base commit) git's
# refusal became an empty list, the log said "0 changed file(s)" and the comment
# ranked the whole repository with no reason given. These run the step's own body
# under bash in a real clone and hand what it leaves to the comment builder.

ROOT = Path(__file__).resolve().parents[2]


def _git_bash() -> str | None:
    """Git for Windows' own bash, beside the git on PATH; None anywhere else."""
    git_exe = shutil.which("git")
    candidate = Path(git_exe).parent.parent / "bin" / "bash.exe" if git_exe else None
    return str(candidate) if candidate and candidate.is_file() else None


def _bash() -> str:
    """The bash a runner's `shell: bash` step runs under. On Windows the `bash`
    on PATH can be the WSL launcher under System32, which cannot read these
    files, so Git for Windows' bash comes first."""
    for found in (_git_bash(), shutil.which("bash")):
        if found and "system32" not in found.lower():
            return found
    pytest.skip("no bash to run the step under")


def _action_step(name: str) -> str:
    yaml = pytest.importorskip("yaml")
    steps = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))["runs"]["steps"]
    return next(step["run"] for step in steps if step.get("name") == name)


def pr_clone(tmp_path: Path, changed: int, *clone_args: str) -> tuple[Path, str]:
    """A pull request branch that edits `changed` files, cloned the way
    actions/checkout clones it; returns the clone and the base commit. With
    none changed the branch sits on its base."""
    src = cc_only_repo(tmp_path / "src", {f"m{i}.py": f"X = {i}\n" for i in range(5)})
    base = head(src)
    git(src, "checkout", "-q", "-b", "pr")
    for i in range(changed):
        (src / f"m{i}.py").write_text(f"X = {i + 10}\n", encoding="utf-8")
    if changed:
        git_commit_all(src, "pr change")
    dst = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", *clone_args, "--branch", "pr", src.as_uri(), str(dst)],
                   check=True, capture_output=True)
    return dst, base


def run_changed_step(clone: Path, base_sha: str) -> tuple[str, Path]:
    """The step's body under `bash --noprofile --norc -eo pipefail`, which is
    what `shell: bash` means on a runner. Returns its log and the state dir.

    The runner's `python` is the one the action installed crapkit into, and
    GITHUB_ACTION_PATH is the action's own checkout, which here is this one."""
    state = clone.parent / "state"
    state.mkdir()
    script = clone.parent / "changed-step.sh"
    script.write_text(_action_step("the changed files"), encoding="utf-8", newline="\n")
    env = {**os.environ, "CRAPKIT_STATE": state.as_posix(), "BASE_SHA": base_sha,
           "GITHUB_ACTION_PATH": ROOT.as_posix(),
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    res = subprocess.run([_bash(), "--noprofile", "--norc", "-eo", "pipefail", script.as_posix()],
                         cwd=clone, capture_output=True, text=True, encoding="utf-8", env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    return (res.stdout + res.stderr).strip(), state


def comment_from(state: Path) -> str:
    worklist = state / "worklist.json"
    worklist.write_text(json.dumps({"active": [
        {"path": "m0.py", "function": "a", "start": 1, "ccn": 7, "risk": 7.0},
        {"path": "m4.py", "function": "b", "start": 1, "ccn": 9, "risk": 9.0}]}), encoding="utf-8")
    out = state / "comment.md"
    subprocess.run([sys.executable, str(ROOT / "tools" / "action" / "comment.py"),
                    "--worklist", str(worklist),
                    "--changed-z", str(state / "crapkit-changed.txt"),
                    "--changed-error", str(state / "crapkit-changed.error"),
                    "--out", str(out)], check=True, capture_output=True)
    return out.read_text(encoding="utf-8")


def test_a_depth_one_checkout_logs_gits_error_and_the_comment_says_why_it_ranks_everything(
        tmp_path: Path):
    clone, base = pr_clone(tmp_path, 1, "--depth", "1")

    log, state = run_changed_step(clone, base)
    comment = comment_from(state)

    assert "0 changed file(s)" not in log, log
    assert f"the base diff {base[:11]}...HEAD failed, so the comment ranks the whole repository" in log, log
    assert "fatal:" in log, log
    assert (state / "crapkit-changed.txt").read_bytes() == b""
    assert "### Worklist: the whole repository, top 2" in comment, comment
    assert "The base diff failed (`fatal:" in comment, comment


def test_a_full_history_checkout_names_the_changed_file_and_leaves_no_error(tmp_path: Path):
    clone, base = pr_clone(tmp_path, 1)

    log, state = run_changed_step(clone, base)
    comment = comment_from(state)

    assert log == "1 changed file(s): m0.py", log
    assert (state / "crapkit-changed.error").read_text(encoding="utf-8") == ""
    assert "### Worklist: 1 changed file" in comment, comment
    assert "The base diff failed" not in comment, comment


@pytest.mark.parametrize("changed, logged", [
    (0, "0 changed file(s)"),
    (3, "3 changed file(s): m0.py, m1.py, m2.py"),
    (4, "4 changed file(s): m0.py, m1.py, m2.py and 1 more"),
    (5, "5 changed file(s): m0.py, m1.py, m2.py and 2 more"),
])
def test_the_step_log_names_three_changed_files_and_counts_the_rest(tmp_path: Path, changed, logged):
    """The line comes from the comment builder, under the runner's python."""
    clone, base = pr_clone(tmp_path, changed)

    log, _ = run_changed_step(clone, base)

    assert log == logged, log


def test_a_push_names_no_base_commit_instead_of_zero_changed_files(tmp_path: Path):
    clone, _ = pr_clone(tmp_path, 1)

    log, state = run_changed_step(clone, "")

    assert log == "no base commit on this event: the comment ranks the whole repository", log
    assert "The base diff failed" not in comment_from(state)
