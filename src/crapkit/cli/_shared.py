"""Helpers more than one command family needs: the JSON envelope and its schema
version, repo config loading, the snapshot store openers, TSV/SARIF writers, the
ratchet file reader, and the gate line every gate prints. Nothing here belongs to
one command; anything that does lives with its family."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import load_config_text
from ..errors import ConfigError, CrapkitError, ToolError
from ..store import SnapshotStore


SCHEMA_VERSION = 1  # bumped whenever a --json field is removed or retyped


def _print_json(payload: dict) -> None:
    """Every machine-readable payload leaves through here, so a wrapper can pin
    the shape it parses and fail loudly when the shape moves."""
    print(json.dumps({**payload, "schema": SCHEMA_VERSION}, sort_keys=True))


def _analysis_tools():
    """Import the analysis stack lazily so an absent tool maps to ToolError (exit 5).

    Under deferred_pygments: lizard's Erlang reader binds pygments at module
    scope, and crapkit analyzes no Erlang, so every process paid 26ms of import
    for a reader it never reaches. The proxies come out again as lizard lands.
    """
    from .._pygdefer import deferred_pygments

    try:
        with deferred_pygments():
            import lizard

            from ..analyze import analyze_files, load_cache, save_cache
    except ImportError as exc:
        raise ToolError(f"required analysis tool unavailable: {exc}") from exc
    return lizard, analyze_files, load_cache, save_cache


def _load_repo_config(root: Path):
    config_path = root / "crapkit.toml"
    if not config_path.is_file():
        raise ConfigError(f"no crapkit.toml at {root} — nothing to analyze")
    return load_config_text(config_path.read_text(encoding="utf-8"))


def _file_sizer(root: Path):
    """Working-tree sizes for the max_file_bytes cut. A tracked path that is gone
    reads as 0: absence is _present_on_disk's business, not the byte ceiling's."""
    def size_of(path: str) -> int:
        try:
            return (root / path).stat().st_size
        except OSError:
            return 0
    return size_of


def _write_tsv(path: Path, lines) -> None:
    """Stream an export to disk. newline="\\n" is the determinism contract: the
    bytes must not pick up the host's line separator. Writing line by line keeps
    the whole document from existing as a string next to the rows it came from."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(lines)


def _emit_findings(root: Path, sarif_path: str | None, github: bool, results: list) -> None:
    from ..sarif import github_annotation
    from ..sarifio import write_sarif

    if sarif_path:
        write_sarif(root / sarif_path, results)
    if github:
        for r in results:
            print(github_annotation(r))


def _load_ratchet_or_die(ratchet_path: Path, name: str) -> list:
    from ..ratchet import load_ratchet

    if not ratchet_path.is_file():
        return []
    try:
        return load_ratchet(ratchet_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"unreadable ratchet file {name}: {exc}") from exc


def _dirty_tag(dirty: bool) -> str:
    return "  [dirty]" if dirty else ""


def _gate_line(v) -> str:
    """One gate violation, however it was decided; verify and `rescore --gate`
    report the same finding, so they must read the same."""
    return (f"  GATE  crap {v.crap:8.1f}  ccn {v.ccn:>3} cov {v.cov:.0%}  "
            f"{v.path}:{v.start}  {v.long_name}  -> {v.remedy}{_dirty_tag(v.dirty)}")


def _latest_scored(store: SnapshotStore):
    from ..store import default_baseline
    return default_baseline(store)


def _open_store(root: Path, first_command: str = "coverage") -> SnapshotStore:
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} — run `crapkit {first_command}` first")
    return SnapshotStore(db_path)


def _ratchet_entries(root: Path, cfg) -> list | None:
    """The committed marks, or None when the repo carries no marks file yet."""
    from ..ratchet import load_ratchet

    ratchet_path = root / cfg.ratchet_file
    if not ratchet_path.is_file():
        return None
    return load_ratchet(ratchet_path.read_text(encoding="utf-8"))


def _load_sources(root: Path, paths: set) -> dict:
    sources = {}
    for rel in paths:
        p = root / rel
        if p.is_file():
            sources[rel] = p.read_text(encoding="utf-8", errors="replace")
    return sources
