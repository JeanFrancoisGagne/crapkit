"""Every command crapkit prints runs as printed in every shell a reader pastes it into.

printed_runs builds a repo whose names hold what shells treat specially and
collects what crapkit prints for it: the report's drill-down commands, brief's
gate and scoped-test commands, the next step a refusal names, init's pip line
and the override receipt's clearing spellings. Each Printed carries the argv it
must deliver, written from the worklist JSON and the docs.

The oracle is the shell. With a stand-in crapkit on PYTHONPATH that prints the
argv it received, each line pasted into cmd.exe (plain and with delayed
expansion), Windows PowerShell 5.1, pwsh 7 and Git Bash on Windows, or bash
elsewhere, must hand crapkit that argv, read the POSIX way: the same options in
any order, the same positionals in order, and a `--` only ends the options.
Then each distinct argv, run by the real crapkit, exits 0, and explain's first
line names the row's path and long name. Together: every printed command runs
and names the JSON handle's function on every shell.

Each shell gets one paste of every line it is tested on (one process for
PowerShell and bash). An encoded PowerShell line is pasted into cmd.exe alone:
its outer text is `powershell -NoProfile -NonInteractive -EncodedCommand` and
base64 (letters, digits, + / =), which no shell rewrites, and each paste of it
starts a PowerShell.
"""
import base64
import json
import os
import sys

import pytest

from accuracy.corpus_goldens import golden_runs, printed_runs, shells
from accuracy.kit import drive, repos, rulings

pytestmark = pytest.mark.process
HERE = shells.here()
ENCODED = "powershell -NoProfile -NonInteractive -EncodedCommand "
ARGV_SOURCES = ("report", "gate", "scoped")
STUB = "import json, sys\n\n\ndef main():\n    print(json.dumps(sys.argv[1:]))\n    return 0\n"
# (shell, printed source) pairs a defect breaks on Windows.
DEFECTS = {("bash", "next-step"): "CG3", ("powershell", "next-step-space"): "CG4",
           ("pwsh", "next-step-space"): "CG4"} if sys.platform == "win32" else {}
CHECK_CLEARED = {
    "bash": "export {v}=set; {line}; [ -z \"${{{v}+x}}\" ] && echo UNSET || echo SET",
    "cmd": "set {v}=set& {line}& if defined {v} (echo SET) else (echo UNSET)",
    "powershell": ("$env:{v} = 'set'; {line}; "
                   "if ($null -eq $env:{v}) {{ 'UNSET' }} else {{ 'SET' }}"),
}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    return printed_runs.measure(golden_runs.shared_base(tmp_path_factory))


@pytest.fixture(scope="module")
def stub_env(tmp_path_factory):
    """The environment in which `crapkit` and `python -m crapkit` print their argv."""
    stub = tmp_path_factory.mktemp("stub")
    (stub / "crapkit" / "cli").mkdir(parents=True)
    (stub / "crapkit" / "__init__.py").write_text("", encoding="utf-8")
    (stub / "crapkit" / "cli" / "__init__.py").write_text(STUB, encoding="utf-8")
    (stub / "crapkit" / "__main__.py").write_text(STUB + "\n\nmain()\n", encoding="utf-8")
    return drive.child_env({"PYTHONPATH": str(stub), "PYTHONIOENCODING": "utf-8"},
                           str(printed_runs.python()))


@pytest.fixture(scope="module")
def spaced_step(tmp_path_factory):
    """The refusal's next step when crapkit ran from an interpreter path holding a
    space; the directory link lives until the module's tests are done."""
    scratch = tmp_path_factory.mktemp("spaced")
    interpreter = printed_runs.spaced_interpreter(scratch)
    try:
        yield printed_runs.refusal_next_step("next-step-space", interpreter,
                                             printed_runs.unmeasured_repo(scratch))
    finally:
        printed_runs.unlink_spaced(scratch)


def _tested_in(shell: str, item) -> bool:
    if item.source == "clear":
        return shell in item.shells
    return not item.text.startswith(ENCODED) or shell == HERE[0]


def _line(shell: str, item) -> str:
    """What gets pasted: the printed text, the pip line with the stand-in
    crapkit in pip's place, or a clearing spelling between setting the
    variable and asking whether it is still set."""
    if item.source == "pip":
        return "crapkit " + item.text.removeprefix("pip ")
    if item.source == "clear":
        template = CHECK_CLEARED.get(shell) or CHECK_CLEARED[
            "cmd" if shell.startswith("cmd") else "powershell"]
        return template.format(v=printed_runs.VARIABLE, line=item.text)
    return item.text


