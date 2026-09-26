"""crapkit/mutate_pool.py run in-process, with git and the suite command replaced.

tests/unit/test_mutate_pool*.py build real worktrees with git, and the mutation
killer suite leaves out every test that spawns git, so those tests never judge a
mutate_pool mutant. Here mutate_pool's git calls and procs.run_bounded are
recording fakes; the lease owner is crapkit's real one. Every expected value
follows from these rules:

- docs/configuration.md "Mutation worktrees": each worker is a detached worktree
  re-prepared at HEAD with dirty, untracked and deleted inputs replayed; surplus
  workers are removed under the pool's exclusive lease; a concurrent run that
  finds the pool locked uses private worktrees; startup recovery removes only
  recognized, abandoned runs; results keep mutant order at every worker count;
  symlink and Windows reparse components are refused; `mutation_workers` is an
  int >= 1 with no upper bound.
- procs.run_bounded's docstring: a command registered with `owner` stops when that
  owner is cancelled or its caller dies, so every git call and suite run made
  under the pool's lease carries the lease's owner.
- mutate_pool's docstrings: Python validates .pyc files by source size and
  whole-second mtime, so the suite runs with PYTHONDONTWRITEBYTECODE=1 and no
  __pycache__ beside the mutated file; the original file always comes back; one
  thread per tree, and every thread is waited for.
- Python docs, os.stat_result.st_file_attributes: a Windows junction reports
  FILE_ATTRIBUTE_REPARSE_POINT and a directory mode, never S_IFLNK.
- RFC 8259 section 8.1: JSON exchanged between systems is UTF-8.
- The refusal and progress lines are what a user reads; they are pinned in full.
"""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import shutil
import stat
import threading
from types import SimpleNamespace

import pytest

from accuracy.kit import rulings
from hang_guard import HANG_SECONDS
from crapkit import mutate_pool
from crapkit.errors import ToolError
from crapkit.locks import exclusive_lock
from crapkit.mutate import Mutant

DASH = "\u2014"
RUN = "ab" * 16  # a temporary run's directory name: 32 hex digits


class Git:
    """mutate_pool's git calls: each records its owner; add and remove make and
    delete the directory, as `git worktree` does."""

    def __init__(self, head="c0ffee", remove=True):
        self.head, self.remove, self.calls, self.on_add, self.second = head, remove, [], None, []

    def head_commit(self, root):
        return self.head

    def status_names(self, root):
        return []

    def worktree_root(self, root):
        return Path(root).resolve()

    def worktree_add(self, root, path, *, owner=None):
        self.calls.append(("add", Path(path).name, owner))
        Path(path).mkdir(parents=True)
        if self.on_add:
            self.on_add()

    def worktree_remove(self, root, path, *, owner=None):
        self.calls.append(("remove", Path(path).name, owner))
        if self.remove:
            shutil.rmtree(path, ignore_errors=True)

    def worktree_reset(self, tree, commit, *, owner=None):
        self.calls.append(("reset", Path(tree).name, owner))


class Suite:
    """procs.run_bounded's stand-in: records each run and what `look` saw in the tree."""

    def __init__(self, code=0, look=None):
        self.code, self.look, self.runs = code, look, []

    def __call__(self, command, timeout, *, cwd, env=None, owner=None, **extra):
        seen = self.look(Path(cwd)) if self.look else None
        self.runs.append(SimpleNamespace(command=command, timeout=timeout, cwd=Path(cwd),
                                         env=env, owner=owner, seen=seen))
        return self.code(seen) if callable(self.code) else self.code


class Owner:
    def __init__(self, fails=None):
        self.fails, self.cancels = fails, 0

    def cancel(self):
        self.cancels += 1
        if self.fails:
            raise self.fails


def _cfg(workers=1, command="python -m pytest -q -x", timeout=41):
    return SimpleNamespace(mutation_command=command, mutation_timeout_seconds=timeout,
                           mutation_workers=workers)


def _patch_git(monkeypatch, git: Git) -> Git:
    for name in ("head_commit", "status_names", "worktree_root", "worktree_add",
                 "worktree_remove", "worktree_reset"):
        monkeypatch.setattr(mutate_pool, name, getattr(git, name))
    return git


