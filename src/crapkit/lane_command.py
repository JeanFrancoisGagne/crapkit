"""How a configured lane starts, and how its command reads.

A lane command runs under the shell, from `root / cwd`, with its `[lane.env]`
pairs merged over the process environment. lanes.py starts the lane and its
flake retest from `launch_spec`, and doctor resolves words and starts its
probes from the same spec: a word looked up from another directory, or on
another PATH, answers for a child the lane never starts.

The command line itself is read here too, the way the shell that runs it
reads it: `shell_words` and `shell_segments` give what that shell hands each
program, and `command_steps` goes one level further, into the script a `bash
-c` or `sh -c` step runs, read with sh's rules on every OS. The full-suite
guard, doctor's pytest-cov probe and data-file check, and the lanes hint all
read those steps, so none of them can see a runner the others miss. A payload
sh cannot split is marked, and doctor WARNs from the mark.

It also owns the launcher token (`{python}`, `{python:DIR}`) init writes into
a lane's command: config expands it once, as it builds the Lane, so all six
readers of `Lane.command` see this OS's launcher.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePath
from typing import NamedTuple

from .repopath import declared

_WINDOWS = os.name == "nt"


# The python a committed crapkit.toml names, spelled so every OS can read it.
# init wrote the launcher of the OS it ran on, `.venv\Scripts\python.exe` or
# `.venv/bin/python`, and the file is committed: the other OS's checkout, with a
# venv of its own, failed every lane. `{python}` reads as `python` on Windows and
# `python3` elsewhere (an Ubuntu without python-is-python3 has no `python`), and
# `{python:DIR}` as the launcher inside the venv at DIR. No backslash and no
# quote, so it survives the TOML basic string init writes it into, and no name
# `prepare_template` fills in, so `{files}` beside it is untouched.
_LAUNCHER_TOKEN = re.compile(r"\{python(?::([^{}\s]+))?\}")


def python_token(venv: str = "") -> str:
    """The launcher token init writes: `{python}`, or `{python:DIR}` for the
    venv at DIR, root-relative with `/` between directories."""
    return f"{{python:{venv}}}" if venv else "{python}"


def expand_launchers(command: str, windows: bool = _WINDOWS) -> str:
    """The command with each launcher token replaced by the launcher of the OS
    reading the file. Anything else, a bare `python` included, stays as
    written. The loader calls this before the full-suite guard reads the
    command, so every reader after it sees the command the shell will run."""
    return _LAUNCHER_TOKEN.sub(lambda token: _launcher(token.group(1), windows), command)


# Keyed by `windows`: the name `{python}` reads as, and the separator and
# layout of the launcher inside a venv.
_BARE_PYTHON = {True: "python", False: "python3"}
_VENV_LAYOUT = {True: ("\\", "Scripts", "python.exe"), False: ("/", "bin", "python")}


def _launcher(venv: str | None, windows: bool) -> str:
    """The venv's DIR is a path crapkit.toml holds, read by repopath's declared
    entry: `\\` separates and a leading `./` names nothing, on every OS."""
    if venv is None:
        return _BARE_PYTHON[windows]
    separator, *layout = _VENV_LAYOUT[windows]
    return separator.join([*filter(None, declared(venv, "file").split("/")), *layout])


def _names(key: str, name: str, windows: bool) -> bool:
    """Is `key` the variable the child reads as `name`? The merge is a plain
    dict update, so on POSIX a lane declaring `Path` adds a second variable and
    leaves `PATH` alone. Only on Windows, where the env block is one
    case-insensitive namespace, does `Path` carry the value the child reads."""
    return key == name or (windows and key.upper() == name)


# A Python child below 3.15 writes a pipe or a file in the ANSI code page on
# Windows and in the locale's encoding on POSIX, and crapkit reads a lane's log
# and quotes it as UTF-8: an accented letter came back as U+FFFD, and a test
# printing an emoji under `pytest -s` raised in the lane and nowhere else.
_STDIO = "PYTHONIOENCODING"


def child_environment(pairs: tuple[tuple[str, str], ...] = (), extra: dict[str, str] | None = None,
                      windows: bool = _WINDOWS) -> dict[str, str]:
    """The environment a lane or mutation child sees: the process environment,
    then PYTHONIOENCODING=utf-8 unless `pairs` names that variable, then
    `pairs`, then `extra`. An inherited PYTHONIOENCODING is replaced, so the
    output crapkit reads back is UTF-8 whatever shell ran crapkit."""
    return {**os.environ, **_stdio(pairs, windows), **dict(pairs), **(extra or {})}


def _stdio(pairs: tuple[tuple[str, str], ...], windows: bool) -> dict[str, str]:
    """UTF-8 stdio for a Python child, or nothing when the lane chose."""
    chosen = any(_names(key, _STDIO, windows) for key, _ in pairs)
    return {} if chosen else {_STDIO: "utf-8"}


class LaunchSpec(NamedTuple):
    """Where a lane's command starts, and what it merges over the environment.

    Hashable on purpose: a probe that asks this child a question keys its memo
    on the spec, so two lanes share an answer only when their children start
    the same way.
    """
    cwd: Path
    env: tuple[tuple[str, str], ...] = ()

    def child_env(self, extra: dict[str, str] | None = None,
                  windows: bool = _WINDOWS) -> dict[str, str]:
        """The environment this lane's child sees: `child_environment` over the
        lane's `[lane.env]` pairs."""
        return child_environment(self.env, extra, windows)

    def popen_kwargs(self, extra: dict[str, str] | None = None) -> dict:
        """The cwd and env to start the child with, as `procs.run_bounded` takes them."""
        return {"cwd": self.cwd, "env": self.child_env(extra)}

    def path(self, windows: bool = _WINDOWS) -> str | None:
        """The PATH the lane declares for its child, or None when it declares
        none and the process PATH is the whole answer. A lane that ships its
        own toolchain through `[lane.env] PATH` runs a runner this process
        cannot see on its own PATH."""
        return next((value for key, value in self.env if _names(key, "PATH", windows)), None)

    def resolve(self, word: str, windows: bool = _WINDOWS) -> str | None:
        r"""Where the child's shell finds this word, or None when it finds nothing.

        Nothing here reads the directory this process stands in. A word
        carrying a separator is a path, and the shell reads it from the
        directory the lane runs in: which() read it from this process's
        directory, so the `.venv\Scripts\python.exe` init writes resolved from
        the repo root and from nowhere else. A bare word is cmd.exe's own search
        on Windows, which looks in the lane's directory before PATH, and on
        POSIX sh's, which reads PATH alone: the lane's PATH when it declares
        one. which() on Windows looked in this process's directory first, so a
        bare `runcov` beside the lane resolved from the root and not from below.
        """
        if os.sep in word or "/" in word:
            candidate = self.cwd / word
            return str(candidate) if candidate.is_file() else None
        if windows:
            return self._cmd_search(word)
        return shutil.which(word, path=self.path(windows))

    def _cmd_env(self) -> dict[str, str]:
        """The child's environment as cmd.exe reads it: one case-insensitive
        namespace, where the lane's `Path` is the process's `PATH`."""
        return {key.upper(): value for key, value in self.child_env().items()}

    def _cmd_directories(self, env: dict[str, str]) -> list[Path]:
        """Where cmd.exe looks for a bare word, in order: the directory it
        starts in, unless the child's environment sets
        NoDefaultCurrentDirectoryInExePath, then each PATH entry. A relative
        entry, `.` included, is read from that same directory, as cmd.exe reads
        it, and an empty one names nothing."""
        here = [] if _NO_CWD_SEARCH in env else [self.cwd]
        entries = (entry.strip('"') for entry in env.get("PATH", "").split(";"))
        return here + [self.cwd / entry for entry in entries if entry]

    def _cmd_search(self, word: str) -> str | None:
        """The file cmd.exe starts for a bare word, or None when it finds none."""
        env = self._cmd_env()
        names = _cmd_names(word, env.get("PATHEXT") or _CMD_PATHEXT)
        return _first_file(self._cmd_directories(env), names)


