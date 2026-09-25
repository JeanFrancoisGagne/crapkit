"""The lane guard: which lane commands crapkit refuses as narrowing a full-suite run.

Every expected verdict is written from docs/lanes.md (How a lane command is
read, lines 62-124, and The full-suite rule, lines 716-808) and the vitest
lane section (lines 442-464), one row per case the docs name, split by the
shell that runs the lane: sh on POSIX, cmd.exe on Windows. Three outside
oracles read the same commands:

- the shell itself: each command runs through sh or cmd.exe with the runner
  swapped for a script that records its argv, and a word crapkit refuses
  must be one the runner receives;
- pytest's own option parser, which says which of those words are
  positionals, and `pytest --collect-only`, which says whether dropping them
  changes what runs (nightly);
- the metamorphic rule the docs state: a flag with its value never makes a
  lane narrowing.

Each test_fix_<sha> holds the rows of one past fix, so its replay at the
commit before that fix goes red.
"""
from __future__ import annotations

from dataclasses import replace
import itertools

import pytest

from accuracy.kit import rulings
from accuracy.verdict_model import cadence
from accuracy.verdict_model import guard_repo as g

OK = "ok"


def py(args: str) -> str:
    return f"{g.PYTEST} {args} {g.COV}"


def vitest(args: str) -> str:
    return f"{g.VITEST} --coverage {args}"


# (row id, lane, the word sh refuses or ok, the word cmd.exe refuses or ok, source)
FLAG_VALUES = [
    ("n8", g.Lane(py("-n 8")), OK, OK, "lanes.md:774-777 a flag's value is not a positional"),
    ("o-kv", g.Lane(py("-o timeout=300")), OK, OK, "lanes.md:774-777"),
    ("p-plugin", g.Lane(py("-p no:randomly")), OK, OK, "lanes.md:774-777"),
    ("deselect", g.Lane(py("--deselect tests/test_a.py::test_slow")), OK, OK, "lanes.md:774-777"),
]
CHAINS = [
    ("and-narrow", g.Lane(f"{g.PYTEST} {g.COV} && {g.PYTEST} calc/hot.py"), "calc/hot.py",
     "calc/hot.py", "lanes.md:101-110 a second run after && is checked too"),
    ("and-other", g.Lane(f"{g.PYTEST} {g.COV} && python -m coverage json"), OK, OK,
     "lanes.md:74 && starts a new command; only runner segments are checked"),
    ("or-narrow", g.Lane(f"{g.PYTEST} {g.COV} || {g.PYTEST} pylib/unit"), "pylib/unit",
     "pylib/unit", "lanes.md:74 || starts a new command"),
]
MID_TOKEN = [("mid-quote", g.Lane(py('--cov-report=json:"cov/py 1.json"')), OK, OK,
              "lanes.md:73 a quote opens a quoted run wherever it sits")]
CARET = [
    ("caret", g.Lane(py('-k ^"not slow^"')), OK, OK, "lanes.md:72 cmd.exe drops the caret outside quotes"),
    ("caret-in-quotes", g.Lane(py('-k "a^b"')), OK, OK, "lanes.md:72 inside a quoted run the caret stays"),
    ("caret-positional", g.Lane(py('^"pylib/unit^"')), "^pylib/unit^", "pylib/unit",
     "lanes.md:72 cmd.exe hands on the quote behind the caret; sh keeps the caret"),
    ("caret-vitest", g.Lane(vitest('^"web/grade.ts^"'), parser="istanbul"), OK, "web/grade.ts",
     "lanes.md:72 and 458-459 on cmd.exe the filter reaches vitest as web/grade.ts"),
]
TESTPATHS_FILE = [
    ("ini-decides", g.Lane(py("more"), testpaths={"pytest.ini": ("more",), "pyproject.toml": ("tests",)}),
     OK, OK, "lanes.md:801-803 pytest.ini decides when present"),
    ("ini-decides-refuse", g.Lane(py("tests"), testpaths={"pytest.ini": ("more",),
                                                          "pyproject.toml": ("tests",)}),
     "tests", "tests", "lanes.md:801-803 pytest.ini decides when present"),
    ("empty-ini", g.Lane(py("tests"), testpaths={"pytest.ini": (), "pyproject.toml": ("tests",)}),
     "tests", "tests", "lanes.md:802 pytest.ini decides even empty"),
    ("tox", g.Lane(py("tests"), testpaths={"tox.ini": ("tests",)}), OK, OK,
     "lanes.md:803 tox.ini decides when it holds a pytest section"),
]
COVER_ALL = [
    ("both", g.Lane(py("tests more")), OK, OK, "lanes.md:798-800 positionals naming every testpaths entry"),
    ("spellings", g.Lane(py("./tests more/")), OK, OK, "lanes.md:804 tests/, ./tests and tests are one entry"),
    ("half", g.Lane(py("tests")), "tests", "tests", "lanes.md:805-806 one entry of several is narrowing"),
    ("below", g.Lane(py("tests/unit more")), "tests/unit", "tests/unit",
     "lanes.md:806-807 tests/unit under testpaths tests is refused"),
]
SHELL_READING = [
    ("single-quotes", g.Lane(py("-m 'not live and not perf'")), OK, "live",
     "lanes.md:71 and 89-94 cmd.exe has no single-quote rule"),
]
SEMICOLON = [("semicolon", g.Lane(f"{g.PYTEST} {g.COV}; echo done"), OK, "echo",
              "lanes.md:77 and 116-120 on sh ; ends the command, to cmd.exe it is a character")]
