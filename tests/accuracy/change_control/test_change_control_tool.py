"""The change-control tool around its rules: the command line, the first lock, the
test counts, the pre-push hook and the goldens it regenerates.

The rules themselves are judged in test_change_control_rules.py; these tests
check that each command reaches them, writes what it says, and refuses with the
command that fixes the refusal.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import hang_guard
from accuracy.change_control import cc_seeds as seeds
from accuracy.kit import corpus_run, repos

REPO = Path(__file__).resolve().parents[3]
cc = seeds.tool()

BASE = seeds.base()


def _files(top: Path) -> dict[str, str]:
    return {path: (top / path).read_text(encoding="utf-8") for path in cc.DirTree(top).paths()}


# --- the check on the command line --------------------------------------------------------------

@pytest.mark.nightly
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
    with pytest.raises(cc.ChangeControlError, match="^git cat-file -t nope exited 128: fatal: "):
        cc.git(tmp_path, "cat-file", "-t", "nope")


TAB_PATH = "tests/accuracy/score_model/a\tb.tsv"


@pytest.mark.process
def test_a_commit_s_tree_reads_every_path_and_batches_the_accuracy_blobs(make_repo, monkeypatch):
    """A path holding a tab (git writes it raw under -z), added to the index only,
    so NTFS never has to spell it, and read back whole; the tests/accuracy blobs
    arrive in one batch, any other file on its own."""
    top = seeds.seeded(make_repo, BASE)
    blob = cc.git(top, "hash-object", "-w", "--stdin", stdin=b"tabbed\n").decode().strip()
    repos.git(top, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo",
              f"100644,{blob},{TAB_PATH}")
    repos.git(top, "commit", "-q", "-m", "a tab", date=repos.EPOCH + 60)
    batches = []
    real = cc._cat_blobs
    monkeypatch.setattr(cc, "_cat_blobs", lambda repo, ids: batches.append(len(ids)) or real(
        repo, ids))

    tree = cc.GitTree(top, "HEAD")
    tab, rulings, module = tree.read(TAB_PATH), tree.read(seeds.RULINGS), tree.read(seeds.MODULE)

    assert TAB_PATH in tree.paths()
    assert (tab, rulings, module) == (b"tabbed\n", BASE[seeds.RULINGS].encode(),
                                      BASE[seeds.MODULE].encode())
    assert batches[1:] == [1] and batches[0] > 5


def test_a_working_tree_answers_nothing_for_a_missing_path(tmp_path):
    assert (cc.DirTree(tmp_path).id("absent.tsv"), cc.DirTree(tmp_path).paths()) == (None, [])
    assert cc.raw_rows(None) == []


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.process
def test_an_outside_process_answers_its_text_from_its_directory_and_environment(tmp_path):
    code = "import os; print(os.getcwd()); print(os.environ['CC_PROBE'])"

    out = cc._process("probe", _python(code), 60, tmp_path, {**os.environ, "CC_PROBE": "probe"})

    assert [Path(out.splitlines()[0]).resolve(), out.splitlines()[1]] == [tmp_path.resolve(),
                                                                          "probe"]


@pytest.mark.process
def test_a_failed_outside_process_names_itself_and_the_last_3000_characters():
    """3,500 bytes of stdout, one byte that is not UTF-8, then the stderr line: the
    message keeps the last 3,000 characters, the bad byte read as U+FFFD."""
    code = ("import sys; sys.stdout.buffer.write(b'a' * 3500 + bytes([255])); "
            "sys.stdout.flush(); sys.stderr.write('the end'); sys.exit(1)")

    with pytest.raises(cc.ChangeControlError) as failed:
        cc._process("probe", _python(code), 60)

    head, _, tail = str(failed.value).partition("\n")
    assert (head, len(tail), tail[-8:]) == ("probe exited 1:", 3000, "\ufffdthe end")


@pytest.mark.nightly
@pytest.mark.process
def test_an_outside_process_stops_at_its_time_limit():
    with pytest.raises(subprocess.TimeoutExpired):
        cc._process("probe", _python("import time; time.sleep(30)"), 0.5)


def test_a_test_that_spawns_a_process_without_the_marker_is_stopped():
    with pytest.raises(AssertionError, match="^this test spawns node eslint_values.cjs: mark it"):
        cc._node(REPO / cc.ESLINT, "x")


# --- declare on the command line -----------------------------------------------------------------

@pytest.mark.nightly
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


@pytest.mark.process
def test_declare_on_the_command_line_names_rulings_regenerates_and_dates_the_change(
        make_repo, oracle, capsys):
    """parse at ccn 9 where radon says 7, covered by the last --against-oracle, R-CCN;
    the first names no rulings row and covers nothing. A definition cites the first
    named ruling of each cell's calc, so the CRAP cell cites R-D5. With no
    --no-regenerate the tool looks for the regenerator, which this tree lacks."""
    oracle("radon")
    top = seeds.seeded(make_repo, BASE)
    seeds.write(top, BASE, seeds.ccn9(BASE))
    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    code = cc.main(["declare", "C3", "--kind", "definition", "--calcs", seeds.CCN,
                    "--reason", "parse reads 9", "--against-oracle", "R-NONE",
                    "--against-oracle", "R-D5", "--against-oracle", "R-CCN", "--repo", str(top)])

    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.startswith(f"{cc.REGENERATE} is not in this tree; the goldens are judged as "
                          "they are\ndeclared C3 (definition: ")
    assert "7 judged by an oracle, ruling(s) R-CCN, R-D5 cover a difference" in out
    assert cc.changes_of(cc.DirTree(top))["C3"]["date"] in {before, after}


# --- the command line's contract -------------------------------------------------------------

def _help(parser) -> str:
    return " ".join(parser.format_help().split())


# Each command's usage line and every help text it shows, as the module docstring
# and CONTRIBUTING state the commands.
HELP = {
    "check": (lambda: cc._check_parser(), "usage: change_control.py [-h] --base BASE", (
        "Judge a diff.", "--base BASE the ref to compare with (merge base)", "--head HEAD",
        "--repo REPO", "--measured MEASURED the CI hand-off directory",
        "--moved MOVED a moved-row map in moved.tsv's columns")),
    "declare": (lambda: cc._declare_parser(), "usage: change_control.py declare [-h] --kind", (
        "Judge and record a change that moves goldens.",
        "--kind {fix,definition,feature,none}",
        "--calcs CALCS the moved calcs, `;` or `,` separated", "--reason REASON",
        "--against-oracle RULING", "--base BASE where the old goldens are (default HEAD)",
        "--no-regenerate judge the goldens as they are; do not remeasure the corpora")),
    "lock": (lambda: cc._lock_parser(), "usage: change_control.py lock [-h] --initial", ()),
    "counts": (lambda: cc._counts_parser(), "usage: change_control.py counts [-h] [--write]",
               ()),
    "pre-push": (lambda: cc._pre_push_parser(),
                 "usage: change_control.py pre-push [-h] remote [url]", ()),
}


@pytest.mark.parametrize("command", sorted(HELP))
def test_each_command_shows_its_usage_and_help(command):
    build, usage, shown = HELP[command]

    text = _help(build())

    assert text.startswith(usage)
    assert [line for line in shown if line not in text] == []
    assert "--repo" not in text or command == "check"


@pytest.mark.parametrize("argv", [
    ["--head", "HEAD"],
    ["declare", "C3", "--reason", "why"],
    ["declare", "C3", "--kind", "fix"],
    ["declare", "C3", "--kind", "bugfix", "--reason", "why"],
    ["lock"],
    ["pre-push"],
])
def test_a_missing_or_unknown_argument_is_a_usage_error(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        cc.main(argv)

    assert stopped.value.code == 2
    assert "error:" in capsys.readouterr().err


def test_each_command_reads_its_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    check = cc._check_parser().parse_args(["--base", "origin/main"])
    declare = cc._declare_parser().parse_args(["C3", "--kind", "none", "--reason", "why"])
    lock = cc._lock_parser().parse_args(["--initial"])
    counts = cc._counts_parser().parse_args([])
    push = cc._pre_push_parser().parse_args(["origin"])

    assert (check.head, check.measured, check.moved) == ("HEAD", None, None)
    assert check.repo.resolve() == tmp_path.resolve()
    assert (declare.calcs, declare.against_oracle, declare.base, declare.no_regenerate,
            declare.repo) == ("", [], "HEAD", False, REPO)
    assert (lock.initial, lock.repo, counts.write, counts.repo) == (True, REPO, False, REPO)
    assert (push.remote, push.url) == ("origin", None)


def test_each_command_reads_its_paths_and_repeated_rulings():
    check = cc._check_parser().parse_args(["--base", "b", "--repo", "r", "--measured", "m",
                                           "--moved", "v.tsv"])
    declare = cc._declare_parser().parse_args([
        "C3", "--kind", "fix", "--reason", "why", "--against-oracle", "R-A",
        "--against-oracle", "R-B", "--no-regenerate", "--repo", "r"])
    counts = cc._counts_parser().parse_args(["--write", "--repo", "r"])
    push = cc._pre_push_parser().parse_args(["origin", "https://example.invalid/x.git"])

    assert (check.repo, check.measured, check.moved) == (Path("r"), Path("m"), Path("v.tsv"))
    assert (declare.against_oracle, declare.no_regenerate, declare.repo) == (
        ["R-A", "R-B"], True, Path("r"))
    assert (counts.write, counts.repo, push.url) == (True, Path("r"),
                                                     "https://example.invalid/x.git")


# --- the first lock and the test counts ----------------------------------------------------------

@pytest.mark.nightly
@pytest.mark.release
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


@pytest.mark.nightly
@pytest.mark.process
def test_counts_says_which_packet_moved_and_write_records_it(make_repo, capsys):
    """The seeded conftest hides a nightly test unless the collect-all switch is set,
    as the kit's does: counts include every tier."""
    top = seeds.seeded(make_repo, {**BASE, "tests/conftest.py": TIERED_CONFTEST})
    test_file = top / seeds.SEED_TEST
    test_file.write_text(test_file.read_text() + NIGHTLY_TEST)

    stale = cc.main(["counts", "--repo", str(top)])
    written = cc.main(["counts", "--write", "--repo", str(top)])

    out = capsys.readouterr().out
    assert (stale, written) == (1, 0)
    assert f"T6 packet score_model collects 4 tests, {cc.COUNTS} says 3" in out
    assert "fix: python tools/accuracy/change_control.py counts --write" in out
    assert (top / cc.COUNTS).read_text() == "packet\ttests\nscore_model\t4\n"


