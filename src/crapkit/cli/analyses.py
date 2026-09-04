"""The standalone analyses that read a snapshot or the working tree rather than
producing one: `coupling` (files that co-change), `mutate` (diff-scoped mutation
testing), `duplication` (near-duplicate functions) and `mcp` (the stdio server
that exposes the read-side tools)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..churn_log import log_lines
from ..errors import ConfigError, CrapkitError
from ..gitio import ls_files
from ..invocation import _self
from ._shared import (_command_root, _file_sizer, _load_repo_config, _load_sources, _open_store,
                      _positive_top, _print_json, _repo_relative)


def _at_default_thresholds(args: argparse.Namespace) -> bool:
    from ..coupling import DEFAULT_MIN_CONFIDENCE, DEFAULT_MIN_SUPPORT

    return (args.min_support, args.min_confidence) == (DEFAULT_MIN_SUPPORT,
                                                       DEFAULT_MIN_CONFIDENCE)


def _coupling_pairs(root: Path, cfg, args: argparse.Namespace) -> list[dict]:
    """The ranking this invocation asked for, off the cache when it can be.

    `--top` is a cut of the stored total order, so it reads warm. A support or
    confidence off the defaults asks a wider question than the file answers, so
    it pays the walk: serving it a subset of the default ranking would hide
    exactly the pairs the wider threshold was named to surface.
    """
    from ..coupling import change_coupling_lines
    from ..coupling_cache import load_coupling

    tracked = ls_files(root)
    if not _at_default_thresholds(args):
        return change_coupling_lines(log_lines(root, cfg.churn_window_months),
                                     min_support=args.min_support,
                                     min_confidence=args.min_confidence,
                                     top=args.top, tracked=set(tracked))
    # `[:None]` is the whole list, which is what `top=None` means to the ranker.
    return load_coupling(root, cfg.churn_window_months, tracked)[:args.top]


def cmd_coupling(args: argparse.Namespace) -> int:
    """Ranked over the tracked set: a pair naming a path git no longer has is a
    recommendation to open a file that is not there."""
    _positive_top("coupling", args.top)
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    pairs = _coupling_pairs(root, cfg, args)
    if args.json:
        _print_json({"pairs": pairs, "window_months": cfg.churn_window_months})
        return 0
    if not pairs:
        print(f"no coupled pairs at support>={args.min_support} "
              f"confidence>={args.min_confidence} in {cfg.churn_window_months}mo")
        return 0
    for p in pairs:
        print(f"  {p['support']:>4}x  {p['confidence']:.0%}  {p['files'][0]}  <->  {p['files'][1]}")
    return 0


def _range_lines(ranges: list) -> set[int]:
    return {line for start, end in ranges for line in range(start, end + 1)}


def _mutation_targets(root: Path, files: list | None, cwd: Path | None = None) -> dict:
    """path -> changed line set (None = whole file). Default is diff-scoped:
    only the working tree's changes vs HEAD grow mutants; `--files` are said
    from `cwd`, where the user stands."""
    from ..diffparse import changed_ranges
    from ..gitio import diff_since

    if files:
        return {_repo_relative(f, root, cwd): None for f in files}
    targets = {p: _range_lines(rs) for p, rs in changed_ranges(diff_since(root, "HEAD")).items()}
    if not targets:
        raise CrapkitError("no changes vs HEAD to mutate — name files with --files")
    return targets


def _corpus_targets(root: Path, cfg, targets: dict) -> tuple[dict, list[str]]:
    """The targets scoring would score, each dropped path named on stderr.

    `mutate` never mutates a test: the diff's files, and the ones `--files`
    names, pass through the predicate `coverage` scores with before a mutant is
    placed, or a survivor in an assertion reads as a hole in the suite. Each
    drop is said, for the reason `_file_mutants` says its refusal: a score built
    on fewer files than the diff holds must say so.
    """
    from ..mutate import OUTSIDE_CORPUS, partition_by_corpus

    kept, outside = partition_by_corpus(targets, cfg, size_of=_file_sizer(root))
    for rel in outside:
        print(f"crapkit: not mutating {rel}: {OUTSIDE_CORPUS}", file=sys.stderr)
    return kept, outside


def _file_mutants(root: Path, rel: str, lines) -> list:
    """One file's mutants: none when it is gone, none when crapkit refuses its
    language. A refusal is printed with the file named — mutate is diff-scoped,
    so an unmutable file arrives beside the ones the run was aimed at, and
    dropping it silently would leave a score built on fewer files than it says.
    """
    from ..mutate import file_mutants, mutation_language, refusal

    path = root / rel
    if not path.is_file():
        return []
    language = mutation_language(rel)
    reason = refusal(language)
    if reason:
        print(f"crapkit: not mutating {rel}: {reason}", file=sys.stderr)
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    return [m._replace(path=rel) for m in file_mutants(text, lines, language)]


def _collect_mutants(root: Path, targets: dict, max_mutants: int) -> list:
    out = []
    for rel, lines in sorted(targets.items()):
        out += _file_mutants(root, rel, lines)
    if len(out) > max_mutants:
        print(f"crapkit: capping at {max_mutants} of {len(out)} mutants "
              f"(raise --max-mutants to run them all)", file=sys.stderr)
        out = out[:max_mutants]
    return out


def _mutation_payload(survivors: list, total: int, outside: list[str]) -> dict:
    return {"mutants": total, "killed": total - len(survivors), "survived": len(survivors),
            "survivors": [m._asdict() for m in survivors], "outside_corpus": outside}


def _print_mutation_text(survivors: list, total: int) -> None:
    killed = total - len(survivors)
    rate = f"{killed / total:.0%}" if total else "n/a"
    print(f"mutation: {killed}/{total} killed ({rate})")
    for m in survivors:
        print(f"  SURVIVED  {m.path}:{m.line}  [{m.op}]  {m.mutated.strip()}")


def _print_mutation(as_json: bool, survivors: list, total: int, outside: list[str]) -> None:
    """A zero-mutant run whose files the corpus cut dropped says so on stdout:
    `0/0 killed` over a diff that held a test file read as a suite with nothing
    to prove, when it was a diff with nothing crapkit would mutate."""
    from ..mutate import OUTSIDE_CORPUS

    if as_json:
        _print_json(_mutation_payload(survivors, total, outside))
    elif outside and not total:
        print(f"mutation: nothing to mutate; {OUTSIDE_CORPUS}: {', '.join(outside)}")
    else:
        _print_mutation_text(survivors, total)


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serve stdio from `--repo`, or from the directory the client started in.

    A client registered globally opens the server in every project, most of
    which have no crapkit.toml. Refusing to start there gave the client a server
    that never answered `initialize`; the tools answer that per call instead.
    A config that EXISTS and is broken still fails fast, before any client
    connects, because every tool would fail the same way anyway.
    """
    from ..mcp_server import serve

    root = _command_root(args.repo)
    if (root / "crapkit.toml").is_file():
        _load_repo_config(root)
    return serve(root)


