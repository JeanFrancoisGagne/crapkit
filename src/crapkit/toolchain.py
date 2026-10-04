"""The toolchain table: which runner a lane runs and what that runner needs.

One row per runner, keyed by its name. init reads the flags it writes into a
lane from here, and every later reader of a runner fact (doctor's runner line,
the runner refusals, the first-run probes) keys on the same row name. Standard
library only at module scope, and never a cli module, so any of them can import
it cheaply.

`infer` answers which row a lane runs. It reads the command through
lane_command's tokenizer, imported where it is called: lane_command asks this
table for its pytest spellings, so a module-scope import either way would make
the two a cycle.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple


class Toolchain(NamedTuple):
    """One runner's facts. A fact the row does not carry yet is None, or empty
    where empty is the whole fact (no extra flags, no environment)."""
    name: str
    # Each way the runner is spelled in a command, as a token sequence: one
    # token for most runners, two for `cargo llvm-cov` and `go test`.
    words: tuple[tuple[str, ...], ...]
    # The package whose presence in package.json's devDependencies names it.
    dev_dependency: str | None = None
    # The command init writes when the package declares no test script.
    # `{python}` stands for the interpreter init settled on.
    init_command: str | None = None
    # "Write the coverage report here": the path follows the flag directly.
    reports_dir_flag: str | None = None
    # The junit reporter's flags; `{cov}` is the report directory as the lane's
    # cwd spells it.
    junit_flags: str | None = None
    # The package the junit reporter needs, or None when the runner ships it.
    junit_package: str | None = None
    # Environment pairs the junit reporter reads its output path from.
    junit_env: tuple[tuple[str, str], ...] = ()
    # Flags that follow the reports directory, each with its leading space.
    extra_flags: str = ""
    # The scoped-tests template, `{files}` the files to narrow the run to.
    related_tests: str | None = None


_ROWS = (
    # pytest raises Interrupted at the END of collection when any module fails
    # to import, so pytest-cov's session finish never runs and the lane writes
    # no coverage JSON at all: one renamed module or one missing optional extra
    # takes the whole lane down and every scope falls to no-lane, while the
    # junit still lands and makes the run look half finished. With
    # --continue-on-collection-errors the modules that did collect run and
    # report, and the uncollected file's tests stay in the junit as errors.
    Toolchain("pytest", (("pytest",),),
              init_command="{python} -m pytest --cov --cov-branch",
              reports_dir_flag="--cov-report=json:",
              junit_flags="--junitxml={cov}/junit-py.xml",
              extra_flags=" --continue-on-collection-errors"),
    # Each JS runner spells "write the coverage report here" its own way and
    # rejects the other's spelling outright. vitest ships its junit reporter, so
    # its flags cost the repo nothing. vitest also writes no coverage report
    # when a test fails, so one red test turned `crapkit coverage` into exit 5
    # naming a missing coverage-final.json: reportOnFailure is vitest's alone,
    # since jest reports on a red run already and exits on a flag it does not
    # know.
    Toolchain("vitest", (("vitest",),), dev_dependency="vitest",
              init_command="npx vitest run --coverage",
              reports_dir_flag="--coverage.reportsDirectory=",
              junit_flags="--reporter=default --reporter=junit --outputFile={cov}/junit.xml",
              extra_flags=" --coverage.reportOnFailure",
              related_tests="npx vitest related --run {files}"),
    # jest needs the separate `jest-junit` package, and naming a reporter jest
    # cannot resolve turns a working lane into an error, so its flags are
    # written only when package.json already carries it. jest-junit takes no
    # path on the command line: package.json, the jest config or these two
    # variables are the whole list, and the first two are the repo's files to
    # own. Without them it drops junit.xml at the repo root.
    Toolchain("jest", (("jest",),), dev_dependency="jest",
              init_command="npx jest --coverage",
              reports_dir_flag="--coverageDirectory=",
              junit_flags="--reporters=default --reporters=jest-junit",
              junit_package="jest-junit",
              junit_env=(("JEST_JUNIT_OUTPUT_DIR", "{cov}"),
                         ("JEST_JUNIT_OUTPUT_NAME", "junit.xml")),
              related_tests="npx jest --findRelatedTests {files}"),
    Toolchain("bun", (("bun",),)),
    Toolchain("deno", (("deno",),)),
    Toolchain("cargo llvm-cov", (("cargo", "llvm-cov"),)),
    Toolchain("go test", (("go", "test"),)),
    Toolchain("c8", (("c8",),)),
)

TOOLCHAINS = MappingProxyType({row.name: row for row in _ROWS})


# --- which row a lane runs ------------------------------------------------------
#
# Read from what the lane runs, never from a config key: the lane's own
# command first, then the package.json script that command runs, then the
# package's devDependencies. Only the first two are written in what runs, so
# only they are `spelled`, and a runner refusal or hint keys on those alone.

# A token spells a runner as its bare name, a path's last part, or either with
# one of these extensions: `.venv\Scripts\pytest.exe`, `node_modules/.bin/vitest.cmd`.
_SPELLED_EXTENSIONS = (".cmd", ".exe", ".js", ".cjs", ".mjs")
# The rows a single token spells, by that token.
_ONE_WORD = MappingProxyType({spelling[0]: row.name for row in _ROWS
                              for spelling in row.words if len(spelling) == 1})
# A script that runs another script is followed this many scripts deep, no deeper.
_SCRIPT_DEPTH = 3
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


class Inferred(NamedTuple):
    """Which row a lane runs and where that was read. `name` is a TOOLCHAINS
    row name, or None when nothing names a row or two rows are named; `source`
    is "command", "script", "package.json" or None; `words` holds the runner
    words found, as written; `script` is the package.json script the runner
    word was written in, None when the command itself spells it."""
    name: str | None
    source: str | None
    words: tuple[str, ...] = ()
    script: str | None = None

    @property
    def spelled(self) -> bool:
        """True when the runner word is written in what runs: the command, or
        the package.json script it runs. devDependencies spell nothing."""
        return self.source in ("command", "script")


UNKNOWN = Inferred(None, None)


def runner_word(token: str, fold: bool = False) -> str | None:
    """The row a single token spells, or None. `fold` reads letter case the
    way Windows does."""
    return _ONE_WORD.get(_bare(token, fold))


def _bare(token: str, fold: bool) -> str:
    """The token's last path part, less one spelled extension, its case folded
    when `fold`."""
    name = _last_part(token).lower() if fold else _last_part(token)
    return next((name[:-len(ext)] for ext in _SPELLED_EXTENSIONS if name.endswith(ext)), name)


def _last_part(path: str) -> str:
    """A path's last part, `/` or `\\` separating on every dialect."""
    return re.split(r"[\\/]", path)[-1]