REDIRECTS = [
    ("redirect", g.Lane(f"{g.PYTEST} {g.COV} > lane.log 2>&1"), OK, OK,
     "lanes.md:76 redirections are the shell's"),
    ("quoted-gt", g.Lane(py('">"')), ">", ">", "lanes.md:76 a quoted > is an argument and stays"),
]
VITEST_OPTIONS = [
    (f"vitest-{name}", g.Lane(vitest(f"{name} {value}"), parser="istanbul"), OK, OK,
     "lanes.md:451-462 a path after a licensed vitest option is its value")
    for name, value in (("--workspace", "vitest.workspace.ts"), ("--diff", "diff.ts"),
                        ("--snapshotEnvironment", "./env.ts"), ("--typecheck.tsconfig", "tsconfig.ts"),
                        ("--config", "vitest.ci.ts"), ("--exclude", "src/legacy.cjs"))
] + [("vitest-filter", g.Lane(vitest("web/grade.ts"), parser="istanbul"), "web/grade.ts",
      "web/grade.ts", "lanes.md:442-448 a file filter with --coverage is refused")]
WORD_BREAKS = [
    ("nbsp", g.Lane(py("-m not\N{NO-BREAK SPACE}live")), OK, OK,
     "lanes.md:78 a non-breaking space is not a word break"),
    ("tab", g.Lane(f"{g.PYTEST}\tpylib/unit\t{g.COV}"), "pylib/unit", "pylib/unit",
     "lanes.md:78 words break on space, tab and line endings"),
]
QUOTED_OPERATOR = [
    ("quoted-and", g.Lane(py('-k "a && b"')), OK, OK, "lanes.md:76 a quoted operator is an argument"),
    ("quoted-pipe", g.Lane(py('-k "x|y"')), OK, OK, "lanes.md:76"),
]
EMPTY_ARGUMENT = [("empty-quotes", g.Lane(py('-k "" tests')), "tests", "tests",
                   "lanes.md:75 an empty pair of quotes writes an empty argument")]
CJS = [("vitest-cjs", g.Lane(vitest("web/legacy.cjs"), parser="istanbul"), "web/legacy.cjs",
        "web/legacy.cjs", "lanes.md:458-459 a path ending in a source suffix is a filter")]
SHLEX = [
    ("marker-expression", g.Lane(py('-m "not live and not perf"')), OK, OK,
     "lanes.md:70 double quotes are the portable spelling: one argument"),
    ("quoted-path-in-k", g.Lane(py('-k "tests/gone.py or x"')), OK, OK,
     "lanes.md:129-130 a path inside a quoted -k is a marker expression"),
]
UNKNOWN_FLAG = [
    ("quiet-path", g.Lane(py("-q pylib/unit")), "pylib/unit", "pylib/unit",
     "lanes.md:785-789 a path or node id outranks the value guess"),
    ("unknown-flag-word", g.Lane(py("--frobnicate level")), OK, OK,
     "lanes.md:784-785 after a flag it does not know, a bare word is its value"),
    ("unknown-flag-path", g.Lane(py("--frobnicate pylib/unit")), "pylib/unit", "pylib/unit",
     "lanes.md:785-786 only a path or node id outranks the guess"),
]