def _spy_owners(monkeypatch) -> list:
    """Every owner crapkit's own_processes yields to mutate_pool, in order."""
    held, real = [], mutate_pool.own_processes

    @contextmanager
    def spy(*args, **kwargs):
        with real(*args, **kwargs) as owner:
            held.append(owner)
            yield owner

    monkeypatch.setattr(mutate_pool, "own_processes", spy)
    return held


def _registered(owners, held) -> bool:
    return all(any(owner is lease for lease in held) for owner in owners)


# --- the whole run ---------------------------------------------------------------------

SOURCE = b"def f(a, b):\n    return a < b\n"
MUTANTS = [Mutant("src/a.py", 2, "    return a < b", "    return a <= b  # KILL", "< -> <="),
           Mutant("src/a.py", 2, "    return a < b", "    return a >= b", "< -> >="),
           Mutant("src/a.py", 2, "    return a < b", "    return a > b  # KILL", "< -> >")]


def _repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/a.py").write_bytes(SOURCE)
    return root


def _suite_reading_the_mutant() -> Suite:
    return Suite(code=lambda text: 1 if "KILL" in text else 0,
                 look=lambda cwd: (cwd / "src/a.py").read_text(encoding="utf-8"))


def _run_twice(monkeypatch, root):
    """A first run builds three workers, a second reuses two and trims the third."""
    git, suite = _patch_git(monkeypatch, Git()), _suite_reading_the_mutant()
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    held, verdicts, marks = _spy_owners(monkeypatch), [], []
    for workers in (3, 2):
        verdicts.append(mutate_pool.run_mutants(root, _cfg(workers), MUTANTS, lambda *a: None))
        marks.append(len(git.calls))
    git.second = sorted(call[:2] for call in git.calls[marks[0]:])
    return verdicts, git, suite, held


def test_verdicts_keep_mutant_order_and_the_source_tree_is_untouched(monkeypatch, tmp_path):
    root = _repo(tmp_path)
    verdicts, git, _, _ = _run_twice(monkeypatch, root)
    assert verdicts == [[True, False, True], [True, False, True]]
    assert (root / "src/a.py").read_bytes() == SOURCE
    assert sorted(call[:2] for call in git.calls if call[0] == "add") == [
        ("add", "w0"), ("add", "w1"), ("add", "w2")]
    assert git.second == [("remove", "w2"), ("reset", "w0"), ("reset", "w1")]


def test_every_git_call_and_suite_run_carries_the_pool_lease_owner(monkeypatch, tmp_path):
    _, git, suite, held = _run_twice(monkeypatch, _repo(tmp_path))
    owners = [call[2] for call in git.calls] + [run.owner for run in suite.runs]
    assert held and None not in owners
    assert _registered(owners, held)


