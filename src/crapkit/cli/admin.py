"""The setup and upkeep commands: `init` (sniff the repo, write a starter
crapkit.toml and extend .gitignore), `doctor` (does crapkit.toml still describe
this repo: keys, scopes, lanes, tools, unmeasured directories, plus the --json
report and the --tune knob advice) and `watch` (poll tracked files and rescore
what moved)."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import load_config_text
from ..doctor import Finding
from ..errors import ConfigError, ToolError
from ..gitio import ls_files
from ..store import SnapshotStore
from ..universe import assign_files, scan_files
from ._shared import _file_sizer, _load_repo_config, _print_json


def _interpreter() -> str:
    """The interpreter name a committed config can call. sys.executable is this
    machine's absolute path and would not survive the repo reaching anyone else."""
    import shutil

    return "python" if shutil.which("python") else "python3"


def _present_markers(root: Path) -> frozenset[str]:
    from ..scaffold import PYTEST_MARKERS

    return frozenset(name for name in PYTEST_MARKERS if (root / name).is_file())


def _package_json(root: Path) -> str:
    path = root / "package.json"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _next_step(scopes: dict, lanes: tuple) -> str:
    """What to run next, which is not the same sentence in all three cases.

    A repo whose every language is cc-only was told to declare a lane per
    coverage command. There is no lane to declare: neither parser reads Go,
    Rust, shell or the six others, `init` just wrote `coverage_optional` for
    each scope, and `crapkit coverage` scores them from complexity alone.
    """
    from ..scaffold import cc_only_scope

    if lanes:
        return (f"detected {len(lanes)} lane(s) from this repo's own files: "
                f"{', '.join(lane.name for lane in lanes)} — next: run `crapkit coverage`")
    if all(cc_only_scope(languages) for languages in scopes.values()):
        return ("no coverage parser reads this repo's languages, so every scope is "
                "cc-only (coverage_optional = true) and needs no lane — next: run "
                "`crapkit coverage`")
    return ("next: declare a [[lane]] per coverage command (see the commented template), "
            "then run `crapkit coverage`")


def _print_init_summary(scopes: dict, lanes: tuple) -> None:
    print(f"wrote crapkit.toml with {len(scopes)} scope(s): {', '.join(scopes)}")
    print(_next_step(scopes, lanes))


def _no_scopes_reason(root: Path) -> str:
    """Why init found nothing to scope.

    crapkit reads `git ls-files`, so source nobody added is source it cannot see
    — and that is the common case on the first command a new user runs. Blaming
    the directory sends them to look in the one place that is already right.
    """
    from ..gitio import untracked_files
    from ..scaffold import source_candidates

    untracked = source_candidates(untracked_files(root))
    if not untracked:
        return "no source files found to scope — is this the repo root?"
    return ("no tracked source files to scope — crapkit scores git-tracked files only; "
            f"run `git add` first ({len(untracked)} untracked source file(s) found)")


def _pytest_cov_probe(command: str) -> bool:
    """Can the interpreter this lane names import pytest_cov? True too when the
    probe itself cannot run — only a clean "no" earns the warning, and a missing
    interpreter is doctor's finding, not this one's."""
    import subprocess

    try:
        return subprocess.run([command.split()[0], "-c", "import pytest_cov"],
                              capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True


def _warn_missing_pytest_cov(lanes: tuple) -> None:
    """The first-run trap, caught where it starts. The py lane shells out to
    `pytest --cov`, and the --cov flags come from pytest-cov — a package of the
    REPO's interpreter, so a crapkit dependency could only ever cover installs
    sharing the suite's venv. Probe the python the lane will actually run and
    say the fix now, instead of `coverage` exiting 5 with a lane log the first
    run has to decode."""
    for lane in lanes:
        if lane.parser == "coveragepy" and "--cov" in lane.command \
                and not _pytest_cov_probe(lane.command):
            print(f"note: lane {lane.name!r} runs `pytest --cov`, and this python cannot "
                  "import pytest_cov — pip install pytest-cov where the suite runs "
                  "(pip install 'crapkit[py]' when that is crapkit's own environment), "
                  "then `crapkit coverage`", file=sys.stderr)


def _extend_gitignore(root: Path, lanes: tuple) -> None:
    """Ignore what adopting crapkit will write: the store, and each lane's
    artifact. Without this the consumer's next `git status` is a wall of
    untracked coverage output nobody asked for."""
    from ..scaffold import gitignore_update

    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    text, added = gitignore_update(current, lanes)
    if not added:
        return
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"added to .gitignore: {', '.join(added)}")


