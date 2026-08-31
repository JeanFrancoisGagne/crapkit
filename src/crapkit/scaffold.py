"""Repo sniffing for `crapkit init`: tracked files in, a starter crapkit.toml out. Pure."""
from __future__ import annotations

import json
from typing import NamedTuple

from .universe import LANGUAGE_EXTENSIONS, exclude_matcher, excluded

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
    "**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**",
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
    # runner config files the docs themselves tell users to create; globs are
    # whole-path, so the root form and the nested form are both required
    "*.config.ts", "*.config.js", "*.config.mts",
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
                   "vitest": f"--reporter=default --reporter=junit --outputFile={_JS_RESULTS}"}
_JS_JUNIT_PACKAGE = {"jest": "jest-junit"}
# jest-junit takes no path on the command line: package.json, the jest config or
# these two variables are the whole list, and the first two are the repo's files
# to own. Without them it drops junit.xml at the repo root, which is the litter
# `artifact` was routed away from in the first place.
_JS_JUNIT_ENV = {"jest": (("JEST_JUNIT_OUTPUT_DIR", _JS_COV_DIR),
                          ("JEST_JUNIT_OUTPUT_NAME", "junit.xml"))}


def _pytest_lane(markers: frozenset[str], interpreter: str) -> LaneSpec | None:
    if not markers.intersection(PYTEST_MARKERS):
        return None
    return LaneSpec("py", f"{interpreter} -m pytest --cov --cov-branch "
                          f"--cov-report=json:{_PY_ARTIFACT} --junitxml={_PY_RESULTS}",
                    _PY_ARTIFACT, "coveragepy", _PY_LANGUAGES, _PY_RESULTS)


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


def _js_junit(runner: str, dev_dependencies: dict) -> tuple[str, str, tuple]:
    """Flags, results_artifact and env for this runner's junit report, or three
    empty values when the reporter it needs is not installed."""
    package = _JS_JUNIT_PACKAGE.get(runner)
    if package is not None and package not in dev_dependencies:
        return "", "", ()
    return f" {_JS_JUNIT_FLAGS[runner]}", _JS_RESULTS, _JS_JUNIT_ENV.get(runner, ())


def _js_lane(package_json: str) -> LaneSpec | None:
    """A test script, or vitest/jest in devDependencies: either says the repo
    already knows how to produce istanbul coverage.

    A named runner gets both of its output files routed under .crapkit/: the
    coverage report, and the junit report the crashed-worker and no-new-failures
    checks read. Init writing the lane without the second one left doctor
    WARNing about the config init had just written.
    """
    package = _load_json(package_json)
    dev = package.get("devDependencies", {})
    script = _npm_test_script(package.get("scripts", {}))
    command = (f"npm run {script} -- --coverage" if script else _js_runner_command(dev))
    runner = _js_runner(dev)
    if command is None:
        return None
    if runner is None:
        return LaneSpec("js", command, _JS_DEFAULT_ARTIFACT, "istanbul", _JS_LANGUAGES)
    junit, results, env = _js_junit(runner, dev)
    routing = f" {_JS_REPORTS_DIR_FLAG[runner]}{_JS_COV_DIR}"
    return LaneSpec("js", command + routing + junit, _JS_ARTIFACT, "istanbul",
                    _JS_LANGUAGES, results, env)


def detect_lanes(markers: frozenset[str], package_json: str, *,
                 interpreter: str = "python") -> tuple[LaneSpec, ...]:
    """The lanes this repo can already run, decided from files alone.

    Nothing is executed and nothing is imported: presence of a pytest marker
    file, and what package.json says about itself, are the whole signal.
    """
    found = (_pytest_lane(markers, interpreter), _js_lane(package_json))
    return tuple(lane for lane in found if lane)


def _quoted(names) -> str:
    return ", ".join(f'"{name}"' for name in names)


def _scope_stanza(name: str, languages: tuple[str, ...]) -> list[str]:
    optional = ["coverage_optional = true"] if cc_only_scope(languages) else []
    return ["[[scope]]", f'name = "{name}"', f'paths = ["{name}"]',
            f"languages = [{_quoted(languages)}]", *optional, ""]


def _exclude_stanza() -> list[str]:
    return ["[exclude]", f"globs = [{_quoted(DEFAULT_EXCLUDES)}]", ""]


def _lane_env(env: tuple[tuple[str, str], ...]) -> list[str]:
    """The inline table a runner that takes its output path from the environment
    needs, and nothing at all for one that takes a flag."""
    pairs = ", ".join(f'{key} = "{value}"' for key, value in env)
    return [f"env = {{ {pairs} }}"] if pairs else []


def _lane_stanza(lane: LaneSpec, scope_names: tuple[str, ...]) -> list[str]:
    results = [f'results_artifact = "{lane.results_artifact}"'] if lane.results_artifact else []
    return ["[[lane]]", f'name = "{lane.name}"', f'command = "{lane.command}"',
            f'artifact = "{lane.artifact}"', *results, f'parser = "{lane.parser}"',
            *_lane_env(lane.env), f"scopes = [{_quoted(scope_names)}]", ""]


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


