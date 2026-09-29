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
read those steps, so none of them can see a runner the others miss, and the
probe and the hint print the install line through `install_python`. A
payload sh cannot split is marked, and doctor WARNs from the mark.

It also owns the launcher token (`{python}`, `{python:DIR}`) init writes into
a lane's command: config expands it once, as it builds the Lane, so all six
readers of `Lane.command` see this OS's launcher.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
from pathlib import Path, PurePath
from typing import NamedTuple

from .invocation import interpreter_word
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


def shell_words(command: str, cmd: bool | None = None) -> list[str]:
    """The words the shell hands the programs on the line, in order. `-m "not
    live and not perf"` is one argument there and must be one token here: a
    whitespace split reads four positionals into it."""
    return [word for words in shell_segments(command, cmd) for word in words]


def shell_segments(command: str, cmd: bool | None = None) -> list[list[str]]:
    """One argv per command on the line, read by the shell that will run it. A
    command that holds no word (a redirection alone) gives no argv. A `bash -c`
    step stays one argv here, its payload a single word: this is what the shell
    starts, and `command_steps` is what finally runs."""
    return [command.argv for command in _commands(command, _reads_as_cmd(cmd))]


class _Command(NamedTuple):
    """One command on the line: the argv its program is handed, the command as
    the line spells it, and under cmd.exe the text cmd.exe hands the program
    behind its name, which bash.exe splits again with sh's quote rules."""
    argv: list[str]
    spelled: str
    rest: str = ""


def _commands(command: str, cmd: bool) -> list[_Command]:
    """The commands on the line that hold a word."""
    found = _cmd_commands(command) if cmd else _sh_commands(command)
    return [one for one in found if one.argv]


# sh's reading, one piece of the line at a time (POSIX Shell Command Language
# 2.2 quoting, 2.3 token recognition, 2.7 redirection, 2.9 lists). An operator
# ends the word in front of it whether or not a blank stands between them, so
# `--cov=x.json&& coverage json` is two commands. A quote that never closes
# matches no quoted form and is read as an ordinary character: sh would refuse
# the line, and a rough lint beats a crash at config load. Blanks are space and
# tab only: a non-breaking space pasted out of rendered docs, U+000B and a
# carriage return are text to sh, though str.isspace() calls them all blanks.
_SH_PIECE = re.compile(r"""
    (?P<blank>[ \t]+)
  | (?P<comment>\#[^\n]*)
  | (?P<operator>&&|\|\||;;|<<-?|>>|<&|>&|<>|>\||[&|;<>()\n])
  | (?P<joined>\\\n)
  | '(?P<single>[^']*)'
  | "(?P<double>(?:[^"\\]|\\.)*)"
  | \\(?P<escaped>.)
  | (?P<bare>.)
""", re.X | re.S)

# Inside double quotes a backslash escapes $, `, ", itself and a line feed, and
# is text before anything else. An escaped line feed is removed (group 1 unset).
_SH_DOUBLE_ESCAPE = re.compile(r'\\(?:([$`"\\])|\n)')

# A quote or an escape read as an ordinary character: one that never closes.
_UNCLOSED = frozenset("'\"\\")


class _ShLine:
    """The commands on a sh line as its pieces arrive, each a list of words,
    and where each command ends on the line."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = [[]]
        self.word: str | None = None  # None: no word is open; "" is the empty word '' writes
        self.digits = False  # the open word is bare digits: `2` in `2>x` names a descriptor
        self.target = False  # the next word is a redirection's target, never an argument
        self.cuts: list[tuple[int, int]] = []  # where each operator that ends a command sits
        self.unclosed = False  # a quote or an escape never closed

    def bare(self, text: str) -> None:
        self.unclosed = self.unclosed or text in _UNCLOSED
        self.digits = (self.word is None or self.digits) and text.isdigit()
        self.word = _grown(self.word, text)

    def quoted(self, text: str) -> None:
        self.digits = False
        self.word = _grown(self.word, text)

    def double(self, text: str) -> None:
        self.quoted(_SH_DOUBLE_ESCAPE.sub(r"\1", text))

    def skip(self, _text: str) -> None:
        """A comment, or a backslash joining two lines: nothing reaches a word."""

    def take(self, piece: re.Match) -> int:
        """Read one piece; the index the next one starts at. A `#` that starts a
        word starts a comment to the end of the line, and one inside a word
        (`a#b`, `a\\;#b`) is text, with the line read on from behind it."""
        kind = piece.lastgroup
        if kind == "comment" and self.word is not None:
            self.bare("#")
            return piece.start() + 1
        _SH_STEPS[kind](self, piece[kind])
        if kind == "operator" and piece[kind][0] not in "<>":
            self.cuts.append(piece.span())
        return piece.end()

    def blank(self, _text: str = "") -> None:
        """The open word ends. The word a redirection opens is the shell's."""
        if self.word is None:
            return
        if not self.target:
            self.commands[-1].append(self.word)
        self.word, self.digits, self.target = None, False, False

    def operator(self, text: str) -> None:
        """`&&`, `|`, `;`, a parenthesis or a line feed starts the next command;
        a redirection takes the next word, and bare digits touching it."""
        redirection = text[0] in "<>"
        if redirection and self.digits:
            self.word, self.digits = None, False
        self.blank()
        self.target = redirection
        if not redirection:
            self.commands.append([])