def cmd_init(args: argparse.Namespace) -> int:
    from ..scaffold import detect_lanes, live_lanes, sniff_scopes, starter_toml

    root = Path(args.repo).resolve()
    toml_path = root / "crapkit.toml"
    if toml_path.is_file():
        raise ConfigError(f"crapkit.toml already exists in {root} — edit it instead")
    scopes = sniff_scopes(ls_files(root))
    if not scopes:
        raise ConfigError(_no_scopes_reason(root))
    # A config whose lanes are all commented out scores every function no-lane,
    # so a fresh repo cannot rank anything until somebody hand-writes a lane.
    lanes = detect_lanes(_present_markers(root), _package_json(root),
                         interpreter=_interpreter())
    text = starter_toml(scopes, lanes)
    load_config_text(text)  # self-check: never write a config crapkit cannot read back
    toml_path.write_text(text, encoding="utf-8", newline="\n")
    _print_init_summary(scopes, lanes)
    _warn_missing_pytest_cov(lanes)
    _extend_gitignore(root, live_lanes(lanes, scopes))
    return 0


def _unknown_key_text(unknown) -> str:
    """Name the key AND the spellings its table accepts. A bare rejection makes
    the reader hunt for a key list the tool never printed anywhere."""
    from ..doctor import table_label, valid_keys

    noun = "keys" if unknown.table else "tables"
    return (f"unknown key {unknown.path} — crapkit ignores it (typo?); "
            f"{table_label(unknown.table)} accepts these {noun}: "
            f"{', '.join(valid_keys(unknown.table))}")


def _doctor_keys(raw: dict) -> list[Finding]:
    from ..doctor import unknown_key_findings

    problems = [Finding("FAIL", _unknown_key_text(u)) for u in unknown_key_findings(raw)]
    return problems or [Finding("ok", "config keys all recognized")]


def _listed_files(files: list[str], show: bool) -> list[Finding]:
    return [Finding("", f"       {f}") for f in files] if show else []


def _doctor_scope_files(files_by_scope: dict, cfg, show_files: bool) -> list[Finding]:
    out: list[Finding] = []
    for scope in cfg.scopes:
        files = files_by_scope.get(scope.name, [])
        out.append(Finding("ok" if files else "FAIL",
                           f"scope {scope.name!r}: {len(files)} files"))
        out += _listed_files(files, show_files)
    return out


def _doctor_unclaimed(unclaimed: tuple[str, ...]) -> list[Finding]:
    """Tracked source in a declared language that no scope path owns. It is
    analyzed by nothing and gated by nothing, and it says so nowhere else.

    The paths ride the finding itself rather than trailing it as loose lines, so
    the machine report names them too.
    """
    if not unclaimed:
        return [Finding("ok", "every tracked source file belongs to a scope")]
    return [Finding("FAIL", f"{len(unclaimed)} tracked file(s) match a scope language but "
                            f"no scope path: {', '.join(unclaimed)} — add a [[scope]] "
                            "claiming them, or an [exclude] glob (docs/configuration.md)")]


def _covered_scope_names(cfg) -> set[str]:
    return {s for lane in cfg.lanes for s in lane.scopes} | cfg.coverage_optional_scopes


def _uncovered_scopes(cfg) -> list[str]:
    # A repo with no lanes at all is the inventory-only case the lane summary
    # already notes; coverage_optional scopes never need one.
    if not cfg.lanes:
        return []
    covered = _covered_scope_names(cfg)
    return [s.name for s in cfg.scopes if s.name not in covered]


def _doctor_uncovered(cfg) -> list[Finding]:
    return [Finding("FAIL", f"scope {name!r} is in no lane's scopes list — its functions "
                            "can only score no-lane (declare a lane, or "
                            "coverage_optional = true)")
            for name in _uncovered_scopes(cfg)]


