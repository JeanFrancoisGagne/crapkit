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
from ._shared import _load_repo_config, _load_sources, _open_store, _print_json


def cmd_coupling(args: argparse.Namespace) -> int:
    """Ranked over the tracked set: a pair naming a path git no longer has is a
    recommendation to open a file that is not there."""
    from ..coupling import change_coupling_lines

    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    pairs = change_coupling_lines(log_lines(root, cfg.churn_window_months),
                                  min_support=args.min_support, min_confidence=args.min_confidence,
                                  top=args.top, tracked=set(ls_files(root)))
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


def _mutation_targets(root: Path, files: list | None) -> dict:
    """path -> changed line set (None = whole file). Default is diff-scoped:
    only the working tree's changes vs HEAD grow mutants."""
    from ..diffparse import changed_ranges
    from ..gitio import diff_since

    if files:
        return {f.replace("\\", "/"): None for f in files}
    targets = {p: _range_lines(rs) for p, rs in changed_ranges(diff_since(root, "HEAD")).items()}
    if not targets:
        raise CrapkitError("no changes vs HEAD to mutate — name files with --files")
    return targets


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


def _print_mutation(as_json: bool, survivors: list, total: int) -> None:
    killed = total - len(survivors)
    if as_json:
        _print_json({"mutants": total, "killed": killed, "survived": len(survivors),
                          "survivors": [m._asdict() for m in survivors]})
        return
    rate = f"{killed / total:.0%}" if total else "n/a"
    print(f"mutation: {killed}/{total} killed ({rate})")
    for m in survivors:
        print(f"  SURVIVED  {m.path}:{m.line}  [{m.op}]  {m.mutated.strip()}")


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serve stdio from `--repo`, or from the directory the client started in.

    A client registered globally opens the server in every project, most of
    which have no crapkit.toml. Refusing to start there gave the client a server
    that never answered `initialize`; the tools answer that per call instead.
    A config that EXISTS and is broken still fails fast, before any client
    connects, because every tool would fail the same way anyway.
    """
    from ..mcp_server import serve

    root = Path(args.repo).resolve()
    if (root / "crapkit.toml").is_file():
        _load_repo_config(root)
    return serve(root)


def cmd_mutate(args: argparse.Namespace) -> int:
    from ..mutate_pool import reporter, run_mutants

    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)
    if not cfg.mutation_command:
        raise ConfigError("mutate needs [crapkit] mutation_command — the suite run once per mutant")
    mutants = _collect_mutants(root, _mutation_targets(root, args.files), args.max_mutants)
    verdicts = run_mutants(root, cfg, mutants, reporter(len(mutants), sys.stderr))
    survivors = [m for m, killed in zip(mutants, verdicts) if not killed]
    _print_mutation(args.json, survivors, len(mutants))
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

    root = Path(args.repo).resolve()
    _load_repo_config(root)  # config errors first, like every command
    store = _open_store(root, first_command="inventory")
    runs = rowful_runs(store)
    if not runs:
        raise CrapkitError(f"no snapshot in {root} — run `crapkit inventory` first")
    rows = store.read_rows(runs[-1]["id"])
    # A loader, never a bound dict: whoever names those texts pins every byte of
    # them across the pair counting (146 MB of peak on a 104 MB repo).
    pairs = find_duplicates(rows, lambda: _load_sources(root, {r.path for r in rows}),
                            min_lines=args.min_lines,
                            similarity=args.similarity, top=args.top)
    _print_duplication(args.json, pairs, runs[-1])
    return 0
