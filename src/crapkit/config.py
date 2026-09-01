"""crapkit.toml parsing. Pure: text in, Config out; every rejection is a ConfigError (exit 3)."""
from __future__ import annotations

import os
import re
import shlex
import tomllib
from collections.abc import Iterator
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
    # Kill the lane when its log has not grown for this many seconds. A total
    # deadline cannot bound a suite that hangs at 0% CPU without also bounding
    # the slow runs it cannot tell apart from one. 0 = no progress watch.
    no_progress_seconds: int = 0
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


# Lane commands run under shell=True: sh on POSIX, cmd.exe on Windows. The two
# read a command line differently, and the guard has to read it like the one
# that will run it, or it accepts a lane cmd.exe breaks and refuses one it runs.
SHELL_IS_CMD = os.name == "nt"


def shell_words(command: str, cmd: bool | None = None) -> list[str]:
    """The words the shell hands the runner. `-m "not live and not perf"` is one
    argument there and must be one token here — a whitespace split reads four
    positionals into it. A command the shell would refuse (a quote that never
    closes) gets the whitespace read instead: a rough lint beats a crash at
    config load."""
    return [word for word, _ in _shell_tokens(command, cmd)]


def _shell_tokens(command: str, cmd: bool | None = None) -> list[tuple[str, bool]]:
    """The words, each with a flag: True when quoting or an escape built it. A
    built word is an argument and never the shell's own syntax, however it is
    spelled — `"&&"` and cmd.exe's `^&` both reach the program as the text `&&`
    and `&`. The whitespace fallback knows no quoting, so it builds nothing."""
    cmd = SHELL_IS_CMD if cmd is None else cmd
    try:
        return _cmd_tokens(command) if cmd else _sh_tokens(command)
    except ValueError:
        return [(word, False) for word in command.split()]


def _sh_tokens(command: str) -> list[tuple[str, bool]]:
    """sh's reading, from shlex, plus the flag. shlex outside posix mode leaves
    the quotes and backslashes in the word, so a word whose two spellings differ
    is one sh built. When the two readings disagree on where the words are
    (`a\\ b` is one word to posix mode and two outside it), nothing is called
    built: the operator split then reads exactly what 0.4.4 read."""
    words = shlex.split(command)
    raw = shlex.split(command, posix=False)
    if len(raw) != len(words):
        raw = words
    return [(word, word != spelling) for word, spelling in zip(words, raw)]


def _uncaret(command: str) -> list[tuple[str, bool]]:
    """cmd.exe's escape. Outside a quoted run `^` is dropped and the character
    behind it is handed on untouched, so `-k ^"not slow^"` reaches the runner as
    `-k "not slow"`. Inside a quoted run cmd.exe leaves the caret alone: `-k
    "a^b"` reaches the runner with its caret, so stripping unconditionally would
    misread the two spellings cmd.exe passes through. Each character carries a
    flag: True when a caret handed it on, which makes it text, not syntax."""
    kept: list[tuple[str, bool]] = []
    chars = iter(command)
    in_quote = False
    for char in chars:
        if char == "^" and not in_quote:
            kept += _escaped(chars)
            continue
        if char == '"':
            in_quote = not in_quote
        kept.append((char, False))
    return kept


def _escaped(chars: Iterator[str]) -> list[tuple[str, bool]]:
    """The character a caret hands on, marked as text. A caret at the end of the
    line escapes nothing: cmd.exe asks for another line, and a lane command is
    one line."""
    char = next(chars, "")
    return [(char, True)] if char else []


def _cmd_tokens(command: str) -> list[tuple[str, bool]]:
    """cmd.exe's reading: a double quote opens or closes a quoted run wherever it
    sits, so `--cov-report=json:"a b\\py.json"` is one word and the quotes
    themselves are dropped; a single quote is an ordinary character, so
    `'not live'` is two words; a backslash separates path components and escapes
    nothing. A caret-escaped quote still opens the run: cmd.exe hands the quote
    itself to the program, and the program's own reader honours it. A quote that
    never closes raises, and shell_words falls back."""
    words: list[tuple[str, bool]] = []
    word, built = "", False
    in_quote = False
    for char, escaped in _uncaret(command):
        if char == '"':
            in_quote, built = not in_quote, True
        elif _ends_the_word(char, in_quote):
            words += _kept(word, built)
            word, built = "", False
        else:
            word, built = word + char, built or escaped
    if in_quote:
        raise ValueError(f"no closing quotation: {command}")
    return words + _kept(word, built)


