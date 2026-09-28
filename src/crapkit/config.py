"""crapkit.toml parsing. Text in, Config out; every rejection is a ConfigError (exit 3).

Pure, with two exceptions, both only given a `root`. The full-suite guard reads
pytest's own configuration in a lane's working directory, and only when the
lane's pytest command carries a positional to judge against `testpaths`. And
every path-valued key is read by repopath's declared entry (_PATH_KEYS), which
gives a declared directory the letter case the directory lists where the
filesystem opens it in another case, because git and every reader after this
compare the text."""
from __future__ import annotations

import os
import posixpath
import tomllib
from pathlib import Path
from typing import NamedTuple

from .errors import ConfigError
from .invocation import quoted_path
from .config_contract import admit, enum_values
from . import lane_command
# shell_words and shell_segments are read through this module by callers that
# predate lane_command's tokenizer.
from .lane_command import Step, command_steps, shell_segments, shell_words  # noqa: F401
from .repopath import Refused, declared, disk_spelling, file_separators
from .repotext import plain_utf8

# `cpp` is the whole C family, C included: lizard resolves every one of its
# suffixes to a single CLikeReader, so a `c` label beside this one could never
# measure differently — and `.h` is the header both dialects share, which no rule
# could assign to one of them.
SUPPORTED_LANGUAGES = frozenset(enum_values("scope", "languages"))
SUPPORTED_PARSERS = frozenset(enum_values("lane", "parser"))
DEFAULT_TARGET = 6

# Only what a vitest command line can carry: this tuple guards istanbul lane
# commands against a positional file filter, and no .swift or .go path appears
# in one.
_SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".js", ".jsx", ".mjs", ".cjs", ".py")


class Scope(NamedTuple):
    name: str
    paths: tuple[str, ...]
    languages: tuple[str, ...]
    target: int | None = None  # per-scope ceiling; None = the repo default
    # Code no test can reach (production-only scripts, generated shims): scored
    # cc-only, and no lane has to claim it.
    coverage_optional: bool = False


class Lane(NamedTuple):
    name: str
    command: str
    artifact: str
    parser: str
    scopes: tuple[str, ...]
    cwd: str = ""
    path_prefix: str = ""
    env: tuple[tuple[str, str], ...] = ()
    full_suite: bool = True
    container_ok: bool = False
    results_artifact: str = ""
    timeout_seconds: int = 0  # 0 = no crapkit-owned timeout
    # Kill the lane when its log has not grown for this many seconds. A total
    # deadline cannot bound a suite that hangs at 0% CPU without also bounding
    # the slow runs it cannot tell apart from one. 0 = no progress watch.
    no_progress_seconds: int = 0
    retries: int = 0
    retest_command: str = ""  # {tests} template for the flake retry before exit 8
    log_max_bytes: int = 16777216  # inherited global bound, not a per-lane TOML key
    # Root-relative paths the command reads. Declared, they let --reuse-unchanged
    # reuse the lane while none of them changed; () keeps the whole-tree rule.
    inputs: tuple[str, ...] = ()


# pytest options that read the NEXT token as their value. `-n 8` is eight
# workers, not a test path, and refusing it sent two reporters (#19, #22) down a
# dead end whose only exit was the attached form. Attached values (`-n8`,
# `--numprocesses=8`) carry their own value and appear nowhere in this set.
_PYTEST_VALUE_FLAGS = frozenset({
    "-c", "-k", "-m", "-n", "-o", "-p", "-r", "-W",
    "--basetemp", "--confcutdir", "--cov", "--cov-config", "--cov-context",
    "--cov-report", "--deselect", "--dist", "--durations", "--ignore",
    "--ignore-glob", "--import-mode", "--junitxml", "--log-file", "--maxfail",
    "--numprocesses", "--rootdir", "--timeout",
})


