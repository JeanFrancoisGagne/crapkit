"""The change-control tool around its rules: the command line, the first lock, the
test counts, the pre-push hook and the goldens it regenerates.

The rules themselves are judged in test_change_control_rules.py; these tests
check that each command reaches them, writes what it says, and refuses with the
command that fixes the refusal.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import sys
import time

import pytest

import hang_guard
from accuracy.change_control import cc_seeds as seeds
from accuracy.kit import corpus_run, repos

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "tools" / "accuracy"
if str(TOOLS) not in sys.path:
    sys.path.append(str(TOOLS))
import change_control as cc  # noqa: E402

BASE = seeds.base()


def _files(top: Path) -> dict[str, str]:
    return {path: (top / path).read_text(encoding="utf-8") for path in cc.DirTree(top).paths()}


# --- the check on the command line --------------------------------------------------------------

@pytest.mark.process
def test_the_check_prints_its_report_and_exits_1_on_a_broken_rule(make_repo, capsys):
    top = seeds.seeded(make_repo, BASE, seeds.module_changed(BASE))

    code = cc.main(["--base", "HEAD~1", "--repo", str(top)])

    out = capsys.readouterr().out
    assert code == 1
    assert out.startswith("change control: FAIL, 1 problem(s) (HEAD~1 at ")
    assert "B6 src/crapkit/score.py holds CRAP score and changed with no declared change" in out


@pytest.mark.process
def test_a_moved_row_map_from_another_tool_joins_the_moved_cells(make_repo, tmp_path, capsys):
    """wheel_diff's map names a CRAP cell the committed goldens do not show; the
    diff declares nothing, so the calc is undeclared."""
    top = seeds.seeded(make_repo, BASE, seeds.replace(BASE, "CONTEXT.md", "# Context", "# Ctx"))
    moved = tmp_path / "moved.tsv"
    moved.write_bytes(cc.table_bytes(cc.MOVED_COLUMNS, [
        (seeds.SCORED, "src/a.py", "f2", "crap", "1.0", "1.5", "", "", "")]))

    code = cc.main(["--base", "HEAD~1", "--repo", str(top), "--moved", str(moved)])

    assert code == 1
    assert "B10 moved calc not declared: CRAP score" in capsys.readouterr().out
    assert cc.read_moved(None) == []


@pytest.mark.process
def test_a_base_that_names_no_commit_is_refused_with_the_fix(make_repo, capsys):
    top = seeds.seeded(make_repo, BASE)

    code = cc.main(["--base", "refs/accuracy/green", "--repo", str(top)])

    assert code == 1
    assert ("change control: refs/accuracy/green names no commit here; fetch it, or pass --base "
            "with a ref that exists") in capsys.readouterr().err


@pytest.mark.process
def test_a_failed_git_call_names_its_command(tmp_path):
    with pytest.raises(cc.ChangeControlError, match="^git cat-file -t nope exited 128: "):
        cc.git(tmp_path, "cat-file", "-t", "nope")


# --- declare on the command line -----------------------------------------------------------------

@pytest.mark.process
def test_declare_on_the_command_line_reads_a_calc_name_that_holds_commas(make_repo, capsys):
    base = seeds.base(seeds.ccn8())
    top = seeds.seeded(make_repo, base)
    seeds.write(top, base, seeds.bump({**base, seeds.SCORED: BASE[seeds.SCORED],
                                       seeds.INVENTORY: BASE[seeds.INVENTORY]}, "12"))

    code = cc.main(["declare", "C3", "--kind", "fix", "--calcs", "ccn_std, ccn_mod and gated ccn",
                    "--reason", "parse's ccn moved", "--no-regenerate", "--repo", str(top)])

    assert code == 0
    assert capsys.readouterr().out.startswith(
        "declared C3 (fix: ccn_std, ccn_mod and gated ccn): 2 locked files relocked")
    assert cc.changes_of(cc.DirTree(top))["C3"]["calcs"] == "ccn_std, ccn_mod and gated ccn"


# --- the first lock and the test counts ----------------------------------------------------------

@pytest.mark.process
def test_the_first_lock_covers_every_lockable_file_and_its_commit_passes(make_repo, capsys):
    """kit-close's commit: `lock --initial` on a tree whose tables hold headers only."""
    before = seeds.uninitialized()
    top = seeds.seeded(make_repo, before)
    assert [problem.rule for problem in cc.in_tree(cc.DirTree(top), cc.running(
        cc.DirTree(top)))] == ["T2"]

    code = cc.main(["lock", "--initial", "--repo", str(top)])

    after = {**before, **_files(top)}
    assert (code, capsys.readouterr().out.strip()) == (
        0, "locked 5 files under C1; metric-digests and test counts written")
    assert cc.lock_of(cc.DirTree(top)) == {
        path: (hashlib.sha256(after[path].encode()).hexdigest(), "C1") for path in seeds.LOCKED}
    assert after[seeds.COUNTS] == "packet\ttests\nscore_model\t3\n"
    seeds.commit(top, before, after, 1)
    assert cc.check(top, "HEAD~1")[0] == 0
    assert cc.main(["lock", "--initial", "--repo", str(top)]) == 1
    assert "the lock is already initialized" in capsys.readouterr().err