@pytest.mark.nightly
@pytest.mark.process
def test_a_collection_error_stops_the_count(make_repo):
    top = seeds.seeded(make_repo, {**BASE, seeds.SEED_TEST: "def broken(:\n"})

    with pytest.raises(cc.ChangeControlError, match="^pytest --collect-only tests/accuracy exited [1-9]"):
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


IN_TREE_SOURCE = "def test_in_tree():\n    assert True\n"
# A regenerator that checks how declare starts it: from the checkout's top, with its
# tests/ first on PYTHONPATH, asked for the goldens.
CHECKED_REGENERATOR = """import os
import sys
from pathlib import Path
assert sys.argv[1:] == ["goldens"], sys.argv
assert Path.cwd().resolve() == Path(__file__).resolve().parents[2]
first = os.environ["PYTHONPATH"].split(os.pathsep)[0]
assert Path(first).resolve() == Path.cwd().resolve() / "tests"
"""
TIERED_CONFTEST = """import os


def pytest_collection_modifyitems(config, items):
    if os.environ.get("CRAPKIT_ACCURACY_COLLECT_ALL") != "1":
        items[:] = [item for item in items if item.get_closest_marker("nightly") is None]
"""
NIGHTLY_TEST = "\n\nimport pytest\n\n\n@pytest.mark.nightly\ndef test_more():\n    assert 2 == 2\n"
DIRTY_ANALYZE = ("import lizard  # an edit nobody committed\n\n"
                 "ANALYSIS_VERSION = 11  # the reader's version\n")