# Lane commands run under shell=True: sh on POSIX, cmd.exe on Windows. The one
# flag every reader of a lane command asks; lane_command reads the line in the
# dialect it names, and admin and verifying say which shell runs the lane.
SHELL_IS_CMD = os.name == "nt"


def _quote_hint(step: Step) -> str:
    """The usual reason a Windows lane trips the guard: a value in single
    quotes, which cmd.exe hands the runner one word per space. Only a step
    cmd.exe read keeps a ' in its words: a `bash -c` payload is read with sh's
    quotes, and telling its author to switch to double quotes sent a lane past
    the guard."""
    if step.cmd and any("'" in word for word in step.words):
        return " (cmd.exe does not treat ' as a quote: write the value in double quotes)"
    return ""


def _looks_like_a_test_path(tok: str) -> bool:
    """The shapes a pytest positional actually takes: a path or a node id."""
    return "/" in tok or "\\" in tok or "::" in tok or tok.endswith(".py")


def _consumes_next(flag: str, following: str) -> bool:
    """Does `flag` read `following` as its value rather than leave it positional?"""
    if "=" in flag:
        return False  # --cov-report=json:x.json already holds its value
    if flag in _PYTEST_VALUE_FLAGS:
        return True
    # An unknown plugin flag takes its value the same way, so a bare word after
    # one belongs to it. A path is the one token that outranks the guess.
    return not _looks_like_a_test_path(following)


def _flag_value_positions(tokens: list[str]) -> set[int]:
    """Indexes of the tokens a flag in front of them swallows."""
    return {i + 1 for i, tok in enumerate(tokens[:-1])
            if tok.startswith("-") and _consumes_next(tok, tokens[i + 1])}


def _tokens_after_pytest(tokens: list[str]) -> list[str]:
    """What pytest itself parses: everything past the `pytest` token, or nothing
    when the word only appears inside another token (`tox -e pytest-lane`)."""
    for i, tok in enumerate(tokens):
        if tok.endswith("pytest"):
            return tokens[i + 1:]
    return []


def _narrowing_arguments(tokens: list[str]) -> list[str]:
    """The positionals left once flags and their values are accounted for. A
    `key=value` token is never one: it is an ini override or an attached value."""
    values = _flag_value_positions(tokens)
    return [tok for i, tok in enumerate(tokens)
            if i not in values and not tok.startswith("-") and "=" not in tok]


# Where pytest keeps `testpaths`, in the order pytest picks its inifile, as
# (file, pytest section, decides even without that section). pytest reads one
# inifile and never consults a lower-ranked one: pytest.ini decides even empty,
# while .pytest.ini and the other three decide only
# when they hold a pytest section. pyproject.toml's section is the
# `[tool.pytest.ini_options]` table, named by an empty section here. Parsed,
# never executed and never imported.
_PYTEST_INI_FILES = (("pytest.ini", "pytest", True), (".pytest.ini", "pytest", False),
                     ("pyproject.toml", "", False), ("tox.ini", "pytest", False),
                     ("setup.cfg", "tool:pytest", False))
PYTEST_CONFIG_FILES = tuple(name for name, _, _ in _PYTEST_INI_FILES)


def _split_testpaths(value) -> tuple[str, ...]:
    """pytest's `args` type: a list as it stands, a string split on whitespace."""
    if isinstance(value, str):
        return tuple(value.split())
    if isinstance(value, list):
        return tuple(str(path) for path in value)
    return ()


def _ini_testpaths(text: str, section: str, always: bool) -> tuple[str, ...] | None:
    """`testpaths` out of an ini file, or None when the file holds no pytest
    section for the search to stop at; a file that decides `always` stops it
    with () instead. A file that will not parse holds none either way."""
    import configparser

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_section(section):
        return () if always else None
    return _split_testpaths(parser.get(section, "testpaths", fallback=""))