def test_every_suite_run_gets_the_configured_deadline_and_no_bytecode(monkeypatch, tmp_path):
    _, _, suite, _ = _run_twice(monkeypatch, _repo(tmp_path))
    assert len(suite.runs) == 8  # a baseline and three mutants, twice
    assert {run.timeout for run in suite.runs} == {41}
    assert all(run.env == {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"} for run in suite.runs)


def test_a_head_that_moves_while_inputs_are_read_refuses(monkeypatch, tmp_path):
    git = _patch_git(monkeypatch, Git())
    heads = iter(["c0ffee", "decade"])
    monkeypatch.setattr(mutate_pool, "head_commit", lambda root: next(heads))
    with pytest.raises(ToolError) as refused:
        mutate_pool._input_snapshot(_repo(tmp_path), [], Path(".crapkit"))
    assert str(refused.value) == "HEAD changed while preparing mutation inputs; rerun mutate"
    assert git.calls == []


def test_a_head_that_moves_while_workers_are_built_refuses(monkeypatch, tmp_path):
    git = _patch_git(monkeypatch, Git())
    git.on_add = lambda: setattr(git, "head", "decade")
    monkeypatch.setattr(mutate_pool, "run_bounded", Suite())
    with pytest.raises(ToolError) as refused:
        mutate_pool.run_mutants(_repo(tmp_path), _cfg(1), MUTANTS[:1], lambda *a: None)
    assert str(refused.value) == "HEAD changed while preparing mutation workers; rerun mutate"


# --- the baseline and one mutant -------------------------------------------------------

def _refusal(tree, runner, verdict, command):
    return (f"mutation_command {runner!r} {verdict} on the UNMUTATED tree, so every mutant "
            f"would read as killed and the score would be 100% {DASH} run `{command}` in "
            f"{tree} and fix it before scoring")


@pytest.mark.parametrize("code, verdict", [(9009, "exits 9009"), (None, "timed out")])
def test_a_baseline_that_fails_is_refused_in_full(monkeypatch, tmp_path, code, verdict):
    monkeypatch.setattr(mutate_pool, "run_bounded", Suite(code))
    with pytest.raises(ToolError) as refused:
        mutate_pool.require_live_suite(tmp_path, _cfg(), owner=Owner())
    assert str(refused.value) == _refusal(tmp_path, "python", verdict, "python -m pytest -q -x")


def test_the_baseline_runs_with_the_deadline_owner_and_suite_environment(monkeypatch, tmp_path):
    suite, owner = Suite(0), Owner()
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    assert mutate_pool.require_live_suite(tmp_path, _cfg(), owner=owner) is None
    [run] = suite.runs
    assert (run.command, run.timeout, run.cwd, run.owner) == ("python -m pytest -q -x", 41,
                                                              tmp_path, owner)
    assert run.env == {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


@pytest.mark.parametrize("command, word", [('"C:/Py 3/python.exe" -m pytest', "C:/Py 3/python.exe"),
                                           ("  ", "  ")])
def test_the_runner_word_is_the_first_shell_word_or_the_whole_command(command, word):
    assert mutate_pool._runner_word(command) == word


def _tree(tmp_path, data: bytes) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_bytes(data)
    return tmp_path


LINE2 = Mutant("src/a.py", 2, "if a < b:", "if a <= b:", "< -> <=")


def test_the_suite_sees_the_mutated_bytes_exactly(monkeypatch, tmp_path):
    before = "s = 'caf\u00e9 \u2192'\nif a < b:\n    pass\n".encode("utf-8")
    tree, suite = _tree(tmp_path, before), Suite(1, look=lambda cwd: (cwd / "src/a.py").read_bytes())
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    assert mutate_pool.run_one(tree, _cfg(), LINE2) is True
    assert suite.runs[0].seen == "s = 'caf\u00e9 \u2192'\nif a <= b:\n    pass\n".encode("utf-8")
    assert (tree / "src/a.py").read_bytes() == before


def test_an_undecodable_byte_still_gets_a_verdict_and_comes_back(monkeypatch, tmp_path):
    before = b"s = 'caf\xe9'\nif a < b:\n    pass\n"
    tree, suite = _tree(tmp_path, before), Suite(0, look=lambda cwd: (cwd / "src/a.py").read_bytes())
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    assert mutate_pool.run_one(tree, _cfg(), LINE2) is False
    assert b"if a <= b:\n" in suite.runs[0].seen
    assert (tree / "src/a.py").read_bytes() == before


def test_no_bytecode_cache_is_left_beside_the_mutated_file(monkeypatch, tmp_path):
    tree = _tree(tmp_path, b"x = 1\nif a < b:\n    pass\n")
    (tree / "src/__pycache__").mkdir()
    (tree / "src/__pycache__/a.cpython-312.pyc").write_bytes(b"stale")
    suite = Suite(1, look=lambda cwd: (cwd / "src/__pycache__").exists())
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    mutate_pool.run_one(tree, _cfg(), LINE2)
    assert suite.runs[0].seen is False


@pytest.mark.parametrize("code, killed", [(0, False), (1, True), (None, True)])
def test_a_mutant_is_killed_by_any_failure_or_the_deadline(monkeypatch, tmp_path, code, killed):
    suite, owner = Suite(code), Owner()
    monkeypatch.setattr(mutate_pool, "run_bounded", suite)
    tree = _tree(tmp_path, b"x = 1\nif a < b:\n    pass\n")
    assert mutate_pool.run_one(tree, _cfg(), LINE2, owner=owner) is killed
    [run] = suite.runs
    assert (run.timeout, run.owner) == (41, owner)
    assert run.env == {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


# --- shards, threads and cancellation --------------------------------------------------

def test_a_cancelled_shard_returns_what_it_finished_and_reports_each_verdict(monkeypatch):
    cancelled, reported, owner = threading.Event(), [], Owner()
    monkeypatch.setattr(mutate_pool, "run_one", lambda tree, cfg, m, owner=None: m == "kill")

    def report(index, mutant, killed):
        reported.append((index, killed))
        cancelled.set()

    done = mutate_pool._run_shard(Path("w0"), _cfg(), [(4, "kill"), (7, "live")], report,
                                  owner, cancelled)
    assert done == [(4, True)] and reported == [(4, True)]


def test_each_shard_runs_under_the_owner_it_was_handed(monkeypatch):
    seen, owner = [], Owner()
    monkeypatch.setattr(mutate_pool, "run_one",
                        lambda tree, cfg, m, owner=None: seen.append(owner) or False)
    mutate_pool._fan_out(_cfg(), [Path("w0"), Path("w1")], [[(0, "a")], [(1, "b")]],
                         lambda *a: None, owner)
    assert len(seen) == 2 and all(one is owner for one in seen)


WIDE = 33  # more than ThreadPoolExecutor's default of min(32, cpus + 4) threads


def test_every_tree_gets_its_own_thread_at_once(monkeypatch):
    gate = threading.Barrier(WIDE, timeout=HANG_SECONDS)
    trees = [Path(f"w{i}") for i in range(WIDE)]
    assert mutate_pool._on_every_tree(lambda root, tree, owner: gate.wait() >= 0, Path("r"),
                                      trees, owner=Owner()) == [True] * WIDE
    monkeypatch.setattr(mutate_pool, "run_one", lambda *a, **k: gate.wait() >= 0)
    done = mutate_pool._fan_out(_cfg(), trees, [[(i, "m")] for i in range(WIDE)],
                                lambda *a: None, Owner())
    assert sorted(done) == [(i, True) for i in range(WIDE)]


def _broken(root, tree, owner):
    raise RuntimeError(f"cannot prepare {tree}")


@pytest.mark.parametrize("fails", [None, OSError("owner gone")])
def test_a_failed_tree_cancels_the_owner_and_its_error_surfaces(fails):
    owner = Owner(fails)
    with pytest.raises(RuntimeError, match="cannot prepare w0") as raised:
        mutate_pool._on_every_tree(_broken, Path("r"), [Path("w0")], owner=owner)
    assert owner.cancels == 1 and raised.value.__cause__ is fails


@pytest.mark.parametrize("fails", [None, OSError("owner gone")])
def test_a_failed_shard_cancels_the_owner_and_its_error_surfaces(monkeypatch, fails):
    owner = Owner(fails)
    monkeypatch.setattr(mutate_pool, "run_one", lambda *a, **k: _broken(None, "w0", None))
    with pytest.raises(RuntimeError, match="cannot prepare w0") as raised:
        mutate_pool._fan_out(_cfg(), [Path("w0")], [[(0, "m")]], lambda *a: None, owner)
    assert owner.cancels == 1 and raised.value.__cause__ is fails


def test_each_tree_action_gets_the_owner():
    owner, seen = Owner(), []
    mutate_pool._on_every_tree(lambda root, tree, owner: seen.append(owner), Path("r"),
                               [Path("w0"), Path("w1")], owner=owner)
    assert seen == [owner, owner]


def test_progress_lines_number_from_one_against_the_total():
    stream = io.StringIO()
    report = mutate_pool.reporter(7, stream)
    report(2, Mutant("src/a.py", 12, "a < b", "a <= b", "< -> <="), True)
    report(6, Mutant("src/b.py", 3, "a < b", "a >= b", "< -> >="), False)
    assert stream.getvalue() == ("  mutant 3/7 src/a.py:12 [< -> <=] killed\n"
                                 "  mutant 7/7 src/b.py:3 [< -> >=] SURVIVED\n")


# --- inputs, links and paths -----------------------------------------------------------

def test_seeding_makes_new_directories_and_tolerates_a_deletion_already_gone(tmp_path):
    files = {"new/deep/f.py": (b"x = 1\n", 0o644), "gone.py": None}
    mutate_pool._seed(tmp_path, files)
    assert (tmp_path / "new/deep/f.py").read_bytes() == b"x = 1\n"
    assert not (tmp_path / "gone.py").exists()


class _Junction:
    """What os.lstat reports for a Windows junction (Python docs,
    os.stat_result.st_file_attributes): a directory mode with the reparse bit."""

    def __str__(self):
        return "C:\\repo\\link"

    def lstat(self):
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755,
                               st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)


def test_a_reparse_point_is_refused_as_a_link():
    with pytest.raises(ToolError) as refused:
        mutate_pool._refuse_link(_Junction())
    assert str(refused.value) == "mutation path C:\\repo\\link is a link; use private regular files"


@pytest.mark.platform("win32")
def test_a_real_junction_in_a_mutation_path_is_refused(tmp_path):
    import _winapi
    (tmp_path / "target").mkdir()
    _winapi.CreateJunction(str(tmp_path / "target"), str(tmp_path / "link"))
    with pytest.raises(ToolError) as refused:
        mutate_pool._unlinked_path(tmp_path, "link/f.py")
    link = Path(os.path.abspath(tmp_path / "link"))
    assert str(refused.value) == f"mutation path {link} is a link; use private regular files"


def test_a_path_that_escapes_the_workspace_is_refused(tmp_path):
    root = tmp_path / "repo"
    with pytest.raises(ToolError) as refused:
        mutate_pool._unlinked_path(root, "../outside.py")
    escaped, base = Path(os.path.abspath(tmp_path / "outside.py")), Path(os.path.abspath(root))
    assert str(refused.value) == f"mutation path {escaped} escapes its workspace {base}"


# --- the kept pool ---------------------------------------------------------------------

def _pool(tmp_path, *names) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    base = mutate_pool.pool_dir(root)
    for name in names:
        (base / name).mkdir(parents=True)
    return root, base


def test_a_surplus_worker_git_cannot_remove_is_refused(monkeypatch, tmp_path):
    root, base = _pool(tmp_path, "w0", "w3")
    git = _patch_git(monkeypatch, Git(remove=False))
    owner = Owner()
    with pytest.raises(ToolError) as refused:
        mutate_pool._trim_pool(root, base, [base / "w0"], owner=owner)
    assert str(refused.value) == "could not remove surplus mutation workers"
    assert [(call[:2], call[2] is owner) for call in git.calls] == [(("remove", "w3"), True)]


def _pool_lock(root: Path) -> Path:
    return root / ".crapkit" / "mutate-pool.lock"


def test_drop_pool_refuses_while_a_run_holds_the_pool(tmp_path):
    root, _ = _pool(tmp_path, "w0")
    with exclusive_lock(_pool_lock(root), label="a running mutate"):
        with pytest.raises(ToolError) as refused:
            mutate_pool.drop_pool(root)
    assert str(refused.value) == ("mutation worktree pool is in use; retry --drop-pool "
                                  "after the run finishes")


@contextmanager
def _undeletable(folder: Path):
    """A file under `folder` this process cannot delete until the block ends: open on
    Windows, which refuses to delete an open file; under a read-only directory elsewhere."""
    stuck = folder / "stuck"
    stuck.mkdir(parents=True)
    (stuck / "held").write_bytes(b"x")
    if os.name == "nt":
        with (stuck / "held").open("rb"):
            yield
        return
    stuck.chmod(0o500)
    try:
        yield
    finally:
        stuck.chmod(0o700)


def test_drop_pool_removes_every_worker_under_its_owner_even_past_a_stuck_file(monkeypatch,
                                                                               tmp_path):
    root, base = _pool(tmp_path, "w0", "w1")
    git, held = _patch_git(monkeypatch, Git()), _spy_owners(monkeypatch)
    with _undeletable(base):
        dropped = mutate_pool.drop_pool(root)
    assert dropped == [base / "w0", base / "w1"]
    assert sorted(call[:2] for call in git.calls) == [("remove", "w0"), ("remove", "w1")]
    assert _registered([call[2] for call in git.calls], held)


def test_a_failed_build_cleanup_leaves_a_pool_another_run_holds(tmp_path):
    root, _ = _pool(tmp_path, "w0")
    lock = _pool_lock(root)
    with exclusive_lock(lock, label="a running mutate"):
        assert mutate_pool._clean_failed_pool(root, 2, lock, RuntimeError("build")) is None


def test_a_failed_build_cleanup_removes_the_pool_under_its_owner(monkeypatch, tmp_path):
    root, _ = _pool(tmp_path, "w0", "w1")
    git, held = _patch_git(monkeypatch, Git()), _spy_owners(monkeypatch)
    mutate_pool._clean_failed_pool(root, 2, _pool_lock(root), RuntimeError("build"))
    assert sorted(call[:2] for call in git.calls) == [("remove", "w0"), ("remove", "w1")]
    assert _registered([call[2] for call in git.calls], held)


# --- temporary runs --------------------------------------------------------------------

def _temporary(tmp_path, workers, text=None) -> tuple[Path, Path]:
    """A temporary run as a concurrent mutate leaves it: receipt, lease, trees."""
    root = tmp_path / "repo\u00e9"
    base = root / ".crapkit/mutate-tmp" / RUN
    base.mkdir(parents=True)
    if text is None:
        mutate_pool._write_temporary_receipt(root, base, workers)
    else:
        (base / "owner.json").write_text(text, encoding="utf-8")
    mutate_pool._temporary_lease(root, base).parent.mkdir(parents=True, exist_ok=True)
    mutate_pool._temporary_lease(root, base).write_bytes(b"")
    return root, base


@pytest.mark.parametrize("workers", [1, 100])
def test_a_temporary_run_of_up_to_100_workers_is_recognized(tmp_path, workers):
    root, base = _temporary(tmp_path, workers)
    assert mutate_pool._temporary_trees(root, base) == [base / f"w{i}" for i in range(workers)]


@rulings.applies("SS6")
def test_ss6_a_temporary_run_of_101_workers_is_recognized(tmp_path):
    root, base = _temporary(tmp_path, 101)
    found = mutate_pool._recover_temporary(root, base, True)
    rulings.pin_ruling("SS6", crapkit=found.status, oracle="planned")


def test_a_utf8_receipt_naming_a_non_ascii_root_is_recognized(tmp_path):
    root = tmp_path / "repo\u00e9"
    root.mkdir()
    receipt = {"version": 1, "root": str(root.resolve()), "run": RUN, "workers": 1}
    root, base = _temporary(tmp_path, 1, json.dumps(receipt, ensure_ascii=False))
    assert mutate_pool._temporary_trees(root, base) == [base / "w0"]


@pytest.mark.parametrize("change, reason", [
    ({"workers": 0}, "temporary mutation receipt has an invalid worker count"),
    ({"workers": "2"}, "temporary mutation receipt has an invalid worker count"),
    ({"run": "cd" * 16}, "temporary mutation receipt does not match this directory"),
])
def test_a_receipt_that_proves_nothing_leaves_the_run_unproven(tmp_path, change, reason):
    root = tmp_path / "repo\u00e9"
    root.mkdir()
    receipt = {"version": 1, "root": str(root.resolve()), "run": RUN, "workers": 1, **change}
    root, base = _temporary(tmp_path, 1, json.dumps(receipt))
    assert mutate_pool._recover_temporary(root, base, False) == mutate_pool.TemporaryRecovery(
        base, "unproven", reason)


def test_a_temporary_run_in_use_stays_active(tmp_path):
    root, base = _temporary(tmp_path, 2)
    with exclusive_lock(mutate_pool._temporary_lease(root, base), label="a running mutate"):
        found = mutate_pool._recover_temporary(root, base, False)
    assert found == mutate_pool.TemporaryRecovery(base, "active",
                                                  "temporary mutation worktrees are in use")


def test_an_abandoned_temporary_run_is_removed_under_its_owner(monkeypatch, tmp_path):
    root, base = _temporary(tmp_path, 2)
    git, held = _patch_git(monkeypatch, Git()), _spy_owners(monkeypatch)
    assert mutate_pool._recover_temporary(root, base, True).status == "planned"
    found = mutate_pool._recover_temporary(root, base, False)
    assert found == mutate_pool.TemporaryRecovery(base, "recovered") and not base.exists()
    assert sorted(call[:2] for call in git.calls) == [("remove", "w0"), ("remove", "w1")]
    assert _registered([call[2] for call in git.calls], held)


def test_a_teardown_that_fails_reads_failed_with_its_reason(monkeypatch, tmp_path):
    root, base = _temporary(tmp_path, 1)
    git = _patch_git(monkeypatch, Git())

    def refuse(root, path, *, owner=None):
        raise ToolError(f"git worktree remove {Path(path).name} failed")

    monkeypatch.setattr(mutate_pool, "worktree_remove", refuse)
    found = mutate_pool._recover_temporary(root, base, False)
    assert found == mutate_pool.TemporaryRecovery(base, "failed", "git worktree remove w0 failed")
    assert git.calls == []