@pytest.mark.nightly
@pytest.mark.process
def test_pre_push_refuses_an_undeclared_module_change_and_runs_its_calc_checks(make_repo,
                                                                               capfd):
    """score.py changes in the pushed commit: CRAP score's test runs, with the in-tree
    rules. analyze.py changes only in the working tree, so its two calcs' tests do not."""
    top = _pushed(make_repo, seeds.module_changed(BASE))
    (top / cc.IN_TREE_TEST).write_text(IN_TREE_SOURCE)
    repos.git(top, "add", cc.IN_TREE_TEST)
    repos.git(top, "commit", "-q", "-m", "in-tree rules", date=repos.EPOCH + 300)
    (top / seeds.ANALYZE).write_text(DIRTY_ANALYZE)

    code = cc.pre_push(top, "origin", _push_line(top))

    out = capfd.readouterr().out
    assert code == 1
    assert "B6 src/crapkit/score.py holds CRAP score and changed with no declared change" in out
    assert "2 passed" in out


@pytest.mark.nightly
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
def test_pre_push_judges_the_pushed_commit_against_the_pushed_to_remote(make_repo, capfd):
    """Two commits over the remote's main: an undeclared module edit, then its kind
    none declaration. Pushing the first commit alone is refused though HEAD passes;
    only upstream/main is fetched, so the base comes from the remote pushed to."""
    bad = seeds.module_changed(BASE)
    good = seeds.change(bad, "C3", "none", "", reason="a comment, nothing moves")
    top = seeds.seeded(make_repo, BASE, bad, good)
    repos.git(top, "update-ref", "refs/remotes/upstream/main", "HEAD~2")
    first = repos.git(top, "rev-parse", "HEAD~1").strip()

    code = cc.pre_push(top, "upstream", f"refs/heads/b {first} refs/heads/b {'0' * 40}\n")

    out = capfd.readouterr().out
    assert code == 1
    assert "B6 src/crapkit/score.py holds CRAP score and changed with no declared change" in out
    assert "no accuracy check to run" not in out and " passed" not in out