class _Hit(NamedTuple):
    """A runner word found: the row, the word as written, and the script it
    was written in (None for the lane's own command)."""
    name: str
    word: str
    script: str | None


class _Walk(NamedTuple):
    """What reading one piece of text needs: the package's scripts, the
    dialect (True for cmd.exe, whose case Windows folds), the script being
    read, and the scripts followed to reach it."""
    scripts: Mapping[str, str]
    cmd: bool
    script: str | None = None
    seen: tuple[str, ...] = ()


def infer(command: str, *, cwd_package=None, root_package=None,
          dialect: str | None = None) -> Inferred:
    """The row `command` runs. `cwd_package` is the scaffold.NpmPackage at the
    lane's cwd or the nearest above it, `root_package` the root's; either may
    be None. `dialect` is "sh" or "cmd", None for the shell lanes run under."""
    package = cwd_package or root_package
    walk = _Walk(package.scripts if package else {}, _reads_as_cmd(dialect))
    hits = _hits(command, walk)
    if hits:
        return _answer(hits)
    return _from_dev_dependencies(cwd_package, root_package)


def _reads_as_cmd(dialect: str | None) -> bool:
    if dialect is None:
        from .config import SHELL_IS_CMD

        return SHELL_IS_CMD
    return dialect == "cmd"


def _answer(hits: list[_Hit]) -> Inferred:
    """One row named: that row, from the command when the command spells it.
    Two rows named: no row, every word kept."""
    words = tuple(dict.fromkeys(hit.word for hit in hits))
    if len({hit.name for hit in hits}) > 1:
        return Inferred(None, None, words)
    first = min(hits, key=lambda hit: hit.script is not None)
    source = "command" if first.script is None else "script"
    return Inferred(first.name, source, words, first.script)