def _doctor_oversized(oversized: tuple[tuple[str, int], ...]) -> list[Finding]:
    """Reported, never a failure: skipping the blob is what max_file_bytes asked for."""
    return [Finding("note", f"{path} ({size} bytes) skipped: over max_file_bytes")
            for path, size in oversized]


def _doctor_scopes(root: Path, cfg, files: list[str], show_files: bool) -> list[Finding]:
    universe = scan_files(files, cfg, size_of=_file_sizer(root))
    return (_doctor_scope_files(universe.by_scope, cfg, show_files)
            + _doctor_unclaimed(universe.unclaimed)
            + _doctor_uncovered(cfg)
            + _doctor_oversized(universe.oversized))


def _lane_problem(root: Path, lane) -> str | None:
    if lane.cwd and not (root / lane.cwd).is_dir():
        return f"lane {lane.name!r}: cwd {lane.cwd!r} does not exist"
    return None


_SCRIPT_SUFFIXES = (".py", ".mjs", ".js", ".ts", ".ps1", ".sh")


def _missing_named_script(cwd: Path, tok: str) -> bool:
    if not tok.endswith(_SCRIPT_SUFFIXES) or tok.startswith("-") or "=" in tok:
        return False
    return not (cwd / tok).is_file()


def _lane_command_problems(root: Path, lane) -> list[str]:
    """Config rot a lane would only reveal 40 minutes in: a runner that no
    longer resolves, a named script that left the repo. Nothing is executed."""
    import shutil

    problems = []
    tokens = lane.command.split()
    if tokens and shutil.which(tokens[0]) is None:
        problems.append(f"lane {lane.name!r}: executable {tokens[0]!r} does not resolve on PATH")
    cwd = root / lane.cwd if lane.cwd else root
    problems += [f"lane {lane.name!r}: command names {tok!r}, which does not exist"
                 for tok in tokens[1:] if _missing_named_script(cwd, tok)]
    return problems


def _doctor_lane_summary(cfg) -> Finding:
    """No lanes is a gap in most repos and the finished state in a cc-only one,
    where every scope declares coverage_optional and `coverage` runs anyway."""
    if cfg.lanes:
        return Finding("ok", f"{len(cfg.lanes)} lane(s) declared")
    if cfg.lane_less_scopes:
        return Finding("note", "no [[lane]] declared — inventory works; coverage needs one")
    return Finding("ok", "no [[lane]] declared: every scope is cc-only, so none is needed")


def _lane_problems(root: Path, cfg) -> list[str]:
    return [p for lane in cfg.lanes
            for p in (_lane_problem(root, lane), *_lane_command_problems(root, lane)) if p]


def _doctor_lanes(root: Path, cfg) -> list[Finding]:
    return ([Finding("FAIL", p) for p in _lane_problems(root, cfg)]
            or [_doctor_lane_summary(cfg)])


def _doctor_artifact_litter(cfg) -> list[Finding]:
    """WARN, never FAIL: a lane writing at the repo root still measures what it
    always did. Failing here would break every consumer that adopted crapkit
    before its lanes wrote under .crapkit/, over tree hygiene."""
    from ..doctor import artifact_litter, scope_top_dirs

    return [Finding("WARN", f"lane {item.lane!r} writes {item.path} at the repo root — "
                            f"point it under .crapkit/ (for example .crapkit/cov/{item.lane}/) "
                            "to keep the tree clean")
            for item in artifact_litter(cfg.lanes, scope_top_dirs(cfg.scopes))]


def _lizard_version() -> str | None:
    try:
        import lizard
    except ImportError:
        return None
    return getattr(lizard, "version", "?")


def _doctor_tools() -> list[Finding]:
    version = _lizard_version()
    if version is None:
        return [Finding("FAIL", "lizard is not importable — pip install lizard")]
    return [Finding("ok", f"lizard {version}")]


def _store_path(root: Path) -> Path:
    return root / ".crapkit" / "crap.sqlite"


def _store_if_any(root: Path) -> SnapshotStore | None:
    """The store, or None when this repo has never run inventory. Doctor is the
    one command that must describe a repo with nothing recorded yet."""
    path = _store_path(root)
    return SnapshotStore(path) if path.is_file() else None