@pytest.mark.process
def test_a_push_that_only_deletes_a_branch_judges_nothing(make_repo, capfd):
    top = _pushed(make_repo, seeds.module_changed(BASE))
    sha = repos.git(top, "rev-parse", "HEAD").strip()

    code = cc.pre_push(top, "origin", f"(delete) {'0' * 40} refs/heads/old {sha}\n")

    assert (code, capfd.readouterr().out) == (0, "")


@pytest.mark.nightly
@pytest.mark.process
def test_the_pre_push_command_reads_the_refs_git_hands_it(make_repo, monkeypatch, capfd):
    """git starts the hook from the top of the checkout; started from src/ the tool
    still finds it, and the base is the main of the remote it was handed."""
    top = seeds.seeded(make_repo, BASE, seeds.module_changed(BASE))
    repos.git(top, "update-ref", "refs/remotes/upstream/main", "HEAD~1")
    monkeypatch.chdir(top / "src")
    monkeypatch.setattr(sys, "stdin", io.StringIO(_push_line(top)))

    assert cc.main(["pre-push", "upstream", "https://example.invalid/crapkit.git"]) == 1
    out = capfd.readouterr().out
    assert "B6 src/crapkit/score.py" in out and "1 passed" in out


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


@pytest.mark.nightly
@pytest.mark.release
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


@pytest.mark.nightly
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

@pytest.mark.nightly
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


@pytest.mark.nightly
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


# --- the JS and TS oracles: ESLint complexity and sonarjs cognitive complexity -------------------

# Worked by hand from the ESLint complexity rule docs (1, plus each if, loop,
# logical operator including ??, and each case in the classic variant or each
# switch in the modified one) and the Sonar paper v1.7 (if and loops +1 plus
# their nesting, a switch +1, a sequence of && +1; ?? is no increment):
#   pick: classic 1 + for + if + && + ?? = 5, modified 5, cognitive for 1 + if 2 + && 1 = 4
#   kind: classic 1 + two cases = 3, modified 1 + switch = 2, cognitive switch 1 = 1
#   flat: 1, 1 and 0
KINDS_TS = '''export function pick<T>(xs: T[], strict: boolean): T | undefined {
  for (const x of xs) {
    if (strict && x) {
      return x;
    }
  }
  return xs[0] ?? undefined;
}

export const kind = (n: number) => {
  switch (n) {
    case 1: return "one";
    case 2: return "two";
    default: return "many";
  }
};

function flat() {
  return 1;
}
'''
TS_PATH = "src/web/kinds.ts"
TS_ROWS = (("pick( xs , strict )", 1, 8), ("kind( n )", 10, 16), ("flat( )", 18, 20))
HAND = {("pick", "ccn_std"): "5", ("pick", "ccn_mod"): "5", ("pick", "ccn"): "5",
        ("pick", "cognitive"): "4", ("kind", "ccn_std"): "3", ("kind", "ccn_mod"): "2",
        ("kind", "ccn"): "2", ("kind", "cognitive"): "1", ("flat", "ccn"): "1",
        ("flat", "cognitive"): "0"}


def _ts_tree(suffix: str = ".ts") -> cc.DictTree:
    path = TS_PATH.replace(".ts", suffix)
    table = "path\tlong_name\tstart\tend\n" + "".join(
        f"{path}\t{name}\t{start}\t{end}\n" for name, start, end in TS_ROWS)
    source = KINDS_TS if suffix != ".js" else KINDS_TS.replace("<T>(xs: T[], strict: boolean): "
                                                               "T | undefined", "(xs, strict)"
                                                               ).replace("(n: number)", "(n)")
    return cc.DictTree({f"{cc.SMALL_CORPUS}/{path}": source.encode(),
                        seeds.SCORED: table.encode()})


@pytest.mark.process
@pytest.mark.nightly
@pytest.mark.parametrize("suffix", [".ts", ".js"])
def test_eslint_and_sonarjs_answer_each_js_and_ts_column(oracle, suffix):
    list(map(oracle, ("eslint", "eslint-plugin-sonarjs", "@typescript-eslint/parser")))

    answers = _judged_hand(_ts_tree(suffix), TS_PATH.replace(".ts", suffix))

    assert answers == {key: (_oracle_of(key[1]), value, True) for key, value in HAND.items()}