@pytest.mark.process
def test_counts_says_which_packet_moved_and_write_records_it(make_repo, capsys):
    top = seeds.seeded(make_repo, BASE)
    test_file = top / seeds.SEED_TEST
    test_file.write_text(test_file.read_text() + "\n\ndef test_more():\n    assert 2 == 2\n")

    stale = cc.main(["counts", "--repo", str(top)])
    written = cc.main(["counts", "--write", "--repo", str(top)])

    out = capsys.readouterr().out
    assert (stale, written) == (1, 0)
    assert f"T6 packet score_model collects 4 tests, {cc.COUNTS} says 3" in out
    assert "fix: python tools/accuracy/change_control.py counts --write" in out
    assert (top / cc.COUNTS).read_text() == "packet\ttests\nscore_model\t4\n"


@pytest.mark.process
def test_a_collection_error_stops_the_count(make_repo):
    top = seeds.seeded(make_repo, {**BASE, seeds.SEED_TEST: "def broken(:\n"})

    with pytest.raises(cc.ChangeControlError, match="collecting tests/accuracy failed"):
        cc.collect_counts(top)


def test_count_problems_name_each_packet_that_differs():
    committed = b"packet\ttests\nkit\t10\nscore_model\t3\n"

    problems = cc.count_problems(committed, {"kit": 10, "score_model": 4, "verdict_model": 2})

    assert [problem.text for problem in problems] == [
        f"packet score_model collects 4 tests, {cc.COUNTS} says 3",
        f"packet verdict_model collects 2 tests, {cc.COUNTS} says 0"]


# --- the pre-push hook ------------------------------------------------------------------------

def _pushed(make_repo, head: dict) -> Path:
    """A repo at `head` whose origin/main is BASE."""
    top = seeds.seeded(make_repo, BASE, head)
    repos.git(top, "update-ref", "refs/remotes/origin/main", "HEAD~1")
    return top


def _push_line(top: Path) -> str:
    sha = repos.git(top, "rev-parse", "HEAD").strip()
    return f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"


@pytest.mark.process
def test_pre_push_refuses_an_undeclared_module_change_and_runs_its_calc_checks(make_repo,
                                                                               capfd):
    top = _pushed(make_repo, seeds.module_changed(BASE))

    code = cc.pre_push(top, "origin", _push_line(top))

    out = capfd.readouterr().out
    assert code == 1
    assert "B6 src/crapkit/score.py holds CRAP score and changed with no declared change" in out
    assert "1 passed" in out


@pytest.mark.process
def test_pre_push_passes_a_declared_fix_and_runs_the_checks_of_the_bumped_module(make_repo,
                                                                                 capfd):
    """The fix bumps ANALYSIS_VERSION in analyze.py, the module of two seeded calcs."""
    top = _pushed(make_repo, seeds.fixed_crap(BASE))

    code = cc.pre_push(top, "upstream", _push_line(top))

    out = capfd.readouterr().out
    assert code == 0, out
    assert "change control: pass" in out and "2 passed" in out


@pytest.mark.process
def test_pre_push_runs_nothing_when_no_calc_module_moved(make_repo, capfd):
    top = _pushed(make_repo, seeds.replace(BASE, "CONTEXT.md", "# Context", "# The context"))

    code = cc.pre_push(top, "origin", _push_line(top))

    assert code == 0
    assert "the push changes no calc module; no accuracy check to run" in capfd.readouterr().out


@pytest.mark.process
def test_pre_push_needs_a_fetched_main(make_repo):
    top = seeds.seeded(make_repo, BASE)

    with pytest.raises(cc.ChangeControlError, match="neither upstream/main nor origin/main is "
                                                    "fetched; run `git fetch upstream` first"):
        cc.remote_main(top, "upstream")


@pytest.mark.process
def test_the_pre_push_command_reads_the_refs_git_hands_it(make_repo, monkeypatch, capfd):
    top = _pushed(make_repo, seeds.module_changed(BASE))
    monkeypatch.chdir(top)
    monkeypatch.setattr(sys, "stdin", io.StringIO(_push_line(top)))

    assert cc.main(["pre-push", "origin", "https://example.invalid/crapkit.git"]) == 1
    assert "B6 src/crapkit/score.py" in capfd.readouterr().out


