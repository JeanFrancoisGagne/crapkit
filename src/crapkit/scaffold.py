"""Repo sniffing for `crapkit init`: tracked files in, a starter crapkit.toml out. Pure."""
from __future__ import annotations

import json
from typing import NamedTuple

from .universe import (LANGUAGE_EXTENSIONS, exclude_matcher, excluded, is_test_file,
                       scopes_with_tests)

_EXT_LANGUAGE = {ext: lang for lang, exts in LANGUAGE_EXTENSIONS.items() for ext in exts}

# The languages a coverage parser can read: coverage.py reads python, istanbul
# reads the JavaScript family and the .vue files a vitest run reports on. Every
# other supported language scores on complexity alone, which is what
# `coverage_optional = true` declares — so init writes the key for a scope built
# entirely from the rest, rather than leaving it to score `no-lane` forever
# against a lane nobody can write.
COVERABLE_LANGUAGES = frozenset({"python", "javascript", "typescript", "tsx", "vue"})


def cc_only_scope(languages: tuple[str, ...]) -> bool:
    """True when no coverage parser reads any of this scope's languages.

    One coverable language is enough to keep the key off: a lane can still
    measure that part, and `coverage_optional` would forgive the whole scope.
    """
    return not any(lang in COVERABLE_LANGUAGES for lang in languages)

DEFAULT_EXCLUDES = (
    # A leading `**/` matches zero or more directories (universe._glob_regex),
    # so each glob reaches the repo root and every nested copy at once. 0.4.12
    # wrote every glob twice, root form beside nested form, because fnmatch
    # demanded a directory in front of `vendor`; the prefix rule replaced that.
    "**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**",
    # Generated trees. A monorepo lead's first next-item was a generated client
    # at crap 90, and ratchet seed signed for it, because nothing here spelled
    # `generated`.
    "**/generated/**", "**/__generated__/**", "**/*.generated.*",
    "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py", "**/conftest.py",
    # Go puts its tests beside the source, so the test-directory rule never
    # fires on them and every _test.go file would score as production code
    "**/*_test.go",
    # Nothing for rust or shell on purpose. Cargo keeps integration tests in
    # `tests/`, which `_TEST_DIR` already cuts, and its unit tests live in a
    # `#[cfg(test)] mod` inside the source file, which no path glob can reach.
    # Shell has no `_test.sh` convention to claim — `deploy_test.sh` is
    # production code in plenty of repos — and the two dotted spellings a shell
    # suite does use are already covered by `**/*.test.*` and `**/*.spec.*`.
    # runner config files the docs themselves tell users to create
    "**/*.config.ts", "**/*.config.js", "**/*.config.mts",
)

_MATCH_DEFAULT_EXCLUDE = exclude_matcher(DEFAULT_EXCLUDES)


def _language_of(path: str) -> str | None:
    for ext, lang in _EXT_LANGUAGE.items():
        if path.endswith(ext):
            return lang
    return None


def _scoped_source(path: str) -> str | None:
    """The top-level dir when this is a scopeable source file, else None.
    Root-level loose files and dot-dirs make bad scopes; the excludes drop
    generated trees and tests the same way inventory later will."""
    top, _, rest = path.partition("/")
    if not rest or top.startswith(".") or excluded(path, _MATCH_DEFAULT_EXCLUDE):
        return None
    return top if _language_of(path) else None


def source_candidates(files: list[str]) -> list[str]:
    """The paths init would put in a scope. Counting them over the UNTRACKED set
    is how init tells "you are in the wrong directory" from "you never ran
    `git add`" — crapkit reads `git ls-files` and sees neither case any other way.
    """
    paths = (raw.replace("\\", "/") for raw in files)
    return [p for p in paths if _scoped_source(p)]


def sniff_scopes(files: list[str]) -> dict[str, tuple[str, ...]]:
    """Top-level source dirs -> their languages, sorted both ways for stable output."""
    langs: dict[str, set[str]] = {}
    for raw in files:
        path = raw.replace("\\", "/")
        top = _scoped_source(path)
        if top is None:
            continue
        langs.setdefault(top, set()).add(_language_of(path))
    return {d: tuple(sorted(v)) for d, v in sorted(langs.items())}


class LaneSpec(NamedTuple):
    """A lane init can write live, plus the scope languages it measures."""
    name: str
    command: str
    artifact: str
    parser: str
    languages: tuple[str, ...]
    results_artifact: str = ""
    env: tuple[tuple[str, str], ...] = ()
    cwd: str = ""  # the directory the command runs in; "" is the repo root


PYTEST_MARKERS = ("pyproject.toml", "pytest.ini", "setup.cfg")

# A lockfile is the repo saying it pins its own environment through a manager,
# and only that manager's `run` binds a command to it. A bare `python` binds to
# whatever venv the shell has active instead: the report this came from ran a
# scaffolded lane in one worktree while the shell held another worktree's venv,
# whose editable install pointed at the other checkout's sources, and pytest
# collected ten ImportErrors. Detection is the same file-presence signal that
# picks the lane itself, so nothing is executed to learn this.
#
# First match wins, so a repo carrying two lockfiles resolves the same way every
# time rather than by dict order.
LOCKFILE_RUNNERS = (("uv.lock", "uv run"), ("poetry.lock", "poetry run"),
                    ("pdm.lock", "pdm run"), ("Pipfile.lock", "pipenv run"))


