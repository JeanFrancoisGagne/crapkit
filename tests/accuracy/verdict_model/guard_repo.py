"""A repository with one lane whose command a test writes, and the verdict crapkit gives it.

The lane guard (docs/lanes.md, How a lane command is read and The full-suite
rule) refuses a coverage lane whose command narrows the suite, naming the word
it read as a positional or a file filter. `verdict()` loads the config through
`crapkit inventory`, which reads every lane before it analyzes anything: "ok"
when the lane loads, else the word the refusal names.

The repository holds tests under tests/, tests/unit/, more/ and pylib/unit/,
with `testpaths` declared in the file a test chooses, and a root conftest.py
that writes pytest's own reading of its positionals (config.args) to the file
PYTEST_ARGS_OUT names: the collection oracle's view of the same command.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from accuracy.kit import drive, repos

COV = "--cov=calc --cov-branch --cov-report=json:.crapkit/cov/py.json"
PYTEST = "python -m pytest"
VITEST = "npx vitest run"
_REFUSAL = re.compile(r"(?:positional argument|file filter) '(.*)' (?:narrows|combined)", re.S)

CONFTEST = '''\
import json
import os


def pytest_configure(config):
    out = os.environ.get("PYTEST_ARGS_OUT")
    if out:
        source = getattr(config, "args_source", None)
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"args": list(config.args),
                                     "source": getattr(source, "name", str(source))}) + "\\n")
        import pytest
        pytest.exit("pytest's positionals recorded", returncode=0)


def pytest_collection_finish(session):
    out = os.environ.get("PYTEST_COUNT_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(str(len(session.items)))
'''
TESTS = {
    "tests/test_a.py": "def test_one():\n    pass\n\n\ndef test_slow():\n    pass\n",
    "tests/unit/test_b.py": "def test_two():\n    pass\n",
    "more/test_m.py": "def test_three():\n    pass\n",
    "pylib/unit/test_p.py": "def test_four():\n    pass\n",
    "calc/hot.py": "def hot(x):\n    return x\n",
    "web/grade.ts": "export function grade(x: number) { return x; }\n",
    "conftest.py": CONFTEST,
}
# docs/lanes.md#the-full-suite-rule: pytest.ini decides when present, then
# pyproject.toml, tox.ini and setup.cfg when they hold a pytest section.
TESTPATHS = {
    "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = [{paths}]\n',
    "pytest.ini": "[pytest]\ntestpaths = {words}\n",
    "tox.ini": "[pytest]\ntestpaths = {words}\n",
    "setup.cfg": "[tool:pytest]\ntestpaths = {words}\n",
}


@dataclass(frozen=True)
class Lane:
    """One lane: the command, its parser, and where each testpaths list is declared."""
    command: str
    parser: str = "coveragepy"
    testpaths: dict = field(default_factory=lambda: {"pyproject.toml": ("tests", "more")})


def toml_string(text: str) -> str:
    """A TOML basic string holding `text` exactly."""
    return json.dumps(text, ensure_ascii=False)


def config(lane: Lane) -> str:
    scope, artifact = (("web", ".crapkit/cov/js/coverage-final.json") if lane.parser == "istanbul"
                       else ("calc", ".crapkit/cov/py.json"))
    language = "typescript" if lane.parser == "istanbul" else "python"
    return (f'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "{scope}"\npaths = ["{scope}"]\n'
            f'languages = ["{language}"]\n\n[exclude]\nglobs = ["tests/**", "more/**", "pylib/**"]\n\n'
            f'[[lane]]\nname = "py"\ncommand = {toml_string(lane.command)}\n'
            f'artifact = "{artifact}"\nparser = "{lane.parser}"\nscopes = ["{scope}"]\n')


def _testpaths_file(name: str, paths: tuple) -> str:
    """The file declaring `paths`; no paths writes the bare section header."""
    text = TESTPATHS[name].format(paths=", ".join(f'"{p}"' for p in paths), words=" ".join(paths))
    return text if paths else text.split("\n")[0] + "\n"


def files(lane: Lane) -> dict:
    out = {**TESTS, "crapkit.toml": config(lane)}
    out.update({name: _testpaths_file(name, paths) for name, paths in lane.testpaths.items()})
    return out


def spec(lane: Lane) -> repos.Spec:
    return repos.Spec(steps=(repos.Commit(files=files(lane), message="seed"),))


def _key(lane: Lane) -> tuple:
    return tuple(sorted(lane.testpaths.items()))


class Repos:
    """One repository per testpaths layout; a lane is judged by writing its
    crapkit.toml into that repository's working tree, which inventory reads."""

    def __init__(self, make_repo):
        self.make_repo, self.built = make_repo, {}

    def root(self, lane: Lane) -> Path:
        key = _key(lane)
        if key not in self.built:
            self.built[key] = self.make_repo(spec(lane)).root
        root = self.built[key]
        (root / "crapkit.toml").write_bytes(config(lane).encode("utf-8"))
        return root

    def verdict(self, lane: Lane) -> str:
        return verdict(self.root(lane))

    def advisory(self, lane: Lane) -> int:
        return advisory_verdict(self.root(lane))