# What breaks one word from the next, outside a quoted run: what cmd.exe splits
# on and what 0.4.4's shlex had. str.isspace() is wider — U+00A0, U+000B, U+000C
# and the unicode separators are all true — and a non-breaking space pasted out
# of rendered docs stays inside the word cmd.exe hands the runner.
_WORD_BREAKS = " \t\r\n"


def _ends_the_word(char: str, in_quote: bool) -> bool:
    """A word break separates words only outside a quoted run."""
    return char in _WORD_BREAKS and not in_quote


def _kept(word: str, built: bool) -> list[tuple[str, bool]]:
    """The word so far. A run of whitespace ends no word, but a pair of quotes
    writes one: cmd.exe hands the program the empty argument in `-k "" tests`,
    and dropping it moved every later token one place left, so the flag in
    front swallowed a path that is really a positional."""
    return [(word, built)] if word or built else []


# The operators that end one command and start another. sh and cmd.exe share
# all four, and a lane that chains a report or an upload step after the run is
# an ordinary shape (`coverage run -m pytest && coverage json`).
_SHELL_OPERATORS = frozenset({"&&", "||", "&", "|"})


# A redirection and its target: `>`, `>>`, `2>`, `2>&1`, `>nul`. Both shells
# keep them, so neither reaches the program's argv (verified cmd.exe argv:
# `--cov=src 2>&1` -> ["--cov=src"]). Not an operator: a redirection belongs to
# the command it sits in and starts no new one.
_REDIRECTION = re.compile(r"\d*[<>]{1,2}")


def shell_segments(command: str, cmd: bool | None = None) -> list[list[str]]:
    """One argv per command on the line, read by the shell that will run it."""
    cmd = SHELL_IS_CMD if cmd is None else cmd
    tokens = _drop_redirections(_shell_tokens(command, cmd))
    if not cmd:
        tokens = _split_semicolons(tokens)
    return _command_segments(tokens, _separators(cmd))


def _separators(cmd: bool) -> frozenset[str]:
    """What ends one command and starts the next. sh adds ';'; to cmd.exe it is
    an ordinary character the program is handed (verified argv for
    `--cov=src; echo done`: ["--cov=src;", "echo", "done"])."""
    return _SHELL_OPERATORS if cmd else _SHELL_OPERATORS | {";"}