def lockfile_runner(present: frozenset[str]) -> str:
    """The manager prefix that pins a python invocation to THIS project's
    environment, or "" when no lockfile in the repo names one."""
    for name, runner in LOCKFILE_RUNNERS:
        if name in present:
            return runner
    return ""


_PY_LANGUAGES = ("python",)
_JS_LANGUAGES = ("javascript", "tsx", "typescript")
_JS_RUNNER_COMMAND = {"jest": "npx jest --coverage", "vitest": "npx vitest run --coverage"}

# Every artifact a scaffolded lane writes lands under .crapkit/, which init
# ignores in the same breath. A 14-lane repo that let each runner default grew
# fifteen coverage-* directories and seven junit files at its root, one per
# lane, and nothing in the tree said which lane owned which.
_COV_DIR = ".crapkit/cov"
_PY_ARTIFACT = f"{_COV_DIR}/py.json"
# The junit file beside it feeds two of verify's checks, the crashed-worker
# trust check and no-new-failures; a lane without one runs with both off (#26).
_PY_RESULTS = f"{_COV_DIR}/junit-py.xml"
_JS_COV_DIR = f"{_COV_DIR}/js"
_JS_ARTIFACT = f"{_JS_COV_DIR}/coverage-final.json"
_JS_RESULTS = f"{_JS_COV_DIR}/junit.xml"
_JS_DEFAULT_ARTIFACT = "coverage/coverage-final.json"

# Each runner spells "write the coverage report here" its own way and rejects
# the other's spelling outright.
_JS_REPORTS_DIR_FLAG = {"jest": "--coverageDirectory=",
                        "vitest": "--coverage.reportsDirectory="}

# The junit half. vitest ships its junit reporter, so the flags cost the repo
# nothing; jest needs the separate `jest-junit` package, and naming a reporter
# jest cannot resolve turns a working lane into an error — so its flags are
# written only when package.json already carries it.
_JS_JUNIT_FLAGS = {"jest": "--reporters=default --reporters=jest-junit",
                   "vitest": "--reporter=default --reporter=junit "
                             "--outputFile={cov}/junit.xml"}

# vitest writes no coverage report when a test fails, so one red test turned
# `crapkit coverage` into exit 5 naming a missing coverage-final.json — a
# message about a file, for a run that was really about a flag. Per runner, not
# shared: jest reports on a red run already and exits on a flag it does not
# know, which is the failure `_js_runner` exists to prevent.
_JS_EXTRA_FLAGS = {"vitest": " --coverage.reportOnFailure"}

# pytest's half of the same rule. pytest raises Interrupted at the END of
# collection when any module fails to import, so pytest-cov's session finish
# never runs and the lane writes no coverage JSON at all: one renamed module or
# one missing optional extra takes the whole lane down and every scope falls to
# no-lane, while the junit still lands and makes the run look half finished.
# With the flag the modules that did collect run and report, and the
# uncollected file's tests stay in the junit as errors, so nothing is hidden.
_PY_COLLECT_FLAG = "--continue-on-collection-errors"
_JS_JUNIT_PACKAGE = {"jest": "jest-junit"}
# jest-junit takes no path on the command line: package.json, the jest config or
# these two variables are the whole list, and the first two are the repo's files
# to own. Without them it drops junit.xml at the repo root, which is the litter
# `artifact` was routed away from in the first place.
_JS_JUNIT_ENV = {"jest": (("JEST_JUNIT_OUTPUT_DIR", "{cov}"),
                          ("JEST_JUNIT_OUTPUT_NAME", "junit.xml"))}


def _pytest_lane(markers: frozenset[str], interpreter: str) -> LaneSpec | None:
    if not markers.intersection(PYTEST_MARKERS):
        return None
    return LaneSpec("py", f"{interpreter} -m pytest --cov --cov-branch "
                          f"--cov-report=json:{_PY_ARTIFACT} --junitxml={_PY_RESULTS} "
                          f"{_PY_COLLECT_FLAG}",
                    _PY_ARTIFACT, "coveragepy", _PY_LANGUAGES, _PY_RESULTS)


# Where pytest keeps `testpaths`, and the order it reads those files in: the
# first file carrying a pytest section ends the search, whether or not that
# section names testpaths, because pytest picks one inifile and never consults a
# lower-ranked one. A repo holding all three is read the way pytest reads it.
# This is the same presence-and-config signal that picks the lane itself — the
# file is parsed, never executed and never imported.
_TESTPATHS_SECTION = {"pytest.ini": "pytest", "setup.cfg": "tool:pytest"}
_TESTPATHS_ORDER = ("pytest.ini", "pyproject.toml", "setup.cfg")


