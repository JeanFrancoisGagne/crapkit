"""`crapkit doctor` checks: does crapkit.toml still describe THIS repo? Pure."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

_KNOWN = {
    "": {"crapkit", "scope", "lane", "exclude"},
    "crapkit": {"target", "churn_window_months", "worklist_floor", "worklist_top",
                "ratchet_file", "alert_command", "scoped_tests", "notes",
                "mutation_command", "mutation_timeout_seconds", "mutation_workers",
                "diff_uncovered_max", "debt_max_age_months", "repayment_min_per_30d",
                "max_parallel_lanes", "analysis_workers", "tighten_max_jump"},
    "scope": {"name", "paths", "languages", "target", "coverage_optional", "notes"},
    "lane": {"name", "command", "artifact", "parser", "scopes", "cwd", "path_prefix", "env",
             "full_suite", "container_ok", "results_artifact", "timeout_seconds",
             "no_progress_seconds", "retries",
             "retest_command"},
    "exclude": {"globs", "max_file_bytes"},
}


_ARRAY_TABLES = frozenset({"scope", "lane"})


class UnknownKey(NamedTuple):
    """One ignored key. The table travels with it: without it the reader cannot
    be told which spellings would have been accepted."""
    path: str   # dotted, as printed: "crapkit.churn_windo_months"
    table: str  # a key of _KNOWN; "" is the top level


# Sorted once at import, not once per rejected key: every unknown-key message
# quotes its whole table, so a config with twenty typos in [crapkit] sorted the
# same fifteen strings twenty times.
_VALID_KEYS = {table: tuple(sorted(keys)) for table, keys in _KNOWN.items()}


def valid_keys(table: str) -> tuple[str, ...]:
    """Everything crapkit reads in one table, sorted so a message never moves."""
    return _VALID_KEYS[table]


def table_label(table: str) -> str:
    """How the table is spelled in crapkit.toml. Arrays of tables double their
    brackets, so a suggestion can be pasted as written."""
    if not table:
        return "crapkit.toml"
    return f"[[{table}]]" if table in _ARRAY_TABLES else f"[{table}]"


def _unknown_in(table: str, mapping: dict, label: str) -> list[UnknownKey]:
    return [UnknownKey(f"{label}.{key}" if label else key, table)
            for key in mapping if key not in _KNOWN[table]]


def unknown_key_findings(raw: dict) -> list[UnknownKey]:
    """Keys crapkit silently ignores — usually typos — each with its table. The
    loader stays lenient (a config must survive version skew); doctor is where
    typos die."""
    problems = _unknown_in("", raw, "")
    for table in ("crapkit", "exclude"):
        problems += _unknown_in(table, raw.get(table, {}), table)
    for table in ("scope", "lane"):
        for row in raw.get(table, ()):
            problems += _unknown_in(table, row, f"{table} {row.get('name', '?')!r}")
    return problems


class Finding(NamedTuple):
    """One doctor line. FAIL decides the exit code, WARN never does, and an
    empty level is a continuation line (the file list under a scope)."""
    level: str
    text: str


_NO_TEMPLATE = (
    "scope {name!r} has a lane but no [crapkit.scoped_tests] template — "
    "`crapkit test-scoped` exits 3 on its files, so whoever edits them is handed "
    'no command to run their tests; add {name} = "<test command>" under '
    "[crapkit.scoped_tests]"
)


def scoped_test_gaps(lanes, scoped_tests) -> tuple[Finding, ...]:
    """Scopes a lane measures that `crapkit test-scoped` cannot run, sorted. WARN.

    Nothing is broken here: the lane runs, coverage lands, the gate holds. What
    is missing is the one command a start-editing packet can hand over, and the
    gap otherwise surfaces as an exit 3 in the middle of somebody's edit.

    The lanes are walked once, not once per scope: a config declaring a lane per
    package has more lanes than scopes, and this is a doctor line, not a survey.
    """
    templated = {scope for scope, _template in scoped_tests}
    laned = {scope for lane in lanes for scope in lane.scopes}
    return tuple(Finding("WARN", _NO_TEMPLATE.format(name=name))
                 for name in sorted(laned - templated))


class Knobs(NamedTuple):
    max_parallel_lanes: int
    analysis_workers: int
    mutation_workers: int


def suggest_knobs(*, cpus: int, lanes: int) -> Knobs:
    """Advisory parallelism for this machine.

    One core stays for the shell watching the run. A lane and a mutation worker
    each hold a whole test suite in memory, so they get a quarter of the box
    rather than a core apiece, and there is never a reason to run more lane
    slots than there are lanes.
    """
    return Knobs(max_parallel_lanes=max(1, min(lanes, cpus // 4)),
                 analysis_workers=max(1, cpus - 1),
                 mutation_workers=max(1, cpus // 4))


def parallel_seconds(durations: tuple[float, ...], slots: int) -> float:
    """Makespan of these lanes on `slots` runners, longest first (LPT).

    A bound, never a promise: lanes contend for the same cores and disk. It is
    exact for the handful of lanes a real config declares.
    """
    ends = [0.0] * max(1, slots)
    for seconds in sorted(durations, reverse=True):
        ends[ends.index(min(ends))] += seconds
    return max(ends)


def _cost_line(slots: int, durations: tuple[float, ...]) -> str:
    if not durations:
        return "# lane cost: no durations recorded yet — suggested from the cpu count alone"
    return (f"# lane cost: {sum(durations):.1f}s serial -> "
            f"~{parallel_seconds(durations, slots):.1f}s across {slots} lane slot(s)")


def tune_lines(*, cpus: int, knobs: Knobs, durations: tuple[float, ...]) -> list[str]:
    """Paste-ready [crapkit] knob lines plus what the suggestion was based on."""
    return [f"# doctor --tune: suggestions for {cpus} cpu(s); nothing was written",
            "[crapkit]",
            f"max_parallel_lanes = {knobs.max_parallel_lanes}",
            f"analysis_workers = {knobs.analysis_workers}",
            f"mutation_workers = {knobs.mutation_workers}",
            _cost_line(knobs.max_parallel_lanes, durations)]


class ArtifactLitter(NamedTuple):
    """One lane output written outside .crapkit/. The lane travels with the path
    because a tree with fifteen coverage directories in it cannot say which lane
    made which."""
    lane: str
    path: str


_STORE_DIR = ".crapkit"


def _first_part(path: str) -> str:
    return path.replace("\\", "/").partition("/")[0]


def scope_top_dirs(scopes) -> frozenset[str]:
    """The top-level directory of every declared scope path.

    A lane that writes inside a package it measures (web/coverage/ beside
    web/src) is that package's business, not root litter.
    """
    return frozenset(_first_part(path) for scope in scopes for path in scope.paths)


def _artifact_top(path: str) -> str:
    """The top-level directory this artifact lands in, or "" for a repo-root
    file — which has no directory to be excused by."""
    normalized = path.replace("\\", "/")
    return _first_part(normalized) if "/" in normalized else ""


def _lane_outputs(lane) -> tuple[str, ...]:
    return tuple(path for path in (lane.artifact, lane.results_artifact) if path)


def artifact_litter(lanes, scope_tops: frozenset[str]) -> tuple[ArtifactLitter, ...]:
    """Lane outputs that dirty the consumer's tree: a file at the repo root, or a
    top-level directory that is neither .crapkit/ nor a scope's own tree.

    Reported per path, in declaration order: a lane arguing about its coverage
    file usually drops a junit report beside it, and folding the two into one
    finding leaves the second one unnamed.
    """
    clean = {_STORE_DIR, *scope_tops}
    return tuple(ArtifactLitter(lane.name, path) for lane in lanes
                 for path in _lane_outputs(lane) if _artifact_top(path) not in clean)


_PLAIN_FILE_MODE = "100644"  # git's non-executable file; 100755 is the armed one


def non_executable_hooks(modes: dict[str, str]) -> tuple[str, ...]:
    """Committed hook files git will silently skip, in path order.

    A hook whose index mode is 100644 does not run on Linux or macOS, so
    `core.hooksPath` installs a gate that never fires. Only plain files are
    reported: a symlink (120000) or a gitlink (160000) is not a mode
    `git update-index --chmod=+x` would fix.
    """
    return tuple(sorted(path for path, mode in modes.items()
                        if mode == _PLAIN_FILE_MODE))


class UnmeasuredDir(NamedTuple):
    directory: str
    functions: int
    example_test: str


@dataclass
class _DirStats:
    functions: int = 0
    flags: set = field(default_factory=set)
    stems: set = field(default_factory=set)


_TEST_DIR_PARTS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


def _dir_of(path: str) -> str:
    return path.rpartition("/")[0]


def _stem_of(path: str) -> str:
    return path.rsplit("/", 1)[-1].split(".")[0]


def _subject_stem(path: str) -> str | None:
    """The source stem a test file names, or None when the name is not a test.
    Four conventions cover every runner crapkit parses: foo.test.ts, foo.spec.ts,
    test_foo.py, foo_test.py."""
    name = path.rsplit("/", 1)[-1]
    stem = _stem_of(path)
    if ".test." in name or ".spec." in name:
        return stem
    if stem.startswith("test_"):
        return stem[len("test_"):]
    if stem.endswith("_test"):
        return stem[:-len("_test")]
    return None


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(p for p in path.split("/") if p)


def _mirrored_parts(test_dir: str) -> tuple[str, ...]:
    return tuple(p for p in _path_parts(test_dir) if p not in _TEST_DIR_PARTS)


def _mirrors(test_dir: str, source_dir: str) -> bool:
    """A tests/ mirror: the test directory, with its test-named components
    dropped, is a path suffix of the source directory. tests/api mirrors src/api;
    a flat tests/ mirrors nothing, or it would claim the whole repo."""
    parts = _mirrored_parts(test_dir)
    return parts == _path_parts(source_dir)[-len(parts):] if parts else False


def _test_files(tracked: list[str]) -> list[str]:
    return sorted(p for p in tracked if _subject_stem(p))


def _matching_test(directory: str, stems: set, test_files: list[str]) -> str | None:
    """The first tracked test file that names this directory's code: a same-stem
    test anywhere in the repo (tests/test_parser.py for core/parser.py),
    a sibling, or a tests/ mirror of the directory."""
    for path in test_files:
        if _subject_stem(path) in stems or _mirrors(_dir_of(path), directory):
            return path
    return None


def _group_dirs(rows, skip_scopes: frozenset[str]) -> dict[str, _DirStats]:
    stats: dict[str, _DirStats] = {}
    for row in rows:
        if row.scope in skip_scopes:
            continue
        entry = stats.setdefault(_dir_of(row.path), _DirStats())
        entry.functions += 1
        entry.flags.add(row.flag)
        entry.stems.add(_stem_of(row.path))
    return stats


def unmeasured_directories(rows, tracked: list[str], *,
                           skip_scopes: frozenset[str] = frozenset()) -> tuple[UnmeasuredDir, ...]:
    """Directories where EVERY scored function is flag "untested" and a test file
    for that directory exists anyway.

    That combination is a tooling gap, not a testing gap: the lane runs, the
    tests pass, and the lane's own include list never looks at this code. One
    measured function anywhere in the directory clears it.
    """
    test_files = _test_files(tracked)
    found = []
    for directory, stats in sorted(_group_dirs(rows, skip_scopes).items()):
        example = _matching_test(directory, stats.stems, test_files) \
            if stats.flags == {"untested"} else None
        if example:
            found.append(UnmeasuredDir(directory, stats.functions, example))
    return tuple(found)


# --- the plugin handshake -----------------------------------------------------
#
# The plugin and the CLI are two artifacts with one version number between them.
# A plugin ahead of the CLI spawns a subcommand argparse does not have and turns
# every edit on the machine into a usage dump; a plugin behind it registers a
# hook the CLI would answer and nobody asked. Neither side notices on its own,
# so `doctor --plugin-root` asks. Pure: the caller reads the two files.

def _version_gap(where: str, version: str, cli_version: str, cli_where: str) -> str | None:
    """One line naming both numbers, the executable the second one came from,
    and both repairs.

    Which side is behind is not decided here. Version ordering across a
    pre-release, a local build and a published wheel is a guess, and a guess
    that names the wrong repair costs more than naming two.

    `cli_where` is the console script the plugin will spawn, which on a machine
    with a venv crapkit and a pipx crapkit is not the module answering this
    question. The path rides this line rather than a line of its own: agreement
    is silence here, and a line printed on success is a line people stop
    reading.
    """
    if version == cli_version:
        return None
    return (f"crapkit doctor: the plugin at {where} is version {version}, and the crapkit "
            f"its hooks spawn ({cli_where}) is {cli_version}. Reinstall whichever is "
            f"behind: `claude plugin install crapkit@crapkit`, or `pip install -U crapkit`.")


def _protocol_gap(where: str, protocols: tuple[str, ...] | None, supported: str) -> str | None:
    """One line when the hook asks for a protocol this CLI does not answer.

    A handler naming no `--protocol` at all is not a gap: argparse defaults it,
    and the default is the supported one. `None` is the other thing entirely, a
    plugin carrying no hooks file, which registers no advisory at all.
    """
    if protocols is None:
        return (f"crapkit doctor: the plugin at {where} ships no hooks/hooks.json, so it "
                f"registers no advisory hook.")
    odd = sorted(set(protocols) - {supported})
    if not odd:
        return None
    return (f"crapkit doctor: the plugin at {where} asks for hook protocol {', '.join(odd)}; "
            f"this crapkit answers {supported}, so `claude-hook` exits 0 silent on every edit.")


def plugin_handshake(*, where: str, version: str | None, cli_version: str, cli_where: str,
                     protocols: tuple[str, ...] | None, supported: str) -> list[str]:
    """Every disagreement between an installed plugin and this CLI, one per line.

    Empty is the answer that matters: the two agree, and a check that prints on
    success is a check people stop reading.

    A missing manifest ends it. There is no version to compare, and a protocol
    line printed underneath would bury the one fact that explains both.
    """
    if version is None:
        return [f"crapkit doctor: the plugin at {where} has no .claude-plugin/plugin.json"]
    return [line for line in (_version_gap(where, version, cli_version, cli_where),
                              _protocol_gap(where, protocols, supported)) if line]