def _judged_hand(tree, path: str) -> dict:
    """{(handle, column): (oracle, its value, agrees)} for each hand-worked cell."""
    judged = {key: cc.judge(tree, cc.Cell(seeds.SCORED, path, *key, "0", value))
              for key, value in HAND.items()}
    return {key: (found.oracle, found.value, found.agrees) for key, found in judged.items()}


def _oracle_of(column: str) -> str:
    return "sonarjs" if column == "cognitive" else "eslint"



@pytest.mark.nightly
@pytest.mark.process
def test_a_ts_file_eslint_cannot_parse_answers_nothing(oracle):
    oracle("eslint")
    tree = cc.DictTree({f"{cc.SMALL_CORPUS}/{TS_PATH}": b"function (\n",
                        seeds.SCORED: f"path\tlong_name\tstart\tend\n{TS_PATH}\tf( )\t1\t1\n"
                        .encode()})

    judged = cc.judge(tree, cc.Cell(seeds.SCORED, TS_PATH, "f", "ccn", "1", "2"))

    assert (judged.oracle, judged.value, judged.agrees) == ("", "", True)


@pytest.mark.nightly
@pytest.mark.process
def test_a_ts_move_eslint_disagrees_with_is_refused_by_name(oracle):
    oracle("eslint")
    judged = cc.judge(_ts_tree(), cc.Cell(seeds.SCORED, TS_PATH, "kind", "ccn", "2", "3"))

    assert not judged.agrees
    assert cc._refusal(judged).startswith(
        f"crapkit now says 3, eslint says 2 at {TS_PATH}:kind (ccn): this looks like a regression")


# --- what a refusal prints ------------------------------------------------------------------------

@pytest.mark.process
def test_a_refusal_lists_the_first_ten_disagreements_and_counts_the_rest(make_repo, oracle):
    """f1 to f11 read CRAP 1.25 at ccn 1 and cov 1.0, where the formula gives 1.0."""
    oracle("radon")
    rows = seeds.scored_rows(f_crap={k: "1.25" for k in range(1, 12)})
    top = seeds.seeded(make_repo, BASE)
    seeds.write(top, BASE, seeds.bump({**BASE, seeds.SCORED: seeds.scored(rows)}, "12"))

    with pytest.raises(cc.ChangeControlError) as refused:
        cc.declare(top, cc.Request("C3", "fix", ("CRAP score",), "f"), regenerate_goldens=False,
                   lizard="1.24.0")

    text = str(refused.value)
    assert text.count("crapkit now says 1.25, kit.exact says 1.0 at src/a.py:") == 10
    assert "  ... and 1 more moved cells an oracle disagrees with" in text
    assert cc.RESTORE not in text


@pytest.mark.nightly
@pytest.mark.process
def test_a_refusal_after_regenerating_says_how_to_put_the_goldens_back(make_repo, oracle):
    oracle("radon")
    base = {**BASE, cc.REGENERATE: CHECKED_REGENERATOR}
    top = seeds.seeded(make_repo, base)
    rows = seeds.scored_rows(parse_ccn=9, parse_crap="19.125")
    seeds.write(top, base, seeds.bump({**base, seeds.SCORED: seeds.scored(rows),
                                       seeds.INVENTORY: seeds.inventory(rows)}, "12"))

    with pytest.raises(cc.ChangeControlError) as refused:
        cc.declare(top, cc.Request("C3", "fix", (seeds.CCN,), "parse"), lizard="1.24.0")

    assert "crapkit now says 9, radon says 7 at src/a.py:parse" in str(refused.value)
    assert str(refused.value).endswith(cc.RESTORE)


def test_a_path_the_code_page_cannot_spell_prints_escaped(monkeypatch):
    out, err = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="cp1252", newline="\n"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(err, encoding="cp1252", newline="\n"))

    cc._console()
    print("src/\u4e2d.py caf\u00e9")
    sys.stdout.flush()

    assert out.getvalue() == b"src/" + b"\\" + b"u4e2d.py caf\xe9\n"


# --- the tool's small helpers, each against values worked out by hand -------------------------

@pytest.mark.parametrize("path, packet", [
    ("tests/accuracy/score_model/hand_score.tsv", "score_model"),
    ("tests/accuracy/kit/fixtures/seed-goldens.lock", "kit"),
    ("tests/accuracy/calcs.tsv", ""),
    ("tests/unit/score/test_x.py", ""),
    ("src/crapkit/score.py", ""),
])
def test_a_path_s_packet_is_the_directory_under_tests_accuracy(path, packet):
    assert cc.packet_of(path) == packet