_TEMPLATES = {
    "coveragepy": ("# [[lane]]", '# name = "py"',
                   '# command = "python -m pytest --cov --cov-branch '
                   f'--cov-report=json:{_PY_ARTIFACT} --junitxml={_PY_RESULTS}"',
                   f'# artifact = "{_PY_ARTIFACT}"',
                   f'# results_artifact = "{_PY_RESULTS}"', '# parser = "coveragepy"'),
    "istanbul": ("# [[lane]]", '# name = "js"',
                 '# command = "npx vitest run --coverage '
                 f'{_JS_REPORTS_DIR_FLAG["vitest"]}{_JS_COV_DIR} '
                 f'{_JS_JUNIT_FLAGS["vitest"]}"',
                 f'# artifact = "{_JS_ARTIFACT}"',
                 f'# results_artifact = "{_JS_RESULTS}"', '# parser = "istanbul"'),
}


# `crapkit test-scoped FILES` runs one of these per scope. A scope with no
# template exits 3, so the stub is written for every scope init found; the value
# is the runner that scope's language usually uses, and the whole block stays
# commented because only the repo knows whether that command is the right one.
_SCOPED_TEST_COMMANDS = {
    "python": "{python} -m pytest {files} -q -p no:cacheprovider",
    "javascript": "npx vitest run {files}",
    "tsx": "npx vitest run {files}",
    "typescript": "npx vitest run {files}",
}
_SCOPED_TEST_PLACEHOLDER = "<your test command> {files}"
_DEFAULT_PYTHON = "python"


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


def python_launcher(lanes: tuple[LaneSpec, ...]) -> str:
    """The python invocation the detected pytest lane already settled on.

    Read back off the lane rather than passed in beside it, so the scoped-tests
    entry runs the suite the way the coverage lane runs it and the two cannot
    drift: a `uv run python` lane with a bare `python` step 4 would measure one
    environment and test another.
    """
    launcher = _pytest_lane_launcher(lanes)
    return _DEFAULT_PYTHON if launcher is None else launcher


def _scoped_test_command(languages: tuple[str, ...], launcher: str) -> str:
    for language in languages:
        if language in _SCOPED_TEST_COMMANDS:
            return _SCOPED_TEST_COMMANDS[language].replace("{python}", launcher)
    return _SCOPED_TEST_PLACEHOLDER


def _runner_confirmed(languages: tuple[str, ...], confirmed: frozenset[str]) -> bool:
    return any(lang in confirmed and lang in _SCOPED_TEST_COMMANDS for lang in languages)


def _confirmed_languages(lanes: tuple[LaneSpec, ...]) -> frozenset[str]:
    """Languages whose scoped command a detected lane already proves.

    Only pytest: the presence signal that wrote the py coverage lane makes
    `python -m pytest {files}` known-good. The js runners stay unconfirmed on
    purpose — which vitest or jest config a file-scoped run needs is exactly
    what presence detection cannot see."""
    return frozenset({"python"} if _pytest_lane_launcher(lanes) is not None else ())


def _scoped_entry_lines(scopes: dict[str, tuple[str, ...]], live: bool,
                        launcher: str) -> list[str]:
    prefix = "" if live else "# "
    return [f'{prefix}{name} = "{_scoped_test_command(languages, launcher)}"'
            for name, languages in scopes.items()]


def _scoped_tests_stub(scopes: dict[str, tuple[str, ...]],
                       confirmed: frozenset[str] = frozenset(),
                       launcher: str = _DEFAULT_PYTHON) -> list[str]:
    """The [crapkit.scoped_tests] block: live entries for scopes whose runner a
    detected lane proves, commented templates for the rest.

    A confirmed runner written commented would hand doctor a warning about a
    gap init could have closed. Every commented line still uncomments as
    written: a stub a reader has to rewrite before it parses is no better than
    the nothing that used to be here.
    """
    live = {n: l for n, l in scopes.items() if _runner_confirmed(l, confirmed)}
    rest = {n: l for n, l in scopes.items() if n not in live}
    intro = ["# `crapkit test-scoped FILES` runs one command per scope, with {files}",
             "# replaced by that scope's files, each quoted."]
    return (intro + _live_block(live, launcher)
            + _commented_block(rest, bool(live), launcher) + [""])


def _live_block(live: dict[str, tuple[str, ...]], launcher: str) -> list[str]:
    if not live:
        return []
    return ["[crapkit.scoped_tests]"] + _scoped_entry_lines(live, True, launcher)


def _commented_block(rest: dict[str, tuple[str, ...]], has_live: bool,
                     launcher: str) -> list[str]:
    if not rest:
        return []
    header = ["# Uncomment what fits:"] + ([] if has_live else ["# [crapkit.scoped_tests]"])
    return header + _scoped_entry_lines(rest, False, launcher)


def _template_lines(covered: set[str], scopes: dict[str, tuple[str, ...]]) -> list[str]:
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
            lines += [*_TEMPLATES[parser], '# scopes = ["<your-scope>"]', ""]
    if not lines:
        return []
    return ["# Declare one [[lane]] per coverage command, then run `crapkit coverage`.", *lines]


def starter_toml(scopes: dict[str, tuple[str, ...]], lanes: tuple[LaneSpec, ...] = ()) -> str:
    lines = ["[crapkit]", "target = 6", ""]
    for name, languages in scopes.items():
        lines += _scope_stanza(name, languages)
    lines += _exclude_stanza()
    live, covered = _live_lanes(lanes, scopes)
    return "\n".join(lines + live + _template_lines(covered, scopes)
                     + _scoped_tests_stub(scopes, _confirmed_languages(lanes),
                                          python_launcher(lanes)))


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