def verdict(root: Path) -> str:
    """'ok' when the lane loads, else the word the refusal names."""
    result = drive.Driver(root).run("inventory")
    if result.code == 0:
        return "ok"
    found = _REFUSAL.search(result.stderr)
    assert result.code == 3 and found, result.stdout + result.stderr
    return found.group(1)


# ccn 8 by McCabe (seven ifs, plus one), over the lanes' target of 6.
OVER_CEILING = "def hot(x):\n" + "".join(f"    if x > {i}:\n        return {i}\n"
                                         for i in range(7)) + "    return x\n"


def advisory_verdict(root: Path) -> int:
    """claude-hook's exit after an unstaged edit that lifts calc/hot.py over
    the ceiling; the committed source is put back after. README.md:812: exit
    2 is the advisory, and a configuration it cannot load is exit 0 in
    silence."""
    source = root / "calc" / "hot.py"
    committed = source.read_bytes()
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(root),
               "tool_input": {"file_path": str(source)}}
    source.write_bytes(OVER_CEILING.encode("utf-8"))
    try:
        return drive.Driver(root).run("claude-hook", "--protocol", "1", stdin=json.dumps(payload)).code
    finally:
        source.write_bytes(committed)


# --- what the shell hands the runner -----------------------------------------------------------

DUMP = '''import json, os, sys
with open(os.environ["ARGV_OUT"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ["ARGV_EXIT"]))
'''


def shell_argvs(root: Path, command: str, runner: str = PYTEST) -> list[list[str]]:
    """The argv each runner of `command` receives when the platform's shell runs
    it (sh on POSIX, cmd.exe on Windows), with the runner swapped for a script
    that records its argv. The command runs twice, the script exiting 0 and
    then 1, so a runner behind `&&` and one behind `||` are both started.
    One list per runner call, without repeats."""
    dump, out = root / "argv_dump.py", root / "argv.jsonl"
    dump.write_text(DUMP, encoding="utf-8")
    out.unlink(missing_ok=True)
    swapped = command.replace(runner, f"python {dump.as_posix()}")
    for code in ("0", "1"):
        env = drive.child_env({"ARGV_OUT": str(out), "ARGV_EXIT": code})
        subprocess.run(swapped, shell=True, cwd=root, env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
    lines = out.read_text(encoding="utf-8").splitlines() if out.exists() else []
    return [json.loads(line) for line in dict.fromkeys(lines)]


def pytest_positionals(root: Path, argv: list[str]) -> list[str] | None:
    """pytest's own positionals for `argv` (config.args when they came from the
    command line), or None when pytest refuses the command line."""
    out = root / "pytest_args.jsonl"
    out.unlink(missing_ok=True)
    env = drive.child_env({"PYTEST_ARGS_OUT": str(out), "PYTEST_ADDOPTS": None})
    subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q",
                           "-p", "no:cacheprovider", *argv], cwd=root, env=env,
                          capture_output=True, text=True, timeout=120)
    if not out.exists():
        return None
    seen = json.loads(out.read_text(encoding="utf-8").splitlines()[-1])
    return seen["args"] if seen["source"] == "ARGS" else []


def collected(root: Path, argv: list[str]) -> int | None:
    """How many tests `pytest --collect-only ARGV` collects (the conftest counts
    session.items, whatever -q does to the output), or None on an error."""
    out = root / "pytest_count.txt"
    out.unlink(missing_ok=True)
    env = drive.child_env({"PYTEST_ADDOPTS": None, "PYTEST_COUNT_OUT": str(out)})
    done = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider",
                           *argv, "--no-cov", "-n", "0"], cwd=root, env=env, capture_output=True,
                          text=True, timeout=120)
    return int(out.read_text(encoding="utf-8")) if out.exists() and done.returncode == 0 else None


WINDOWS = os.name == "nt"


# --- the two word-split oracles ----------------------------------------------------------------
# Two readers outside crapkit split a lane command into the argv of each runner
# call: shlex for sh, and CommandLineToArgvW, the reader a program started by
# cmd.exe builds its argv with, after cmd.exe's own pass over the line.

SH_ENDS = frozenset({"&&", "||", "&", "|", ";"})  # POSIX 2.9.3 lists and 2.9.2 pipelines
SH_REDIRECTIONS = frozenset({"<", ">", ">>", "<&", ">&", "<>", ">|"})  # POSIX 2.7, no heredoc