def _push(top: Path, remote: Path) -> tuple:
    """`git push` with core.hooksPath at this checkout's git-hooks, the way
    CONTRIBUTING sets it up, and this interpreter first on PATH."""
    repos.git(top, "config", "core.hooksPath", (REPO / "git-hooks").as_posix())
    env = {**os.environ, "PATH": os.pathsep.join((str(Path(sys.executable).parent),
                                                  os.environ["PATH"]))}
    started = time.monotonic()
    done = hang_guard.run(["git", "push", "-q", str(remote), "HEAD:refs/heads/main", "--force"],
                          cwd=top, env=env, text=True, encoding="utf-8", errors="replace")
    return done, time.monotonic() - started


@pytest.mark.process
def test_the_hook_script_stops_a_real_push_within_a_minute(make_repo, tmp_path):
    """git runs git-hooks/pre-push itself: an undeclared module change is refused,
    and a declared fix goes through."""
    remote = tmp_path / "remote.git"
    repos.git(tmp_path, "init", "-q", "--bare", str(remote))
    bad = _pushed(make_repo, seeds.module_changed(BASE))
    good = _pushed(make_repo, seeds.fixed_crap(BASE))

    refused, refused_seconds = _push(bad, remote)
    accepted, accepted_seconds = _push(good, remote)

    assert refused.returncode != 0 and "B6 src/crapkit/score.py" in refused.stdout + refused.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert max(refused_seconds, accepted_seconds) < 60


# --- regeneration, with crapkit measuring the kit's seed corpus ---------------------------------

SMALL = "tests/accuracy/corpus_goldens/small"
GOLDENS = "tests/accuracy/corpus_goldens/goldens/small"
# A stand-in for the corpus packet's tools/accuracy/regenerate.py: `goldens`
# measures the small corpus with kit.corpus_run and rewrites goldens/small.
REGENERATOR = f'''import sys
import tempfile
from pathlib import Path
sys.path.insert(0, {str(REPO / "tests")!r})
from accuracy.kit import corpus_run, goldens
root = Path(__file__).resolve().parents[2]
assert sys.argv[1:] == ["goldens"], sys.argv
with tempfile.TemporaryDirectory() as work:
    clock = corpus_run.date_now(root / "tests/accuracy/corpus_goldens/corpus.toml")
    run = corpus_run.measure(root / "{SMALL}", Path(work), clock)
    for name, text in goldens.goldens_of(run).items():
        (root / "{GOLDENS}" / name).write_bytes(text.encode("utf-8"))
'''


def _seed_tree() -> dict[str, str]:
    """An initialized tree whose small corpus and goldens are the kit's seed ones,
    with a regenerator that remeasures them."""
    corpus = {f"{SMALL}/{path}": data.decode("utf-8")
              for path, data in repos.tree(corpus_run.SEED).items()}
    goldens = {f"{GOLDENS}/{path.name}": path.read_text(encoding="utf-8")
               for path in sorted((corpus_run.SEED.parent / "seed-goldens").iterdir())}
    tree = {path: text for path, text in seeds.uninitialized().items()
            if not path.startswith("tests/accuracy/corpus_goldens/")}
    return {**tree, **corpus, **goldens, cc.REGENERATE: REGENERATOR}


def _initialized(make_repo) -> tuple[Path, dict]:
    before = _seed_tree()
    top = seeds.seeded(make_repo, before)
    cc.lock_initial(top, cc.running(cc.DirTree(top)), "2026-09-24", {"score_model": 3})
    after = {**before, **_files(top)}
    seeds.commit(top, before, after, 1)
    return top, after


@pytest.mark.nightly
@pytest.mark.process
def test_regeneration_reproduces_the_committed_goldens(make_repo):
    top, _ = _initialized(make_repo)

    text = cc.declare(top, cc.Request("C2", "none", (), "remeasured, nothing moved"))

    assert text.startswith("declared C2 (none: no calc): 0 locked files relocked, 0 golden cells")
    assert repos.git(top, "status", "--porcelain", "--", GOLDENS) == ""


@pytest.mark.nightly
@pytest.mark.process
def test_regenerated_moves_are_judged_against_radon_complexipy_and_the_formula(make_repo):
    """`unused` gains a branch: crapkit's ccn, cognitive and CRAP for it move, and
    each moved cell with a Python oracle agrees with that oracle."""
    top, _ = _initialized(make_repo)
    source = top / SMALL / "src/py/calc.py"
    source.write_text(source.read_text().replace("    if flag:\n        return 1\n",
                                                 "    if flag:\n        return 1\n"
                                                 "    if flag is None:\n        return 2\n"))

    assert cc.regenerate(top) == ""
    head = cc.DirTree(top)

    judged = [cc.judge(head, cell) for cell in cc.moved_cells(cc.GitTree(top, "HEAD"), head)]

    assert {("unused", "ccn"), ("unused", "cognitive"), ("unused", "crap")} <= _answered(judged)
    assert list(filter(_disagrees, judged)) == []


