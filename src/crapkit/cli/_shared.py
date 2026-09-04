"""Helpers more than one command family needs: the JSON envelope and its schema
version, repo config loading, the snapshot store openers, TSV/SARIF writers, the
ratchet file reader, the argument guards every command spells the same way, and
the gate line every gate prints. Nothing here belongs to one command; anything
that does lives with its family."""
from __future__ import annotations

import json
import posixpath
import sys
from pathlib import Path

from ..config import load_config_text
from ..errors import ConfigError, CrapkitError, ToolError
from ..invocation import _self
from ..repotext import repo_text
from ..rootfind import find_root
from ..store import SnapshotStore


SCHEMA_VERSION = 1  # bumped whenever a --json field is removed or retyped


def _positive_top(command: str, top: int) -> int:
    """`--top N` names how many rows to hand back, so nothing under 1 is a
    question anyone asks.

    Unchecked, the same 0 meant two different wrong things. `next-item` widened
    it back to one with `max(top, 1)` and handed out an item the caller had not
    asked for, claim and all. `duplication` and `coupling` sliced `[:0]` and
    printed their all-clear over a tree full of pairs: an exit-0 clean bill of
    health that was false, which is what a CI gate reads. A negative sliced from
    the tail and dropped rows with nothing said.
    """
    if top < 1:
        raise ConfigError(f"{command} --top must be >= 1, got {top}")
    return top


def _scope_names(cfg, requested: list[str] | None) -> list[str]:
    """`--scope NAME` names a declared scope, or it is a configuration error.

    The scope clause is an exact match on the identity table, so a name no
    `[[scope]]` declares cuts the read to nothing: `worklist --scope frontend`
    on a repo whose scopes are `api` and `web` printed `0 active, 0 dormant`
    at exit 0, which a CI step reads as a clean pass, and `next-item --scope
    biling` answered `empty: true` with every reason at 0, the payload an
    agent reads as a finished scope. A typo and a declared scope with nothing
    queued produced one payload. Exit 3 is the loader's own class, the one
    `lane ... references undeclared scope(s)` already raises for the same
    mistake made in crapkit.toml.
    """
    names = list(requested or [])
    declared = _declared_scopes(cfg)
    unknown = [name for name in names if name not in declared]
    if unknown:
        raise ConfigError(_unknown_scope_message(unknown, declared))
    return names


def _declared_scopes(cfg) -> list[str]:
    return [s.name for s in cfg.scopes]


def _unknown_scope_message(unknown: list[str], declared: list[str]) -> str:
    named = ", ".join(repr(name) for name in unknown)
    return f"no scope named {named}; declared: {', '.join(declared)}"


def _command_root(repo: str | None) -> Path:
    """The crapkit root a command works in.

    `--repo` names an exact root and walks nowhere. Without it the root is the
    nearest crapkit.toml at or above the working directory (ADR 0002: nearest
    wins, a `.git` entry without one stops the walk), named on stderr when it
    is not the working directory itself, so `cd web && crapkit worklist` reads
    the root configuration that claims web/ and says which file it read. When
    the walk finds nothing the working directory is the root, so the refusal
    `_load_repo_config` raises names where the user stands.
    """
    if repo is not None:
        return Path(repo).resolve()
    cwd = Path.cwd().resolve()
    found = find_root(cwd)
    if found is None:
        return cwd
    if found != cwd:
        print(f"crapkit: using crapkit.toml at {found}", file=sys.stderr)
    return found


def _stand(repo: str | None) -> Path | None:
    """Where a relative path argument is read from: the working directory when
    the root came from the walk, nothing when `--repo` named the root. The
    rebase belongs to discovery (ADR 0002), not to the flag: `--repo ..` from
    web/ reads `web/src/grade.py` against the root it named, as on 0.4.15."""
    return None if repo is not None else Path.cwd()


def _repo_relative(raw: str, root: Path = Path("."), cwd: Path | None = None) -> str:
    r"""One spelling for a file argument, whatever the shell handed in.

    `src/a.py`, `src\a.py`, `./src/a.py` and the absolute path tab completion
    returns all name one file, and every one of them has to reach
    `universe.owning_scope` as the repo-relative posix path the scopes are
    declared in. Three commands spelled this as `raw.replace("\\", "/")` and
    nothing else, so the `./` form — the one shells, `find` and coding agents
    produce most often — matched no scope prefix in any of them: `test-scoped`
    called it a file belonging to no declared scope, and `rescore --gate` scored
    nothing and passed a gate the same file failed spelled relative.

    `cwd` is where the user stands, handed in only when the root came from
    the walk (`_stand`). Below the root, a relative argument is rebased from
    there (ADR 0002): `grade.py` typed in web/src names web/src/grade.py, and
    one climbing out of the root is refused like an absolute path outside it.
    At the root, from a directory outside it, or under `--repo`, the argument
    is root-relative as it always was.
    """
    path = raw.replace("\\", "/")
    if _is_rooted(path):
        return _under_root(path, root)
    if _below(cwd, root):
        return _under_root(str(cwd / path), root)
    return posixpath.normpath(path)