ROWS = (FLAG_VALUES + CHAINS + MID_TOKEN + CARET + TESTPATHS_FILE + COVER_ALL + SHELL_READING
        + SEMICOLON + REDIRECTS + VITEST_OPTIONS + WORD_BREAKS + QUOTED_OPERATOR + EMPTY_ARGUMENT
        + CJS + SHLEX + UNKNOWN_FLAG)
# The shell-word check runs, on push, the first row of each group whose answer
# turns on the shell's quoting; nightly it runs every row.
PUSH_ROWS = {group[0][0] for group in (FLAG_VALUES, CHAINS, MID_TOKEN, CARET, SHELL_READING,
                                       WORD_BREAKS, QUOTED_OPERATOR, EMPTY_ARGUMENT, SHLEX)}


def expected(row) -> str:
    return row[3] if g.WINDOWS else row[2]


@pytest.fixture(scope="module")
def guard(repo_templates, tmp_path_factory):
    """One repository per testpaths layout, shared by the module's tests."""
    numbers, base = itertools.count(), tmp_path_factory.mktemp("guard")
    return g.Repos(lambda spec: repo_templates.copy(spec, base / f"repo{next(numbers)}"))


def _judge(guard, rows) -> dict:
    """{row id: (crapkit's verdict, the docs' verdict)} for this platform's shell."""
    return {row[0]: (guard.verdict(row[1]), expected(row)) for row in rows}


def _agree(guard, rows) -> None:
    judged = _judge(guard, rows)
    assert {key: pair for key, pair in judged.items() if pair[0] != pair[1]} == {}


def _ids(rows) -> list[str]:
    return [row[0] for row in rows]


# --- one test per past fix -----------------------------------------------------------------

@pytest.mark.process
def test_fix_a8699a5(guard):
    """A flag's value is not a positional test path."""
    _agree(guard, FLAG_VALUES)


@pytest.mark.process
def test_fix_cc6969c(guard):
    """A chained command reads as one argv per segment."""
    _agree(guard, CHAINS)


@pytest.mark.process
def test_fix_482a6c7(guard):
    """A quote that opens mid-token reads as cmd.exe reads it."""
    _agree(guard, MID_TOKEN)


@pytest.mark.process
def test_fix_5643945(guard):
    """cmd.exe's caret escape outside quoted runs."""
    _agree(guard, CARET)


@pytest.mark.nightly
@pytest.mark.process
def test_fix_8cbbb87(guard):
    """testpaths come from the one config file pytest reads."""
    _agree(guard, TESTPATHS_FILE)


@pytest.mark.process
def test_fix_9863e1d(guard):
    """A positional passes only when the positionals cover every testpaths entry."""
    _agree(guard, COVER_ALL)


@pytest.mark.process
def test_fix_53da05f(guard):
    """claude-hook parses crapkit.toml without the CLI's loader; its lane
    guard reads pytest's testpaths from the same root. A lane whose
    positionals name every testpaths entry loads, so an edit over the ceiling
    is advised (exit 2); one naming one entry of two is refused, which the
    hook answers with silence (exit 0)."""
    rows = {row[0]: row for row in COVER_ALL}
    picked = (rows["both"], rows["half"])
    got = {row[0]: guard.advisory(row[1]) for row in picked}
    assert got == {row[0]: _advised(row) for row in picked}


def _advised(row) -> int:
    """README.md:812: exit 2 for an edit over the ceiling, 0 when the lane is refused."""
    return 2 if expected(row) == OK else 0


@pytest.mark.process
def test_doctor_reads_a_lane_command_with_shell_words(guard):
    _agree(guard, SHELL_READING)


@pytest.mark.process
def test_a_semicolon_ends_a_command_on_the_sh_path(guard):
    _agree(guard, SEMICOLON)


@pytest.mark.process
def test_redirections_are_not_argv(guard):
    _agree(guard, REDIRECTS)


@pytest.mark.process
def test_vitest_options_that_read_a_path(guard):
    _agree(guard, VITEST_OPTIONS)