# cmd.exe's defaults, used when the child's environment does not say otherwise.
_CMD_PATHEXT = ".COM;.EXE;.BAT;.CMD"
_NO_CWD_SEARCH = "NODEFAULTCURRENTDIRECTORYINEXEPATH"


def _cmd_names(word: str, pathext: str) -> list[str]:
    """The file names cmd.exe tries for a bare word: the word itself when it
    already ends in one of PATHEXT's extensions, else the word with each
    extension added, in PATHEXT's order."""
    extensions = [ext for ext in pathext.upper().split(";") if ext]
    if PurePath(word).suffix.upper() in extensions:
        return [word]
    return [word + ext for ext in extensions]


def _first_file(directories: list[Path], names: list[str]) -> str | None:
    """The first directory, then the first name within it, that is a file."""
    found = (directory / name for directory in directories for name in names)
    return next((str(path) for path in found if path.is_file()), None)


def launch_spec(root: Path, lane) -> LaunchSpec:
    """How `lane` starts under `root`: the directory lanes.py hands the shell,
    and the `[lane.env]` pairs it merges. A config Lane and the LaneSpec init
    writes both carry the two fields."""
    return LaunchSpec(root / lane.cwd if lane.cwd else root, tuple(lane.env))


# --- how the line reads ----------------------------------------------------------
#
# Lane commands run under shell=True: sh on POSIX, cmd.exe on Windows. The two
# read a command line differently, and every reader has to read it like the one
# that will run it, or the guard accepts a lane cmd.exe breaks and refuses one
# it runs. Which shell that is stays `config.SHELL_IS_CMD`, the one flag admin,
# verifying and the tests that pin a shell set; it is read at call time, so a
# pinned shell reaches every reader here too.