def _toml_table(data, *keys: str):
    """One key path through nested tables, or None the moment it leaves them."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _toml_testpaths(text: str) -> tuple[str, ...] | None:
    """None unless `[tool.pytest.ini_options]` is there, the table pytest looks
    for before it reads a pyproject as its inifile."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    section = _toml_table(data, "tool", "pytest", "ini_options")
    if not isinstance(section, dict):
        return None
    return _split_testpaths(section.get("testpaths"))


def _pytest_text(directory: str | os.PathLike, name: str) -> str | None:
    """Read one candidate file without confusing absence with an empty file."""
    try:
        return plain_utf8((Path(directory) / name).read_bytes())
    except OSError:
        return None


def _pytest_testpaths(read_text) -> tuple[str, ...]:
    for name, section, always in _PYTEST_INI_FILES:
        text = read_text(name)
        if text is None:
            continue
        found = _toml_testpaths(text) if not section else _ini_testpaths(text, section, always)
        if found is not None:
            return found
    return ()


def pytest_testpaths_texts(texts: dict[str, str]) -> tuple[str, ...]:
    """Select testpaths from supplied config texts using pytest's precedence."""
    return _pytest_testpaths(texts.get)


def pytest_testpaths_at(directory: str | os.PathLike) -> tuple[str, ...]:
    """Read testpaths from the first deciding pytest config in this directory."""
    return _pytest_testpaths(lambda name: _pytest_text(directory, name))


def _as_testpath(token: str, base: Path | None = None) -> str:
    """One spelling for the comparison: forward slashes, no leading `./`, no
    trailing separator. `tests/`, `./tests` and `tests` name one directory.
    Given the directory it is read from, it takes the case that directory
    lists: on NTFS `Tests` is tests/."""
    spelled = file_separators(token)
    if spelled.startswith("./"):
        spelled = spelled[2:]
    spelled = spelled.rstrip("/")
    return disk_spelling(base, spelled) if base is not None and spelled else spelled


def _declared_testpaths(positionals: list[str], lane_dir: Path | None) -> set[str]:
    """The configured `testpaths` entries in the guard's spelling, or an empty
    set when there is nothing to judge. pytest's files are opened only when
    there is a positional to judge and a directory to read them in, so the
    common lane costs a read-only command no file read."""
    if not positionals or lane_dir is None:
        return set()
    return {_as_testpath(path, lane_dir) for path in pytest_testpaths_at(lane_dir)}


def _outside_testpaths(positionals: list[str], lane_dir: Path | None) -> list[str]:
    """The positionals that narrow the run: all of them, unless together they
    name every configured `testpaths` entry, in which case the entries drop out
    and only the extras are left. `pytest tests` under `testpaths = ["tests"]`
    collects exactly what a bare `pytest` collects, and refusing it sent a
    maintainer to `full_suite = false` on a lane that runs the whole suite;
    `pytest tests` under `testpaths = ["tests", "integration"]` collects half
    of what a bare `pytest` does, which is the narrowing this guard exists to
    refuse, so one entry of several is not enough."""
    declared = _declared_testpaths(positionals, lane_dir)
    if not declared <= {_as_testpath(tok, lane_dir) for tok in positionals}:
        return positionals
    # An empty `declared` is a subset of anything and drops nothing below.
    return [tok for tok in positionals if _as_testpath(tok, lane_dir) not in declared]


def _validate_coveragepy_command(name: str, command: str, lane_dir: Path | None = None) -> None:
    # Subset coverage under a suite with cross-file pollution is run-order-dependent;
    # a full-suite lane refuses positional narrowing. Scoped suites opt out with
    # full_suite = false, an explicit and reviewable decision. Every chained
    # step is read, a `bash -c` payload's included: a second pytest run
    # narrows just as much as the first.
    for step in command_steps(command).steps:
        _refuse_pytest_narrowing(name, step, lane_dir)


