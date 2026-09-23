"""`crapkit doctor` checks: does crapkit.toml still describe THIS repo? Pure."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import NamedTuple

from .universe import LANGUAGE_EXTENSIONS, scopes_with_tests

from .config_contract import known_keys

_KNOWN = known_keys()


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


_FILES_WITHOUT_TESTS = (
    "scope {name!r} template names {{files}} but no test file lives under {paths}: "
    "`crapkit test-scoped` would hand the runner source paths to collect from and find "
    "no tests (runner exit 5, crapkit exit 1); drop {{files}} so the template runs the "
    "whole suite, or point it at the tests"
)


def _files_without_tests(name: str, template: str, scope_paths: dict,
                         tested: frozenset[str]) -> bool:
    return name in scope_paths and "{files}" in template and name not in tested


def files_template_gaps(scoped_tests, scope_paths: dict[str, tuple[str, ...]],
                        tracked) -> tuple[Finding, ...]:
    """A `{files}` template on a scope whose declared paths hold no test file,
    sorted by scope. FAIL.

    That template is the one init wrote for every python scope before 0.5.0,
    and on the ordinary pkg/ + tests/ layout it hands pytest a source file to
    collect from: `no tests ran`, runner exit 5, which four reporters read as
    the suite failing. A template for a scope the config does not declare is
    the loader's business, not this check's.
    """
    tested = scopes_with_tests(tracked, scope_paths)
    gaps = sorted(name for name, template in scoped_tests
                  if _files_without_tests(name, template, scope_paths, tested))
    return tuple(Finding("FAIL", _FILES_WITHOUT_TESTS.format(
        name=name, paths=", ".join(scope_paths[name]))) for name in gaps)


def _coverage_data_file(lane) -> str:
    """Where a coveragepy lane's data file lands, as far as the config says.

    COVERAGE_FILE from the lane's env, else coverage.py's default in the
    directory the lane starts in. A command that passes its own `--data-file`
    is keyed by that command, so it shares a file with no other lane."""
    if "--data-file" in lane.command:
        return lane.command
    name = dict(lane.env).get("COVERAGE_FILE") or ".coverage"
    return os.path.normcase(os.path.normpath(os.path.join(lane.cwd or ".", name)))


def shared_coverage_data(lanes) -> tuple[tuple[str, ...], ...]:
    """The names of coveragepy lanes that write one data file, one group each.

    Two such lanes run at once collide on it: coverage.py's sqlite file refuses
    the second writer with `table coverage_schema already exists`."""
    groups: dict[str, list[str]] = {}
    for lane in lanes:
        if lane.parser == "coveragepy":
            groups.setdefault(_coverage_data_file(lane), []).append(lane.name)
    return tuple(tuple(names) for _, names in sorted(groups.items()) if len(names) > 1)


def shared_data_words(names: tuple[str, ...]) -> tuple[str, str]:
    """What one group shares and the fix, in the words doctor and doctor --tune
    both print."""
    listed = ", ".join(repr(name) for name in names)
    return (f"lanes {listed} write one coverage.py data file", "give each its own, for example "
            f"env = {{ COVERAGE_FILE = \".coverage.{names[0]}\" }}")


class Knobs(NamedTuple):
    max_parallel_lanes: int
    analysis_workers: int
    mutation_workers: int
    shared: tuple[tuple[str, ...], ...] = ()  # lane groups that held the slots at 1


def suggest_knobs(*, cpus: int, lanes: int, shared: tuple[tuple[str, ...], ...] = ()) -> Knobs:
    """Advisory parallelism for this machine.

    One core stays for the shell watching the run. A lane and a mutation worker
    each hold a whole test suite in memory, so they get a quarter of the box
    rather than a core apiece, and there is never a reason to run more lane
    slots than there are lanes. Lanes that share a coverage.py data file hold
    the slots at 1: the scheduler does not know which lanes may run together.
    """
    slots = 1 if shared else max(1, min(lanes, cpus // 4))
    return Knobs(max_parallel_lanes=slots,
                 analysis_workers=max(1, cpus - 1),
                 mutation_workers=max(1, cpus // 4), shared=shared)


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


def _held_lines(shared: tuple[tuple[str, ...], ...]) -> list[str]:
    out = []
    for names in shared:
        what, fix = shared_data_words(names)
        out.append(f"# held at 1: {what}, and two lanes on it at once can lose one to "
                   f"sqlite3.OperationalError; {fix}, then rerun doctor --tune")
    return out


def tune_lines(*, cpus: int, knobs: Knobs, durations: tuple[float, ...]) -> list[str]:
    """Paste-ready [crapkit] knob lines plus what the suggestion was based on."""
    return [f"# doctor --tune: suggestions for {cpus} cpu(s); nothing was written",
            "[crapkit]",
            f"max_parallel_lanes = {knobs.max_parallel_lanes}",
            *_held_lines(knobs.shared),
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


_UNMATCHED_INPUT = (
    "lane {lane!r}: inputs entry {entry!r} matches no file that is tracked, or untracked "
    "and not ignored, so --reuse-unchanged never sees a change through it; fix the "
    "spelling or drop the entry"
)


def _under(entry: str, path: str) -> bool:
    return entry == "." or path == entry or path.startswith(f"{entry}/")


def _matches_nothing(entry: str, visible: tuple[str, ...]) -> bool:
    return not any(_under(entry, path) for path in visible)


def unmatched_inputs(lanes, visible: tuple[str, ...]) -> tuple[Finding, ...]:
    """Lane `inputs` entries no visible path falls under. FAIL: git reads an entry
    as a literal pathspec, so a typo such as `scr` for `src` matches nothing and
    keeps the lane reusable while its real sources change. The config still
    loads, so a path created later is only doctor's problem until it exists."""
    return tuple(Finding("FAIL", _UNMATCHED_INPUT.format(lane=lane.name, entry=entry))
                 for lane in lanes for entry in lane.inputs
                 if _matches_nothing(entry, visible))


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
    """One directory's share of a run: how many functions it holds, how many of
    them carry a verdict other than untested, and the file stems and language
    families to match a test on."""
    functions: int = 0
    others: int = 0
    stems: set = field(default_factory=set)
    families: set = field(default_factory=set)