def _from_dev_dependencies(*packages) -> Inferred:
    """The row whose dev_dependency the nearest package names, when it names
    exactly one; the root's when the nearest names none."""
    for package in filter(None, packages):
        named = [row.name for row in _ROWS if row.dev_dependency in package.dev_dependencies]
        if named:
            return Inferred(named[0], "package.json") if len(named) == 1 else UNKNOWN
    return UNKNOWN


def _hits(text: str, walk: _Walk) -> list[_Hit]:
    """Every runner word in `text`, read step by step through lane_command's
    tokenizer, a `bash -c` payload included."""
    from .lane_command import command_steps

    return [hit for step in command_steps(text, walk.cmd).steps
            for hit in _step_hits(list(step.words), walk)]


def _step_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """The runner words of one step: past its variable assignments, read by
    the reader its first word names."""
    while words and _ASSIGNMENT.match(words[0]):
        words = words[1:]
    if not words:
        return []
    return _reader(words[0], walk.cmd)(words, walk)


def _reader(head: str, fold: bool):
    """The reader for a step headed by `head`. A python is told by the head's
    last part, `/` and `\\` separating on every host: PurePath on Linux read
    `.venv\\Scripts\\python.exe` whole and named no python."""
    from .lane_command import is_python

    if is_python(_last_part(head)):
        return _module_hits
    return _READERS.get(_bare(head, fold), _row_hits)


def _row_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """The row the step's first words spell, or none."""
    head = _bare(words[0], walk.cmd)
    for row in _ROWS:
        for spelling in row.words:
            if (head, *words[1:len(spelling)]) == spelling:
                return [_Hit(row.name, " ".join(words[:len(spelling)]), walk.script)]
    return []


def _past_options(words: list[str], takes_value: frozenset[str] = frozenset()) -> list[str]:
    """The words from the first one that is not an option or an option's value."""
    at = 0
    while at < len(words) and words[at].startswith("-"):
        at += 2 if words[at] in takes_value else 1
    return words[at:]


def _module_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """`python -m X` and `coverage run -m X`: the module X runs, read as a step."""
    for at, word in enumerate(words[1:], 1):
        if word == "-m":
            return _step_hits(words[at + 1:], walk)
        if not word.startswith("-") and word != "run":
            return []
    return []


def _through(takes_value: frozenset[str] = frozenset()):
    """A reader for a transparent wrapper: what follows its options is the step."""
    def read(words: list[str], walk: _Walk) -> list[_Hit]:
        return _step_hits(_past_options(words[1:], takes_value), walk)
    return read


def _manager_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """uv, poetry, pipenv, pdm and hatch: `run` hands the rest to the step."""
    rest = _past_options(words[1:], _MANAGER_VALUES)
    return _through(_MANAGER_VALUES)(rest, walk) if rest[:1] == ["run"] else []


def _opaque(words: list[str], walk: _Walk) -> list[_Hit]:
    """make, just, tox and nox run a recipe crapkit does not read."""
    return []


# --- script runners: into the package.json script they name -----------------------

_TEST_WORDS = frozenset({"test", "t", "tst"})
_RUN_WORDS = frozenset({"run", "run-script", "rum", "urn"})


def _script_named(rest: list[str]) -> str | None:
    """The script `test` or `run X` names, or None."""
    if rest[:1] and rest[0] in _TEST_WORDS:
        return "test"
    if len(rest) > 1 and rest[0] in _RUN_WORDS:
        return rest[1]
    return None


def _follow(name: str, walk: _Walk) -> list[_Hit]:
    """The runner words of the script `name`, read with the command's rules.
    A script the package lacks, a cycle and a level past the depth name nothing."""
    if name not in walk.scripts or name in walk.seen or len(walk.seen) >= _SCRIPT_DEPTH:
        return []
    inner = walk._replace(script=name, seen=(*walk.seen, name))
    return _hits(walk.scripts[name], inner)


def _npm_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """`npm test` and `npm run X`."""
    name = _script_named(_past_options(words[1:], _NPM_VALUES))
    return _follow(name, walk) if name else []