def _split_steps(items: list) -> tuple[list, list]:
    """(the other lines, a refusal's next steps)."""
    steps = [item for item in items if item.source.startswith("next-step")]
    return [item for item in items if item not in steps], steps


@pytest.fixture(scope="module")
def pasted(run, stub_env, spaced_step, tmp_path_factory):
    """pasted(shell): (Printed, Pasted) for every line tested in that shell, from
    one paste per shell."""
    cache = {}
    bare_env = printed_runs.without_crapkit_command(stub_env)

    def paste_group(shell: str, items: list, env: dict) -> list:
        results = shells.paste(shell, [_line(shell, item) for item in items], run.root, env,
                               tmp_path_factory.mktemp(f"paste-{shell}"))
        return list(zip(items, results))

    def paste(shell: str) -> list:
        """A refusal's next step is pasted where no `crapkit` command is on PATH:
        it is printed for a reader who started crapkit with `python -m crapkit`."""
        if shell not in cache:
            rest, steps = _split_steps([item for item in (*run.printed, *spaced_step)
                                        if _tested_in(shell, item)])
            cache[shell] = paste_group(shell, rest, stub_env) + paste_group(shell, steps, bare_env)
        return cache[shell]
    return paste


def _argv(result: shells.Pasted) -> list:
    lines = result.stdout.strip().splitlines()
    try:
        return json.loads(lines[-1])
    except (IndexError, ValueError):
        return [f"exit {result.code}", result.stdout[-300:], result.stderr[-300:]]


def _of(pairs: list, *sources: str) -> list:
    return [(item, result) for item, result in pairs if item.source in sources]


def _only(pairs: list, source: str) -> tuple:
    """The one (Printed, Pasted) pair of a source crapkit prints once."""
    found = _of(pairs, source)
    assert found, f"crapkit printed no {source} line"
    [pair] = found
    return pair


def _wrong(pairs: list) -> list:
    """The pasted lines whose argv crapkit read differs from what they must hand it."""
    return [(item.source, item.text, list(item.argv), _argv(result)) for item, result in pairs
            if printed_runs.meaning(_argv(result)) != printed_runs.meaning(item.argv)]


def _holding(pasted, shell: str, found) -> list:
    """The argv-carrying pairs whose argv has a word `found` accepts."""
    return [(item, result) for item, result in _of(pasted(shell), *ARGV_SOURCES)
            if any(found(word) for word in item.argv)]


@pytest.mark.parametrize("shell", HERE)
def test_every_printed_command_hands_crapkit_its_arguments(pasted, shell):
    """A report row's explain command and brief's commands, pasted unchanged,
    reach crapkit intact."""
    pairs = _of(pasted(shell), *ARGV_SOURCES)

    assert {item.source for item, _ in pairs} == set(ARGV_SOURCES)
    assert _wrong(pairs) == []


@pytest.mark.parametrize("shell", HERE)
def test_a_leading_hyphen_path_reaches_crapkit_as_a_path(pasted, shell):
    """R124: `-top.py` in a printed command is a path, not an option: the
    explain, gate and scoped lines each hand it to crapkit after `--`."""
    pairs = _holding(pasted, shell, lambda word: word.startswith("-") and not word.startswith("--"))

    assert {item.source for item, _ in pairs} == set(ARGV_SOURCES)
    assert _wrong(pairs) == []


def test_a_quoted_handle_reaches_crapkit_intact(pasted):
    """R125: the explain line for `run( self , mode = "fast" )` hands crapkit
    the handle with its double quotes, in each shell it is pasted into: on
    Windows crapkit prints it as an encoded PowerShell command, pasted into cmd."""
    pairs = [pair for shell in HERE for pair in _holding(pasted, shell, lambda word: '"' in word)]

    assert {item.source for item, _ in pairs} == {"report"}
    assert _wrong(pairs) == []


def _first_line(result) -> str:
    return (result.stdout.splitlines() or [""])[0]


def _wanted(argv, result, names: dict) -> str:
    """explain's first line names the path and the long name; other commands
    are judged by their exit code alone."""
    _, _, positionals = printed_runs.meaning(argv)
    if argv[0] != "explain":
        return _first_line(result)
    return f"{positionals[0]}  {names[positionals]}"