def _drop_mutate_pool(root: Path) -> int:
    """`--drop-pool`: the worktrees `mutation_workers > 1` keeps between runs,
    gone. No config is read first, because the reason to reach for this is a
    repo whose pool outlived whatever built it, and a crapkit.toml that no
    longer parses must not stand between a user and four checkouts of their own
    repo taking up disk."""
    from ..mutate_pool import drop_pool, pool_dir

    removed = drop_pool(root)
    print(f"removed {len(removed)} pooled worktrees from {pool_dir(root)}"
          if removed else f"no mutation worktree pool at {pool_dir(root)}")
    return 0


def cmd_mutate(args: argparse.Namespace) -> int:
    from ..mutate_pool import reporter, run_mutants

    root = _command_root(args.repo)
    if args.drop_pool:
        return _drop_mutate_pool(root)
    cfg = _load_repo_config(root)
    if not cfg.mutation_command:
        raise ConfigError("mutate needs [crapkit] mutation_command — the suite run once per mutant")
    targets, outside = _corpus_targets(root, cfg,
                                       _mutation_targets(root, args.files, cwd=Path.cwd()))
    mutants = _collect_mutants(root, targets, args.max_mutants)
    verdicts = run_mutants(root, cfg, mutants, reporter(len(mutants), sys.stderr))
    survivors = [m for m, killed in zip(mutants, verdicts) if not killed]
    _print_mutation(args.json, survivors, len(mutants), outside)
    return 0


def _print_duplication(as_json: bool, pairs, latest: dict) -> None:
    if as_json:
        _print_json({"run_id": latest["id"], "pairs": pairs})
        return
    if not pairs:
        print("no near-duplicate functions found")
        return
    for p in pairs:
        a, b = p["functions"]
        print(f"  {p['similarity']:.0%}  {a['path']}:{a['start']} {a['long_name']}  ==  "
              f"{b['path']}:{b['start']} {b['long_name']}")


def cmd_duplication(args: argparse.Namespace) -> int:
    from ..dup import find_duplicates
    from ..store import rowful_runs

    _positive_top("duplication", args.top)
    root = _command_root(args.repo)
    _load_repo_config(root)  # config errors first, like every command
    store = _open_store(root, first_command="inventory")
    runs = rowful_runs(store)
    if not runs:
        raise CrapkitError(f"no snapshot in {root} — run `{_self()} inventory` first")
    rows = store.read_rows(runs[-1]["id"])
    # A loader, never a bound dict: whoever names those texts pins every byte of
    # them across the pair counting (146 MB of peak on a 104 MB repo).
    pairs = find_duplicates(rows, lambda: _load_sources(root, {r.path for r in rows}),
                            min_lines=args.min_lines,
                            similarity=args.similarity, top=args.top)
    _print_duplication(args.json, pairs, runs[-1])
    return 0
