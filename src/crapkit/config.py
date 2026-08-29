"""crapkit.toml parsing. Pure: text in, Config out; every rejection is a ConfigError (exit 3)."""
from __future__ import annotations

import shlex
import tomllib
from typing import NamedTuple

from .errors import ConfigError

# `cpp` is the whole C family, C included: lizard resolves every one of its
# suffixes to a single CLikeReader, so a `c` label beside this one could never
# measure differently — and `.h` is the header both dialects share, which no rule
# could assign to one of them.
SUPPORTED_LANGUAGES = frozenset({"typescript", "tsx", "javascript", "python", "swift",
                                 "go", "rust", "shell", "cpp", "objectivec", "vue",
                                 "java", "zig", "powershell"})
SUPPORTED_PARSERS = frozenset({"istanbul", "coveragepy"})
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
    retries: int = 0
    retest_command: str = ""  # {tests} template for the flake retry before exit 8


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


def _shell_words(command: str) -> list[str]:
    """The words the shell hands the runner. Lane commands run under shell=True,
    so `-m 'not live and not perf'` is one argument there and must be one token
    here — a whitespace split reads four positionals into it. A command shlex
    refuses (an unbalanced quote) gets the whitespace read instead: a rough
    lint on a command sh will refuse anyway beats a crash at config load."""
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


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


def _validate_coveragepy_command(name: str, command: str) -> None:
    # Subset coverage under a suite with cross-file pollution is run-order-dependent;
    # a full-suite lane refuses positional narrowing. Scoped suites opt out with
    # full_suite = false, an explicit and reviewable decision.
    tokens = _shell_words(command)
    if "pytest" not in " ".join(tokens):
        return
    for tok in _narrowing_arguments(_tokens_after_pytest(tokens)):
        raise ConfigError(
            f"lane {name!r}: positional argument {tok!r} narrows a full-suite coverage run; "
            f"drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), "
            f"or set full_suite = false deliberately")


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
    "-c", "--config", "--dir", "--environment", "--exclude", "--outputFile",
    "--pool", "--project", "--reporter", "--root", "--shard",
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
    # the combination is a config error, not a runtime surprise.
    tokens = _shell_words(command)
    if not _asks_for_coverage(tokens):
        return
    for i in range(_first_filter_position(tokens), len(tokens)):
        if _is_file_filter(tokens[i], tokens[i - 1]):
            raise ConfigError(
                f"lane {name!r}: file filter {tokens[i]!r} combined with --coverage silently narrows "
                f"the coverage include set; drop the filter or use a dedicated config")


class Config(NamedTuple):
    target: int = DEFAULT_TARGET

    @property
    def scope_targets(self) -> dict[str, int]:
        """Every scope's effective ceiling: its own target or the repo default."""
        return {s.name: (s.target if s.target is not None else self.target) for s in self.scopes}

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
    mutation_command: str = ""  # the suite run once per mutant; nonzero exit = killed
    mutation_timeout_seconds: int = 300  # a mutant that loops forever counts as killed
    mutation_workers: int = 1  # >1 runs mutants in that many detached git worktrees
    diff_uncovered_max: int | None = None  # verify exit 9 past this many dead changed lines
    # A tighten claims an improvement; one commit measured twice cannot have
    # improved. Past this factor between two runs of the same commit, verify
    # holds the mark instead of tightening it.
    tighten_max_jump: float = 2.0
    debt_max_age_months: int | None = None  # ratchet report --enforce flags older marks
    repayment_min_per_30d: int | None = None  # --enforce flags a stalled burn-down
    max_parallel_lanes: int = 1  # lanes running at once; 1 = strictly serial
    analysis_workers: int = 0  # lizard pool size; 0 = one worker per core
    # Operational traps the repo learned the hard way. They lived as TOML
    # comments, which the parser drops, so no payload could ever quote them.
    notes: tuple[str, ...] = ()
    # Only the scopes that wrote one, so a scope with nothing to say costs a
    # payload no key. A plain dict, because these end up in --json output.
    scope_notes: dict[str, tuple[str, ...]] = {}


def load_config_text(text: str) -> Config:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"crapkit.toml does not parse: {exc}") from exc
    try:
        return _build_config(raw)
    except KeyError as exc:
        raise ConfigError(f"crapkit.toml is missing a required key: {exc}") from exc


def _parse_scope(row: dict) -> Scope:
    languages = tuple(row.get("languages", ()))
    unknown = set(languages) - SUPPORTED_LANGUAGES
    if unknown:
        raise ConfigError(f"unsupported language(s) {sorted(unknown)} in scope {row.get('name')!r}")
    scope_target = row.get("target")
    if scope_target is not None and (not isinstance(scope_target, int) or scope_target < 1):
        raise ConfigError(f"scope {row.get('name')!r}: target must be a positive int, got {scope_target!r}")
    return Scope(name=row["name"], paths=tuple(row["paths"]), languages=languages,
                 target=scope_target,
                 coverage_optional=bool(row.get("coverage_optional", False)))


def _notes(row: dict, where: str) -> tuple[str, ...]:
    """The `notes` list, rejected unless every entry is a string.

    A bare `notes = "..."` is the trap TOML sets: it is iterable, so it would
    load as one note per letter and every reader would print them that way.
    """
    raw = row.get("notes", [])
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ConfigError(f"{where}: notes must be a list of strings, got {raw!r}")
    return tuple(raw)