_SH_STEPS = {"blank": _ShLine.blank, "comment": _ShLine.skip, "operator": _ShLine.operator,
             "joined": _ShLine.skip, "single": _ShLine.quoted, "double": _ShLine.double,
             "escaped": _ShLine.quoted, "bare": _ShLine.bare}


def _sh_line(command: str) -> _ShLine:
    """The line as sh splits it, with sh's redirections out."""
    line, at = _ShLine(), 0
    while at < len(command):
        at = line.take(_SH_PIECE.match(command, at))
    line.blank()
    return line


def _sh_commands(command: str) -> list[_Command]:
    """Each command on the line as sh splits it, spelled as the line spells it."""
    line = _sh_line(command)
    starts = [0] + [end for _, end in line.cuts]
    ends = [start for start, _ in line.cuts] + [len(command)]
    return [_Command(argv, command[start:end].strip())
            for argv, start, end in zip(line.commands, starts, ends)]


def _sh_script(script: str) -> list[_Command]:
    """A `-c` script's commands, read as sh reads it. Raises ValueError when a
    quote or an escape in it never closes: sh refuses such a script, so nothing
    inside it can be judged."""
    if _sh_line(script).unclosed:
        raise ValueError(f"a quote or an escape never closes: {script}")
    return [one for one in _sh_commands(script) if one.argv]


# cmd.exe's delimiters: space, tab, `;`, `,`, `=`, U+000B, U+000C and U+00A0.
# The program splits its line on space and tab only, so the rest reach it as
# text: `;` ends nothing (verified argv for `--cov=src; echo done`:
# ["--cov=src;", "echo", "done"]).
_CMD_DELIMITERS = " \t;,=\x0b\x0c\xa0"

# cmd.exe's own pass over the line, before the program sees it. A double quote
# opens or closes a quoted run and stays in the text, and a run left open takes
# the rest of the line. Outside a run a caret is dropped and hands on the
# character behind it, so a quote it hands on opens no run for cmd.exe. `&&`,
# `||`, `&` and `|` end the command whatever touches them, and a `(` where a
# command starts opens a block, inside which `)` ends it. A redirection leaves
# with its target, which starts past any delimiters and ends at the next one
# (inside a block at a `)` as well), and with a handle digit when a delimiter,
# a quote, `&`, `|`, `(` or `)` stands before the digit, caret or not (`a2>x`
# hands on `a2`, `a^|2>x` on `a|`). The delimiters between one redirection and
# the next leave with them (`>lane.log;2>&1` hands on nothing); anywhere else
# they stay. cmd.exe drops a carriage return and runs the first line only.
def _cmd_piece(target_ends: str) -> re.Pattern:
    """cmd.exe's pieces, with what else ends a redirection target."""
    return re.compile(rf"""
        (?P<run>"[^"\n]*"?)
      | \^(?P<escaped>[^\n]?)
      | (?P<end>&&|\|\||[&|])
      | (?P<open>\()
      | (?P<close>\))
      | (?P<redirection>(?:(?<![^{_CMD_DELIMITERS}"&|()])[0-9])?(?:>>?|<)
            (?:&[{_CMD_DELIMITERS}]*[0-9]
              |[{_CMD_DELIMITERS}]*(?:"[^"\n]*"?|\^[^\n]?|[^{_CMD_DELIMITERS}<>&|"^\n{target_ends}])*)
            (?:[{_CMD_DELIMITERS}]*(?=[0-9]?[<>]))?)
      | (?P<stop>\n.*)
      | (?P<char>.)
    """, re.X | re.S)