def _problem(driver, argv, names: dict):
    result = driver.run(*argv)
    if (result.code, _first_line(result)) == (0, _wanted(argv, result, names)):
        return None
    return argv, result.code, _first_line(result), result.stderr[-300:]


def _argvs(run) -> list[tuple]:
    return sorted({item.argv for item in run.printed if item.source in ARGV_SOURCES})


def test_every_printed_argv_exits_0_and_names_its_function(run):
    driver = drive.Driver(run.root, date_now=repos.EPOCH + 86_400)
    names = {(row["path"], printed_runs.handle(row)): row["function"] for row in run.rows}
    problems = [_problem(driver, argv, names) for argv in _argvs(run)]

    assert list(filter(None, problems)) == []


def _decoded(text: str) -> str:
    return base64.b64decode(text[len(ENCODED):]).decode("utf-16-le")


def spells_the_console_script(text: str) -> bool:
    """A plain line starts with `crapkit `; an encoded one looks crapkit up by name."""
    if text.startswith(ENCODED):
        return "Get-Command crapkit " in _decoded(text)
    return text.startswith("crapkit ")


@pytest.mark.parametrize("source", ("gate", "scoped"))
def test_brief_commands_spell_the_console_script(run, source):
    """R123 (#37): docs/agent-json.md shows brief's commands verbatim as
    `crapkit ...`; each worklist row's brief prints one of each."""
    brief = [item.text for item in run.printed if item.source == source]

    assert len(brief) == len(run.rows)
    assert [text for text in brief if not spells_the_console_script(text)] == []


def _step_params():
    params = []
    for shell in HERE:
        for source in ("next-step", "next-step-space"):
            ruling = DEFECTS.get((shell, source))
            mark = rulings.applies(ruling) if ruling else None
            marks = [mark] if isinstance(mark, pytest.MarkDecorator) else []
            params.append(pytest.param(shell, source, marks=marks, id=f"{source}-{shell}"))
    return params


@pytest.mark.parametrize("shell, source", _step_params())
def test_the_next_step_a_refusal_prints_runs_in_every_shell(pasted, shell, source):
    """R127: `<python> -m crapkit coverage`, printed when `python -m crapkit` started
    crapkit, runs where it is read and no `crapkit` command is installed: from a
    plain interpreter path and from one whose directory name holds a space."""
    _, result = _only(pasted(shell), source)
    ruling = DEFECTS.get((shell, source))
    if ruling:
        rulings.pin_ruling(ruling, crapkit=f"exit {result.code}", oracle="exit 0")

    assert (result.code, _argv(result)) == (0, ["coverage"])


@pytest.mark.parametrize("shell", HERE)
def test_each_clearing_spelling_clears_the_override_in_its_shell(pasted, shell):
    """R128: the receipt names a spelling for every shell a reader commits from,
    and each one clears the variable there."""
    pairs = _of(pasted(shell), "clear")

    assert pairs, f"the override receipt names no spelling for {shell}"
    assert [(item.text, result.stdout.strip()) for item, result in pairs
            if result.stdout.strip().splitlines()[-1:] != ["UNSET"]] == []


@pytest.mark.parametrize("shell", HERE)
def test_the_pip_line_hands_pip_one_requirement(pasted, shell):
    """R126: `pip install "crapkit[py]"` reaches pip as install crapkit[py]. The
    stand-in crapkit takes pip's place: the program named first does not change
    how any of these shells splits what follows it."""
    item, result = _only(pasted(shell), "pip")

    assert _argv(result) == list(item.argv)


# --- the printed text, per OS -------------------------------------------------------------

@pytest.mark.golden
def test_the_printed_text_equals_its_golden(run):
    golden = printed_runs.golden_path()

    assert printed_runs.printed_text(run) == golden.read_bytes().decode("utf-8"), (
        f"regenerate {golden.name} with `python tools/accuracy/regenerate.py goldens` on this "
        "OS, then declare the change")


def test_every_shell_the_plan_names_is_installed_here():
    missing = []
    for shell in HERE:
        try:
            shells.executable(shell)
        except shells.ShellMissing as absent:
            missing.append(str(absent))

    assert missing == []
    assert os.name != "nt" or set(HERE) == {"cmd", "cmd-delayed", "powershell", "pwsh", "bash"}