def _reads_as_cmd(cmd: bool | None) -> bool:
    """The dialect a caller named, else the shell lane commands run under."""
    if cmd is not None:
        return cmd
    from .config import SHELL_IS_CMD

    return SHELL_IS_CMD


class _Token(NamedTuple):
    """One word as the shell hands it on. `built` is True when quoting or an
    escape built it: such a word is an argument and never the shell's own
    syntax, however it is spelled (`"&&"` and cmd.exe's `^&` both reach the
    program as text). `raw` is the word as the line spells it, quotes kept:
    cmd.exe hands a program the line itself, and bash.exe splits it again with
    its own rules."""
    text: str
    built: bool
    raw: str


_EMPTY = _Token("", False, "")


def shell_words(command: str, cmd: bool | None = None) -> list[str]:
    """The words the shell hands the runner. `-m "not live and not perf"` is one
    argument there and must be one token here: a whitespace split reads four
    positionals into it. A command the shell would refuse (a quote that never
    closes) gets the whitespace read instead: a rough lint beats a crash at
    config load."""
    return [token.text for token in _shell_tokens(command, _reads_as_cmd(cmd))]


def _shell_tokens(command: str, cmd: bool) -> list[_Token]:
    """The words, each with how it was built. The whitespace fallback knows no
    quoting, so it builds nothing."""
    try:
        return _cmd_tokens(command) if cmd else _sh_tokens(command)
    except ValueError:
        return [_Token(word, False, word) for word in command.split()]


def _sh_tokens(command: str) -> list[_Token]:
    """sh's reading, from shlex, plus the flag. shlex outside posix mode leaves
    the quotes and backslashes in the word, so a word whose two spellings differ
    is one sh built. When the two readings disagree on where the words are
    (`a\\ b` is one word to posix mode and two outside it), nothing is called
    built: the operator split then reads exactly what 0.4.4 read. Raises
    ValueError on a quote that never closes."""
    words = shlex.split(command)
    raw = shlex.split(command, posix=False)
    if len(raw) != len(words):
        raw = words
    return [_Token(word, word != spelling, spelling) for word, spelling in zip(words, raw)]


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