@pytest.mark.process
def test_words_break_on_space_tab_and_line_ends_only(guard):
    _agree(guard, WORD_BREAKS)


@pytest.mark.process
def test_a_quoted_operator_is_a_word(guard):
    _agree(guard, QUOTED_OPERATOR)


@pytest.mark.process
def test_an_empty_quoted_argument_is_kept(guard):
    _agree(guard, EMPTY_ARGUMENT)


@pytest.mark.process
def test_a_cjs_positional_is_a_file_filter(guard):
    _agree(guard, CJS)


@pytest.mark.process
def test_narrowed_lane_command_is_not_full_suite(guard):
    """A double-quoted marker expression is one argument on both shells."""
    _agree(guard, SHLEX + UNKNOWN_FLAG)


# --- oracle: the shell's own argv -------------------------------------------------------------

def _runner(lane: g.Lane) -> str:
    return g.VITEST if lane.parser == "istanbul" else g.PYTEST


@pytest.mark.process
@pytest.mark.parametrize("row", cadence.tiered(ROWS, push=PUSH_ROWS, ids=_ids(ROWS)))
def test_the_refused_word_is_one_the_shell_hands_the_runner(guard, row):
    """Run through sh (POSIX) or cmd.exe (Windows) with the runner swapped for
    an argv recorder: the word crapkit names must reach the runner as a whole
    argument, and a command crapkit reads as one runner call must start one."""
    root = guard.root(row[1])
    word = g.verdict(root)
    argvs = g.shell_argvs(root, row[1].command, _runner(row[1]))

    assert argvs, "the shell started no runner"
    assert word == OK or any(word in argv for argv in argvs), (word, argvs)


# --- metamorphic: a flag with its value never flips the verdict ----------------------------------

PYTEST_ROWS = [row for row in ROWS if row[1].parser == "coveragepy"]
FLAGS = "-n 8 -o timeout=300 --cov-report=json:.crapkit/cov/extra.json -p no:randomly"


@pytest.mark.process
def test_adding_flags_and_their_values_never_flips_the_verdict(guard):
    """lanes.md:774-777: `-n 8`, `-o timeout=300` and `-p no:randomly` all
    pass; appended to any command they leave its verdict where it was."""
    judged = {row[0]: guard.verdict(replace(row[1], command=f"{row[1].command} {FLAGS}"))
              for row in PYTEST_ROWS}
    assert {key: word for key, word in judged.items() if word != expected(_row(key))} == {}


def _row(key: str):
    return next(row for row in ROWS if row[0] == key)


# --- oracle: pytest's own collection (nightly) -------------------------------------------------

def _narrows(root, argv: list[str]) -> bool | None:
    """Whether pytest runs something else with the command's positionals than
    without them (a positional that errors counts); None when pytest refuses
    the command line before it reads one."""
    positionals = g.pytest_positionals(root, argv)
    if positionals is None:
        return None
    return bool(positionals) and _runs_something_else(root, argv, positionals)


def _runs_something_else(root, argv: list[str], positionals: list[str]) -> bool:
    rest = [word for word in argv if word not in positionals]
    full = g.collected(root, argv)
    return full is None or full != g.collected(root, rest)


# Rows pytest itself refuses: the guard reads an option it does not know.
PYTEST_REFUSES = {"unknown-flag-word": "V5", "unknown-flag-path": "V5.1"}


@pytest.mark.nightly
@pytest.mark.process
@pytest.mark.parametrize("row", PYTEST_ROWS, ids=_ids(PYTEST_ROWS))
def test_the_guard_agrees_with_pytest_s_collection(guard, row):
    """pytest --collect-only with and without the positionals: equal counts
    mean a full suite, which crapkit must load; fewer (or a collection error)
    mean a narrowed one, which it must refuse. A command line pytest itself
    refuses (an option it does not know) is ruling V5."""
    root = guard.root(row[1])
    verdicts = [_narrows(root, argv) for argv in g.shell_argvs(root, row[1].command)]
    word = g.verdict(root)
    if row[0] in PYTEST_REFUSES:
        assert None in verdicts
        rulings.pin_ruling(PYTEST_REFUSES[row[0]], crapkit=word, oracle="refused")
        return
    assert (word != OK) == any(verdicts), (word, verdicts)