_TEST_DIR_PARTS = frozenset({"test", "tests", "__tests__", "spec", "specs"})


def _dir_of(path: str) -> str:
    return path.rpartition("/")[0]


def _stem_of(path: str) -> str:
    return path.rsplit("/", 1)[-1].split(".")[0]


# One family per coverage parser, not one per lizard reader: a vitest run
# measures .ts, .tsx, .js and the .vue components beside them as one suite, and
# its tests are .spec.ts whatever the component is. Every other language is its
# own family.
_JS_FAMILY = frozenset({"javascript", "typescript", "tsx", "vue"})


def _family_of(path: str) -> str | None:
    """The language family a path's extension puts it in, or None for a file
    no lizard reader parses (a .md, a .json, a .txt)."""
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        if path.endswith(extensions):
            return "javascript" if language in _JS_FAMILY else language
    return None


def _subject_stem(path: str) -> str | None:
    """The source stem a test file names, or None when the name is not a test.
    Four conventions cover every runner crapkit parses: foo.test.ts, foo.spec.ts,
    test_foo.py, foo_test.py. The extension has to be one a reader parses:
    docs/_mermaid_test.md is named like a test and is not one."""
    name = path.rsplit("/", 1)[-1]
    stem = _stem_of(path)
    if _family_of(path) is None:
        return None
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
    a flat tests/ mirrors nothing, or it would claim the whole repo, and a
    directory with no test-named component (a root src/) is no tests/ tree."""
    parts = _mirrored_parts(test_dir)
    if not parts or len(parts) == len(_path_parts(test_dir)):
        return False
    return parts == _path_parts(source_dir)[-len(parts):]


def _nearest_below(directory: str, candidates: tuple[str, ...]) -> str | None:
    """The test below the directory with the fewest path components, then the
    first alphabetically. The repo root has nothing below it: "below the root"
    would be the whole repo."""
    below = (p for p in candidates if _dir_of(p).startswith(directory + "/"))
    return min(below, key=lambda p: (len(_path_parts(p)), p), default=None)


def _tests_by_family(tracked: list[str]) -> dict[str, tuple[str, ...]]:
    """The tracked test files, sorted, under the language family each is in."""
    by_family: dict[str, list[str]] = {}
    for path in sorted(p for p in tracked if _subject_stem(p)):
        by_family.setdefault(_family_of(path), []).append(path)
    return {family: tuple(paths) for family, paths in by_family.items()}


def _first(paths: tuple[str, ...], qualifies) -> str | None:
    return next((p for p in paths if qualifies(p)), None)


def _matching_test(directory: str, stems: set, families: set,
                   tests: dict[str, tuple[str, ...]]) -> str | None:
    """The tracked test file that names this directory's code, the nearest first:
    a test in the directory, then the nearest one below it, then a tests/ mirror
    of the directory, then a same-stem test anywhere (tests/test_parser.py for
    core/parser.py).

    Every tier only looks at tests in `families`, the language families of
    the files the store scored here. The directory's other files do not count: a Python
    directory with a static/ folder of .js below it is still Python, and on a
    repo with twenty handler.test.ts files a stem alone paired a Python
    directory with a TypeScript test.
    """
    candidates = tuple(sorted(p for family in families for p in tests.get(family, ())))
    tiers = (lambda: _first(candidates, lambda p: _dir_of(p) == directory),
             lambda: _nearest_below(directory, candidates),
             lambda: _first(candidates, lambda p: _mirrors(_dir_of(p), directory)),
             lambda: _first(candidates, lambda p: _subject_stem(p) in stems))
    return next(filter(None, (tier() for tier in tiers)), None)


def _group_dirs(counts) -> dict[str, _DirStats]:
    stats: dict[str, _DirStats] = {}
    for path, functions, others in counts:
        entry = stats.setdefault(_dir_of(path), _DirStats())
        entry.functions += functions
        entry.others += others
        entry.stems.add(_stem_of(path))
        entry.families.add(_family_of(path))
    return stats


def unmeasured_directories(counts, tracked: list[str]) -> tuple[UnmeasuredDir, ...]:
    """Directories where EVERY scored function is flag "untested" and a test file
    for that directory exists anyway.

    That combination is a tooling gap, not a testing gap: the lane runs, the
    tests pass, and the lane's own include list never looks at this code. One
    measured function anywhere in the directory clears it.

    `counts` is the run grouped per path, the shape SnapshotStore.count_by_path
    returns: (path, functions, functions flagged anything but untested). The
    store groups a hundred thousand rows into a few thousand paths and leaves
    the coverage_optional scopes out, so the rule never reads a scored row.
    """
    test_files = _tests_by_family(tracked)
    found = []
    for directory, stats in sorted(_group_dirs(counts).items()):
        example = _matching_test(directory, stats.stems, stats.families, test_files) \
            if not stats.others else None
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
    plugin whose hooks file is missing or unreadable.
    """
    if protocols is None:
        return (f"crapkit doctor: the plugin at {where} has no readable hooks/hooks.json; "
                f"reinstall the plugin or repair that file before relying on its advisory hook.")
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