@pytest.mark.parametrize("path, name", [
    ("tests/accuracy/corpus_goldens/goldens/small/scored.tsv", "small"),
    ("tests/accuracy/corpus_goldens/goldens/requests/surfaces/worklist.json", "requests"),
    ("tests/accuracy/corpus_goldens/goldens/report.html", ""),
    ("src/a.py", ""),
])
def test_a_golden_s_set_is_the_directory_right_under_goldens(path, name):
    assert cc.golden_set(path) == name


READER = "Python reader: spans, names, inline_body, unread-def net"


@pytest.mark.parametrize("path, column, calcs", [
    ("src/a.py", "crap", {"CRAP score"}),
    ("src/a.py", "ccn", {seeds.CCN, READER}),
    ("src/a.py", "row", {"Function discovery and spans", READER}),
    ("cmd/root.go", "ccn", {seeds.CCN}),
    ("src/a.py", "a_new_column", {"Inventory rows and TSV exports"}),
])
def test_a_moved_column_is_declared_by_its_calc_and_its_language_s_reader(path, column, calcs):
    """The plan's matrix: a reader calc exists for Python, JS/TS, Rust, shell and
    PowerShell, and owns spans and ccn, never CRAP."""
    cell = cc.Cell(seeds.SCORED, path, "f", column, "1", "2")

    assert set(cc.acceptable(cell)) == calcs
    assert cc.primary(cell) == sorted(calcs - {READER})[0]


ULP = 2.0 ** -52  # math.ulp(1.0)


@pytest.mark.parametrize("value, oracle, close", [
    ("1.0", "1.0", True),
    (repr(1.0 + 4 * ULP), "1.0", True),
    (repr(1.0 + 5 * ULP), "1.0", False),
    ("inf", "1.0", False),
    ("present", "present", True),
    ("absent", "present", False),
])
def test_a_value_agrees_within_4_ulp_or_as_equal_text(value, oracle, close):
    assert cc._close(value, oracle) is close


def test_a_tree_is_locked_once_it_has_a_change_or_a_lock_row():
    changes_only = {cc.CHANGES: BASE[cc.CHANGES], cc.LOCK: "path\tsha256\tchange\n"}
    lock_only = {cc.CHANGES: BASE[cc.CHANGES].splitlines()[0] + "\n", cc.LOCK: BASE[cc.LOCK]}
    neither = {cc.CHANGES: lock_only[cc.CHANGES], cc.LOCK: changes_only[cc.LOCK]}

    assert [cc._initialized(seeds.tool().DictTree({path: text.encode() for path, text in
                                                    tree.items()}))
            for tree in (changes_only, lock_only, neither)] == [True, True, False]


def test_versions_compare_part_by_part_as_numbers():
    assert cc._version("1.24.0-rc1") == (1, 24, 0, "rc1")
    assert cc._version("1.10.0") > cc._version("1.9.2")


def test_a_metric_line_holds_the_documented_fields_and_crap_at_4_dp():
    row = {"ccn_std": "3", "ccn_mod": "2", "ccn": "2", "start": "5", "end": "9",
           "crap": "2.000049", "cov": "0.75"}

    assert cc._metric_line(("src/a.py", "f"), row) == \
        "src/a.py\tf\t3\t2\t2\t\t\t\t\t5\t9\t2.0000\t0.75"
    assert cc._metric_line(("src/a.py", "g"), {}) == "src/a.py\tg" + "\t" * 11


def test_a_table_row_appends_under_one_header_on_its_own_line(tmp_path):
    table = tmp_path / "new" / "dir" / "t.tsv"

    cc._append(table, ("a", "b"), ["1", "2"])
    cc._append(table, ("a", "b"), ["3", "4"])
    ragged = tmp_path / "ragged.tsv"
    ragged.write_bytes(b"a\tb\n1\t2")
    cc._append(ragged, ("a", "b"), ["3", "4"])

    assert table.read_bytes() == b"a\tb\n1\t2\n3\t4\n"
    assert ragged.read_bytes() == b"a\tb\n1\t2\n3\t4\n"