def _ini_testpaths(text: str, section: str) -> tuple[str, ...] | None:
    """`testpaths` out of an ini file, or None when the file carries no pytest
    section for the search to stop at. A file that will not parse carries
    nothing: init is describing the repo, not judging its pytest config."""
    import configparser

    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_section(section):
        return None
    return tuple(parser.get(section, "testpaths", fallback="").split())


def _toml_value(data, *keys: str):
    """One key path through nested tables, or None the moment it leaves them."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _toml_testpaths(text: str) -> tuple[str, ...] | None:
    """None unless `[tool.pytest.ini_options]` is there, which is the table
    pytest itself looks for before it reads a pyproject as its inifile."""
    import tomllib

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    section = _toml_value(data, "tool", "pytest", "ini_options")
    if not isinstance(section, dict):
        return None
    found = section.get("testpaths")
    return tuple(str(path) for path in found) if isinstance(found, list) else ()


def _testpaths_of(name: str, text: str) -> tuple[str, ...] | None:
    if name == "pyproject.toml":
        return _toml_testpaths(text)
    return _ini_testpaths(text, _TESTPATHS_SECTION[name])


def pytest_testpaths(marker_texts: dict[str, str]) -> tuple[str, ...]:
    """The paths a bare `pytest` in this repo collects, in the order declared.

    More than one of them is the shape that can defeat the full-suite lane: a
    conftest two testpaths both import registers twice under
    `--import-mode=importlib` and the whole run dies during collection, with no
    full-suite command left to write. init cannot know whether that happens
    without running the suite, so it reads the count and writes the fallback
    commented out.

    The first file carrying a pytest section answers, empty section included:
    that is the file pytest reads, and a `[pytest]` naming no testpaths means a
    bare `pytest` collects from the rootdir. Falling through to the next file
    would name paths the repo's own run never collects.
    """
    for name in _TESTPATHS_ORDER:
        found = _testpaths_of(name, marker_texts.get(name, ""))
        if found is not None:
            return found
    return ()


def _npm_test_script(scripts: dict) -> str | None:
    """The script name npm would run tests with: "test" when it exists, else the
    alphabetically first name starting with "test", so the choice never moves."""
    if "test" in scripts:
        return "test"
    named = sorted(name for name in scripts if name.startswith("test"))
    return named[0] if named else None


def _js_runner_command(dev_dependencies: dict) -> str | None:
    for runner in sorted(_JS_RUNNER_COMMAND):
        if runner in dev_dependencies:
            return _JS_RUNNER_COMMAND[runner]
    return None


def _load_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _js_runner(dev_dependencies: dict) -> str | None:
    """The runner whose flags this lane may carry, or None when package.json
    names neither runner or both.

    `npm test` can run anything, so devDependencies is the only thing that names
    the runner — and naming it wrong is worse than the litter: jest exits on
    vitest's `--coverage.reportsDirectory` and vitest exits on jest's
    `--coverageDirectory`. Unresolved, the lane keeps the directory its runner
    already defaults to and doctor says so.
    """
    named = [runner for runner in sorted(_JS_REPORTS_DIR_FLAG) if runner in dev_dependencies]
    return named[0] if len(named) == 1 else None


def _js_junit(runner: str, dev_dependencies: dict, cov_dir: str) -> tuple[str, str, tuple]:
    """Flags, results_artifact and env for this runner's junit report, or three
    empty values when the reporter it needs is not installed.

    `cov_dir` is the report directory as the lane's own cwd spells it, so a
    workspace lane's junit flags climb back to the root exactly the way its
    coverage flag does. `results_artifact` stays root-relative either way:
    crapkit reads it from the root, never from the lane's cwd.
    """
    package = _JS_JUNIT_PACKAGE.get(runner)
    if package is not None and package not in dev_dependencies:
        return "", "", ()
    env = tuple((key, value.format(cov=cov_dir))
                for key, value in _JS_JUNIT_ENV.get(runner, ()))
    return f" {_JS_JUNIT_FLAGS[runner].format(cov=cov_dir)}", _JS_RESULTS, env


def _npm_test_command(scripts: dict) -> str | None:
    """The `npm run` line for this package's test script, or None when it
    declares none and the runner has to be called directly."""
    script = _npm_test_script(scripts)
    return f"npm run {script} -- --coverage" if script else None


def _js_command(package: dict) -> str | None:
    """What npm would run tests with, or the runner's own command when the
    package declares no test script at all."""
    return (_npm_test_command(package.get("scripts", {}))
            or _js_runner_command(package.get("devDependencies", {})))


def _js_routed_lane(package: dict, command: str, runner: str, cwd: str) -> LaneSpec:
    """The lane with both of its reports routed under .crapkit/, run from `cwd`.

    Every path in the command is written from `cwd`, so a lane running in a
    workspace climbs one `../` per level to reach the root that holds
    `.crapkit/`. `artifact` does not: crapkit resolves that against the root.
    """
    up = "../" * (cwd.count("/") + 1) if cwd else ""
    cov_dir = up + _JS_COV_DIR
    junit, results, env = _js_junit(runner, package.get("devDependencies", {}), cov_dir)
    routing = f" {_JS_REPORTS_DIR_FLAG[runner]}{cov_dir}"
    return LaneSpec("js", command + routing + _JS_EXTRA_FLAGS.get(runner, "") + junit,
                    _JS_ARTIFACT, "istanbul", _JS_LANGUAGES, results, env, cwd)


def _js_root_lane(package: dict) -> LaneSpec | None:
    """A test script, or vitest/jest in devDependencies: either says the repo
    already knows how to produce istanbul coverage.

    A named runner gets both of its output files routed under .crapkit/: the
    coverage report, and the junit report the crashed-worker and no-new-failures
    checks read. Init writing the lane without the second one left doctor
    WARNing about the config init had just written.
    """
    command = _js_command(package)
    if command is None:
        return None
    runner = _js_runner(package.get("devDependencies", {}))
    if runner is None:
        return LaneSpec("js", command, _JS_DEFAULT_ARTIFACT, "istanbul", _JS_LANGUAGES)
    return _js_routed_lane(package, command, runner, "")


def _runner_workspaces(packages: dict[str, str]) -> list[tuple[str, str]]:
    """The workspace directories whose own devDependencies name one runner,
    each paired with the runner it named, so no caller asks twice."""
    named = ((directory, _js_runner(_load_json(text).get("devDependencies", {})))
             for directory, text in packages.items() if directory)
    return sorted((directory, runner) for directory, runner in named if runner)


def _js_workspace_lane(packages: dict[str, str]) -> LaneSpec | None:
    """The lane for the one workspace that owns a runner, or None when the
    workspaces name none or several.

    A monorepo's root test script only chains the workspaces' own, so the root
    package.json names no runner and the lane init built from it could not
    produce a coverage artifact at all: unrouted command, no results_artifact,
    two doctor WARNs on a config init had just written. The workspace that ships
    the runner is the one that can produce it, so the lane runs there.

    Several workspaces naming a runner is the case file presence cannot decide,
    and it falls back to the root lane rather than picking one.
    """
    named = _runner_workspaces(packages)
    if len(named) != 1:
        return None
    directory, runner = named[0]
    package = _load_json(packages[directory])
    command = _npm_test_command(package.get("scripts", {})) or _JS_RUNNER_COMMAND[runner]
    return _js_routed_lane(package, command, runner, directory)


def _packages(package_json: str | dict[str, str]) -> dict[str, str]:
    """package.json texts keyed by the directory holding them, "" for the root.

    A bare string is the root's text and nothing else, which is what init read
    before workspaces got their turn and what a caller passing one still means.
    """
    return {"": package_json} if isinstance(package_json, str) else package_json


def _js_lane(package_json: str | dict[str, str]) -> LaneSpec | None:
    """The js lane, from the root package.json and the workspaces beside it.

    The root wins when it names a runner itself. Only a root that names none
    hands the question to the workspaces, so a repo that already worked keeps
    the lane it always got.
    """
    packages = _packages(package_json)
    root = _load_json(packages.get("", ""))
    if _js_runner(root.get("devDependencies", {})) is None:
        workspace = _js_workspace_lane(packages)
        if workspace is not None:
            return workspace
    return _js_root_lane(root)


def detect_lanes(markers: frozenset[str], package_json: str | dict[str, str], *,
                 interpreter: str = "python") -> tuple[LaneSpec, ...]:
    """The lanes this repo can already run, decided from files alone.

    Nothing is executed and nothing is imported: presence of a pytest marker
    file, and what the package.json files say about themselves, are the whole
    signal.
    """
    found = (_pytest_lane(markers, interpreter), _js_lane(package_json))
    return tuple(lane for lane in found if lane)


def _quoted(names) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _scope_stanza(name: str, languages: tuple[str, ...]) -> list[str]:
    optional = ["coverage_optional = true"] if cc_only_scope(languages) else []
    return ["[[scope]]", f'name = "{name}"', f'paths = ["{name}"]',
            f"languages = [{_quoted(languages)}]", *optional, ""]


# What a reader editing the list needs to know before they add to it: why one
# form per pattern is enough, and why no glob for tests/ appears below.
_EXCLUDE_INTRO = (
    "# A leading **/ matches zero or more directories, so each glob below reaches the",
    "# repo root and every nested copy. Test directories leave the corpus on their own.",
)


def _exclude_stanza() -> list[str]:
    """One glob per line. The 0.4.x form was a 405-character line carrying every
    pattern twice, which nobody could read, let alone edit."""
    globs = [f'  "{glob}",' for glob in DEFAULT_EXCLUDES]
    return ["[exclude]", *_EXCLUDE_INTRO, "globs = [", *globs, "]", ""]


def _lane_env(env: tuple[tuple[str, str], ...]) -> list[str]:
    """The inline table a runner that takes its output path from the environment
    needs, and nothing at all for one that takes a flag."""
    pairs = ", ".join(f'{key} = "{value}"' for key, value in env)
    return [f"env = {{ {pairs} }}"] if pairs else []


def _lane_stanza(lane: LaneSpec, scope_names: tuple[str, ...]) -> list[str]:
    results = [f'results_artifact = "{lane.results_artifact}"'] if lane.results_artifact else []
    cwd = [f'cwd = "{lane.cwd}"'] if lane.cwd else []
    return ["[[lane]]", f'name = "{lane.name}"', f'command = "{lane.command}"',
            f'artifact = "{lane.artifact}"', *results, f'parser = "{lane.parser}"',
            *cwd, *_lane_env(lane.env), f"scopes = [{_quoted(scope_names)}]", ""]


# What a reader who hits the collection error needs in front of them: the shape
# of the fix, in this repo's own paths, rather than a page to go find.
_TESTPATH_STUB_INTRO = (
    "# This repo's pytest config names several testpaths. If they cannot be collected",
    "# in one process (a conftest two of them import registers twice under",
    "# --import-mode=importlib, and the run dies before a test runs), the lane above",
    "# has no command to write. Replace it with these, one lane per testpath: each",
    "# declares full_suite = false and its own artifact, so every testpath stays",
    "# measured and each lane fails on its own.",
)


def _testpath_slug(testpath: str) -> str:
    """A lane name out of a testpath. It also names the lane's artifact and log
    file, so anything a path may hold and a filename may not becomes a dash."""
    stripped = testpath.replace("\\", "/").strip("./")
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stripped)


def _testpath_lane(lane: LaneSpec, testpath: str) -> LaneSpec:
    """The detected pytest lane narrowed to one testpath, carrying its own
    artifact pair: two lanes may not declare the same artifact path."""
    name = f"{lane.name}-{_testpath_slug(testpath)}"
    artifact = f"{_COV_DIR}/{name}.json"
    results = f"{_COV_DIR}/junit-{name}.xml"
    command = (lane.command.replace(_PYTEST_INVOCATION, f"{_PYTEST_INVOCATION}{testpath} ")
               .replace(lane.artifact, artifact).replace(lane.results_artifact, results))
    return lane._replace(name=name, command=command, artifact=artifact,
                         results_artifact=results)


def _commented_lane(lane: LaneSpec, scope_names: tuple[str, ...]) -> list[str]:
    """One lane stanza, commented out. `full_suite = false` is not decoration:
    the command carries a positional, which is exactly what the full-suite guard
    refuses, so a stub without it would not load when uncommented."""
    body = [line for line in _lane_stanza(lane, scope_names) if line]
    return [f"# {line}" for line in body + ["full_suite = false"]] + [""]


def _detected_pytest_lane(lanes: tuple[LaneSpec, ...]) -> LaneSpec | None:
    for lane in lanes:
        if _PYTEST_INVOCATION in lane.command:
            return lane
    return None


def _testpath_lane_stubs(lanes: tuple[LaneSpec, ...], scopes: dict[str, tuple[str, ...]],
                         testpaths: tuple[str, ...]) -> list[str]:
    """The one-lane-per-testpath fallback, commented out, or nothing.

    Nothing for a single testpath, which collects in one process by definition,
    and nothing when no detected pytest lane measures a scope: there is no lane
    to split and the stubs would name paths nothing in this config covers.
    """
    lane = _detected_pytest_lane(lanes)
    scope_names = _scopes_for(lane, scopes) if lane else ()
    if not scope_names or len(testpaths) < 2:
        return []
    stubs = [line for path in testpaths
             for line in _commented_lane(_testpath_lane(lane, path), scope_names)]
    return [*_TESTPATH_STUB_INTRO, *stubs]


def _scopes_for(lane: LaneSpec, scopes: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(name for name, languages in scopes.items()
                 if set(languages).intersection(lane.languages))


def live_lanes(lanes: tuple[LaneSpec, ...],
               scopes: dict[str, tuple[str, ...]]) -> tuple[LaneSpec, ...]:
    """The detected lanes init actually writes: the ones with a scope to measure.

    A lane with an empty scopes list measures nothing, so it goes back to being a
    template rather than papering over the gap — and it leaves no artifact behind
    either, which is what the .gitignore side of init needs to know.
    """
    return tuple(lane for lane in lanes if _scopes_for(lane, scopes))


def _live_lanes(lanes: tuple[LaneSpec, ...],
                scopes: dict[str, tuple[str, ...]]) -> tuple[list[str], set[str]]:
    """Stanzas for the lanes init writes, and the parsers they cover."""
    lines: list[str] = []
    covered = set()
    for lane in live_lanes(lanes, scopes):
        lines += _lane_stanza(lane, _scopes_for(lane, scopes))
        covered.add(lane.parser)
    return lines, covered


# The python in the coveragepy template is a placeholder for the same reason
# the scoped-tests entries carry one: what a reader uncomments has to be the
# command init would have written live. A repo whose lockfile pins `uv run`
# read a bare `python` here and got the environment bug one uncomment later.
_TEMPLATES = {
    "coveragepy": ("# [[lane]]", '# name = "py"',
                   '# command = "{python} -m pytest --cov --cov-branch '
                   f'--cov-report=json:{_PY_ARTIFACT} --junitxml={_PY_RESULTS} '
                   f'{_PY_COLLECT_FLAG}"',
                   f'# artifact = "{_PY_ARTIFACT}"',
                   f'# results_artifact = "{_PY_RESULTS}"', '# parser = "coveragepy"'),
    "istanbul": ("# [[lane]]", '# name = "js"',
                 '# command = "npx vitest run --coverage '
                 f'{_JS_REPORTS_DIR_FLAG["vitest"]}{_JS_COV_DIR}'
                 f'{_JS_EXTRA_FLAGS["vitest"]} '
                 f'{_JS_JUNIT_FLAGS["vitest"].format(cov=_JS_COV_DIR)}"',
                 f'# artifact = "{_JS_ARTIFACT}"',
                 f'# results_artifact = "{_JS_RESULTS}"', '# parser = "istanbul"'),
}


# `crapkit test-scoped FILES` runs one of these per scope. A scope with no
# template exits 3, so an entry is written for every scope init found: live
# where the repo's own files prove the command, commented where only the repo
# knows. The form follows where the tests live and which runner package.json
# names, never the language alone. The 0.4.x `{files}` template handed pytest a
# source file to collect from on the ordinary pkg/ + tests/ layout and exited
# 5, and a jest workspace was handed a vitest command.
_PYTEST_FILES = "{python} -m pytest {files} -q -p no:cacheprovider"
_PYTEST_SUITE = "{python} -m pytest {positional}-q -p no:cacheprovider"
_JS_RELATED = {"jest": "npx jest --findRelatedTests {files}",
               "vitest": "npx vitest related --run {files}"}
_NPM_WORKSPACE = "npm run {script} -w {directory}"
_SCOPED_TEST_PLACEHOLDER = "<your test command> {files}"
_DEFAULT_PYTHON = "python"


class ScopedEntry(NamedTuple):
    """One [crapkit.scoped_tests] line, the comment line that goes above it,
    and whether init writes it live."""
    command: str
    why: str
    live: bool


class _ScopedFacts(NamedTuple):
    """What the repo's files say about its tests, read once for every scope."""
    launcher: str
    confirmed: frozenset[str]  # languages whose runner a detected lane proves
    tested: frozenset[str]     # scopes whose own paths hold a test file
    test_dir: str              # the one test directory outside every scope, or ""
    covered: bool              # does pytest's testpaths already collect test_dir?
    packages: dict[str, str]   # package.json texts by directory, "" for the root