@dataclass(frozen=True)
class ShToken:
    """One shlex token. `operator`: sh's punctuation made it, which a quoted
    `"&&"` never is. `glued`: no blank stands between it and the token before."""
    text: str
    operator: bool
    glued: bool


def sh_tokens(command: str) -> list[ShToken]:
    """shlex.shlex(posix=True, punctuation_chars=True) with whitespace_split on and
    comments off, as shlex.split sets them. shlex returns the operator `&&` and a
    quoted `"&&"` as one string, so the first character of each token's text in
    the line tells them apart: only an operator starts with punctuation."""
    source = io.StringIO(command)
    lexer = shlex.shlex(source, posix=True, punctuation_chars=True)
    lexer.whitespace_split, lexer.commenters = True, ""
    tokens, start, glued = [], 0, False
    for text in iter(lexer.get_token, None):
        first = command[start:].lstrip(lexer.whitespace)[:1]
        tokens.append(ShToken(text, first in lexer.punctuation_chars, glued))
        start, glued = _next_start(command, source.tell(), lexer.whitespace)
    return tokens


def _next_start(command: str, read: int, blanks: str) -> tuple[int, bool]:
    """Where the next token's text starts, and whether it touches the last one.
    shlex ends a token on a blank, which it consumes, or on a character it
    pushes back (an operator after a word, a word after an operator)."""
    pushed_back = read > 0 and command[read - 1] not in blanks
    return (read - 1 if pushed_back else read), pushed_back


def sh_segments(command: str) -> list[list[str]]:
    """The words of each command on the line, read through shlex: an operator in
    SH_ENDS starts the next command, and a redirection takes its target word and
    a glued descriptor number (`2>&1`) out of the argv."""
    segments: list[list[str]] = [[]]
    tokens = iter(sh_tokens(command))
    for token in tokens:
        if not token.operator:
            segments[-1].append(token.text)
        elif token.text in SH_ENDS:
            segments.append([])
        else:
            _redirect(segments[-1], token, tokens)
    return segments


def _redirect(words: list[str], token: ShToken, tokens) -> None:
    """POSIX 2.7: `[n]op word`. The target is the next token; n is the digits
    word right before the operator, when nothing stands between them."""
    assert token.text in SH_REDIRECTIONS, f"the sh oracle does not model {token.text!r}"
    next(tokens, None)
    if token.glued and words and words[-1].isdigit():
        words.pop()


# cmd.exe's pass over the line before the program sees it (docs/lanes.md:70-76):
# a double quote opens or closes a quoted run and stays in the text; outside a
# run a caret is dropped and hands on the character behind it, and a quote it
# hands on opens no run for cmd.exe; `&&`, `||`, `&` and `|` end the command; a
# redirection, with its descriptor digit and its target or `&1`, is taken out.
_CMD_PASS = re.compile(r'''(?P<run>"[^"]*"?)
    | \^(?P<escaped>.?)
    | (?P<ends>&&|\|\||[&|])
    | (?P<redirect>(?:(?<![^ \t])[0-9])?(?:>>?|<)(?:&[0-9]|[ \t]*(?:"[^"]*"?|[^ \t&|<>"]*)))
    | (?P<char>.)''', re.X | re.S)


def cmd_lines(command: str) -> list[str]:
    """The command line cmd.exe hands the program of each command on the line.
    The blanks before an operator stay: inside an unclosed run they are text."""
    lines = [""]
    for part in _CMD_PASS.finditer(command):
        if part.lastgroup == "ends":
            lines.append("")
        elif part.lastgroup != "redirect":
            lines[-1] += part.group(part.lastgroup)
    return [line.lstrip(" \t") for line in lines]


def command_line_to_argv(line: str) -> list[str]:
    """CommandLineToArgvW's words for one command line (win32 only). python.exe
    builds its argv with the UCRT's reader, which differs on a doubled quote
    inside a quoted run (`"x"" y"`: one word to the UCRT, two here); no row
    holds one, and the check compares every split with python's own argv."""
    import ctypes
    from ctypes import wintypes

    shell32, kernel32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    words = shell32.CommandLineToArgvW(line, ctypes.byref(count))
    try:
        return words[:count.value]
    finally:
        kernel32.LocalFree(ctypes.cast(words, ctypes.c_void_p))


def cmd_segments(command: str) -> list[list[str]]:
    """The words of each command on the line: cmd.exe's pass, then
    CommandLineToArgvW on each line it hands a program."""
    return [command_line_to_argv(line) for line in cmd_lines(command) if line]


def runner_argvs(segments: list[list[str]], runner: str) -> list[list[str]]:
    """The argv of each runner call: the words after the runner's own, one list
    per call, without repeats, as shell_argvs records them."""
    head = runner.split()
    calls = (tuple(words[len(head):]) for words in segments if words[:len(head)] == head)
    return [list(call) for call in dict.fromkeys(calls)]