def test_the_small_corpus_is_read_in_place_or_written_out_once(tmp_path, monkeypatch):
    nested = f"{cc.SMALL_CORPUS}/src/pkg/b.py"
    tree = seeds.tool().DictTree({nested: b"def b():\n    return 1\n",
                                  f"{cc.SMALL_CORPUS}/src/pkg/c.py": b"c = 1\n"})
    monkeypatch.delenv(cc.CORPUS_ENV, raising=False)

    written = cc.corpus_dir(tree, "small")

    assert (written / "src" / "pkg" / "b.py").read_bytes() == b"def b():\n    return 1\n"
    assert (written / "src" / "pkg" / "c.py").read_bytes() == b"c = 1\n"
    assert cc.corpus_dir(tree, "small") == written
    assert cc.corpus_dir(cc.DirTree(tmp_path), "small") == tmp_path / cc.SMALL_CORPUS
    assert cc.corpus_dir(tree, "requests") is None


def test_a_full_corpus_member_is_read_under_the_corpus_directory(tmp_path, monkeypatch):
    (tmp_path / "requests" / "src").mkdir(parents=True)
    (tmp_path / "requests" / "src" / "api.py").write_bytes(b"x = 1\n")
    monkeypatch.setenv(cc.CORPUS_ENV, str(tmp_path))

    assert cc.corpus_dir(cc.DictTree({}), "requests") == tmp_path / "requests"
    assert cc._outside("requests", "src/api.py") == b"x = 1\n"
    assert cc._outside("requests", "src/gone.py") is None
    monkeypatch.delenv(cc.CORPUS_ENV)
    assert cc._outside("requests", "src/api.py") is None


def test_the_moved_block_lists_the_first_ten_files_no_row_explains():
    files = [f"tests/accuracy/corpus_goldens/goldens/small/claims-{k:02d}.json"
             for k in range(11)]

    block = cc.moved_block(seeds.tool().DictTree({}), [], files)

    assert block[:2] == ["moved calcs: Inventory rows and TSV exports (11 files)",
                         "moved golden files no moved row explains (first 10 of 11):"]
    assert block[2:] == [f"  {path}\tInventory rows and TSV exports" for path in files[:10]]


def test_the_command_line_is_read_from_sys_argv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["change_control.py", "counts", "--help"])

    with pytest.raises(SystemExit) as shown:
        cc.main()
    with pytest.raises(SystemExit) as empty:
        cc.main([])

    assert (shown.value.code, empty.value.code) == (0, 2)
    assert capsys.readouterr().out.startswith("usage: change_control.py counts")


def _plan(*judged, kind="fix", digest=None):
    request = cc.Request("C3", kind, ("CRAP score",) if kind != "none" else (), "f1 fixed.")
    return cc.Plan(request, list(judged), {}, ["a", "b"], digest, "11")


F1_CRAP = cc.Cell(seeds.SCORED, "src/a.py", "f1", "crap", "1.5", "1.0")
F1_NESTING = cc.Cell(seeds.SCORED, "src/a.py", "f1", "nesting", "0", "1")
F1_NLOC = cc.Cell(seeds.SCORED, "src/a.py", "f1", "nloc", "2", "3")


def test_moved_tsv_is_written_under_a_new_directory_and_rewritten(tmp_path):
    plan = _plan((cc.Judgement(F1_CRAP, "kit.exact", "1.0"), "R-D5"))

    cc._write_moved(tmp_path, plan)
    cc._write_moved(tmp_path, plan)

    assert (tmp_path / cc.MOVED / "C3.moved.tsv").read_text() == (
        "golden\tpath\thandle\tcolumn\told\tnew\toracle\toracle_value\truling\n"
        f"{seeds.SCORED}\tsrc/a.py\tf1\tcrap\t1.5\t1.0\tkit.exact\t1.0\tR-D5\n")


def test_the_declare_summary_counts_cells_oracles_rulings_and_the_digest_row():
    plan = _plan((cc.Judgement(F1_CRAP, "kit.exact", "1.0"), "R-D5"),
                 (cc.Judgement(F1_NESTING, "", ""), ""), (cc.Judgement(F1_NLOC, "", ""), ""),
                 digest={"digest": "x"})

    text = cc.summary(plan, cc.Running("12", "1.24.0"))

    assert text.splitlines() == [
        "declared C3 (fix: CRAP score): 2 locked files relocked, 3 golden cells moved, 1 judged "
        "by an oracle, ruling(s) R-D5 cover a difference",
        "2 moved cells have no oracle here; their packet's oracle checks judge them",
        "metric-digests: new row for analysis 12, lizard 1.24.0 (ANALYSIS_VERSION was 11 at the "
        "base)",
        "add to CHANGELOG.md under ## Unreleased:",
        "- f1 fixed. (accuracy change C3)"]
    assert cc.summary(_plan(kind="none"), cc.Running("11", "1.24.0")) == (
        "declared C3 (none: no calc): 2 locked files relocked, 0 golden cells moved, 0 judged "
        "by an oracle")