def _newest_coverage_run(store: SnapshotStore) -> dict | None:
    runs = [r for r in store.list_runs() if r["kind"] == "coverage"]
    return runs[-1] if runs else None


@dataclass
class _DirCount:
    """One directory's share of a run: how many functions it holds, how many of
    them carry a verdict other than untested, and the file stems to match on."""
    functions: int = 0
    others: int = 0
    stems: set = field(default_factory=set)


def _dirs_from_counts(counts: list[tuple]) -> dict[str, _DirCount]:
    from ..doctor import _dir_of, _stem_of

    dirs: dict[str, _DirCount] = {}
    for path, functions, others in counts:
        entry = dirs.setdefault(_dir_of(path), _DirCount())
        entry.functions += functions
        entry.others += others
        entry.stems.add(_stem_of(path))
    return dirs


def _unmeasured_gaps(counts: list[tuple], tracked: list[str]) -> tuple:
    """doctor.unmeasured_directories, fed per-path counts instead of per-row rows.

    Same rule, same order, same findings: a directory qualifies when nothing in
    it carries a verdict other than untested and a tracked test file names its
    code. The matching itself stays doctor's, so the mirror rule has one copy.
    """
    from ..doctor import UnmeasuredDir, _matching_test, _test_files

    test_files = _test_files(tracked)
    found = []
    for directory, stats in sorted(_dirs_from_counts(counts).items()):
        example = _matching_test(directory, stats.stems, test_files) if not stats.others else None
        if example:
            found.append(UnmeasuredDir(directory, stats.functions, example))
    return tuple(found)


def _doctor_unmeasured(root: Path, cfg, files: list[str]) -> list[Finding]:
    """WARN, never FAIL: a directory whose functions are all untested while its
    tests exist is a lane that runs without measuring the code it covers.

    The store does the grouping. This used to build a hundred thousand
    sixteen-field rows to read three fields off each of them, then filter the
    coverage_optional scopes back out after reading them.
    """
    store = _store_if_any(root)
    run = _newest_coverage_run(store) if store else None
    if run is None:
        return []
    counts = store.count_by_path(run["id"], flag="untested",
                                 skip_scopes=cfg.coverage_optional_scopes)
    return [Finding("WARN", f"{g.directory}: {g.functions} function(s) all flagged untested "
                            f"while {g.example_test} exists — tests exist but no lane "
                            "measures them")
            for g in _unmeasured_gaps(counts, files)]


def _hook_modes(root: Path) -> dict[str, str]:
    """Index modes of the files the repo's `core.hooksPath` points at.

    Empty when no hooks path is configured, when it points outside the worktree
    (an absolute path is a legitimate setup, and `git ls-files` refuses it with
    exit 128), and when nothing under it is tracked — the local `.git/hooks`
    route commits no files, so there is no bit to be wrong.
    """
    from ..errors import GitError
    from ..gitio import config_value, index_modes

    hooks_path = config_value(root, "core.hooksPath")
    if not hooks_path:
        return {}
    try:
        return index_modes(root, hooks_path)
    except GitError:
        return {}


def _doctor_hook_modes(root: Path) -> list[Finding]:
    """WARN, never FAIL: on Windows the bit is unreadable from the filesystem and
    the hook still runs, so a Windows author must not be blocked by it. On Linux
    and macOS git skips a 100644 hook without a word, which is how crapkit's own
    contributor gate armed nothing."""
    from ..doctor import non_executable_hooks

    return [Finding("WARN", f"{path} is not executable in the index — core.hooksPath "
                            "is set, so Unix clones silently skip it; fix with "
                            f"`git update-index --chmod=+x {path}` and commit")
            for path in non_executable_hooks(_hook_modes(root))]


_CG_SIGNATURE = b"CGPH"
_BLOOM_CHUNK = b"BIDX"  # the changed-path Bloom filter index


def _git_dir(root: Path) -> Path:
    """This repo's git directory. `.git` is a FILE in a linked worktree and in a
    submodule, and its one line names the directory it stands for."""
    dot = root / ".git"
    if not dot.is_file():
        return dot
    named = dot.read_text(encoding="utf-8").partition("gitdir:")[2].strip()
    return (root / named).resolve()