_CMD_PIECE, _CMD_BLOCK_PIECE = _cmd_piece(""), _cmd_piece(")")


class _CmdLines:
    """The line cmd.exe hands the program of each command, as the pieces arrive."""

    def __init__(self) -> None:
        self.lines = [""]
        self.blocks = 0  # the parentheses cmd.exe has opened and not closed

    def read(self, command: str, at: int) -> int:
        """Read the piece at `at`; the index the next one starts at."""
        piece = (_CMD_BLOCK_PIECE if self.blocks else _CMD_PIECE).match(command, at)
        _CMD_STEPS[piece.lastgroup](self, piece[piece.lastgroup])
        return piece.end()

    def text(self, text: str) -> None:
        self.lines[-1] += text

    def end(self, _text: str = "") -> None:
        self.lines.append("")

    def skip(self, _text: str) -> None:
        """A redirection, or the lines behind the first: no program sees them."""

    def open(self, text: str) -> None:
        """`(` opens a block where a command starts, and is text anywhere else."""
        if self.lines[-1].strip(_CMD_DELIMITERS):
            self.text(text)
        else:
            self.blocks += 1

    def close(self, text: str) -> None:
        """`)` ends the command inside a block, and is text outside one."""
        if not self.blocks:
            return self.text(text)
        self.blocks -= 1
        self.end()


_CMD_STEPS = {"run": _CmdLines.text, "escaped": _CmdLines.text, "char": _CmdLines.text,
              "end": _CmdLines.end, "open": _CmdLines.open, "close": _CmdLines.close,
              "redirection": _CmdLines.skip, "stop": _CmdLines.skip}


def _cmd_lines(command: str) -> list[str]:
    """The command line cmd.exe hands the program of each command on the line.
    cmd.exe reads the whole line before it runs any of it, and runs none of it
    when a block is still open where the line ends."""
    command, lines, at = command.replace("\r", ""), _CmdLines(), 0
    while at < len(command):
        at = lines.read(command, at)
    return [] if lines.blocks else lines.lines


# The program cmd.exe starts: past its delimiters, up to the next one outside a
# quoted run, a caret in front of it or not. cmd.exe drops the blanks behind the
# name, U+000B and U+000C among them, and hands the program the rest.
_CMD_NAME = re.compile(rf'[{_CMD_DELIMITERS}]*((?:"[^"]*"?|[^{_CMD_DELIMITERS}"])*)[ \t\x0b\x0c]*')


# How the program reads the line cmd.exe hands it: the C runtime's rules, which
# python.exe and node.exe build their argv with. Blanks are space and tab. A
# quote opens or closes a run anywhere, and inside a run a doubled quote writes
# one. Backslashes are text except in front of a quote: 2n of them write n and
# the quote opens or closes a run, 2n+1 write n and a literal quote.
_RUNNER_PIECE = re.compile(r'(?P<slashes>\\*)(?P<quotes>"+)|(?P<blank>[ \t]+)|[^ \t"\\]+|\\+')