# The shape that marks a detected lane as the pytest lane, read in one place:
# the launcher and the confirmed-runner check spelled it two ways once, so a
# command ending at `-m pytest` was the pytest lane to one and not the other,
# and init wrote a commented template carrying a `uv run python` the same file
# had just refused to confirm.
_PYTEST_INVOCATION = " -m pytest "


def _pytest_lane_launcher(lanes: tuple[LaneSpec, ...]) -> str | None:
    """The python the lane that runs pytest runs it with, or None when no
    detected lane runs pytest at all."""
    for lane in lanes:
        head, sep, _ = lane.command.partition(_PYTEST_INVOCATION)
        if sep:
            return head
    return None


def python_launcher(lanes: tuple[LaneSpec, ...], fallback: str = _DEFAULT_PYTHON) -> str:
    """The python invocation the detected pytest lane already settled on, and
    `fallback` when no detected lane runs pytest.

    Read back off the lane rather than passed in beside it, so the scoped-tests
    entry runs the suite the way the coverage lane runs it and the two cannot
    drift: a `uv run python` lane with a bare `python` step 4 would measure one
    environment and test another.

    A repo with no pytest marker file has no lane to read, and every python
    line init writes for it is commented. `fallback` is what init would have
    run had there been one, so those lines uncomment into the same environment.
    """
    launcher = _pytest_lane_launcher(lanes)
    return fallback if launcher is None else launcher