def _answered(judged: list) -> set[tuple[str, str]]:
    return {(judgement.cell.handle, judgement.cell.column) for judgement in judged
            if judgement.oracle}


def _disagrees(judgement) -> bool:
    return not judgement.agrees


@pytest.mark.process
def test_declare_stops_when_the_regenerator_fails(make_repo):
    top = seeds.seeded(make_repo, {**BASE, cc.REGENERATE: "import sys\nsys.exit('no corpus')\n"})

    with pytest.raises(cc.ChangeControlError, match=r"regenerate.py goldens exited 1:\n.*no corpus"):
        cc.declare(top, cc.Request("C3", "none", (), "a refactor"), lizard="1.24.0")


@pytest.mark.process
def test_declare_without_a_regenerator_says_it_judged_the_goldens_as_they_are(make_repo):
    top = seeds.seeded(make_repo, BASE)
    seeds.write(top, BASE, seeds.module_changed(BASE))

    text = cc.declare(top, cc.Request("C3", "none", (), "a comment"), lizard="1.24.0")

    assert text.splitlines()[:2] == [
        "tools/accuracy/regenerate.py is not in this tree; the goldens are judged as they are",
        "declared C3 (none: no calc): 0 locked files relocked, 0 golden cells moved, 0 judged by "
        "an oracle"]


# --- declare in a diff with more than one change -------------------------------------------------

@pytest.mark.process
def test_the_working_tree_changes_are_edits_deletions_and_new_files(make_repo):
    top = seeds.seeded(make_repo, {**BASE, ".gitignore": "*.log\n"})
    (top / seeds.MODULE).write_text("changed\n", encoding="utf-8")
    (top / seeds.HOOK).unlink()
    (top / "src/crapkit/new.py").write_text("x = 1\n", encoding="utf-8")
    (top / "run.log").write_text("ignored\n", encoding="utf-8")

    assert cc.worktree_changes(top, "HEAD") == {seeds.MODULE, seeds.HOOK, "src/crapkit/new.py"}


def _write_back(top: Path, tree: dict, *paths: str) -> None:
    for path in paths:
        (top / path).write_bytes(tree[path].encode("utf-8"))


@pytest.mark.process
def test_a_none_change_declared_after_a_fix_answers_only_for_what_is_left(make_repo):
    """parse's ccn goes from 8 to 7 and score.py gains a comment: the fix declares
    the move, then a kind none change covers score.py, and the commit passes."""
    base = seeds.base(seeds.ccn8())
    top = seeds.seeded(make_repo, base)
    head = seeds.module_changed(seeds.bump({**base, seeds.SCORED: BASE[seeds.SCORED],
                                            seeds.INVENTORY: BASE[seeds.INVENTORY]}, "12"))
    seeds.write(top, base, head)

    cc.declare(top, cc.Request("C3", "fix", (seeds.CCN,), "parse's ccn"), regenerate_goldens=False,
               lizard="1.24.0")
    text = cc.declare(top, cc.Request("C4", "none", (), "a comment in score.py"),
                      regenerate_goldens=False, lizard="1.24.0")
    tree = seeds.with_bug(seeds.changelog(_files(top), "C3"))
    _write_back(top, tree, seeds.BUGS, seeds.RETRO, "CHANGELOG.md")
    repos.git(top, "add", "-A")
    repos.git(top, "commit", "-q", "-m", "fix parse's ccn", date=repos.EPOCH + 120)

    code, verdict = cc.check(top, "HEAD~1", lizard="1.24.0")
    assert text.startswith("declared C4 (none: no calc): 0 locked files relocked, 0 golden cells")
    assert code == 0, verdict


@pytest.mark.process
def test_a_fix_of_a_calc_no_golden_shows_is_declared_with_nothing_moved(make_repo):
    top = seeds.seeded(make_repo, BASE)
    seeds.write(top, BASE, seeds.replace(BASE, seeds.HOOK, "row not in marks",
                                         "row.key not in marks"))

    text = cc.declare(top, cc.Request("C3", "fix", ("Pre-commit gate",), "the gate reads keys"),
                      regenerate_goldens=False, lizard="1.24.0")

    assert text.startswith("declared C3 (fix: Pre-commit gate): 0 locked files relocked")


@pytest.mark.process
def test_a_fix_of_a_calc_no_golden_shows_needs_its_module_changed(make_repo):
    top = seeds.seeded(make_repo, BASE)

    with pytest.raises(cc.ChangeControlError) as refused:
        cc.declare(top, cc.Request("C3", "fix", ("Pre-commit gate",), "the gate reads keys"),
                   regenerate_goldens=False, lizard="1.24.0")

    assert "declared calc did not move: Pre-commit gate" in str(refused.value)
    assert "nothing moved since the lock" in str(refused.value)