def _cmd_tokens(command: str) -> list[_Token]:
    """cmd.exe's reading: a double quote opens or closes a quoted run wherever it
    sits, so `--cov-report=json:"a b\\py.json"` is one word and the quotes
    themselves are dropped; a single quote is an ordinary character, so
    `'not live'` is two words; a backslash separates path components and escapes
    nothing. A caret-escaped quote still opens the run: cmd.exe hands the quote
    itself to the program, and the program's own reader honours it. A quote that
    never closes raises ValueError."""
    words: list[_Token] = []
    word, in_quote = _EMPTY, False
    for char, escaped in _uncaret(command):
        if _ends_the_word(char, in_quote):
            words, word = words + _kept(word), _EMPTY
        else:
            in_quote ^= char == '"'
            word = _grown(word, char, escaped)
    if in_quote:
        raise ValueError(f"no closing quotation: {command}")
    return words + _kept(word)


def _grown(word: _Token, char: str, escaped: bool) -> _Token:
    """The word with one more character. A double quote builds the word and
    stays only in its raw spelling; any other character joins the text."""
    if char == '"':
        return _Token(word.text, True, word.raw + char)
    return _Token(word.text + char, word.built or escaped, word.raw + char)


# What breaks one word from the next, outside a quoted run: what cmd.exe splits
# on and what 0.4.4's shlex had. str.isspace() is wider: U+00A0, U+000B, U+000C
# and the unicode separators are all true, and a non-breaking space pasted out
# of rendered docs stays inside the word cmd.exe hands the runner.
_WORD_BREAKS = " \t\r\n"


def _ends_the_word(char: str, in_quote: bool) -> bool:
    """A word break separates words only outside a quoted run."""
    return char in _WORD_BREAKS and not in_quote


def _kept(word: _Token) -> list[_Token]:
    """The word so far. A run of whitespace ends no word, but a pair of quotes
    writes one: cmd.exe hands the program the empty argument in `-k "" tests`,
    and dropping it moved every later token one place left, so the flag in
    front swallowed a path that is really a positional."""
    return [word] if word.text or word.built else []


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
    """One argv per command on the line, read by the shell that will run it. A
    `bash -c` step stays one argv here, its payload a single word: this is what
    the shell starts, and `command_steps` is what finally runs."""
    cmd = _reads_as_cmd(cmd)
    return [[token.text for token in segment]
            for segment in _segments(_shell_tokens(command, cmd), cmd)]


def _segments(tokens: list[_Token], cmd: bool) -> list[list[_Token]]:
    """The tokens with the shell's plumbing taken out, one list per command."""
    tokens = _drop_redirections(tokens)
    if not cmd:
        tokens = _split_semicolons(tokens)
    return _command_segments(tokens, _separators(cmd))


def _separators(cmd: bool) -> frozenset[str]:
    """What ends one command and starts the next. sh adds ';'; to cmd.exe it is
    an ordinary character the program is handed (verified argv for
    `--cov=src; echo done`: ["--cov=src;", "echo", "done"])."""
    return _SHELL_OPERATORS if cmd else _SHELL_OPERATORS | {";"}


def _split_semicolons(tokens: list[_Token]) -> list[_Token]:
    """sh's ';' as its own word. shlex leaves it stuck to the word in front
    (`--cov=src;`), so the first command read clean while the next command's
    words landed in its argv, and the lane was refused naming a program pytest
    never sees. A quoted ';' is an argument and stays where it is."""
    out: list[_Token] = []
    for token in tokens:
        if token.built or not token.text.endswith(";"):
            out.append(token)
        else:
            out += _kept(_Token(token.text[:-1], False, token.raw[:-1])) + [_Token(";", False, ";")]
    return out


def _drop_redirections(tokens: list[_Token]) -> list[_Token]:
    """The words left once the shell has taken its own plumbing. Reading `>` as
    a word refused a lane naming a positional the program is never handed."""
    kept: list[_Token] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif _redirects(token):
            skip = _takes_the_next_word(token.text)
        else:
            kept.append(token)
    return kept


