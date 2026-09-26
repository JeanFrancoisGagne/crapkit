"""`crapkit doctor` checks: does crapkit.toml still describe THIS repo? Pure."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from pathlib import PurePath
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


_DATA_FILE_FLAG = re.compile(r"""--data-file[=\s]+["']?([^\s"']+)""")


def _coverage_data_file(lane) -> str:
    """Where a coveragepy lane's data file lands, as far as the config says.

    The command's own `--data-file`, else COVERAGE_FILE from the lane's env,
    else coverage.py's default, in the directory the lane starts in."""
    flag = _DATA_FILE_FLAG.search(lane.command)
    name = flag.group(1) if flag else dict(lane.env).get("COVERAGE_FILE") or ".coverage"
    return os.path.normcase(os.path.normpath(os.path.join(lane.cwd or ".", name)))


def _coverage_data_files(lanes) -> dict[str, str]:
    """Each coveragepy lane's data file, by lane name."""
    return {lane.name: _coverage_data_file(lane) for lane in lanes if lane.parser == "coveragepy"}


def _lanes_taken_in(base: str, files: dict[str, str]) -> tuple[str, ...]:
    """The lanes whose data files a lane on `base` touches. pytest-cov deletes
    every `<base>.*` beside `base` when a lane starts and combines them when it
    ends, which takes in another lane's `<base>.b` and the pieces it writes
    while it runs."""
    return tuple(name for name, path in files.items() if path == base or (
        path.startswith(base + ".") and os.sep not in path[len(base) + 1:]))


def shared_coverage_data(lanes) -> tuple[tuple[str, ...], ...]:
    """The names of coveragepy lanes whose data files one of them deletes and
    combines, one group per such lane's file.

    Two lanes on one file collide on it: coverage.py's sqlite file refuses the
    second writer with `table coverage_schema already exists`. A lane left on
    `.coverage` beside one on `.coverage.b` deletes the other's pieces while
    it writes them, which Windows refuses with WinError 32."""
    files = _coverage_data_files(lanes)
    groups = {base: _lanes_taken_in(base, files) for base in files.values()}
    return tuple(names for _, names in sorted(groups.items()) if len(names) > 1)


def shared_data_words(names: tuple[str, ...]) -> tuple[str, str]:
    """What one group shares and the fix, in the words doctor and doctor --tune
    both print."""
    listed = ", ".join(repr(name) for name in names)
    each = " and ".join(f"env = {{ COVERAGE_FILE = \".coverage.{name}\" }} in lane {name!r}"
                        for name in names)
    return (f"lanes {listed} write coverage.py data files that one of them deletes and combines",
            f"give each lane its own COVERAGE_FILE, for example {each}")


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
        out.append(f"# held at 1: {what}, and two of them at once can fail one lane; "
                   f"{fix}, then rerun doctor --tune")
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

# The commands that move an installed plugin to the marketplace's current copy,
# and what makes a running client load it, per harness. `claude plugin install`
# over an older install prints "already installed" and moves nothing. The
# update runs once per scope that holds the install (see InstallScope). A
# Codex marketplace is added at a release tag, and `codex plugin marketplace
# upgrade` keeps it at that tag, so Codex's refresh adds it again at the tag
# of the CLI's release ({version}).
CODEX_MARKETPLACE_ADD = ("codex plugin marketplace add https://github.com/JeanFrancoisGagne/crapkit.git "
                         "--ref v{version} --sparse .claude-plugin --sparse plugin")
_PLUGIN_UPDATE = {
    "claude": (("claude plugin marketplace update crapkit",),
               ("claude plugin update crapkit@crapkit --scope {scope}",),
               "restart Claude Code's sessions"),
    "codex": (("codex plugin marketplace remove crapkit", CODEX_MARKETPLACE_ADD),
              ("codex plugin add crapkit@crapkit",), "start a new Codex task"),
}
_PLAIN_RELEASE = re.compile(r"\d+(?:\.\d+)*")


class InstallScope(NamedTuple):
    """One scope Claude Code's installed_plugins.json records an install under,
    and the project directory a project or local install belongs to.

    Claude Code picks which project a `--scope project` or `--scope local`
    command acts on from the directory it runs in: outside it, `claude plugin
    update` moved the first project install on the list, and `claude plugin
    install` writes to whatever project the shell stands in. So a repair names
    that directory.
    """
    scope: str
    project: str | None = None


USER_SCOPE = (InstallScope("user"),)


def _for_scope(commands: tuple[str, ...], at: InstallScope) -> str:
    spelled = ", then ".join(f"`{command.format(scope=at.scope)}`" for command in commands)
    return f"{spelled} (run in {at.project})" if at.project else spelled


def _for_each_scope(commands: tuple[str, ...], scopes: tuple[InstallScope, ...]) -> str:
    """`commands` spelled once per scope that holds the install, in the order
    installed_plugins.json lists them; the user scope when none is known."""
    return "; ".join(_for_scope(commands, at) for at in scopes or USER_SCOPE)


class InPlace(NamedTuple):
    """A local directory marketplace Claude Code loads the plugin from in
    place, and the commands that update it and restore one of its files:
    `git -C DIR pull` and `git -C ROOT checkout --` for a git checkout, None
    for a directory that is no checkout. `claude plugin update` only refreshes
    the cache copy, which is not the one that runs."""
    marketplace: str
    pull: str | None = None
    checkout: str | None = None


_LOADS_IN_PLACE = ("(Claude Code loads it in place from the local directory marketplace at {at}, "
                   "and `claude plugin update` does not change it)")

# The commands that put an install's files back, per harness. `claude plugin
# update` keeps an install whose version did not move, so a file it holds wrong
# comes back only through a reinstall, run once per scope that holds it.
_REINSTALL = {"claude": ("claude plugin uninstall crapkit@crapkit --scope {scope}",
                         "claude plugin install crapkit@crapkit --scope {scope}"),
              "codex": ("codex plugin remove crapkit@crapkit", "codex plugin add crapkit@crapkit")}


class _Install(NamedTuple):
    """The install a line is about: where it is, the harness that installed
    it, the scopes that hold it, and the directory Claude Code loads it from
    in place, if any."""
    where: str
    harness: str
    scopes: tuple[InstallScope, ...]
    in_place: InPlace | None


class _Repairs(NamedTuple):
    """How each side moves: the plugin's commands after "update it", what
    makes a running client load it, the CLI's upgrade, and the clause that
    brings back a hooks file doctor cannot read."""
    plugin: str
    reload: str
    cli: str
    hooks: str


def _in_place_fix(where: str, cli_version: str, in_place: InPlace) -> str:
    how = (f"with `{in_place.pull}`" if in_place.pull
           else f"by copying crapkit {cli_version}'s plugin/ directory over {where}")
    return f"{how} " + _LOADS_IN_PLACE.format(at=in_place.marketplace)


def _plugin_fix(install: _Install, cli_version: str) -> str:
    """How the plugin moves: its local directory, when Claude Code loads it
    in place, else its harness's update, once per scope that holds it."""
    if install.in_place:
        return _in_place_fix(install.where, cli_version, install.in_place)
    fetch, update, _ = _PLUGIN_UPDATE[install.harness]
    fetched = ", then ".join(f"`{command.format(version=cli_version)}`" for command in fetch)
    return f"with {fetched}, then {_for_each_scope(update, install.scopes)}"


def _restore(install: _Install, cli_version: str, file: str) -> str:
    """The clause that brings back one of the plugin's files: from git or
    crapkit's own copy in the directory Claude Code loads in place, else the
    harness's reinstall, once per scope that holds the install."""
    in_place = install.in_place
    if in_place is None:
        return f"reinstall it with {_for_each_scope(_REINSTALL[install.harness], install.scopes)}"
    how = (f"with `{in_place.checkout} {file}`" if in_place.checkout
           else f"by copying crapkit {cli_version}'s plugin/{file} to {file} under {install.where}")
    return f"restore it {how} " + _LOADS_IN_PLACE.format(at=in_place.marketplace)


def plugin_harness(where: str, codex_home: str | None) -> str:
    """"codex" for a plugin Codex installed (under CODEX_HOME, or a .codex
    directory), else "claude"."""
    parts = PurePath(where).parts
    under_home = bool(codex_home) and PurePath(where).is_relative_to(codex_home)
    return "codex" if under_home or ".codex" in parts else "claude"


def _plain(version: str) -> tuple[int, ...] | None:
    return tuple(int(n) for n in version.split(".")) if _PLAIN_RELEASE.fullmatch(version) else None


def _behind(version: str, cli_version: str) -> str | None:
    """"plugin" or "cli" when both are plain releases, else None: a
    pre-release or a local build does not order against a release plainly
    enough to send someone to one repair."""
    plugin, cli = _plain(version), _plain(cli_version)
    if plugin is None or cli is None:
        return None
    return "plugin" if plugin < cli else "cli"


def _repair(behind: str | None, repairs: _Repairs) -> str:
    if behind == "plugin":
        return f"The plugin is behind; update it {repairs.plugin}, and {repairs.reload}."
    if behind == "cli":
        return f"The CLI is behind; upgrade it with `{repairs.cli}`."
    return (f"Update whichever is behind: the plugin {repairs.plugin}; the CLI "
            f"with `{repairs.cli}`.")


def _version_gap(where: str, version: str, cli_version: str, cli_where: str,
                 repairs: _Repairs) -> str | None:
    """One line naming both numbers, the executable the second one came from,
    which side is behind, and the commands that move it.

    The plugin's repair (`repairs.plugin`) is its harness's: Claude Code's
    update lines, one per scope that holds the install, Codex's refresh for a
    plugin Codex installed, or an update of the local directory Claude Code
    loads it from in place. The CLI's is the upgrade of the installer that owns
    the launcher. Both are named when the versions do not order plainly.

    `cli_where` is the console script the plugin will spawn, which on a machine
    with a venv crapkit and a pipx crapkit is not the module answering this
    question. The path rides this line rather than a line of its own: agreement
    is silence here, and a line printed on success is a line people stop
    reading.

    The plugin repair is the README's refresh pair. The plugin is already
    installed, so `claude plugin install` only answers that it is and leaves
    the old version where the hooks run it.
    """
    if version == cli_version:
        return None
    return (f"crapkit doctor: the plugin at {where} is version {version}, and the crapkit "
            f"its hooks spawn ({cli_where}) is {cli_version}. "
            + _repair(_behind(version, cli_version), repairs))


def _protocol_behind(odd: list[str], supported: str) -> str | None:
    """"plugin" when every protocol the hooks ask for is older than the one
    this CLI answers, "cli" when every one is newer, else None."""
    sides = {_behind(protocol, supported) for protocol in odd}
    return sides.pop() if len(sides) == 1 else None


def _protocol_gap(where: str, protocols: tuple[str, ...] | None, supported: str,
                  repairs: _Repairs) -> str | None:
    """One line when the hook asks for a protocol this CLI does not answer,
    naming the side that is behind and the commands that move it.

    A handler naming no `--protocol` at all is not a gap: argparse defaults it,
    and the default is the supported one. `None` is the other thing entirely, a
    plugin whose hooks file is missing or unreadable, and its line names how
    the file comes back.
    """
    if protocols is None:
        return (f"crapkit doctor: the plugin at {where} has no readable hooks/hooks.json; "
                f"{repairs.hooks}, and {repairs.reload} before relying on its advisory hook.")
    odd = sorted(set(protocols) - {supported})
    if not odd:
        return None
    return (f"crapkit doctor: the plugin at {where} asks for hook protocol {', '.join(odd)}; "
            f"this crapkit answers {supported}, so `claude-hook` exits 0 silent on every edit. "
            + _repair(_protocol_behind(odd, supported), repairs))


_NOT_A_ROOT = ("crapkit doctor: the plugin at {where} has no .claude-plugin/plugin.json, so it is "
               "no plugin root; name the plugin root or a directory above it, or run `crapkit "
               "doctor --plugin-root` with no PATH to check the installs Claude Code and Codex "
               "recorded.")


def _no_manifest(install: _Install, cli_version: str, on_disk: bool) -> str:
    """The line for a root with no version to read: no manifest at all, which
    is a directory that is no plugin root, or one doctor cannot read, which is
    an install to put back."""
    if not on_disk:
        return _NOT_A_ROOT.format(where=install.where)
    return (f"crapkit doctor: the plugin at {install.where} has no readable "
            ".claude-plugin/plugin.json, so it has no version to compare; "
            f"{_restore(install, cli_version, '.claude-plugin/plugin.json')}, and "
            f"{_PLUGIN_UPDATE[install.harness][2]}.")


_STALE_COPY = (
    "crapkit doctor: the plugin at {where} is version {version}, and so is the marketplace's copy "
    "at {source}, but {count} between them ({named}); `claude plugin update` keeps an install "
    "whose version did not move, so reinstall it with {reinstall}, and restart Claude Code's "
    "sessions."
)
_NAMED_FILES = 2


def _files_differ(count: int) -> str:
    return "1 file differs" if count == 1 else f"{count} files differ"


def _first_files(differing: tuple[str, ...]) -> str:
    shown, rest = ", ".join(differing[:_NAMED_FILES]), len(differing) - _NAMED_FILES
    return f"{shown} and {rest} more" if rest > 0 else shown


def stale_copy(*, where: str, version: str | None, source: str, source_version: str | None,
               differing: tuple[str, ...], scopes: tuple[InstallScope, ...]) -> str | None:
    """One line when an install and the marketplace's copy carry one version
    and different files. Main between releases keeps the release's version
    string, so `claude plugin update` answers "already at the latest version"
    and the install keeps the release's files. A different version is the
    update's business, and says nothing here. The reinstall is named once per
    scope that holds the install."""
    if not differing or version != source_version:
        return None
    return _STALE_COPY.format(where=where, version=version, source=source,
                              reinstall=_for_each_scope(_REINSTALL["claude"], scopes),
                              count=_files_differ(len(differing)), named=_first_files(differing))


def plugin_handshake(*, where: str, version: str | None, cli_version: str, cli_where: str,
                     protocols: tuple[str, ...] | None, supported: str, harness: str = "claude",
                     cli_upgrade: str = "python -m pip install --upgrade crapkit",
                     scopes: tuple[InstallScope, ...] = USER_SCOPE,
                     in_place: InPlace | None = None,
                     manifest_on_disk: bool = False) -> list[str]:
    """Every disagreement between an installed plugin and this CLI, one per
    line, each naming the command that closes it.

    Empty is the answer that matters: the two agree, and a check that prints on
    success is a check people stop reading.

    A missing manifest ends it. There is no version to compare, and a protocol
    line printed underneath would bury the one fact that explains both.
    `manifest_on_disk` tells a manifest doctor cannot read, an install to put
    back, from none at all, a directory that is no plugin root.
    """
    install = _Install(where, harness, scopes, in_place)
    if version is None:
        return [_no_manifest(install, cli_version, manifest_on_disk)]
    repairs = _Repairs(_plugin_fix(install, cli_version), _PLUGIN_UPDATE[harness][2], cli_upgrade,
                       _restore(install, cli_version, "hooks/hooks.json"))
    return [line for line in (_version_gap(where, version, cli_version, cli_where, repairs),
                              _protocol_gap(where, protocols, supported, repairs)) if line]


# --- where a check passes without judging anything ------------------------------
#
# Each finding below is a place the gate or a lane is set up and does not run,
# and nothing else says so: the coverage guard refuses only when `coverage`
# starts, git skips a hook it was sent away from, and pre-commit in CI judges
# an index nobody staged. Pure: the caller reads the environment and the files.

_CONTAINER_LANE = (
    "lane {name!r} runs a coverage.py suite and this is a container ({marker}): "
    "`crapkit coverage` refuses it with exit 5; if the container is sized for the suite, "
    "set container_ok = true on the lane (docs/lanes.md#containers)"
)


def container_marker(environ, dockerenv: bool) -> str | None:
    """What makes this machine a container for the lane runner, in the words a
    user can check, or None. lanes.py's guard reads this same function, so
    doctor cannot pass a lane `crapkit coverage` then refuses."""
    if environ.get("CRAPKIT_INSIDE_CONTAINER") == "1":
        return "CRAPKIT_INSIDE_CONTAINER=1"
    return "/.dockerenv exists" if dockerenv else None


def refused_in_container(lane) -> bool:
    """A lane the runner refuses inside a container: a coverage.py suite that
    does not say container_ok = true."""
    return lane.parser == "coveragepy" and not lane.container_ok


def container_lane_findings(lanes, marker: str | None) -> tuple[Finding, ...]:
    """The coverage.py lanes `crapkit coverage` will refuse here, one WARN each.

    A devcontainer, Codespaces, Codex cloud or a CI job in a container passed
    doctor and then refused its first coverage run; the refusal is right, and
    doctor is where a user asks whether the setup will run."""
    if marker is None:
        return ()
    return tuple(Finding("WARN", _CONTAINER_LANE.format(name=lane.name, marker=marker))
                 for lane in lanes if refused_in_container(lane))


_SENT_UNSET = "core.hooksPath is unset and git runs {effective}"
_SENT_SET = "core.hooksPath ({scope} config: {value}) sends git to {effective}"
_UNGATED = "so every commit here skips the gate without a word"
_SKIPPED_HOOK = ("{path} runs crapkit's gate, but {sent}, " + _UNGATED + "; run `{arm}` in this "
                 "repo, or call crapkit hook-precommit from {edit}")
_UNINSTALLED = (".pre-commit-config.yaml names crapkit-gate, but {sent}, which pre-commit did not "
                "write, " + _UNGATED + "; {fix}")
_INSTALL = "run `pre-commit install` in this repo"
_INSTALL_REFUSED = ("`pre-commit install` refuses while core.hooksPath is set, so call crapkit "
                    "hook-precommit from {edit}")
# What a hook that hands the commit to the pre-commit framework says: the file
# `pre-commit install` (or prek's) writes, or a hand-written `pre-commit run`.
_RUNS_FRAMEWORK = re.compile(r"hook-impl|\b(?:pre-commit|prek) run\b")


class HookRoute(NamedTuple):
    """The pre-commit file git spawns in this checkout, what it runs (husky's
    stub followed by the file it hands the commit to), the file a gate line
    belongs in, and the core.hooksPath setting that sent git there ("" and ""
    when unset)."""
    effective: str
    effective_text: str
    edit: str
    scope: str
    value: str


class GateHook(NamedTuple):
    """A pre-commit file the repo holds (its own hooks directory, or one it
    commits), what it says, and the git config line that makes git run it."""
    path: str
    text: str
    arm: str


def _runs_gate(text: str, framework: bool) -> bool:
    """Does this hook text run crapkit's gate? Directly, or through the
    pre-commit framework when the repo's config names crapkit-gate."""
    return "crapkit" in text or (framework and bool(_RUNS_FRAMEWORK.search(text)))


def _sent(route: HookRoute) -> str:
    return (_SENT_SET if route.scope else _SENT_UNSET).format(**route._asdict())


def _framework_fix(route: HookRoute) -> str:
    return _INSTALL_REFUSED.format(edit=route.edit) if route.scope else _INSTALL


def skipped_gates(route: HookRoute, hooks: tuple[GateHook, ...],
                  framework: bool) -> tuple[Finding, ...]:
    """Every gate the repo sets up that git never runs, one WARN each.

    A hook that runs crapkit and is not the file git spawns: Route 1's hook
    under a global core.hooksPath, Route 2's committed hook in a clone that
    skipped its `git config` line, either one after husky took core.hooksPath
    back. With none of those, a .pre-commit-config.yaml naming crapkit-gate
    whose framework hook git does not run: Route 3 before `pre-commit
    install`. WARN: the commit still succeeds, it is just not gated."""
    if _runs_gate(route.effective_text, framework):
        return ()
    found = _skipped_hooks(route, hooks, framework)
    if found or not framework:
        return found
    return (Finding("WARN", _UNINSTALLED.format(sent=_sent(route), fix=_framework_fix(route))),)


def _skipped_hooks(route: HookRoute, hooks: tuple[GateHook, ...],
                   framework: bool) -> tuple[Finding, ...]:
    return tuple(Finding("WARN", _SKIPPED_HOOK.format(path=hook.path, sent=_sent(route),
                                                      arm=hook.arm, edit=route.edit))
                 for hook in hooks if _runs_gate(hook.text, framework))


# --- two installs on one PATH ------------------------------------------------------
#
# The shell, a git hook, the plugin's hooks and an MCP client each start the bare
# name `crapkit` from the PATH they inherit. A user who upgraded into a new
# environment and kept the old one has two launchers, and which one a program
# runs depends on the order its PATH lists them.

_LAUNCHER_SKEW = (
    "PATH holds {count} crapkit launchers: {listed}. The shell, a git hook, the plugin's "
    "hooks and an MCP client each run the first one their own PATH lists, so they can run "
    "different versions; uninstall the copies you do not use, or upgrade them to one version"
)
_LAUNCHERS_AGREE = (
    "PATH holds {count} crapkit launchers, all {version}: {listed}; an upgrade has to reach "
    "each of them, or they drift apart"
)


def _one_version(launchers: tuple[tuple[str, str | None], ...]) -> str | None:
    """The version every launcher answered, or None when they differ or one
    answered nothing."""
    versions = {version for _, version in launchers}
    return versions.pop() if len(versions) == 1 else None


def _skew_line(launchers: tuple[tuple[str, str | None], ...]) -> Finding:
    listed = ", ".join(f"{path} ({version or 'no version answered'})" for path, version in launchers)
    return Finding("WARN", _LAUNCHER_SKEW.format(count=len(launchers), listed=listed))


def launcher_skew(launchers: tuple[tuple[str, str | None], ...]) -> tuple[Finding, ...]:
    """One finding when PATH holds more than one launcher: a WARN naming each
    with its version once they disagree or one answers none, a note while they
    agree. Disagreeing launchers are this repo's problem as much as the
    machine's: the git hook can judge a commit with one version while the shell
    records marks with another, and the plugin and MCP client run whichever
    their PATH lists first. `launchers` is (path, version) in PATH order, None
    for no answer."""
    if len(launchers) < 2:
        return ()
    version = _one_version(launchers)
    if version is None:
        return (_skew_line(launchers),)
    return (Finding("note", _LAUNCHERS_AGREE.format(
        count=len(launchers), version=version, listed=", ".join(path for path, _ in launchers))),)


# --- the marks file's merge driver ------------------------------------------------
#
# docs/ratchet.md installs the driver in two steps: a committed attribute and a
# `git config` line every clone runs, because git takes no driver command from a
# committed file. A clone that skipped the config line merges the marks file as
# text: git falls back when no driver by that name is defined, and says nothing.

# `git check-attr merge` answers these for a path no custom driver claims:
# no attribute, -merge, merge, and git's three built-in drivers.
_NO_CUSTOM_DRIVER = frozenset({"unspecified", "unset", "set", "text", "binary", "union"})
_UNDEFINED_DRIVER = (
    "{path} has merge={driver} in its git attributes, but merge.{driver}.driver is not set in "
    "this clone, so git merges the marks file as text and leaves its conflicts to be resolved "
    "by hand; run `git config merge.{driver}.driver \"crapkit ratchet merge %O %A %B\"` "
    "(docs/ratchet.md#the-git-merge-driver)"
)


def undefined_merge_driver(path: str, driver: str, command: str) -> tuple[Finding, ...]:
    """One WARN when the marks file's merge attribute names a driver this
    clone's git config does not define. `command` is that driver's
    configured command, "" when unset."""
    if driver in _NO_CUSTOM_DRIVER or command:
        return ()
    return (Finding("WARN", _UNDEFINED_DRIVER.format(path=path, driver=driver)),)


# --- the harness floor ------------------------------------------------------------
#
# Claude Code passes a hook handler's `args` from 2.1.139 on. An older release
# runs the handler's bare `command`, so each of the plugin's PostToolUse hooks
# starts `crapkit` with no subcommand: argparse exits 2 with its usage on every
# edit, and asyncRewake hands that usage to the model.

CLAUDE_CODE_ARGS_FLOOR = "2.1.139"
_LEADING_RELEASE = re.compile(r"\s*(\d+)\.(\d+)\.(\d+)")


def _release(text: str) -> tuple[int, ...] | None:
    match = _LEADING_RELEASE.match(text)
    return tuple(int(part) for part in match.groups()) if match else None


# --- the coverage.py floor ---------------------------------------------------------
#
# coverage.py writes the per-function regions crapkit scores from since 7.6. A
# lane whose interpreter carries an older one runs its suite, writes a report,
# and `crapkit coverage` refuses that report with exit 5. The lane probe starts
# that interpreter anyway, so it asks coverage's version on the same start.

_COVERAGE_FLOOR = (
    "lane {name!r} runs coverage {version} ({executable}), which writes no function "
    "regions, so `crapkit coverage` refuses its report with exit 5 (needs coverage >= {floor}); "
    "install {floor} or later there with `{upgrade}` and raise any pin that holds it lower"
)


def coverage_floor_gap(name: str, executable: str, version: str,
                       upgrade: str) -> tuple[Finding, ...]:
    """One FAIL when the lane's coverage.py predates function regions. A
    version this cannot read says nothing: the lane's own run will."""
    from .coverage_py import REGIONS_FLOOR

    found = _release(version)
    floor = tuple(int(part) for part in REGIONS_FLOOR.split("."))
    if found is None or found >= floor:
        return ()
    return (Finding("FAIL", _COVERAGE_FLOOR.format(name=name, version=version, executable=executable,
                                                   floor=REGIONS_FLOOR, upgrade=upgrade)),)


def claude_code_floor_gap(where: str, answer: str) -> str | None:
    """One line when the Claude Code at `where` answered `--version` with a
    release below the floor; None at or past it, or for an answer that names
    no version."""
    found = _release(answer)
    if found is None or found >= _release(CLAUDE_CODE_ARGS_FLOOR):
        return None
    return (f"crapkit doctor: Claude Code {'.'.join(map(str, found))} ({where}) predates "
            f"{CLAUDE_CODE_ARGS_FLOOR}, the first release that passes a hook's args, so each of "
            "the plugin's hooks starts a bare `crapkit`, which exits 2 with its usage on every "
            "edit. Update Claude Code (`claude update`), then restart its sessions.")