def _confirmed_languages(lanes: tuple[LaneSpec, ...]) -> frozenset[str]:
    """Languages whose scoped command a detected lane already proves.

    Only pytest: the presence signal that wrote the py coverage lane makes
    `python -m pytest` known-good. The js runners stay unconfirmed on purpose,
    because which vitest or jest config a file-scoped run needs is exactly
    what presence detection cannot see; a workspace's own test script is the
    one js form the repo itself vouches for, and `_js_entry` writes it live."""
    return frozenset({"python"} if _pytest_lane_launcher(lanes) is not None else ())


def _test_top(raw: str) -> str:
    """The top-level directory of a tracked test file, "" for anything else."""
    path = raw.replace("\\", "/")
    top, sep, _ = path.partition("/")
    return top if sep and is_test_file(path) else ""


def _repo_test_dir(tracked, scopes: dict[str, tuple[str, ...]]) -> str:
    """The one top-level directory outside every scope that holds test files,
    or "" when there is none or more than one to name. Naming one of two would
    run half the suite and call it whole."""
    tops = {top for top in map(_test_top, tracked) if top and top not in scopes}
    return next(iter(tops)) if len(tops) == 1 else ""


def _covered_by_testpaths(test_dir: str, testpaths: tuple[str, ...]) -> bool:
    """Does a bare `pytest` already collect test_dir, because testpaths names
    it or a directory under it? Then a positional would only repeat the config,
    and the full-suite guard reads a repeated positional as narrowing."""
    cleaned = [path.replace("\\", "/").strip("./") for path in testpaths]
    return bool(test_dir) and any(p == test_dir or p.startswith(test_dir + "/") for p in cleaned)