def _redirects(token: _Token) -> bool:
    """Is this the shell's plumbing? A quoted `">"` is an argument: quoting is
    how an operator is written when the program is meant to get it."""
    return not token.built and _REDIRECTION.match(token.text) is not None


def _takes_the_next_word(word: str) -> bool:
    """`> run.log` opens the word behind it; `2>run.log` and `2>&1` carry their
    target, and the word behind them is the program's again."""
    return _REDIRECTION.fullmatch(word) is not None


def _command_segments(tokens: list[_Token], separators: frozenset[str]) -> list[list[_Token]]:
    """One list per command on the line. Only the segment a runner sits in is
    that runner's argv: reading `pytest --cov && coverage json` flat called
    `coverage` a positional pytest is never handed. A word quoting or an escape
    built is an argument whatever it spells, so `-k "a && b"`, `"&&"` and
    cmd.exe's `^&` all stay inside their segment."""
    segments: list[list[_Token]] = [[]]
    for token in tokens:
        if token.text in separators and not token.built:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


# --- what finally runs: into a `bash -c` or `sh -c` payload ----------------------


class Step(NamedTuple):
    """One program the line starts and the words it is handed. `cmd` is True
    when cmd.exe's rules read them, which leave a ' inside a word; a payload's
    steps are always sh's."""
    words: tuple[str, ...]
    cmd: bool


class CommandSteps(NamedTuple):
    """Every step a lane command runs, a `bash -c` or `sh -c` payload read as
    the commands it holds. `unreadable` marks each step whose payload sh cannot
    split (a quote that never closes), spelled as the line writes it: nothing
    inside it was read, and doctor says so."""
    steps: tuple[Step, ...]
    unreadable: tuple[str, ...] = ()


def command_steps(command: str, cmd: bool | None = None) -> CommandSteps:
    """The steps the line runs, read by the shell that will run it and then, for
    a `bash -c` or `sh -c` step, by sh's rules for the script it hands on. The
    guard judged `bash -c "pytest tests/unit"` as three words with no pytest in
    them, and doctor's probe saw no python to ask."""
    cmd = _reads_as_cmd(cmd)
    return _steps(_shell_tokens(command, cmd), cmd)


def _steps(tokens: list[_Token], cmd: bool) -> CommandSteps:
    """Each command on the line, a payload read as the steps it holds."""
    return _joined([_descended(segment, cmd) for segment in _segments(tokens, cmd)])


def _joined(parts: list[CommandSteps]) -> CommandSteps:
    return CommandSteps(sum((part.steps for part in parts), ()),
                        sum((part.unreadable for part in parts), ()))


def _descended(segment: list[_Token], cmd: bool) -> CommandSteps:
    """One command: its payload's steps when it is a shell running a script,
    else the command as read. A payload sh cannot split leaves the command as
    read, marked."""
    as_read = CommandSteps((Step(tuple(token.text for token in segment), cmd),))
    try:
        script = _payload(segment, cmd)
        return as_read if script is None else _steps(_sh_tokens(script), False)
    except ValueError:
        return as_read._replace(unreadable=(" ".join(token.raw for token in segment),))


# The shells whose `-c` script is sh text, matched against the last part of the
# word: `bash`, `sh`, `/usr/bin/bash`, `C:\Program Files\Git\bin\bash.exe`.
_SH_NAME = re.compile(r"(?:ba)?sh(?:\.exe)?", re.IGNORECASE)


def _payload(segment: list[_Token], cmd: bool) -> str | None:
    """The script a `bash -c` or `sh -c` step hands its shell, or None when the
    step runs no such script."""
    if not segment or not _SH_NAME.fullmatch(re.split(r"[\\/]", segment[0].text)[-1]):
        return None
    return _script_of(_shell_argv(segment[1:], cmd))


