"""Run every mutant in a private detached worktree, at any worker count.

Capture changed and untracked inputs once, including tests, configuration and
deletions, then apply that snapshot to every worker's checkout of HEAD. Results
merge in mutant order. Neither the runner nor a mutant writes to the source tree.

The command runs with cwd set to the worktree. A consumer whose test command
resolves the code under test from somewhere else (an editable install pointing
at the main checkout, a global site-packages copy) would measure unmutated
code and score every mutant a survivor: keep the command cwd-relative.

Keep worktrees at `<root>/.crapkit/mutate-pool/w0..wN` for reuse. Resetting them
to HEAD and cleaning them removes the preceding run's changes. Ignored local
dependencies are not copied; the configured command supplies its own setup.

An OS lock at `.crapkit/mutate-pool.lock` protects reuse and removal. A second
run uses temporary worktrees while the pool is occupied. Cleanup refuses an
occupied pool, and removing the pool never removes the lock file's identity.
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from .config import shell_words
from .errors import GitError, ToolError
from .gitio import head_commit, status_names, worktree_add, worktree_remove, worktree_reset, worktree_root
from .mutate import apply_mutant
from .procs import own_processes, run_bounded

try:  # the lock, through whichever of the two the platform has
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


def _suite_env() -> dict:
    """Python validates .pyc files by source SIZE and whole-second mtime, so a
    written cache would answer for the next mutant of the same size."""
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def require_live_suite(tree: Path, cfg, *, owner=None) -> None:
    """Refuse to score anything until the command has passed once with nothing
    mutated.

    `run_one` collapses every failure mode into one boolean, so a command that
    cannot run here reads as a suite that killed every mutant. docs ship
    `mutation_command = "python -m pytest -q -x"`, a bare name, and on a machine
    whose PATH `python` is not the one holding pytest — a hook, a cron, cmd.exe,
    the Windows Store stub that exits 9009 — every mutant exits nonzero without
    ever importing the code under test and `mutate` prints a 100% score for a
    suite that never ran. A baseline that fails is a different sentence from a
    mutation score, so it is said instead of one.
    """
    code = run_bounded(cfg.mutation_command, cfg.mutation_timeout_seconds,
                       cwd=tree, env=_suite_env(), owner=owner)
    if code == 0:
        return
    raise ToolError(f"mutation_command {_runner_word(cfg.mutation_command)!r} "
                    f"{_baseline_verdict(code)} on the UNMUTATED tree, so every mutant "
                    "would read as killed and the score would be 100% — run "
                    f"`{cfg.mutation_command}` in {tree} and fix it before scoring")


def _runner_word(command: str) -> str:
    """The word the shell will try to start, read the way that shell reads it."""
    words = shell_words(command)
    return words[0] if words else command


def _baseline_verdict(code: int | None) -> str:
    return "timed out" if code is None else f"exits {code}"


def run_one(tree: Path, cfg, mutant, *, owner=None) -> bool:
    """True = killed. The original file ALWAYS comes back, whatever happens."""
    p = _private_file(tree, mutant.path)
    original = p.read_bytes()
    # Python validates .pyc files by source SIZE + mtime in WHOLE SECONDS: two
    # same-size mutants applied within one second would reuse the first one's
    # stale bytecode and read as false survivors. Kill the cache, write none.
    shutil.rmtree(p.parent / "__pycache__", ignore_errors=True)
    env = _suite_env()
    try:
        p.write_text(apply_mutant(original.decode("utf-8", "replace"), mutant),
                     encoding="utf-8", newline="")
        # run_bounded, not subprocess.run: the command is a whole test suite
        # under a shell, and the timeout has to kill the tree. run()'s timeout
        # kills the shell alone and leaves the suite running, so a mutant that
        # loops forever was scored dead while its suite ran on to the end -
        # one of them per mutant, all at once on the single-worker path.
        # None is the deadline: a mutant that loops forever is dead.
        return run_bounded(cfg.mutation_command, cfg.mutation_timeout_seconds,
                           cwd=tree, env=env, owner=owner) != 0
    finally:
        _private_file(tree, mutant.path).write_bytes(original)


def _shards(indexed: list, workers: int) -> list[list]:
    """Round-robin: worker w takes mutants w, w+K, w+2K. Even to within one
    mutant however the list is shaped, and each shard keeps list order."""
    return [indexed[w::workers] for w in range(workers)]


def _merge(done: list) -> list[bool]:
    """(index, killed) pairs from every worker back into mutant order."""
    return [killed for _, killed in sorted(done)]


def _run_shard(tree: Path, cfg, shard: list, report, owner) -> list:
    out = []
    for index, mutant in shard:
        killed = run_one(tree, cfg, mutant, owner=owner)
        report(index, mutant, killed)
        out.append((index, killed))
    return out


def _input_snapshot(root: Path, targets: list, state: Path) -> tuple[str, dict]:
    """Freeze every working-tree change once, including tests and deletions."""
    head = head_commit(root)
    paths = sorted(set(status_names(root)) | set(targets))
    files = {rel: _snapshot_file(_unlinked_path(root, rel)) for rel in paths
             if not Path(rel).is_relative_to(state)}
    if head_commit(root) != head:
        raise ToolError("HEAD changed while preparing mutation inputs; rerun mutate")
    return head, files


def _snapshot_file(path: Path) -> tuple[bytes, int] | None:
    if not path.exists():
        return None
    return path.read_bytes(), path.stat().st_mode


def _refuse_link(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    reparse = getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ToolError(f"mutation path {path} is a link; use private regular files")


def _unlinked_path(root: Path, relative) -> Path:
    """Check each component without following symlinks or Windows reparse points."""
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(root / relative))
    if not path.is_relative_to(root):
        raise ToolError(f"mutation path {path} escapes its workspace {root}")
    current = root
    _refuse_link(current)
    for part in path.relative_to(root).parts:
        current /= part
        _refuse_link(current)
    return path


def _private_file(root: Path, relative) -> Path:
    path = _unlinked_path(root, relative)
    if path.exists() and path.stat().st_nlink > 1:
        raise ToolError(f"mutation path {path} has multiple links; use private regular files")
    return path


def _seed(tree: Path, files: dict) -> None:
    """Apply the same captured bytes and deletions to every private worker."""
    destinations = {rel: _private_file(tree, rel) for rel in files}
    for rel, saved in files.items():
        dst = destinations[rel]
        if saved is None:
            dst.unlink(missing_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(saved[0])
        dst.chmod(saved[1])


def _on_every_tree(action, root: Path, trees: list) -> None:
    """`action(root, tree)` on one thread per tree, and WAIT FOR THEM ALL, even
    once one has raised. Leaving a checkout in flight is what turns a failed add
    into a leaked worktree: the cleanup walks the list, finds nothing at that
    path yet, and the abandoned thread creates it a moment later."""
    with ThreadPoolExecutor(max_workers=len(trees)) as pool:
        for done in [pool.submit(action, root, tree) for tree in trees]:
            done.result()


def pool_dir(root: Path) -> Path:
    """Where the kept checkouts live. Under `.crapkit/`, which is the directory
    crapkit already writes its cache and store into and which consuming repos
    already ignore, so the pool adds no top-level noise to `git status` that
    running crapkit at all did not already add."""
    return root / ".crapkit" / "mutate-pool"


@contextmanager
def _worktrees(root: Path, count: int):
    """N detached checkouts for the workers to run in.

    Kept between runs when this process wins the pool's lock, thrown away when
    a peer run holds it. Either way the caller gets `count` trees and hands
    them back at the end of the block.
    """
    with _owned_worktrees(root, count) as (trees, owner):
        yield trees


@contextmanager
def _owned_worktrees(root: Path, count: int):
    lock = _unlinked_path(root, pool_dir(root).parent / "mutate-pool.lock")
    with own_processes([lock], optional=True, label="mutation worktree pool") as owner:
        keeper = _pooled(root, count) if owner.held else _throwaway(root, count)
        with keeper as trees:
            yield trees, owner


@contextmanager
def _pooled(root: Path, count: int):
    """The kept set, re-prepared on the way in and LEFT ON DISK on the way out.

    A build that fails takes the whole pool with it, because half a pool reused
    is silent where a fresh add that dies is loud. A run that fails does not:
    its trees are dirty, and dirty is what the next run's re-prepare is for.
    """
    base = pool_dir(root)
    trees = [base / f"w{i}" for i in range(count)]
    for tree in trees:
        _unlinked_path(root, tree)
    try:
        _stock(root, base, trees, head_commit(root))
    except BaseException:
        _drop(root, base, trees)
        raise
    yield trees


def _stock(root: Path, base: Path, trees: list, head: str) -> None:
    """Every tree at `head` with nothing of the last run in it, however it got
    there: re-prepared when the pool is there to re-prepare, built when it is
    not."""
    if all(tree.is_dir() for tree in trees) and _reprepared(root, trees, head):
        return
    _drop(root, base, trees)
    _on_every_tree(worktree_add, root, trees)


def _reprepared(root: Path, trees: list, head: str) -> bool:
    """False, not a raise, when git refuses one of them. A directory git no
    longer knows as a worktree — a killed run, a hand-deleted admin entry, a
    copied folder — is a pool to rebuild, not a `mutate` to fail."""
    try:
        _on_every_tree(partial(_reset_to, head), root, trees)
    except GitError:
        return False
    return True


def _reset_to(head: str, root: Path, tree: Path) -> None:
    """`_on_every_tree` hands its action (root, tree); the commit rides in
    front. The main repo's sha, never the literal HEAD, which inside a linked
    worktree means the commit that worktree was built at."""
    worktree_reset(tree, head)


def _drop(root: Path, base: Path, trees: list) -> None:
    """Through `worktree remove`, never an rmtree alone: an abandoned checkout
    leaves an entry in `git worktree list` that outlives the directory."""
    for tree in trees:
        _unlinked_path(base, tree)
    if trees:
        _on_every_tree(worktree_remove, root, trees)


def drop_pool(root: Path) -> list:
    """Every kept checkout removed, and the base with it. Returns what was
    there, for the command that prints it."""
    base = _unlinked_path(root, pool_dir(root))
    if not base.exists():
        return []
    with _pool_lock(root) as held:
        if not held:
            raise ToolError("mutation worktree pool is in use; retry --drop-pool after the run finishes")
        trees = sorted(p for p in base.glob("w*") if p.is_dir())
        _drop(root, base, trees)
        shutil.rmtree(base, ignore_errors=True)
        return trees


@contextmanager
def _throwaway(root: Path, count: int):
    """What every run did before the pool, and what the second concurrent run
    in one repo still does: a private base, gone on the way out — after a clean
    run, after an exception, and after an add that failed halfway through.

    The adds run together, and so do the removes. Each one is a full checkout
    that spends its time waiting on the disk rather than on a core, so four of
    them serialized cost 54.5 s on a 31,620-file repo against 25.1 s overlapped.
    """
    base = Path(tempfile.mkdtemp(prefix="crapkit-mutate-"))
    trees = [base / f"w{i}" for i in range(count)]
    try:
        _on_every_tree(worktree_add, root, trees)
        yield trees
    finally:
        _teardown(root, base, trees)


def _teardown(root: Path, base: Path, trees: list) -> None:
    _on_every_tree(worktree_remove, root, trees)
    shutil.rmtree(base, ignore_errors=True)


@contextmanager
def _pool_lock(root: Path):
    """Exclusive, and an answer rather than a wait: False means a peer run owns
    the pool and this one takes the throwaway path.

    The OS holds it, so it dies with the process. A lock file created and
    deleted by hand would survive a killed run and lock every later run out of
    the pool for good — a 35 s regression per run that nothing reports.
    """
    base = pool_dir(root)
    base.parent.mkdir(parents=True, exist_ok=True)
    # Outside the removable pool: deleting a held lock creates two lock owners.
    handle = os.open(base.parent / "mutate-pool.lock", os.O_CREAT | os.O_RDWR)
    held = _take_lock(handle)
    try:
        yield held
    finally:
        _drop_lock(handle, held)
        os.close(handle)


def _take_lock(handle: int) -> bool:
    """True = this process owns the pool now."""
    try:
        if msvcrt is not None:
            msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _drop_lock(handle: int, held: bool) -> None:
    if not held:
        return
    if msvcrt is not None:
        os.lseek(handle, 0, os.SEEK_SET)
        msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle, fcntl.LOCK_UN)


def _fan_out(cfg, trees: list, shards: list, report, owner) -> list:
    with ThreadPoolExecutor(max_workers=len(trees)) as pool:
        futures = [pool.submit(_run_shard, tree, cfg, shard, report, owner)
                   for tree, shard in zip(trees, shards)]
        return [pair for f in futures for pair in f.result()]


def _run_parallel(root: Path, cfg, mutants: list, workers: int, report) -> list[bool]:
    shards = _shards(list(enumerate(mutants)), workers)
    checkout = worktree_root(root)
    prefix = root.resolve().relative_to(checkout)
    targets = sorted({(prefix / m.path).as_posix() for m in mutants})
    head, files = _input_snapshot(checkout, targets, prefix / ".crapkit")
    with _owned_worktrees(root, workers) as (trees, owner):
        if head_commit(root) != head:
            raise ToolError("HEAD changed while preparing mutation workers; rerun mutate")
        for tree in trees:
            _seed(tree, files)
        # In the worker's own checkout, which is where the mutants will run: a
        # baseline taken at the root would clear a command the worktree cannot
        # start.
        execution_roots = [tree / prefix for tree in trees]
        require_live_suite(execution_roots[0], cfg, owner=owner)
        done = _fan_out(cfg, execution_roots, shards, report, owner)
    return _merge(done)


def run_mutants(root: Path, cfg, mutants: list, report) -> list[bool]:
    """One killed flag per mutant, in mutant order. Workers past the mutant
    count would only pay for empty worktrees.

    No mutants is no score to protect. The baseline is there to stop a broken
    runner from printing 100%, and an empty run prints no percentage at all: a
    diff with nothing mutable returned at exit 0 without starting anything, and
    running a whole test suite for it would be a new bill and a new exit 5 on an
    invocation that was free.
    """
    if not mutants:
        return []
    workers = min(cfg.mutation_workers, len(mutants))
    return _run_parallel(root, cfg, mutants, workers, report)


def reporter(total: int, stream):
    """Progress lines, one per finished mutant. Workers race, so the write is
    locked: a half-written line read as a survivor list is worse than no line."""
    lock = threading.Lock()

    def report(index: int, mutant, killed: bool) -> None:
        verdict = "killed" if killed else "SURVIVED"
        with lock:
            print(f"  mutant {index + 1}/{total} {mutant.path}:{mutant.line} "
                  f"[{mutant.op}] {verdict}", file=stream)

    return report