def _suite_why(name: str, facts: _ScopedFacts) -> str:
    where = f"no test file under {name}/, so the whole suite runs"
    if not facts.test_dir:
        return where
    if facts.covered:
        return f"{where}; pytest's testpaths already collects {facts.test_dir}/"
    return f"{where}, from {facts.test_dir}/"


def _python_entry(name: str, facts: _ScopedFacts) -> ScopedEntry:
    """`{files}` where the scope holds its own tests; the whole-suite form
    everywhere else, naming the repo's test directory unless testpaths already
    collects it. `_scoped_command` runs a template with no {files} verbatim."""
    live = "python" in facts.confirmed
    if name in facts.tested:
        return ScopedEntry(_PYTEST_FILES.replace("{python}", facts.launcher),
                           f"{name}/ holds its own tests, so {{files}} narrows the run "
                           "to the files named", live)
    positional = f"{facts.test_dir} " if facts.test_dir and not facts.covered else ""
    command = _PYTEST_SUITE.replace("{python}", facts.launcher).replace("{positional}", positional)
    return ScopedEntry(command, _suite_why(name, facts), live)


def _workspace_script(packages: dict[str, str], directory: str) -> str | None:
    return _npm_test_script(_load_json(packages.get(directory, "")).get("scripts", {}))