def _below(cwd: Path | None, root: Path) -> bool:
    """Whether the working directory sits strictly under the root, the one
    place a relative argument means something other than root-relative."""
    return cwd is not None and root.resolve() in cwd.resolve().parents


def _is_rooted(path: str) -> bool:
    """A rooted path with no drive (`/tmp/a.py` on Windows) counts too: it names
    the current drive's root, not a place under the repo."""
    named = Path(path)
    return named.is_absolute() or bool(named.root)


def _under_root(path: str, root: Path) -> str:
    """An absolute argument, said the way the scopes are declared. One that
    lands outside the repo is refused rather than matched against nothing:
    scoring no functions is not an answer to a path crapkit cannot place."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        raise ConfigError(f"{path} is outside the repo at {root}") from None


def _repo_out_path(root: Path, out: str) -> Path:
    """Where a writer flag puts its file, with the directory to hold it.

    `report --out` created a missing parent; `--export`, `--sarif` and
    `--emit-baseline` opened the path straight and died on FileNotFoundError
    with a Python traceback and exit 1, a code crapkit's exit table does not
    define. `coverage --sarif` died there after the run was already committed to
    the store, so a run that had succeeded looked unrecoverable. A relative path
    is repo-relative and may not climb out of the tree; an absolute one is the
    caller naming a destination on purpose. `report --out` is the rule's origin
    and now reads it from here, so the four writers cannot drift.
    """
    rooted = _is_rooted(out)
    path = Path(out) if rooted else (root / out).resolve()
    if not rooted and root.resolve() not in path.parents:
        raise ConfigError(f"{out!r} is repo-relative and climbs out of {root}; "
                          "pass an absolute path to write outside it")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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
        raise ConfigError(f"no crapkit.toml at {root} - nothing to analyze")
    return load_config_text(repo_text(config_path, "crapkit.toml"), root=root)


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
        write_sarif(_repo_out_path(root, sarif_path), results)
    if github:
        for r in results:
            print(github_annotation(r))


def _ratchet_or_die(text: str, name: str) -> list:
    """The marks in a file crapkit is about to REWRITE, or a named refusal.

    Strict on purpose: seed, prune, move and the merge driver all write the file
    back, and salvaging a line crapkit could not parse would delete a mark the
    repo signed for."""
    from ..ratchet import load_ratchet

    try:
        return load_ratchet(text)
    except ValueError as exc:
        raise ConfigError(f"unreadable ratchet file {name}: {exc}") from exc


def _load_ratchet_or_die(ratchet_path: Path, name: str) -> list:
    if not ratchet_path.is_file():
        return []
    return _ratchet_or_die(repo_text(ratchet_path, name), name)


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
        raise CrapkitError(f"no snapshot in {root} — run `{_self()} {first_command}` first")
    return SnapshotStore(db_path)


def _ratchet_entries(root: Path, cfg) -> list | None:
    """The committed marks, or None when the repo carries no marks file yet.

    Lenient, because every caller here only READS the marks: `explain`, `brief`
    and `rescore --gate`. A line crapkit cannot parse carries no mark, so
    dropping it can only make the gate stricter, never let a regression through.
    One hand-edited short line used to reach explain and brief, which are also
    two of the MCP tools an agent calls, as a raw ValueError traceback, with the
    trajectory, source, dark lines and churn the caller asked for all sitting
    there available.
    """
    from ..ratchet import read_ratchet

    ratchet_path = root / cfg.ratchet_file
    if not ratchet_path.is_file():
        return None
    entries, complaints = read_ratchet(repo_text(ratchet_path, cfg.ratchet_file))
    for complaint in complaints:
        print(f"crapkit: skipped an unreadable mark in {cfg.ratchet_file}: {complaint}",
              file=sys.stderr)
    return entries


def _load_sources(root: Path, paths: set) -> dict:
    sources = {}
    for rel in paths:
        p = root / rel
        if p.is_file():
            sources[rel] = p.read_text(encoding="utf-8", errors="replace")
    return sources
