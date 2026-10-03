"""Helpers more than one command family needs: the JSON envelope and its schema
version, repo config loading, the snapshot store openers, TSV/SARIF writers, the
ratchet file reader, the argument guards every command spells the same way, and
the gate line every gate prints. Nothing here belongs to one command; anything
that does lives with its family."""
from __future__ import annotations

import json
import os
import posixpath
import sys
from pathlib import Path

from ..config import load_config_text
from ..errors import ConfigError, CrapkitError, GitError, ToolError, UnreadableNameError
from ..gitpaths import readable, shown
from ..invocation import _self, quoted_path
from ..merge import UNREAD_ADVICE
from ..named import first_few
from ..repopath import on_a_share, rooted, typed, typed_path
from ..rootfind import find_root
from ..store import SnapshotStore
from ..repotext import marks_text, os_text, repo_text
from ..universe import claiming_scope, left_out_lines, scan_files


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


def behind_head(git):
    """The `pick_baseline` predicate verify, `ratchet seed`, `ratchet prune` and
    `runs list` share, so all four name one baseline: a run is set aside only
    when git says its commit is not at or behind HEAD. A commit git cannot place
    stays in, as it always did: verify's ancestor check still refuses it with
    exit 4, and naming the shallow clone is that check's job. `git` is a
    `GitFacts`, which asks git once per commit."""
    return lambda run: git.ancestry(run["commit"]) is not False


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
    `_load_repo_config` raises names where the user stands. A working directory
    on a network share walks nowhere: every root above it is on the share and
    refused, and a stat there can fail with the share's own error. A leading `~`
    is the user's home: an MCP client starts the server without a shell, and
    cmd.exe expands none, so `--repo ~/app` named `<cwd>/~/app`.
    """
    if repo is not None:
        return _on_its_drive(typed_path(os.path.expanduser(repo)))
    cwd = _on_its_drive(typed_path(os.getcwd()))
    found = None if on_a_share(cwd) else find_root(cwd)
    if found is None:
        return cwd
    if found != cwd:
        print(f"crapkit: using crapkit.toml at {found}", file=sys.stderr)
    return found


def _init_root(repo: str | None) -> Path:
    r"""Where `init` writes: `--repo`, or where the user stands, read the way
    every other command reads a root, with no walk, since init adopts no
    crapkit.toml above it. It read `--repo /c/...` as C:\c\... and ended in a
    traceback, and started its probes on a network share."""
    root = _on_its_drive(typed_path(os.getcwd() if repo is None else repo))
    _refuse_a_share(root)
    return root


def _stand(repo: str | None) -> Path | None:
    r"""Where a relative path argument is read from: the working directory when
    the root came from the walk, nothing when `--repo` named the root. The
    rebase belongs to discovery (ADR 0002), not to the flag: `--repo ..` from
    web/ reads `web/src/grade.py` against the root it named, as on 0.4.15. A
    session in `\\localhost\C$\repo` stands on its drive (repopath.typed_path)."""
    return None if repo is not None else typed_path(os.getcwd())


def _on_its_drive(path: Path) -> Path:
    r"""`path` resolved, unless resolving puts it on a network share.

    On Windows resolve() answers a mapped drive with the share behind it:
    `Z:\repo` becomes `\\server\share\repo`, where cmd.exe cannot start a lane
    and runs it in C:\Windows instead. Such a root keeps the letter it was
    typed with. A root typed as a share is only made absolute, never looked up
    on the network: `_load_repo_config` refuses it."""
    if on_a_share(path):
        return Path(os.path.abspath(path))
    resolved = path.resolve()
    return Path(os.path.abspath(path)) if on_a_share(resolved) else resolved


def _refuse_a_share(root: Path) -> None:
    r"""A root on a network share, typed so, stood in or reached any other way,
    stops here, before crapkit reads a file there or starts a child: cmd.exe
    would run every lane in C:\Windows."""
    if on_a_share(root):
        on_the_drive = Path("Z:\\") / root.relative_to(root.anchor)
        raise ConfigError(
            f"the root {root} is on a network share, where cmd.exe cannot start a lane "
            r"(it runs it in C:\Windows instead). Map the share to a drive letter "
            f"(net use Z: {root.drive}) and run crapkit from {on_the_drive}")


def _repo_relative(raw: str, root: Path = Path("."), cwd: Path | None = None) -> str:
    """One spelling for a file argument, whatever the shell handed in: git's,
    through repopath's typed entry. `cwd` is where the user stands (`_stand`),
    handed in only when the root came from the walk. A file outside the root is
    refused rather than matched against nothing: scoring no functions is not an
    answer to a path crapkit cannot place. A name that is not UTF-8 goes on to
    `_readable_argument`."""
    return _readable_argument(_placed(raw, root, cwd), root)


def _placed(raw: str, root: Path, cwd: Path | None) -> str:
    """A file argument as git spells it under `root`, or the refusal of a path
    outside it."""
    rel = typed(raw, root, cwd)
    if rel is None:
        raise ConfigError(f"{shown(raw)} is outside the repo at {shown(str(root))}")
    return rel


def _scored_arguments(files, root: Path, cfg, cwd: Path | None = None) -> list[str]:
    """The root-relative names a scoring command reads from its file arguments.

    A name that is not UTF-8 and that no scope takes is left out with one
    stderr line, as a scan leaves such a name out: `rescore --gate`
    refused it at exit 3, where hook-precommit passes the same staged file.
    A name a scope takes still gets the rename refusal."""
    placed = sorted({_placed(raw, root, cwd) for raw in files})
    left_out = _left_out_arguments(placed, root, cfg)
    _say_left_out_arguments(left_out)
    return sorted({_not_a_directory(_readable_argument(rel, root), root)
                   for rel in placed if rel not in left_out})


def _not_a_directory(rel: str, root: Path) -> str:
    """A file argument, refused when it names a directory. A directory has no
    functions of its own and no scope takes it, so `rescore --gate src`, `.`
    and `""` judged 0 functions and passed while a file inside failed the gate
    when named, and `check_gate` answered `ok: true`. One named like a source
    file reached the analyzer and ended in a PermissionError traceback."""
    if (root / rel).is_dir():
        raise ConfigError(f"{shown(rel)} is a directory; name the source files in it")
    return rel


def _left_out_arguments(names: list[str], root: Path, cfg) -> list[str]:
    """The files on disk whose names are not UTF-8 and that no scope takes."""
    return [rel for rel in names if not readable(rel) and os.path.lexists(root / rel)
            and claiming_scope(rel, cfg) is None]


def _say_left_out_arguments(names: list[str]) -> None:
    for rel in names:
        print(f"crapkit: left out {shown(rel)}: its name is not UTF-8 and no scope takes it, "
              "so nothing in it is scored", file=sys.stderr)


def _readable_argument(rel: str, root: Path) -> str:
    """A root-relative name as text a store, a marks file and a row can hold.

    A name in bytes that are not UTF-8 arrives holding a lone surrogate, which
    none of them can hold. When a file exists under that name the answer is the
    rename, since no lookup can ever key it; otherwise each such byte reads as
    U+FFFD, and the command answers for a file it does not have in its own
    words."""
    if readable(rel):
        return rel
    if os.path.lexists(root / rel):
        raise _name_refusal(rel, root)
    return os_text(rel)


def _name_refusal(rel: str, root: Path) -> UnreadableNameError:
    """The exit-3 refusal of a file argument whose name is not UTF-8, for the
    CLI and for the MCP tools that answer it without starting the CLI."""
    return UnreadableNameError(f"{shown(rel)} is named in bytes that are not UTF-8, and crapkit "
                               "reads every path as UTF-8: rename it (git mv) to a UTF-8 name",
                               [rel], _uncommitted(root, [rel]))


def _uncommitted(root: Path, names) -> frozenset[str]:
    """Which of `names` hold uncommitted edits or are not tracked: verify's
    `dirty`, which a refusal's `unread_files` carries. Read only on the way
    out of a refusal. A file git never tracked is dirty, a file the index holds
    and the working tree lacks is an uncommitted deletion, and in a tree git
    cannot list every name is untracked."""
    from ..gitio import ls_files, status_names

    try:
        clean = set(ls_files(root)) - set(status_names(root))
    except GitError:
        clean = set()
    return frozenset(name for name in names if name not in clean)


def _scan(root: Path, files: list[str], cfg):
    """`scan_files` over the working tree at `root`: sizes read off disk, and
    a refusal whose `unread_files` says which names are dirty."""
    try:
        return scan_files(files, cfg, size_of=_file_sizer(root))
    except UnreadableNameError as exc:
        raise exc.with_dirty(_uncommitted(root, exc.names)) from None


def _say_left_out(names: tuple[str, ...]) -> None:
    """The stderr lines for the unreadable names a command's scan left out:
    one per name, the first five, then a count of the rest. Each command
    calls this at its one scan, so it names each name once."""
    for line in left_out_lines(names):
        print(line, file=sys.stderr)


def _unreadable_json(names: tuple[str, ...]) -> list[str]:
    r"""The `unreadable_names` field: each name with its bytes that are not
    UTF-8 as `\xNN`, the spelling the stderr line uses."""
    return [shown(name) for name in names]


def _repo_out_path(root: Path, out: str, flag: str = "--out") -> Path:
    r"""Where a writer flag puts its file, with the directory to hold it.

    `report --out` created a missing parent; `--export`, `--sarif` and
    `--emit-baseline` opened the path straight and died on FileNotFoundError
    with a Python traceback and exit 1, a code crapkit's exit table does not
    define. `coverage --sarif` died there after the run was already committed to
    the store, so a run that had succeeded looked unrecoverable. A relative path
    is repo-relative and may not climb out of the tree; an absolute one is the
    caller naming a destination on purpose. `report --out` is the rule's origin
    and now reads it from here, so the four writers cannot drift.

    The path is typed, so repopath's typed entry reads it: on Windows Git
    Bash's `/c/...` and WSL's `/mnt/c/...` name their drive. `Path(out)` read
    `/c/Users/...` as `C:\c\Users\...` and wrote the file into a new tree there.
    """
    path = _out_target(root, out, flag)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _out_target(root: Path, out: str, flag: str) -> Path:
    """The file `flag` names, or the refusal of one crapkit cannot write: a
    relative path that climbs out of the tree, or a directory, which ended in
    a PermissionError traceback at exit 1 after the run was stored."""
    named = typed_path(out)
    path = named if rooted(named) else _inside_the_tree(root, named, out)
    if path.is_dir():
        raise ConfigError(f"{flag} {quoted_path(out)} is a directory; name a file to write")
    return path


def _inside_the_tree(root: Path, named: Path, out: str) -> Path:
    path = (root / named).resolve()
    if root.resolve() not in path.parents:
        raise ConfigError(f"{quoted_path(out)} is repo-relative and climbs out of {root}; "
                          "pass an absolute path to write outside it")
    return path


def _refuse_unwritable_outputs(root: Path, outputs: dict[str, str | None]) -> None:
    """Each writer flag's path checked before the command does any work, so a
    refusal lands before a run is stored: `verify --sarif DIR` recorded its
    run as verdict=ok and then crashed. `outputs` maps a flag to its value."""
    for flag, out in outputs.items():
        if out:
            _out_target(root, out, flag)


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
    _refuse_a_share(root)
    config_path = root / "crapkit.toml"
    if not config_path.is_file():
        raise ConfigError(no_config(root))
    return load_config_text(repo_text(config_path, "crapkit.toml"), root=root)


def no_config(root: Path) -> str:
    """The refusal for a directory with no crapkit.toml, with the way forward.

    It named none, so a monorepo command run one package over, or a hook armed
    before `init`, left the reader to guess. A crapkit.toml git tracks below
    `root` gets the --repo that reaches it. With none below, the line names both
    ways forward and the configurations elsewhere in the checkout, spelled from
    `root` the way `--repo` takes them. A root that is not a directory gets the
    bare line: there is nothing to adopt or list there.
    """
    refusal = f"no crapkit.toml at {root} - nothing to analyze"
    if not root.is_dir():
        return refusal
    return refusal + (_roots_below_hint(root) or _init_or_repo(root))


def _init_or_repo(root: Path) -> str:
    from ..gitio import tracked_configs

    held = tracked_configs(root)
    nearby = f"; this checkout holds {', '.join(held[:3])}" if held else ""
    return (f"; run `{_self()} init` there to adopt it, or pass --repo DIR to name a "
            f"directory that holds one{nearby}")


def _from_cwd(path: Path) -> str:
    """`path` the way the caller types it: relative when it sits under the
    working directory, absolute otherwise."""
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def _roots_below(root: Path) -> list[str]:
    """Each directory below `root` whose crapkit.toml git tracks, spelled from
    the working directory. Empty outside a repository. `no_config`, the one
    caller, hands it a directory: a file has nothing below it to list."""
    from ..gitio import tracked_named

    try:
        found = tracked_named(root, "crapkit.toml")
    except GitError:
        return []
    return sorted(_from_cwd(root / Path(path).parent) for path in found if "/" in path)


def _roots_below_hint(root: Path) -> str:
    """The next step when crapkit.toml sits below where crapkit looked.

    Git runs a pre-commit hook from the repository top and CI starts a step
    there, and the walk goes up from where it stands (ADR 0002), never down. A
    monorepo whose crapkit.toml sits in packages/api refused every commit with
    the directory it looked in and no word about the one it wanted. The listing
    runs on the way to this refusal and nowhere else.
    """
    roots = _roots_below(root)
    if not roots:
        return ""
    if len(roots) == 1:
        return f"; crapkit.toml sits below it in {roots[0]}: pass --repo {roots[0]}"
    return (f"; crapkit.toml sits below it in {first_few(roots)}: "
            "pass --repo with the one to score")


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
        write_sarif(_repo_out_path(root, sarif_path, "--sarif"), results)
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


def _marks_file_text(ratchet_path: Path) -> str:
    """The marks file as every reader reads it: UTF-16 by its byte-order mark,
    else UTF-8 with a BOM dropped and each other byte as U+FFFD
    (`repotext.marks_text`). A writer reads through `RatchetFile`, which
    refuses to save a byte this read replaced."""
    return marks_text(ratchet_path.read_bytes())


def _load_ratchet_or_die(ratchet_path: Path, name: str) -> list:
    if not ratchet_path.is_file():
        return []
    return _ratchet_or_die(_marks_file_text(ratchet_path), name)


def _gate_line(v, unmeasured: bool = False) -> str:
    """One gate violation as verify's gate_violation row prints it; `rescore
    --gate` passes `unmeasured` to print `cov -` for a cov no measurement
    stands behind."""
    from ..verify import gate_line

    return gate_line(v, v.dirty, unmeasured)


def _print_unread(unread: dict[str, str], what: str, file=None) -> None:
    """The changed files a gate refuses, `what` saying which ("staged",
    "changed"), and what to do; nothing when every file was read. Each file's
    line is verify's unread_file row."""
    from ..verify import UnreadFile, lines_of

    if not unread:
        return
    print(f"crapkit gate: {len(unread)} {what} file(s) could not be read, so no function in "
          "them was judged:", file=file)
    files = [UnreadFile(path, reason) for path, reason in sorted(unread.items())]
    for line in lines_of("unread_file", files):
        print(line, file=file)
    print(UNREAD_ADVICE, file=file)


def _latest_scored(store: SnapshotStore):
    from ..store import default_baseline
    return default_baseline(store)


def _open_store(root: Path, first_command: str = "coverage") -> SnapshotStore:
    db_path = root / ".crapkit" / "crap.sqlite"
    if not db_path.is_file():
        raise CrapkitError(f"no snapshot in {root} - run `{_self()} {first_command}` first")
    return SnapshotStore(db_path)


def _proved_paths(root: Path, proof, marks, *, moves_marks: bool = False) -> set:
    """The files whose history the legacy mark proof must cover.

    A reader compares a mark with a row of its own file, so it proves the rows'
    files: check_gate, the commit gate, explain and brief prove the few files
    they read, and worklist and verify every file their run holds. explain
    proves the run it resolved the name in, which holds the file even when the
    newest run dropped it. `ratchet prune` also moves a mark off a file git
    renamed, which the working tree no longer has, onto a proved file, so seed
    and prune, which share one proof, pass `moves_marks` and prove every marked
    file the tree lost too.
    """
    paths = {row.path for row in proof}
    if not moves_marks:
        return paths
    marked = {entry.path for entry in marks}
    return paths | _lost_files(root, marked - paths)


def _lost_files(root: Path, paths: set) -> set:
    """The paths the working tree no longer holds.

    One listing per folder, not one stat per file: a large consumer repo marks
    9,105 files, 0.18 to 0.31 s to stat one by one and 0.03 s to list. A name
    the listing lacks reads as lost, so a case or Unicode mismatch can only
    prove a file the proof did not need.
    """
    folders: dict[str, set[str]] = {}
    for path in paths:
        folder, _, name = path.rpartition("/")
        folders.setdefault(folder, set()).add(name)
    return {posixpath.join(folder, name) for folder, names in folders.items()
            for name in names - _listing(root / folder)}


def _listing(folder: Path) -> set[str]:
    try:
        return set(os.listdir(folder))
    except OSError:  # the folder went with the file
        return set()


def _identity_history(root: Path, store, paths: set) -> set:
    """Collision groups any stored run held in `paths`."""
    if store is not None:
        return store.historical_collision_groups(paths)
    db = root / ".crapkit" / "crap.sqlite"
    if not db.is_file():
        return set()
    from contextlib import closing

    with closing(SnapshotStore(db)) as opened:
        return opened.historical_collision_groups(paths)


def _check_ratchet_identity(text: str, root: Path, name: str, rows, store=None,
                            entries=None, *, moves_marks: bool = False) -> int:
    """The key version the marks in `text` can be compared under, or a refusal.
    `entries` is `read_ratchet(text)[0]` when the caller already parsed it, so
    the proof parses the file only when nobody has. `moves_marks` is for the
    writer that moves a mark onto another file, as `_proved_paths` says."""
    from ..ratchet import (KEY_VERSION, check_reader_keys, checked_key_version, parsed_marks,
                           read_key_version)

    try:
        marks = parsed_marks(text, entries)
        check_reader_keys(text, marks)
        if read_key_version(text) == KEY_VERSION or not marks:
            return KEY_VERSION
        proof = rows() if callable(rows) else rows
        paths = _proved_paths(root, proof, marks, moves_marks=moves_marks)
        return checked_key_version(text, proof, historical=_identity_history(root, store, paths),
                                   entries=marks)
    except ValueError as exc:
        raise ConfigError(f"{name}: {exc}") from exc


def _ratchet_key_version(root: Path, cfg, rows, store=None, entries=None) -> int:
    path = root / cfg.ratchet_file
    text = _marks_file_text(path) if path.is_file() else ""
    return _check_ratchet_identity(text, root, cfg.ratchet_file, rows, store, entries)


def _ratchet_entries(root: Path, cfg, rows=None, store=None) -> list | None:
    """The committed marks, or None when the repo carries no marks file yet.

    Lenient, because every caller here only READS the marks: `explain`, `brief`
    and `rescore --gate`. Rows may be deferred until legacy identity needs them.
    A line crapkit cannot parse carries no mark, so
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
    text = _marks_file_text(ratchet_path)
    entries, complaints = read_ratchet(text)
    if rows is not None:
        _check_ratchet_identity(text, root, cfg.ratchet_file, rows, store, entries)
    for complaint in complaints:
        print(f"crapkit: skipped an unreadable mark in {cfg.ratchet_file}: {complaint}",
              file=sys.stderr)
    return entries


def _load_sources(root: Path, paths: set) -> dict:
    """Each file's text as the scorer read it, so `brief --json`'s `source`
    holds the `é` a cp1252 file holds, the `é` its long_name already showed,
    where a UTF-8 read put U+FFFD for an agent to write back."""
    from ..analyze import decode_source

    sources = {}
    for rel in paths:
        p = root / rel
        if p.is_file():
            sources[rel] = decode_source(p.read_bytes())
    return sources