def _js_entry(name: str, facts: _ScopedFacts) -> ScopedEntry:
    """A workspace runs its own test script from the root; a root scope gets
    the related-tests mode of the runner package.json names; nothing named
    gets the placeholder. Keyed by runner, never by language: jest exits on
    vitest's flags and vitest on jest's."""
    script = _workspace_script(facts.packages, name)
    if script:
        return ScopedEntry(_NPM_WORKSPACE.format(script=script, directory=name),
                           f"{name}/ is an npm workspace with its own {script} script, "
                           "run from the root with -w", True)
    runner = _js_runner(_load_json(facts.packages.get("", "")).get("devDependencies", {}))
    if runner:
        return ScopedEntry(_JS_RELATED[runner], f"{runner}'s related-tests mode, keyed by "
                           "the runner package.json names", False)
    return ScopedEntry(_SCOPED_TEST_PLACEHOLDER,
                       "package.json names no single runner; replace the placeholder", False)


def _scoped_entry(name: str, languages: tuple[str, ...], facts: _ScopedFacts) -> ScopedEntry:
    """The entry for one scope, from its languages and the repo's facts."""
    if "python" in languages:
        return _python_entry(name, facts)
    if any(lang in _JS_LANGUAGES for lang in languages):
        return _js_entry(name, facts)
    return ScopedEntry(_SCOPED_TEST_PLACEHOLDER,
                       f"no runner known for {', '.join(languages)}; replace the placeholder",
                       False)


def _scoped_lines(scopes: dict[str, tuple[str, ...]],
                  facts: _ScopedFacts) -> tuple[list[str], list[str]]:
    """Live entry lines and commented entry lines, each under the one comment
    line that names the form chosen and why."""
    live: list[str] = []
    rest: list[str] = []
    for name, languages in scopes.items():
        entry = _scoped_entry(name, languages, facts)
        target = live if entry.live else rest
        prefix = "" if entry.live else "# "
        target += [f"# {name}: {entry.why}", f'{prefix}{name} = "{entry.command}"']
    return live, rest


def _scoped_facts(scopes: dict[str, tuple[str, ...]], lanes: tuple[LaneSpec, ...],
                  launcher: str, testpaths: tuple[str, ...], tracked,
                  package_json: str | dict[str, str]) -> _ScopedFacts:
    test_dir = _repo_test_dir(tracked, scopes)
    return _ScopedFacts(launcher, _confirmed_languages(lanes),
                        scopes_with_tests(tracked, {name: (name,) for name in scopes}),
                        test_dir, _covered_by_testpaths(test_dir, testpaths),
                        _packages(package_json))


def _scoped_tests_stub(scopes: dict[str, tuple[str, ...]], facts: _ScopedFacts) -> list[str]:
    """The [crapkit.scoped_tests] block: live entries where the repo's own files
    prove the command, commented templates for the rest.

    A confirmed runner written commented would hand doctor a warning about a
    gap init could have closed. Every commented line still uncomments as
    written: a stub a reader has to rewrite before it parses is no better than
    the nothing that used to be here.
    """
    live, rest = _scoped_lines(scopes, facts)
    intro = ["# `crapkit test-scoped FILES` runs one command per scope, with {files}",
             "# replaced by that scope's files, each quoted; a template with no {files}",
             "# runs as written, which is how a scope whose tests live elsewhere runs them."]
    return intro + _live_block(live) + _commented_block(rest, bool(live)) + [""]


def _live_block(live: list[str]) -> list[str]:
    return ["[crapkit.scoped_tests]"] + live if live else []


def _commented_block(rest: list[str], has_live: bool) -> list[str]:
    if not rest:
        return []
    header = ["# Uncomment what fits:"] + ([] if has_live else ["# [crapkit.scoped_tests]"])
    return header + rest