def _parse_scopes(rows) -> tuple[tuple[Scope, ...], dict[str, tuple[str, ...]]]:
    """Every [[scope]] row and its notes, off ONE walk of the rows.

    Notes hang on the same rows the scopes come from, so collecting them in a
    second pass would re-read and re-validate every row for nothing — and, when
    the rows arrive as an iterator, would find none of them.
    """
    scopes: list[Scope] = []
    notes: dict[str, tuple[str, ...]] = {}
    for row in rows:
        scope = _parse_scope(row)
        scopes.append(scope)
        row_notes = _notes(row, f"scope {scope.name!r}")
        if row_notes:
            notes[scope.name] = row_notes
    return tuple(scopes), notes


def _validate_lane_command(parser: str, full_suite: bool, name: str, command: str) -> None:
    if parser == "istanbul":
        _validate_istanbul_command(name, command)
    if parser == "coveragepy" and full_suite:
        _validate_coveragepy_command(name, command)


def _parse_lane(row: dict, scope_names: set) -> Lane:
    parser = row["parser"]
    if parser not in SUPPORTED_PARSERS:
        raise ConfigError(f"lane {row.get('name')!r}: unsupported parser {parser!r}")
    lane_scopes = tuple(row.get("scopes", ()))
    unknown_scopes = set(lane_scopes) - scope_names
    if unknown_scopes:
        raise ConfigError(f"lane {row.get('name')!r} references undeclared scope(s) {sorted(unknown_scopes)}")
    full_suite = bool(row.get("full_suite", True))
    _validate_lane_command(parser, full_suite, row.get("name", "?"), row["command"])
    return Lane(name=row["name"], command=row["command"], artifact=row["artifact"],
                parser=parser, scopes=lane_scopes,
                cwd=row.get("cwd", ""), path_prefix=row.get("path_prefix", ""),
                env=tuple(sorted((str(k), str(v)) for k, v in row.get("env", {}).items())),
                full_suite=full_suite, container_ok=bool(row.get("container_ok", False)),
                results_artifact=row.get("results_artifact", ""),
                timeout_seconds=_nonneg_int(row, "timeout_seconds"),
                retries=_nonneg_int(row, "retries"),
                retest_command=row.get("retest_command", ""))


def _nonneg_int(row: dict, key: str) -> int:
    value = row.get(key, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(
            f"lane {row.get('name', '?')!r}: {key} must be a non-negative int, got {value!r}")
    return value


def _reject_shared_artifacts(lanes: list) -> None:
    seen_artifacts: dict[str, str] = {}
    for lane in lanes:
        for artifact in filter(None, (lane.artifact, lane.results_artifact)):
            if artifact in seen_artifacts and seen_artifacts[artifact] != lane.name:
                raise ConfigError(
                    f"lanes {seen_artifacts[artifact]!r} and {lane.name!r} share the artifact path "
                    f"{artifact!r}; reused paths cross-attribute coverage under --reuse-artifacts")
            seen_artifacts[artifact] = lane.name


def _build_config(raw: dict) -> Config:
    scope_rows = raw.get("scope", [])
    if not scope_rows:
        raise ConfigError("crapkit.toml declares no [[scope]] — nothing to analyze")
    scopes, scope_notes = _parse_scopes(scope_rows)
    scope_names = {s.name for s in scopes}
    lanes = [_parse_lane(row, scope_names) for row in raw.get("lane", [])]
    _reject_shared_artifacts(lanes)
    main = raw.get("crapkit", {})
    return Config(
        target=int(main.get("target", DEFAULT_TARGET)),
        scopes=scopes,
        exclude_globs=tuple(raw.get("exclude", {}).get("globs", ())),
        max_file_bytes=_optional_int(raw.get("exclude", {}), "max_file_bytes"),
        churn_window_months=int(main.get("churn_window_months", 12)),
        worklist_floor=int(main.get("worklist_floor", 5)),
        worklist_top=int(main.get("worklist_top", 50)),
        lanes=tuple(lanes),
        ratchet_file=main.get("ratchet_file", "crapkit-ratchet.tsv"),
        alert_command=main.get("alert_command", ""),
        scoped_tests=tuple(sorted((str(k), str(v)) for k, v in main.get("scoped_tests", {}).items())),
        mutation_command=main.get("mutation_command", ""),
        mutation_timeout_seconds=int(main.get("mutation_timeout_seconds", 300)),
        mutation_workers=_positive_int(main, "mutation_workers", 1),
        diff_uncovered_max=_optional_int(main, "diff_uncovered_max"),
        tighten_max_jump=_factor(main, "tighten_max_jump", 2.0),
        debt_max_age_months=_optional_int(main, "debt_max_age_months"),
        repayment_min_per_30d=_optional_int(main, "repayment_min_per_30d"),
        max_parallel_lanes=_bounded_int(main, "max_parallel_lanes", default=1, minimum=1),
        analysis_workers=_bounded_int(main, "analysis_workers", default=0, minimum=0),
        notes=_notes(main, "[crapkit]"),
        scope_notes=scope_notes,
    )


def _bounded_int(main: dict, key: str, *, default: int, minimum: int) -> int:
    value = main.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ConfigError(f"{key} must be an int >= {minimum}, got {value!r}")
    return value


def _factor(main: dict, key: str, default: float) -> float:
    """A ratio knob: any number at or above 1. Below 1 would refuse a tighten
    where nothing moved, which stops the ratchet falling and says nothing."""
    value = main.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1:
        raise ConfigError(f"{key} must be a number >= 1, got {value!r}")
    return float(value)


def _positive_int(main: dict, key: str, default: int) -> int:
    value = main.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConfigError(f"{key} must be a positive int, got {value!r}")
    return value


def _optional_int(main: dict, key: str) -> int | None:
    value = main.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{key} must be a non-negative int, got {value!r}")
    return value