def _object_info_dir(root: Path) -> Path:
    """Where the commit-graph lives. A linked worktree keeps its own git
    directory and shares the main one's objects, which `commondir` names."""
    gitdir = _git_dir(root)
    common = gitdir / "commondir"
    if common.is_file():
        gitdir = (gitdir / common.read_text(encoding="utf-8").strip()).resolve()
    return gitdir / "objects" / "info"


def _graph_files(info: Path) -> list[Path]:
    """Every commit-graph layer this repo has: the single file, or the layers a
    chain file names. `git maintenance` writes the chain, `gc` writes the file."""
    chain = info / "commit-graphs" / "commit-graph-chain"
    if not chain.is_file():
        single = info / "commit-graph"
        return [single] if single.is_file() else []
    return [info / "commit-graphs" / f"graph-{line}.graph"
            for line in chain.read_text(encoding="utf-8").split()]


def _graph_chunks(path: Path) -> frozenset | None:
    """The chunk ids one commit-graph declares, or None when it declares none we
    can trust. The header is signature, version, hash version, chunk count, base
    count; then a 12-byte table entry per chunk plus a terminator. The chunk
    bodies are never read, so this is one short read per layer.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
            if len(head) < 8 or head[:4] != _CG_SIGNATURE:
                return None
            toc = fh.read((head[6] + 1) * 12)
    except OSError:
        return None
    return frozenset(toc[i:i + 4] for i in range(0, len(toc), 12))


def _bloomless_graphs(root: Path) -> list[Path]:
    """Commit-graph layers written without changed-path Bloom filters."""
    layers = [(path, _graph_chunks(path)) for path in _graph_files(_object_info_dir(root))]
    return [path for path, chunks in layers if chunks and _BLOOM_CHUNK not in chunks]


def _doctor_commit_graph(root: Path) -> list[Finding]:
    """WARN, never FAIL: a commit-graph carrying no changed-path Bloom filters.

    Every per-file history walk crapkit makes — churn, `brief`,
    `explain --history` — asks git which commits touched one path, and without
    the filters git opens every tree along the way: 1,147 ms against 194 ms on
    the flagship consumer's 72,470 commits. A repo with NO commit-graph is left
    alone; there is no shape to fix, and git decides when a history wants one.
    """
    if not _bloomless_graphs(root):
        return []
    return [Finding("WARN", "the commit-graph carries no changed-path Bloom filters, so every "
                            "per-file history walk (churn, brief, explain --history) opens "
                            "every tree it passes — fix with `git commit-graph write "
                            "--reachable --changed-paths`")]


def _doctor_findings(root: Path, cfg, raw: dict, files: list[str],
                     show_files: bool) -> list[Finding]:
    return (_doctor_keys(raw)
            + _doctor_scopes(root, cfg, files, show_files)
            + _doctor_lanes(root, cfg)
            + _doctor_artifact_litter(cfg)
            + _doctor_hook_modes(root)
            + _doctor_commit_graph(root)
            + _doctor_tools()
            + _doctor_scoped_tests(cfg)
            + _doctor_unmeasured(root, cfg, files))


def _doctor_scoped_tests(cfg) -> list[Finding]:
    from ..doctor import scoped_test_gaps
    return list(scoped_test_gaps(cfg.lanes, cfg.scoped_tests))


def _at_level(findings: list[Finding], level: str) -> list[str]:
    return [f.text for f in findings if f.level == level]


def _version_report() -> dict:
    import platform

    return {"crapkit": __version__, "lizard": _lizard_version(),
            "python": platform.python_version()}


def _store_report(root: Path) -> dict:
    path = _store_path(root)
    present = path.is_file()
    return {"path": ".crapkit/crap.sqlite", "present": present,
            "size_bytes": path.stat().st_size if present else 0}


def _newest_run_report(store: SnapshotStore | None) -> dict | None:
    runs = store.list_runs() if store else []
    if not runs:
        return None
    return {"id": runs[-1]["id"], "kind": runs[-1]["kind"],
            "verdict_ok": runs[-1]["verdict_ok"]}


def _lane_report(root: Path, lane, stamp: dict) -> dict:
    return {"artifact": lane.artifact,
            "artifact_present": (root / lane.artifact).is_file(),
            "commit": stamp.get("commit"),
            "name": lane.name,
            "seconds": stamp.get("seconds")}


def _lane_reports(root: Path, cfg) -> list[dict]:
    from ..lanes import read_stamps

    stamps = read_stamps(root)
    return [_lane_report(root, lane, stamps.get(lane.artifact, {})) for lane in cfg.lanes]


def _doctor_report(root: Path, cfg, findings: list[Finding]) -> dict:
    """Everything a wrapper needs to tell lane rot from a stale artifact without
    parsing prose: versions, store, newest run, per-lane stamps, findings."""
    from ..analyze import ANALYSIS_VERSION

    return {"analysis_version": ANALYSIS_VERSION,
            "lanes": _lane_reports(root, cfg),
            "newest_run": _newest_run_report(_store_if_any(root)),
            "problems": _at_level(findings, "FAIL"),
            "store": _store_report(root),
            "versions": _version_report(),
            "warnings": _at_level(findings, "WARN")}


def _print_findings(findings: list[Finding]) -> None:
    for f in findings:
        print(f"{f.level:<4} {f.text}" if f.level else f.text)
    problems = _at_level(findings, "FAIL")
    print("doctor: no problems found" if not problems else f"doctor: {len(problems)} problem(s)")


def _emit_doctor(root: Path, cfg, findings: list[Finding], as_json: bool) -> None:
    if as_json:
        _print_json(_doctor_report(root, cfg, findings))
        return
    _print_findings(findings)


def _junit_seconds(path: Path) -> float | None:
    from ..junitparse import suite_seconds

    if not path.is_file():
        return None
    try:
        return suite_seconds(path.read_text(encoding="utf-8"))
    except ToolError:
        return None


def _lane_seconds(root: Path, lane, stamps: dict) -> float | None:
    """What this lane costs, best signal first: the duration its own run
    recorded, else the wall time its junit report claims. None means this lane
    has never left a cost signal on disk — which is not the same as costing 0."""
    recorded = stamps.get(lane.artifact, {}).get("seconds")
    if isinstance(recorded, (int, float)):
        return float(recorded)
    return _junit_seconds(root / lane.results_artifact) if lane.results_artifact else None


def _lane_durations(root: Path, cfg) -> tuple[float, ...]:
    from ..lanes import read_stamps

    stamps = read_stamps(root)
    measured = [_lane_seconds(root, lane, stamps) for lane in cfg.lanes]
    return tuple(s for s in measured if s is not None)


def _doctor_tune(root: Path, cfg) -> int:
    """Advisory only: knob lines from this machine's cpu count and whatever lane
    durations are already on disk. Nothing is written and nothing is executed."""
    import os

    from ..doctor import suggest_knobs, tune_lines

    cpus = os.cpu_count() or 1
    knobs = suggest_knobs(cpus=cpus, lanes=len(cfg.lanes))
    for line in tune_lines(cpus=cpus, knobs=knobs, durations=_lane_durations(root, cfg)):
        print(line)
    return 0


def _plugin_json(path: Path):
    """One JSON file off an installed plugin, or None.

    Missing, unreadable and half-written all read the same, because doctor's job
    here is to name the file rather than to raise inside it. A plugin cache is
    written by an installer this process does not control.
    """
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _hook_handlers(hooks: dict) -> list[dict]:
    """Every command handler in a hooks.json, flat across events and matchers."""
    return [handler for event in hooks.get("hooks", {}).values()
            for matcher in event for handler in matcher.get("hooks", [])]


def _named_protocol(handler: dict) -> str | None:
    """The `--protocol` value one handler spawns crapkit with, or None.

    Paired off the arg list rather than indexed past the flag: a handler whose
    args end at `--protocol` is malformed, and reading it must not raise.
    """
    args = handler.get("args", [])
    return dict(zip(args, args[1:])).get("--protocol")


def _hook_protocols(root: Path) -> tuple[str, ...] | None:
    """Every protocol the plugin's hooks name, or None when it ships no hooks
    file. The empty tuple is the third state: a hooks file naming no protocol,
    which argparse defaults to the supported one."""
    hooks = _plugin_json(root / "hooks" / "hooks.json")
    if not isinstance(hooks, dict):
        return None
    named = [_named_protocol(handler) for handler in _hook_handlers(hooks)]
    return tuple(p for p in named if p is not None)


def _manifest_version(root: Path) -> str | None:
    manifest = _plugin_json(root / ".claude-plugin" / "plugin.json")
    return manifest.get("version") if isinstance(manifest, dict) else None


def _doctor_plugin(plugin_root: str) -> int:
    """`--plugin-root PATH`: the installed plugin against this CLI.

    No repo is read. The plugin cache is not a repo, and an operator asking
    whether their plugin is behind their CLI is rarely standing in one.

    `PROTOCOL` comes from the hook module itself, so the number doctor promises
    and the number `claude-hook` accepts cannot drift apart.
    """
    from ..doctor import plugin_handshake
    from .claude_hook import PROTOCOL

    root = Path(plugin_root)
    lines = plugin_handshake(where=str(root), version=_manifest_version(root),
                             cli_version=__version__, protocols=_hook_protocols(root),
                             supported=PROTOCOL)
    for line in lines:
        print(line)
    return 1 if lines else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import tomllib

    if args.plugin_root:
        return _doctor_plugin(args.plugin_root)
    root = Path(args.repo).resolve()
    cfg = _load_repo_config(root)  # a config that does not parse already exits 3 here
    if args.tune:
        return _doctor_tune(root, cfg)
    raw = tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8"))
    findings = _doctor_findings(root, cfg, raw, ls_files(root), args.show_files)
    _emit_doctor(root, cfg, findings, args.json)
    return 1 if _at_level(findings, "FAIL") else 0


def _watch_rescore(root: Path, moved: list[str]) -> None:
    import subprocess

    present = [f for f in moved if (root / f).is_file()]
    if not present:
        return
    # flush: watch output exists to be tailed live; a block-buffered pipe sits silent
    print(f"--- changed: {', '.join(moved)}", flush=True)
    # a subprocess so a half-saved syntax error can never kill the watcher
    subprocess.run([sys.executable, "-m", "crapkit", "rescore", *present, "--repo", str(root)])


def _watched_files(root: Path, cfg) -> list[str]:
    """Every tracked file a scope claims, flat — the whole subject of one poll."""
    by_scope = assign_files(ls_files(root), cfg, size_of=_file_sizer(root))
    return [f for files in by_scope.values() for f in files]


def _watch_cycles(cycles: int | None):
    """The poll counter: `cycles` polls, or an endless one when nothing bounds it.

    Unbounded is the default, because a watcher an operator starts is meant to
    outlive the shell it was typed into. A bound is what lets the loop be driven
    to a known end — by a test, or by a caller that wants one sweep and its exit
    code rather than a process to kill.
    """
    from itertools import count

    return count() if cycles is None else range(cycles)


def _watch_banner(watched: int, interval: float, cycles: int | None) -> str:
    """The first line, naming how this run ends. Telling an operator to press
    ctrl-c on a `--cycles 3` run describes a loop that is not the one running."""
    stop = "ctrl-c to stop" if cycles is None else f"{cycles} poll(s) then stop"
    return f"watching {watched} tracked files every {interval}s — {stop}"


def _watch_cycle(root: Path, files: list[str], prev: dict[str, float],
                 interval: float) -> dict[str, float]:
    """One poll: wait, re-stat, rescore whatever moved; the new snapshot out."""
    import time

    from ..watch import changed_paths, snapshot_mtimes

    time.sleep(interval)
    cur = snapshot_mtimes(root, files)
    moved = changed_paths(prev, cur)
    if moved:
        _watch_rescore(root, moved)
    return cur


def cmd_watch(args: argparse.Namespace) -> int:
    from ..watch import snapshot_mtimes

    root = Path(args.repo).resolve()
    files = _watched_files(root, _load_repo_config(root))
    prev = snapshot_mtimes(root, files)
    print(_watch_banner(len(prev), args.interval, args.cycles), flush=True)
    try:
        for _ in _watch_cycles(args.cycles):
            prev = _watch_cycle(root, files, prev, args.interval)
    except KeyboardInterrupt:
        pass
    return 0