def _shell_argv(rest: list[_Token], cmd: bool) -> list[str]:
    """The arguments the shell program is handed. Under sh they are the words sh
    read. cmd.exe hands bash.exe (Git for Windows, MSYS2, Cygwin) the line as
    written, and bash.exe splits it with sh's quote rules, ' included: that is
    how `bash -c 'pytest tests'` reaches bash as one script under cmd.exe.
    Raises ValueError when those rules find a quote that never closes."""
    if not cmd:
        return [token.text for token in rest]
    return shlex.split(" ".join(token.raw for token in rest))


# The shell options that read the next word as their value, so the value of
# `-o pipefail` is never taken for the script.
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O", "--rcfile", "--init-file"})


def _script_of(argv: list[str]) -> str | None:
    """The script `-c` names: the first operand past the options, once an
    option cluster holding `c` is among them (`-c`, `-lc`, `-e -c`, `-c --`).
    None when the shell runs a file or reads its input instead."""
    at = _first_operand(argv)
    operands = argv[at:]
    if operands[:1] == ["--"]:
        operands = operands[1:]
    runs_script = any(map(_names_c, argv[:at]))
    return operands[0] if runs_script and operands else None


def _first_operand(argv: list[str]) -> int:
    """Where the shell's options end: the first word that is neither an option
    nor an option's value. `--` ends them too, and stands there."""
    at = 0
    while at < len(argv) and _is_option(argv[at]):
        at += 2 if argv[at] in _SHELL_VALUE_OPTIONS else 1
    return at


def _is_option(word: str) -> bool:
    return word.startswith(("-", "+")) and word != "--"


def _names_c(word: str) -> bool:
    """A short option cluster holding `c`: `-c`, `-lc`, `-ec`."""
    return word.startswith("-") and not word.startswith("--") and "c" in word[1:]


def first_word(command: str) -> str:
    """The word the shell will try to start, read the way that shell reads the
    line: a quoted interpreter path stays one word, where a whitespace split
    would break it at its space. "" when the line holds no word at all."""
    words = shell_words(command)
    return words[0] if words else ""


def pytest_step(command: str, cmd: bool | None = None) -> list[str]:
    """The one command on the line that runs pytest, or nothing when none does.
    A lane chains steps (`coverage run -m pytest --cov=pylib && coverage json`),
    and only the step holding pytest says anything about pytest-cov. A `bash -c`
    payload is read down to the step inside it that runs pytest."""
    for step in command_steps(command, cmd).steps:
        if any(token.endswith("pytest") for token in step.words):
            return list(step.words)
    return []


# The names a python answers to, matched against the last segment of the word:
# `python`, `python3`, `py`, `python3.12`, `C:/Program Files/py/python.exe`.
# `-c "import pytest_cov"` and `-m pip` are a python's flags and nobody else's:
# `coverage -c` takes a config file and rejects the code.
_PYTHON_NAME = re.compile(r"py(thon3?(\.\d+)?)?(\.exe)?$", re.IGNORECASE)


def is_python(word: str) -> bool:
    """Does this word name a python interpreter? A bare name or a path, with or
    without a version suffix."""
    return _PYTHON_NAME.fullmatch(PurePath(word).name) is not None


def pytest_python(command: str, cmd: bool | None = None) -> str | None:
    """The python heading the step that runs pytest, or None when no python does.

    The head of that step, not the command's first word: `cd web && python -m
    pytest --cov` runs pytest with `python`. And it has to be a python.
    `coverage run -m pytest` names no interpreter at all, and a bare `pytest
    --cov` starts with pytest. An environment manager heads its step for the
    same reason: `uv run` and its siblings create or sync the project
    environment before running anything, so nothing may be asked of it, and
    `uv -m pip install pytest-cov` is not a command.
    """
    step = pytest_step(command, cmd)
    return step[0] if step and is_python(step[0]) else None


def pytest_head(command: str) -> str:
    """The word in front of pytest: the manager or tool the lane runs pytest
    through when no python does. A lane that chains steps runs pytest after
    `&&`, so this is the step's head, not the command's first word; the first
    word only when no step names pytest at all."""
    step = pytest_step(command)
    return step[0] if step else first_word(command)