def _refuse_pytest_narrowing(name: str, step: Step, lane_dir: Path | None = None) -> None:
    """One command's argv. A segment that runs no pytest has nothing to narrow,
    and a positional equal to a configured testpaths entry narrows nothing.

    Two exits, not one. A scoped suite opts out with `full_suite = false`. A
    suite that cannot collect all its testpaths in one process has no full-suite
    command to write at all, and taking the opt-out on a single narrowed lane
    leaves its other testpaths unmeasured with nothing saying so, which is why
    the message names the multi-lane pattern rather than only the flag.
    """
    positionals = _narrowing_arguments(_tokens_after_pytest(list(step.words)))
    for tok in _outside_testpaths(positionals, lane_dir):
        raise ConfigError(
            f"lane {name!r}: positional argument '{tok}' narrows a full-suite coverage run; "
            f"drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), "
            f"or set full_suite = false deliberately{_quote_hint(step)}; a suite whose "
            f"testpaths cannot be collected in one process needs one lane per testpath, "
            f"each with full_suite = false and its own artifact")


def _asks_for_coverage(tokens: list[str]) -> bool:
    return any(t == "--coverage" or t.startswith("--coverage") for t in tokens)


def _first_filter_position(tokens: list[str]) -> int:
    # Only tokens after the test runner's `run` subcommand can be positional file
    # filters; the runner script path itself (node scripts/run-vitest.mjs ...) is not.
    return tokens.index("run") + 1 if "run" in tokens else 0


# vitest options that read the NEXT token as their value. A source path after one
# of these is that value — the config file, an exclude glob, a reporter module —
# not a positional filter. Unlike the pytest guard this list is the whole licence:
# a filter here has to end in a source suffix already, so guessing that an unknown
# flag swallows one would retire the check instead of sharpening it.
_VITEST_VALUE_FLAGS = frozenset({
    "-c", "-t", "--config", "--coverage.exclude", "--coverage.extension",
    "--coverage.include", "--coverage.provider", "--coverage.reporter",
    "--coverage.reportsDirectory", "--diff", "--dir", "--environment",
    "--exclude", "--globalSetup", "--outputFile", "--pool", "--project",
    "--reporter", "--root", "--setupFiles", "--shard", "--snapshotEnvironment",
    "--testNamePattern", "--typecheck.tsconfig", "--workspace",
})


def _is_file_filter(tok: str, preceding: str) -> bool:
    if tok.startswith("-"):
        return False  # a flag (e.g. --coverage.exclude=**/*.test.ts) is never a positional filter
    if preceding in _VITEST_VALUE_FLAGS:
        return False
    return tok.endswith(_SOURCE_SUFFIXES)


def _validate_istanbul_command(name: str, command: str) -> None:
    # The measured vitest trap: any file filter passed beside --coverage silently
    # narrows the coverage include set. A lane command is fixed configuration, so
    # the combination is a config error, not a runtime surprise. Each chained
    # step is its own argv: a script path in a post-run step is that step's,
    # and a vitest run after `npm run build`, or inside `sh -c`, is still a
    # vitest run.
    for step in command_steps(command).steps:
        _refuse_istanbul_filter(name, list(step.words))


def _refuse_istanbul_filter(name: str, tokens: list[str]) -> None:
    """One command's argv. A segment that asks for no coverage narrows none."""
    if not _asks_for_coverage(tokens):
        return
    for i in range(_first_filter_position(tokens), len(tokens)):
        if _is_file_filter(tokens[i], tokens[i - 1]):
            raise ConfigError(
                f"lane {name!r}: file filter '{tokens[i]}' combined with --coverage silently narrows "
                f"the coverage include set; drop the filter or use a dedicated config")