def runner_workspaces(package_json: str | dict[str, str]) -> list[tuple[str, str]]:
    """Every workspace directory whose package.json names one runner, paired
    with that runner, for init's summary to name when it could not pick a lane
    among them."""
    return _runner_workspaces(_packages(package_json))


def _template_stanza(parser: str, launcher: str) -> list[str]:
    """One commented lane template, with the python it names filled in. The js
    templates carry no placeholder, so they come back as written."""
    return [line.replace("{python}", launcher) for line in _TEMPLATES[parser]]


def _template_lines(covered: set[str], scopes: dict[str, tuple[str, ...]],
                    launcher: str = _DEFAULT_PYTHON) -> list[str]:
    """Commented lane templates for the parsers no live lane covers.

    None at all when every scope is cc-only. Neither parser reads any language
    in the repo, so both templates would be a step the reader cannot take, under
    a heading telling them to take it before running `crapkit coverage`.
    """
    if all(cc_only_scope(languages) for languages in scopes.values()):
        return []
    # the template's scope is a placeholder on purpose: writing a real scope
    # name pointed a TS lane template at a python project's sources
    lines: list[str] = []
    for parser in sorted(_TEMPLATES):
        if parser not in covered:
            lines += [*_template_stanza(parser, launcher), '# scopes = ["<your-scope>"]', ""]
    if not lines:
        return []
    return ["# Declare one [[lane]] per coverage command, then run `crapkit coverage`.", *lines]


def starter_toml(scopes: dict[str, tuple[str, ...]], lanes: tuple[LaneSpec, ...] = (),
                 *, interpreter: str = _DEFAULT_PYTHON,
                 testpaths: tuple[str, ...] = (), tracked=(),
                 package_json: str | dict[str, str] = "") -> str:
    """The starter crapkit.toml. `interpreter` is the python a committed config
    on this repo can call, the lockfile's manager prefix included; every python
    line the file holds names it, commented templates as much as live lanes.

    `testpaths` is what the repo's pytest config collects. More than one of them
    and the file also carries the fallback for a suite that cannot collect them
    together, commented out: one lane per testpath at `full_suite = false`.

    `tracked` is the repo's tracked file list and `package_json` the texts
    `detect_lanes` read; together they decide which scoped-test form each scope
    gets. A caller passing neither gets the whole-suite form for python and
    the placeholder for js, the two forms that cannot collect nothing.
    """
    lines = ["[crapkit]", "target = 6", ""]
    for name, languages in scopes.items():
        lines += _scope_stanza(name, languages)
    lines += _exclude_stanza()
    live, covered = _live_lanes(lanes, scopes)
    live += _testpath_lane_stubs(lanes, scopes, testpaths)
    launcher = python_launcher(lanes, interpreter)
    facts = _scoped_facts(scopes, lanes, launcher, testpaths, tracked, package_json)
    return "\n".join(lines + live + _template_lines(covered, scopes, launcher)
                     + _scoped_tests_stub(scopes, facts))


_STORE_IGNORE = ".crapkit/"


def _artifact_ignore(artifact: str) -> str:
    """What to ignore for one lane's artifact. A file inside a directory ignores
    the DIRECTORY: istanbul writes a whole coverage/ tree beside
    coverage-final.json, and ignoring the one file leaves the rest untracked."""
    top, sep, _ = artifact.partition("/")
    return f"{top}/" if sep else top


def _runner_droppings(lane: LaneSpec) -> list[str]:
    """What the lane's runner leaves beside the artifact; a pytest lane drops
    coverage's data file and bytecode caches into the consumer's tree."""
    return [".coverage", "__pycache__/"] if lane.parser == "coveragepy" else []


def gitignore_entries(lanes: tuple[LaneSpec, ...]) -> list[str]:
    """Everything adopting crapkit will drop in the consumer's tree: its own
    store, the artifact of each lane init wrote, and the runner's droppings.
    Order is stable and duplicates collapse, so two lanes sharing a directory
    ignore it once."""
    entries = [_STORE_IGNORE, *(entry for lane in lanes
                                for entry in (_artifact_ignore(lane.artifact),
                                              *_runner_droppings(lane)))]
    return list(dict.fromkeys(entries))


def _appended(current: str, entries: list[str]) -> str:
    """The new entries under their own heading, after whatever was already there."""
    block = "# crapkit\n" + "".join(f"{entry}\n" for entry in entries)
    if not current:
        return block
    separator = "\n" if current.endswith("\n") else "\n\n"
    return current + separator + block


def gitignore_update(current: str, lanes: tuple[LaneSpec, ...]) -> tuple[str, list[str]]:
    """The .gitignore this repo needs, and the entries it gained. Pure.

    Idempotent: an entry the file already carries is never written twice, so a
    repo that adopted crapkit by hand gets no duplicate lines.
    """
    present = {line.strip() for line in current.splitlines()}
    added = [entry for entry in gitignore_entries(lanes) if entry not in present]
    if not added:
        return current, []
    return _appended(current, added), added