def _package_runner(takes_value: frozenset[str]):
    """pnpm and yarn: `exec` and `dlx` run the step that follows; `test`,
    `run X` and an `X` naming a script follow that script; any other `X` is a
    binary the package manager runs, read as a step."""
    def read(words: list[str], walk: _Walk) -> list[_Hit]:
        rest = _past_options(words[1:], takes_value)
        if rest[:1] and rest[0] in ("exec", "dlx"):
            return _step_hits(rest[1:], walk)
        name = _script_named(rest) or _own_script(rest, walk)
        return _follow(name, walk) if name else _step_hits(rest, walk)
    return read


def _own_script(rest: list[str], walk: _Walk) -> str | None:
    """`pnpm X` and `yarn X` with X a script of the package: X."""
    return rest[0] if rest[:1] and rest[0] in walk.scripts else None


# --- runtimes: node, bun and deno -----------------------------------------------------


def _stem_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """The script-stem rule: a runner word that is a hyphen- or
    underscore-separated part of the stem of the script file a runtime runs
    spells that runner. `run-vitest.mjs` spells vitest, `run.vitest.mjs`
    nothing: a dot does not separate."""
    if not words:
        return []
    return [_Hit(row, words[0], walk.script) for row in _stem_rows(words[0], walk.cmd)]


def _stem_rows(script: str, fold: bool) -> list[str]:
    """The rows the parts of the script file's stem spell, each once."""
    name = _last_part(script)
    stem = name.rpartition(".")[0] or name
    found = (_ONE_WORD.get(part.lower() if fold else part) for part in re.split(r"[-_]", stem))
    return [row for row in dict.fromkeys(found) if row]


def _node_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    return _stem_hits(_past_options(words[1:], _NODE_VALUES), walk)


def _bun_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """`bun test` runs bun's runner, `bun x` a binary, `bun run X` a script or
    a file, and `bun FILE` that file."""
    rest = _past_options(words[1:], _NODE_VALUES)
    if rest[:1] == ["test"]:
        return [_Hit("bun", words[0], walk.script)]
    if rest[:1] == ["x"]:
        return _step_hits(rest[1:], walk)
    if rest[:1] == ["run"]:
        return _run_hits(_past_options(rest[1:], _NODE_VALUES), walk)
    return _stem_hits(rest, walk)


def _run_hits(rest: list[str], walk: _Walk) -> list[_Hit]:
    """`bun run X`: the script X when the package has one, else the file X."""
    if _own_script(rest, walk):
        return _follow(rest[0], walk)
    return _stem_hits(rest, walk)


def _deno_hits(words: list[str], walk: _Walk) -> list[_Hit]:
    """`deno test` runs deno's runner and `deno run FILE` that file."""
    rest = _past_options(words[1:])
    if rest[:1] == ["test"]:
        return [_Hit("deno", words[0], walk.script)]
    return _stem_hits(_past_options(rest[1:], _NODE_VALUES), walk) if rest[:1] == ["run"] else []


_NPX_VALUES = frozenset({"-p", "--package"})
_PNPM_VALUES = frozenset({"--dir", "-C", "--filter", "-F"})
_YARN_VALUES = frozenset({"--cwd"})
_NPM_VALUES = frozenset({"--prefix", "-w", "--workspace"})
_ENV_VALUES = frozenset({"-u", "--unset", "-C", "--chdir"})
_MANAGER_VALUES = frozenset({"--with", "--python", "-p", "--directory", "--project", "--extra",
                             "--group", "--package", "--env-file", "-C", "-P", "-e", "--env"})
_NODE_VALUES = frozenset({"-r", "--require", "--import", "--loader", "--experimental-loader",
                          "-e", "--eval", "-p", "--print"})

# The first word of a step that is read through, or stopped at; any other word
# is matched against the rows. A python's `-m X` is read by its own rule.
_READERS = MappingProxyType({
    "npx": _through(_NPX_VALUES), "bunx": _through(_NPX_VALUES),
    "env": _through(_ENV_VALUES), "cross-env": _through(),
    "pnpm": _package_runner(_PNPM_VALUES), "yarn": _package_runner(_YARN_VALUES),
    "npm": _npm_hits, "coverage": _module_hits,
    **dict.fromkeys(("uv", "poetry", "pipenv", "pdm", "hatch"), _manager_hits),
    "node": _node_hits, "bun": _bun_hits, "deno": _deno_hits,
    **dict.fromkeys(("make", "just", "tox", "nox"), _opaque),
})
