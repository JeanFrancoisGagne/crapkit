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
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from .gitio import worktree_add, worktree_remove
from .mutate import apply_mutant
from .procs import run_bounded


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


@contextmanager
def _worktrees(root: Path, count: int):
    """N detached checkouts, all gone on the way out — after a clean run, after
    an exception, and after an add that failed halfway through the set.

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