def _runner_quotes(slashes: int, quotes: int, in_run: bool) -> tuple[str, bool]:
    """What backslashes and the quotes behind them write, and whether a quoted
    run is open after them."""
    text = "\\" * (slashes // 2) + '"' * (slashes % 2)
    quotes -= slashes % 2
    while quotes:
        if in_run and quotes > 1:
            text, quotes = text + '"', quotes - 2
        else:
            in_run, quotes = not in_run, quotes - 1
    return text, in_run


def _grown(word: str | None, text: str) -> str:
    """The open word with `text` behind it. None is no word yet: a pair of
    quotes opens one even when it writes nothing, so `-k "" tests` is three."""
    return (word or "") + text


def _closed(word: str | None) -> list[str]:
    return [] if word is None else [word]


def _runner_words(line: str) -> list[str]:
    """The argv the program builds from the line cmd.exe hands it."""
    words: list[str] = []
    word, in_run = None, False
    for piece in _RUNNER_PIECE.finditer(line):
        if piece["blank"] and not in_run:
            words, word = words + _closed(word), None
        elif piece["quotes"]:
            text, in_run = _runner_quotes(len(piece["slashes"]), len(piece["quotes"]), in_run)
            word = _grown(word, text)
        else:
            word = _grown(word, piece[0])
    return words + _closed(word)


def _cmd_command(line: str) -> _Command:
    """The program's name as cmd.exe ends it, then the argv the program builds
    from the rest: `python;-m pytest` starts python with `;-m` and `pytest`."""
    name = _CMD_NAME.match(line)
    rest = line[name.end():]
    return _Command(_runner_words(name[1]) + _runner_words(rest), line.strip(), rest)


def _cmd_commands(command: str) -> list[_Command]:
    """One command per command on the line: cmd.exe's pass, then the program's
    own reader over the line cmd.exe hands it. The two differ, and the guard
    has to read both: `^"` is text to cmd.exe and a quote to the program, and
    `\\"` is a quote to cmd.exe and text inside the program's quoted run."""
    return [_cmd_command(line) for line in _cmd_lines(command)]


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
    return _steps(_commands(command, cmd), cmd)


def _steps(commands: list[_Command], cmd: bool) -> CommandSteps:
    """Each command, a payload read as the steps it holds. A line that holds
    no word runs one step with no words."""
    if not commands:
        return CommandSteps((Step((), cmd),))
    return _joined([_descended(command, cmd) for command in commands])


def _joined(parts: list[CommandSteps]) -> CommandSteps:
    return CommandSteps(sum((part.steps for part in parts), ()),
                        sum((part.unreadable for part in parts), ()))


def _descended(command: _Command, cmd: bool) -> CommandSteps:
    """One command: its payload's steps when it is a shell running a script,
    else the command as read. A payload sh cannot split leaves the command as
    read, marked."""
    as_read = CommandSteps((Step(tuple(command.argv), cmd),))
    try:
        script = _payload(command, cmd)
        return as_read if script is None else _steps(_sh_script(script), False)
    except ValueError:
        return as_read._replace(unreadable=(command.spelled,))


# The shells whose `-c` script is sh text, matched against the last part of the
# word: `bash`, `sh`, `/usr/bin/bash`, `C:\Program Files\Git\bin\bash.exe`.
_SH_NAME = re.compile(r"(?:ba)?sh(?:\.exe)?", re.IGNORECASE)


def _payload(command: _Command, cmd: bool) -> str | None:
    """The script a `bash -c` or `sh -c` step hands its shell, or None when the
    step runs no such script."""
    if not _SH_NAME.fullmatch(re.split(r"[\\/]", command.argv[0])[-1]):
        return None
    return _script_of(_shell_argv(command, cmd))


def _shell_argv(command: _Command, cmd: bool) -> list[str]:
    """The arguments the shell program is handed. Under sh they are the words sh
    read. cmd.exe hands bash.exe (Git for Windows, MSYS2, Cygwin) the line as
    written, and bash.exe splits it with sh's quote rules, ' included: that is
    how `bash -c 'pytest tests'` reaches bash as one script under cmd.exe.
    Raises ValueError when those rules find a quote that never closes."""
    if not cmd:
        return command.argv[1:]
    return shlex.split(command.rest)


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


def install_python(word: str, spec: LaunchSpec) -> str:
    r"""The lane's python as the first word of an install line a reader pastes.

    A bare name stays as the lane spells it. A path is read from the lane's
    directory, and the reader pastes from wherever they stand, into cmd.exe,
    PowerShell or Git Bash: `.venv\Scripts\python.exe -m pip install pytest-cov`
    found nothing outside the lane's directory, and Git Bash ran it as
    `.venvScriptspython.exe`. So a path goes out as the file it names, spelled
    the way a next step spells crapkit's own interpreter. The word as written
    when it names no file.
    """
    resolved = spec.resolve(word) if os.sep in word or "/" in word else None
    return interpreter_word(resolved) if resolved else word


def pytest_head(command: str) -> str:
    """The word in front of pytest: the manager or tool the lane runs pytest
    through when no python does. A lane that chains steps runs pytest after
    `&&`, so this is the step's head, not the command's first word; the first
    word only when no step names pytest at all."""
    step = pytest_step(command)
    return step[0] if step else first_word(command)