def _split_semicolons(tokens: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """sh's ';' as its own word. shlex leaves it stuck to the word in front
    (`--cov=src;`), so the first command read clean while the next command's
    words landed in its argv, and the lane was refused naming a program pytest
    never sees. A quoted ';' is an argument and stays where it is."""
    out: list[tuple[str, bool]] = []
    for word, built in tokens:
        if built or not word.endswith(";"):
            out.append((word, built))
        else:
            out += _kept(word[:-1], False) + [(";", False)]
    return out


def _drop_redirections(tokens: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    """The words left once the shell has taken its own plumbing. Reading `>` as
    a word refused a lane naming a positional the program is never handed."""
    kept: list[tuple[str, bool]] = []
    skip = False
    for word, built in tokens:
        if skip:
            skip = False
        elif _redirects(word, built):
            skip = _takes_the_next_word(word)
        else:
            kept.append((word, built))
    return kept


def _redirects(word: str, built: bool) -> bool:
    """Is this the shell's plumbing? A quoted `">"` is an argument: quoting is
    how an operator is written when the program is meant to get it."""
    return not built and _REDIRECTION.match(word) is not None


def _takes_the_next_word(word: str) -> bool:
    """`> run.log` opens the word behind it; `2>run.log` and `2>&1` carry their
    target, and the word behind them is the program's again."""
    return _REDIRECTION.fullmatch(word) is not None


def _command_segments(tokens: list[tuple[str, bool]],
                      separators: frozenset[str]) -> list[list[str]]:
    """One list per command on the line. Only the segment a runner sits in is
    that runner's argv: reading `pytest --cov && coverage json` flat called
    `coverage` a positional pytest is never handed. A word quoting or an escape
    built is an argument whatever it spells, so `-k "a && b"`, `"&&"` and
    cmd.exe's `^&` all stay inside their segment."""
    segments: list[list[str]] = [[]]
    for word, built in tokens:
        if word in separators and not built:
            segments.append([])
        else:
            segments[-1].append(word)
    return segments


def _quote_hint(command: str) -> str:
    """The usual reason a Windows lane trips the guard: a value in single
    quotes, which cmd.exe hands the runner one word per space."""
    if SHELL_IS_CMD and "'" in command:
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


def _validate_coveragepy_command(name: str, command: str) -> None:
    # Subset coverage under a suite with cross-file pollution is run-order-dependent;
    # a full-suite lane refuses positional narrowing. Scoped suites opt out with
    # full_suite = false, an explicit and reviewable decision. Every chained
    # segment is read: a second pytest run narrows just as much as the first.
    for segment in shell_segments(command):
        _refuse_pytest_narrowing(name, command, segment)


def _refuse_pytest_narrowing(name: str, command: str, tokens: list[str]) -> None:
    """One command's argv. A segment that runs no pytest has nothing to narrow.

    Two exits, not one. A scoped suite opts out with `full_suite = false`. A
    suite that cannot collect all its testpaths in one process has no full-suite
    command to write at all, and taking the opt-out on a single narrowed lane
    leaves its other testpaths unmeasured with nothing saying so, which is why
    the message names the multi-lane pattern rather than only the flag.
    """
    for tok in _narrowing_arguments(_tokens_after_pytest(tokens)):
        raise ConfigError(
            f"lane {name!r}: positional argument '{tok}' narrows a full-suite coverage run; "
            f"drop it, attach it to the flag it belongs to (-n8, --numprocesses=8), "
            f"or set full_suite = false deliberately{_quote_hint(command)}; a suite whose "
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
    # segment is its own argv: a script path in a post-run step is that step's,
    # and a vitest run after `npm run build` is still a vitest run.
    for segment in shell_segments(command):
        _refuse_istanbul_filter(name, segment)


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


def _unrooted(raw: str) -> str:
    r"""One declared scope path spelled the way `git ls-files` spells a path.

    universe.py hoists the declared string straight into a prefix, so `./src`
    looked for `./src/...` while git emits `src/a.py`. Backslashes were already
    collapsed one layer down, which made the tool look like it normalized paths
    when it normalized one spelling of them.
    """
    path = raw.replace("\\", "/").rstrip("/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _scope_path(name, raw: str) -> str:
    """A declared path a tracked file could actually match.

    `./src` claimed nothing: the scope scored zero files, every file under it
    came back unclaimed, and neither doctor FAIL named the dot — so the reader
    was sent to declare a second scope for a path the first one already owned.
    A path that climbs above the root or names a drive can never match a
    tracked path at all, so it is refused here rather than reported later as an
    empty scope. `..` is refused as a SEGMENT, not as a prefix: `src/../etc` is
    the spelling a reader reaches for when they mean a sibling directory, and
    matching a leading `../` alone let it through into the same silent empty
    scope. A bare `.` is left exactly as it is: whether a scope can declare the
    repo root is the matcher's question, not this one's, and `doctor` already
    reports such a scope as `0 files`.
    """
    path = _unrooted(raw)
    if path == "" or ".." in path.split("/") or ":" in path:
        raise ConfigError(f"scope {name!r}: path {raw!r} can never match a tracked file — "
                          "scope paths are repo-relative, with no drive and no `..` "
                          "(docs/configuration.md)")
    return path


def _parse_scope(row: dict) -> Scope:
    languages = tuple(row.get("languages", ()))
    unknown = set(languages) - SUPPORTED_LANGUAGES
    if unknown:
        raise ConfigError(f"unsupported language(s) {sorted(unknown)} in scope {row.get('name')!r}")
    scope_target = row.get("target")
    if scope_target is not None and (not isinstance(scope_target, int) or scope_target < 1):
        raise ConfigError(f"scope {row.get('name')!r}: target must be a positive int, got {scope_target!r}")
    return Scope(name=row["name"],
                 paths=tuple(_scope_path(row.get("name"), p) for p in row["paths"]),
                 languages=languages,
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
                no_progress_seconds=_nonneg_int(row, "no_progress_seconds"),
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
