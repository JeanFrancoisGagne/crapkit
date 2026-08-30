"""Where mutants actually run. One worker (the default) mutates the live
working tree, exactly as this command always has. `mutation_workers = N` gives
each worker its own detached git worktree instead, because a mutant is a write
to a source file: two of them in one tree would read each other's edits.

Two things the parallel path owes the serial one. Results merge by the mutant's
position in the list, never by who finished first, so the same tree reports the
same JSON at any worker count. And the worktree is a checkout of HEAD while
`mutate` is diff-scoped against the working tree, so the targeted files are
copied in as they are on disk — uncommitted lines are the ones being mutated.

The command runs with cwd set to the worktree. A consumer whose test command
resolves the code under test from somewhere else (an editable install pointing
at the main checkout, a global site-packages copy) would measure unmutated
code and score every mutant a survivor: keep the command cwd-relative.

Those worktrees are KEPT, at `<root>/.crapkit/mutate-pool/w0..wN`. Building four
of them costs 35.5 s on a 31,459-file repo and re-preparing the kept four costs
0.44 s, and `mutate` is diff-scoped, so that build was most of a run's wall
clock. Re-preparing is `git checkout --force <sha>` naming the main repo's HEAD,
then `git clean -xdff`: the last run's mutant goes back, the last suite's
artifacts go away, and a commit made since lands. What is left on disk is four
checkouts, which `crapkit mutate --drop-pool` removes.

The pool is one directory shared by every run in the repo, where mkdtemp gave
each run its own, so entry takes an exclusive lock on `.crapkit/mutate-pool/`.
A second `mutate` in the same repo finds it held and falls back to the old
throwaway base: slower, never the first run's tree with a second run's mutant.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from .errors import GitError
from .gitio import head_commit, worktree_add, worktree_remove, worktree_reset
from .mutate import apply_mutant
from .procs import run_bounded

try:  # the lock, through whichever of the two the platform has
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


def run_one(tree: Path, cfg, mutant) -> bool:
    """True = killed. The original file ALWAYS comes back, whatever happens."""
    p = tree / mutant.path
    original = p.read_bytes()
    # Python validates .pyc files by source SIZE + mtime in WHOLE SECONDS: two
    # same-size mutants applied within one second would reuse the first one's
    # stale bytecode and read as false survivors. Kill the cache, write none.
    shutil.rmtree(p.parent / "__pycache__", ignore_errors=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
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
                           cwd=tree, env=env) != 0
    finally:
        p.write_bytes(original)


def _shards(indexed: list, workers: int) -> list[list]:
    """Round-robin: worker w takes mutants w, w+K, w+2K. Even to within one
    mutant however the list is shaped, and each shard keeps list order."""
    return [indexed[w::workers] for w in range(workers)]


def _merge(done: list) -> list[bool]:
    """(index, killed) pairs from every worker back into mutant order."""
    return [killed for _, killed in sorted(done)]


def _run_shard(tree: Path, cfg, shard: list, report) -> list:
    out = []
    for index, mutant in shard:
        killed = run_one(tree, cfg, mutant)
        report(index, mutant, killed)
        out.append((index, killed))
    return out


def _seed(root: Path, tree: Path, rel_paths: list) -> None:
    """The worktree checked out HEAD; the mutants were grown from the working
    tree. Copy the targeted files over so both agree on what line 40 is."""
    for rel in rel_paths:
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, dst)


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
    with _pool_lock(root) as held:
        keeper = _pooled(root, count) if held else _throwaway(root, count)
        with keeper as trees:
            yield trees


@contextmanager
def _pooled(root: Path, count: int):
    """The kept set, re-prepared on the way in and LEFT ON DISK on the way out.

    A build that fails takes the whole pool with it, because half a pool reused
    is silent where a fresh add that dies is loud. A run that fails does not:
    its trees are dirty, and dirty is what the next run's re-prepare is for.
    """
    base = pool_dir(root)
    trees = [base / f"w{i}" for i in range(count)]
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
    leaves an entry in `git worktree list` that outlives the directory. The base
    stays, because the lock this process is holding is a file inside it."""
    if trees:
        _on_every_tree(worktree_remove, root, trees)


def drop_pool(root: Path) -> list:
    """Every kept checkout removed, and the base with it. Returns what was
    there, for the command that prints it."""
    base = pool_dir(root)
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
    base.mkdir(parents=True, exist_ok=True)
    handle = os.open(base / ".lock", os.O_CREAT | os.O_RDWR)
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


def _fan_out(cfg, trees: list, shards: list, report) -> list:
    with ThreadPoolExecutor(max_workers=len(trees)) as pool:
        futures = [pool.submit(_run_shard, tree, cfg, shard, report)
                   for tree, shard in zip(trees, shards)]
        return [pair for f in futures for pair in f.result()]


def _run_parallel(root: Path, cfg, mutants: list, workers: int, report) -> list[bool]:
    shards = _shards(list(enumerate(mutants)), workers)
    targets = sorted({m.path for m in mutants})
    with _worktrees(root, workers) as trees:
        for tree in trees:
            _seed(root, tree, targets)
        done = _fan_out(cfg, trees, shards, report)
    return _merge(done)


def run_mutants(root: Path, cfg, mutants: list, report) -> list[bool]:
    """One killed flag per mutant, in mutant order. Workers past the mutant
    count would only pay for empty worktrees."""
    workers = min(cfg.mutation_workers, len(mutants))
    if workers > 1:
        return _run_parallel(root, cfg, mutants, workers, report)
    return _merge(_run_shard(root, cfg, list(enumerate(mutants)), report))


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