class Config(NamedTuple):
    target: int = DEFAULT_TARGET

    @property
    def scope_targets(self) -> dict[str, int]:
        """Every scope's effective ceiling: its own target or the repo default."""
        return {s.name: (s.target if s.target is not None else self.target) for s in self.scopes}

    def ceiling_of(self, scope: str) -> int:
        """The ceiling one scope's functions are judged against: the scope's own
        `target` when it sets one, else the repo's. The one spelling of that
        rule for every command holding a Config; a scope nothing declared is
        judged at the repo ceiling."""
        return self.scope_targets.get(scope, self.target)

    @property
    def ceilings(self) -> dict[str, int]:
        """The ceilings in force, as a summary labels them: `default` and only
        the scopes whose own target differs from it."""
        own = {name: c for name, c in self.scope_targets.items() if c != self.target}
        return {"default": self.target, **own}

    @property
    def coverage_optional_scopes(self) -> frozenset[str]:
        """The scopes scored cc-only: no coverage join, and no lane required."""
        return frozenset(s.name for s in self.scopes if s.coverage_optional)

    @property
    def lane_less_scopes(self) -> tuple[str, ...]:
        """Scopes no lane measures and no `coverage_optional` excuses.

        Empty is the licence to run with no lanes at all: every scope is either
        measured by one or scored cc-only, so there is nothing left for a lane
        to say.

        The two readers weigh a non-empty answer differently, and the difference
        is deliberate. `verify` refuses outright and names the list. `coverage`
        refuses only when it also selected no lane, and otherwise scores the
        scope and flags every row `no-lane` — a flag it could not print at all
        if owing a lane refused the run. `doctor` is what says so out of band.
        """
        covered = {s for lane in self.lanes for s in lane.scopes} | self.coverage_optional_scopes
        return tuple(s.name for s in self.scopes if s.name not in covered)

    @property
    def scope_paths(self) -> dict[str, tuple[str, ...]]:
        """Every scope's declared paths, by name — what a lane's scopes resolve to."""
        return {s.name: s.paths for s in self.scopes}
    scopes: tuple[Scope, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    max_file_bytes: int | None = None  # files bigger than this leave the corpus; None = no limit
    churn_window_months: int = 12
    worklist_floor: int = 5
    worklist_top: int = 50
    lanes: tuple[Lane, ...] = ()
    ratchet_file: str = "crapkit-ratchet.tsv"
    alert_command: str = ""
    scoped_tests: tuple[tuple[str, str], ...] = ()
    mutation_command: str = ""  # the suite run once per mutant; nonzero = killed, 5 = no verdict
    mutation_timeout_seconds: int = 300  # past it a mutant counts as killed, reported as timed_out
    mutation_workers: int = 1  # one retained detached worktree per worker
    diff_uncovered_max: int | None = None  # verify exit 9 past this many dead changed lines
    # A tighten claims an improvement; one commit measured twice cannot have
    # improved. Past this factor between two runs of the same commit, verify
    # holds the mark instead of tightening it.
    tighten_max_jump: float = 2.0
    debt_max_age_months: int | None = None  # ratchet report --enforce flags older marks
    repayment_min_per_30d: int | None = None  # --enforce flags a stalled burn-down
    max_parallel_lanes: int = 1  # lanes running at once; 1 = strictly serial
    analysis_workers: int = 0  # requested lizard workers; 0 = automatic sizing
    analysis_worker_budget: int = 0  # shared pool slot ceiling; 0 = available CPUs
    log_max_bytes: int = 16777216  # each active/backup lane log; 0 = unlimited
    # Operational traps the repo learned the hard way. They lived as TOML
    # comments, which the parser drops, so no payload could ever quote them.
    notes: tuple[str, ...] = ()
    # Only the scopes that wrote one, so a scope with nothing to say costs a
    # payload no key. A plain dict, because these end up in --json output.
    scope_notes: dict[str, tuple[str, ...]] = {}


def load_config_text(text: str, *, root: str | os.PathLike | None = None) -> Config:
    """`root` is the directory crapkit.toml sits in. Given, it lets the
    full-suite guard read pytest's `testpaths` where each lane runs; absent, a
    positional in a pytest command is judged from the command alone."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"crapkit.toml does not parse: {exc}") from exc
    try:
        admit(raw)
        return _build_config(raw, root)
    except KeyError as exc:
        raise ConfigError(f"crapkit.toml is missing a required key: {exc}") from exc


# Every path-valued key crapkit.toml holds, and the kind repopath's declared
# entry reads it as: "file" (a path the OS opens), "scope", "prefix" and "input"
# (a directory as git spells it, in the case the directory lists) or "glob".
_PATH_KEYS = {
    "scope.paths": "scope",
    "lane.path_prefix": "prefix",
    "lane.inputs": "input",
    "lane.cwd": "file",
    "lane.artifact": "file",
    "lane.results_artifact": "file",
    "crapkit.ratchet_file": "file",
    "exclude.globs": "glob",
}


def _path(key: str, raw: str, root: str | os.PathLike | None = None, owner: str = "") -> str:
    """One value of a path-valued key, read by repopath's declared entry as
    _PATH_KEYS marks the key. A refusal names the key's owner and the value as
    written, so `scope 'web': path '../web'` says which line to fix, with one
    backslash where the reader typed one."""
    try:
        return declared(raw, _PATH_KEYS[key], root)
    except Refused as exc:
        raise ConfigError(f"{owner}{quoted_path(raw)} {exc}") from None


def _parse_scope(row: dict, root: str | os.PathLike | None = None) -> Scope:
    languages = tuple(row.get("languages", ()))
    scope_target = row.get("target")
    return Scope(name=row["name"],
                 paths=tuple(_path("scope.paths", p, root, f"scope {row.get('name')!r}: path ")
                             for p in row["paths"]),
                 languages=languages,
                 target=scope_target,
                 coverage_optional=row.get("coverage_optional", False))


def _parse_scopes(rows, root: str | os.PathLike | None = None
                  ) -> tuple[tuple[Scope, ...], dict[str, tuple[str, ...]]]:
    """Every [[scope]] row and its notes, off ONE walk of the rows.

    Notes hang on the same rows the scopes come from, so collecting them in a
    second pass would re-read and re-validate every row for nothing — and, when
    the rows arrive as an iterator, would find none of them.
    """
    scopes: dict[str, Scope] = {}
    notes: dict[str, tuple[str, ...]] = {}
    for row in rows:
        scope = _parse_scope(row, root)
        if scope.name in scopes:
            raise ConfigError(f"duplicate scope name {scope.name!r}; each scope needs its own name")
        scopes[scope.name] = scope
        row_notes = tuple(row.get("notes", ()))
        if row_notes:
            notes[scope.name] = row_notes
    return tuple(scopes.values()), notes


def _expanded(command: str) -> str:
    """The command with its launcher tokens expanded for the OS reading the
    file, once, as the Lane is built (lane_command.expand_launchers), so every
    reader of the command sees the one the shell will run."""
    return lane_command.expand_launchers(command)


def _validate_lane_command(parser: str, full_suite: bool, name: str, command: str,
                           lane_dir: Path | None = None) -> None:
    if parser == "istanbul":
        _validate_istanbul_command(name, command)
    if parser == "coveragepy" and full_suite:
        _validate_coveragepy_command(name, command, lane_dir)


def _lane_dir(root: str | os.PathLike | None, cwd: str) -> Path | None:
    """Where the lane's command runs, which is where pytest picks its inifile;
    None without a root, and then no file is read."""
    if root is None:
        return None
    return Path(root) / cwd if cwd else Path(root)


# What Windows refuses in a file name: these characters, the control
# characters, the device names below (with or without an extension), and a
# name that ends in a dot or a space. A lane's name is part of the file names
# crapkit writes for it, so the loader refuses such a name on every OS.
_NAME_CHARS = frozenset('<>:"/\\|?*')
_DEVICE_NAMES = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                           *(f"{port}{n}" for port in ("COM", "LPT")
                             for n in (*"0123456789", "\u00b9", "\u00b2", "\u00b3"))})


def _lane_name(name: str) -> None:
    """The lane's name, refused when Windows could not use it as a file name:
    `unit?` ended `crapkit coverage` in a traceback, and `a:b` wrote the lane's
    log into an alternate data stream of a file named .crapkit/lane-a."""
    problem = _unusable_file_name(name)
    if problem:
        raise ConfigError(f"lane {name!r}: {problem}; crapkit writes the lane's log to "
                          ".crapkit/lane-<name>.log and refuses such a name on every OS so "
                          "one crapkit.toml works on each; rename the lane")


def _unusable_file_name(name: str) -> str:
    """Why Windows cannot use `name` as a file name, or "" when it can."""
    held = _refused_characters(name)
    if held:
        return f"the name holds {' '.join(map(_shown, held))}, which Windows refuses in a file name"
    device = name.split(".")[0].rstrip(" ").upper()
    if device in _DEVICE_NAMES:
        return f"{device} is a device name Windows reserves, so no file can take it"
    return _unusable_end(name)


def _refused_characters(name: str) -> list[str]:
    return sorted({ch for ch in name if ch in _NAME_CHARS or ord(ch) < 32})


def _shown(ch: str) -> str:
    return f"U+{ord(ch):04X}" if ord(ch) < 32 else ch


def _unusable_end(name: str) -> str:
    if not name:
        return "the name is empty"
    if name[-1] in ". ":
        return f"the name ends in {'a dot' if name[-1] == '.' else 'a space'}, which Windows drops"
    return ""


def _parse_lane(row: dict, scope_names: set, root: str | os.PathLike | None = None) -> Lane:
    _lane_name(row["name"])
    parser = row["parser"]
    lane_scopes = tuple(row.get("scopes", ()))
    unknown_scopes = set(lane_scopes) - scope_names
    if unknown_scopes:
        raise ConfigError(f"lane {row.get('name')!r} references undeclared scope(s) {sorted(unknown_scopes)}")
    full_suite = row.get("full_suite", True)
    cwd = _path("lane.cwd", row.get("cwd", ""))
    command = _expanded(row["command"])
    _validate_lane_command(parser, full_suite, row.get("name", "?"), command,
                           _lane_dir(root, cwd))
    return Lane(name=row["name"], command=command,
                artifact=_lane_output(row.get("name"), "artifact", row["artifact"]),
                parser=parser, scopes=lane_scopes,
                cwd=cwd, path_prefix=_path("lane.path_prefix", row.get("path_prefix", ""), root),
                env=tuple(sorted(row.get("env", {}).items())),
                full_suite=full_suite, container_ok=row.get("container_ok", False),
                results_artifact=_lane_output(row.get("name"), "results_artifact",
                                              row.get("results_artifact", "")),
                timeout_seconds=row.get("timeout_seconds", 0),
                no_progress_seconds=row.get("no_progress_seconds", 0),
                retries=row.get("retries", 0),
                retest_command=_expanded(row.get("retest_command", "")),
                inputs=_lane_inputs(row, root))


def _lane_output(name, key: str, raw: str) -> str:
    """A declared lane output, refused when it names the directory crapkit.toml
    sits in: the runner clears each declared path before an attempt and reads
    the one file there, and `artifact = "."` ended `crapkit coverage` in a
    traceback."""
    value = _path(f"lane.{key}", raw)
    if _names_the_root(value, required=key == "artifact"):
        raise ConfigError(f"lane {name!r}: {key} names the directory crapkit.toml sits in "
                          f"(written {quoted_path(raw)}), not a file; set it to the report "
                          "file the lane's command writes")
    return value


def _names_the_root(value: str, required: bool) -> bool:
    """Empty, `.` or `cov/..`. An optional output left empty is one the lane
    does not declare."""
    return (bool(value) or required) and posixpath.normpath(value or ".") == "."


def _lane_inputs(row: dict, root: str | os.PathLike | None = None) -> tuple[str, ...]:
    owner = f"lane {row.get('name')!r}: inputs entry "
    return tuple(_path("lane.inputs", entry, root, owner) for entry in row.get("inputs", ()))


def _reject_shared_artifacts(lanes: list, root=None) -> None:
    seen_artifacts: dict[str, str] = {}
    for lane in lanes:
        for artifact in filter(None, (lane.artifact, lane.results_artifact)):
            key = os.path.normcase(os.path.abspath(os.path.join(root or '.', artifact)))
            if key in seen_artifacts:
                raise ConfigError(
                    f"lanes {seen_artifacts[key]!r} and {lane.name!r} share the artifact path "
                    f"{quoted_path(artifact)}; reused paths cross-attribute coverage under --reuse-artifacts")
            seen_artifacts[key] = lane.name


def _unique_lanes(rows, scope_names: set, root) -> list[Lane]:
    """The lanes, keyed on the name as Windows and macOS compare file names,
    which ignore case: `unit` and `Unit` would write one log file."""
    lanes: dict[str, Lane] = {}
    for row in rows:
        lane = _parse_lane(row, scope_names, root)
        _refuse_second(lanes.get(lane.name.lower()), lane)
        lanes[lane.name.lower()] = lane
    return list(lanes.values())


def _refuse_second(earlier: Lane | None, lane: Lane) -> None:
    if earlier is None:
        return
    if earlier.name == lane.name:
        raise ConfigError(f"duplicate lane name {lane.name!r}; each lane needs its own name")
    raise ConfigError(f"lanes {earlier.name!r} and {lane.name!r} differ only in case, and "
                      "Windows and macOS ignore case in a file name, so both would write the "
                      f"log .crapkit/lane-{lane.name}.log; rename one")


def _scoped_tests(main: dict) -> tuple[tuple[str, str], ...]:
    """Each scope's test-scoped template, by scope name, with its launcher
    token expanded; `{files}` stays for test-scoped to fill in."""
    return tuple(sorted((name, _expanded(template))
                        for name, template in main.get("scoped_tests", {}).items()))


def _build_config(raw: dict, root: str | os.PathLike | None = None) -> Config:
    scopes, scope_notes = _parse_scopes(raw["scope"], root)
    scope_names = {s.name for s in scopes}
    main = raw.get("crapkit", {})
    lanes = [lane._replace(log_max_bytes=main.get("log_max_bytes", 16777216))
             for lane in _unique_lanes(raw.get("lane", []), scope_names, root)]
    _reject_shared_artifacts(lanes, root)
    return Config(
        target=main.get("target", DEFAULT_TARGET),
        scopes=scopes,
        exclude_globs=tuple(_path("exclude.globs", glob)
                            for glob in raw.get("exclude", {}).get("globs", ())),
        max_file_bytes=raw.get("exclude", {}).get("max_file_bytes"),
        churn_window_months=main.get("churn_window_months", 12),
        worklist_floor=main.get("worklist_floor", 5),
        worklist_top=main.get("worklist_top", 50),
        lanes=tuple(lanes),
        ratchet_file=_path("crapkit.ratchet_file", main.get("ratchet_file", "crapkit-ratchet.tsv")),
        alert_command=main.get("alert_command", ""),
        scoped_tests=_scoped_tests(main),
        mutation_command=_expanded(main.get("mutation_command", "")),
        mutation_timeout_seconds=main.get("mutation_timeout_seconds", 300),
        mutation_workers=main.get("mutation_workers", 1),
        diff_uncovered_max=main.get("diff_uncovered_max"),
        tighten_max_jump=float(main.get("tighten_max_jump", 2.0)),
        debt_max_age_months=main.get("debt_max_age_months"),
        repayment_min_per_30d=main.get("repayment_min_per_30d"),
        max_parallel_lanes=main.get("max_parallel_lanes", 1),
        analysis_workers=main.get("analysis_workers", 0),
        analysis_worker_budget=main.get("analysis_worker_budget", 0),
        log_max_bytes=main.get("log_max_bytes", 16777216),
        notes=tuple(main.get("notes", ())),
        scope_notes=scope_notes,
    )
