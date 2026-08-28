"""crapkit.toml parsing. Pure: text in, Config out; every rejection is a ConfigError (exit 3)."""
from __future__ import annotations

import tomllib
from typing import NamedTuple

from .errors import ConfigError

SUPPORTED_LANGUAGES = frozenset({"typescript", "tsx", "javascript", "python", "swift",
                                 "go", "rust", "shell"})
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


def _validate_coveragepy_command(name: str, command: str) -> None:
    # Subset coverage under a suite with cross-file pollution is run-order-dependent;
    # a full-suite lane refuses positional narrowing. Scoped suites opt out with
    # full_suite = false, an explicit and reviewable decision.
    tokens = command.split()
    if "pytest" not in " ".join(tokens):
        return
    seen_pytest = False
    for tok in tokens:
        if tok.endswith("pytest"):
            seen_pytest = True
            continue
        if seen_pytest and not tok.startswith("-"):
            raise ConfigError(
                f"lane {name!r}: positional argument {tok!r} narrows a full-suite coverage run; "
                f"drop it or set full_suite = false deliberately")


def _asks_for_coverage(tokens: list[str]) -> bool:
    return any(t == "--coverage" or t.startswith("--coverage") for t in tokens)


def _first_filter_position(tokens: list[str]) -> int:
    # Only tokens after the test runner's `run` subcommand can be positional file
    # filters; the runner script path itself (node scripts/run-vitest.mjs ...) is not.
    return tokens.index("run") + 1 if "run" in tokens else 0


def _is_file_filter(tok: str, preceding: str) -> bool:
    if tok.startswith("-"):
        return False  # a flag (e.g. --coverage.exclude=**/*.test.ts) is never a positional filter
    return tok.endswith(_SOURCE_SUFFIXES) and preceding != "--config"


def _validate_istanbul_command(name: str, command: str) -> None:
    # The measured vitest trap: any file filter passed beside --coverage silently
    # narrows the coverage include set. A lane command is fixed configuration, so
    # the combination is a config error, not a runtime surprise.
    tokens = command.split()
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