def test_a_table_written_from_nothing_gets_its_header(tmp_path):
    request = cc.Request("C3", "fix", ("CRAP score",), "f1 fixed.", (), "2026-09-25")
    digest = {"analysis_version": "12", "lizard_version": "1.24.0", "corpus": "c",
              "digest": "d", "change": "C3"}
    plan = cc.Plan(request, [], {"x.tsv": ("ab", "C3")}, ["x.tsv"], digest, "11")

    cc.write_plan(tmp_path, plan, cc.Running("12", "1.24.0"))

    assert (tmp_path / cc.CHANGES).read_text() == (
        "id\tdate\tkind\tcalcs\tanalysis_version\tlizard_version\tchangelog\treason\n"
        "C3\t2026-09-25\tfix\tCRAP score\t12\t1.24.0\t#unreleased\tf1 fixed.\n")
    assert (tmp_path / cc.LOCK).read_text() == "path\tsha256\tchange\nx.tsv\tab\tC3\n"
    assert (tmp_path / cc.DIGESTS).read_text() == (
        "analysis_version\tlizard_version\tcorpus\tdigest\tchange\n12\t1.24.0\tc\td\tC3\n")


def test_a_calc_list_keeps_an_unknown_name_after_a_known_one():
    known = {"CRAP score", seeds.CCN}

    assert cc.parse_calcs("CRAP score, No such calc", known) == ["CRAP score", "No such calc"]
    assert cc.parse_calcs(f"{seeds.CCN}, CRAP score", known) == ["CRAP score", seeds.CCN]


def test_a_none_change_counts_moved_cells_and_files_together():
    assert cc._none_moves([F1_CRAP], ["tests/accuracy/corpus_goldens/goldens/small/x.json"]) == [
        "2 golden cells or files moved; kind none declares a change that moves nothing"]


def test_sonarjs_leaves_out_a_zero_only_for_a_function_it_found():
    """sonarjs reports no value for a function whose cognitive complexity is 0; a
    start line where ESLint found no function has no value for any rule."""
    found = {"functions": [(5, 6)], "values": [{"rule": "classic", "line": 5, "value": 2}]}

    assert [cc._rule_value(found, 5, rule) for rule in ("classic", "cognitive")] == ["2", "0"]
    assert [cc._rule_value(found, 9, rule) for rule in ("classic", "cognitive")] == [None, None]


def test_an_oracle_with_no_source_answers_nothing():
    row = {"start": "1", "long_name": "f( )"}

    assert (cc.ast_row(row, None), cc.complexipy_cognitive(row, None)) == (None, None)


def test_eslint_not_installed_names_the_install_command(tmp_path, monkeypatch):
    monkeypatch.setattr(cc.oracles, "node_modules", lambda tier: tmp_path)

    with pytest.raises(cc.ChangeControlError, match="^oracle eslint is not installed; run: npm ci "
                                                    "--prefix tools/accuracy/node/push$"):
        cc.eslint_values("function f() {}\n", ".js")


def test_the_moved_block_shows_no_file_list_when_no_file_moved_alone():
    block = cc.moved_block(seeds.tool().DictTree({}), [F1_CRAP], [])

    assert block == ["moved calcs: CRAP score (1 cells)", "moved rows (first 1 of 1):",
                     "  golden\tpath\thandle\tcolumn\told\tnew\toracle",
                     f"  {seeds.SCORED}\tsrc/a.py\tf1\tcrap\t1.5\t1.0\t-"]


LOCKED_SCORED = {**BASE, "tests/accuracy/corpus_goldens/goldens/history/scored.tsv":
                 BASE[seeds.SCORED]}


def test_a_moved_golden_belongs_to_a_fresh_change_only_while_its_lock_row_matches():
    """f1's CRAP moves. Relocked under the fresh C3 it is that change's; relocked
    under the old C2, or in a history table the lock does not hold, it is still
    to declare."""
    history = "tests/accuracy/corpus_goldens/goldens/history/scored.tsv"
    moved = seeds.scored(seeds.scored_rows(f_crap={1: "1.0"}))
    head = seeds.change({**LOCKED_SCORED, seeds.SCORED: moved, history: moved}, "C3", "fix",
                        "CRAP score")
    fresh, old = seeds.relock(head, "C3", seeds.SCORED), seeds.relock(head, "C2", seeds.SCORED)

    def goldens(tree):
        return sorted({cell.golden for cell in cc.moves_of(
            seeds._tree_bytes(LOCKED_SCORED), seeds._tree_bytes(tree), frozenset()).cells})

    assert goldens(fresh) == [history]
    assert goldens(old) == [history, seeds.SCORED]
