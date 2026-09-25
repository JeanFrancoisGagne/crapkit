"""A repo whose names hold what shells treat specially, and every command crapkit
prints for it, measured once per session.

Names: a space, a leading hyphen, #, a non-ASCII letter, $ % ! and a backtick
in one path, & ( ) ^ in another, a single quote, a handle holding double quotes
(two classes that both define run(), one with a "fast" default), and two
anonymous JavaScript functions. Every function has ccn 7 in a cc-only scope
whose ceiling is 6, so each is over its ceiling and gets a report row.

What it prints, as Printed(source, text, argv): `argv` is what the line must
hand the program, written from the worklist JSON (path, handle) and the docs,
never parsed out of the line.

- report: each row's drill-down command, `explain PATH HANDLE`;
- gate, scoped: each brief's commands.gate (`rescore PATH --gate`) and
  commands.scoped_tests (`test-scoped PATH`);
- next-step: what `python -m crapkit worklist` prints before any run, `run
  <python> -m crapkit coverage first`; refusal_next_step() gives it for another
  spelling of the interpreter, such as spaced_interpreter()'s link through a
  directory whose name holds a space;
- pip: init's note for an interpreter without pytest_cov, `pip install
  "crapkit[py]"`, whose argv must be `install crapkit[py]`;
- clear: the override receipt's spellings that clear CRAPKIT_OVERRIDE_REASON,
  each with the shell the receipt names for it.
"""
from __future__ import annotations

from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from filelock import FileLock

from accuracy.kit import drive, repos

BODY = ("    if v == 1:\n        return 1\n    if v == 2:\n        return 2\n"
        "    if v == 3:\n        return 3\n    if v == 4:\n        return 4\n"
        "    if v == 5:\n        return 5\n    if v == 6:\n        return 6\n    return 0\n")
JS_BODY = ("  if (v === 1) return 1;\n  if (v === 2) return 2;\n  if (v === 3) return 3;\n"
           "  if (v === 4) return 4;\n  if (v === 5) return 5;\n  if (v === 6) return 6;\n"
           "  return 0;\n")
INDENTED = "".join(f"    {line}\n" for line in BODY.splitlines())
CONFIG = """[crapkit]
target = 6
alert_command = "python -c pass"

[crapkit.scoped_tests]
src = 'python -c "import sys; print(sys.argv[1:])" {files}'

[[scope]]
name = "src"
paths = ["src", "-top.py"]
languages = ["python", "javascript"]
coverage_optional = true
"""
FILES = {
    "crapkit.toml": CONFIG,
    "src/with space.py": "def spaced(v):\n" + BODY,
    "-top.py": "def top(v):\n" + BODY,
    "src/x#y.py": "def hashed(v):\n" + BODY,
    "src/café.py": "def café(v):\n" + BODY,
    "src/a$b%c!d`e.py": "def expands(v):\n" + BODY,
    "src/a&b(c)^d.py": "def operators(v):\n" + BODY,
    "src/it's.py": "def quoted(v):\n" + BODY,
    "src/twins.py": ('class Fast:\n    def run(self, mode="fast"):\n' + INDENTED
                     + "\n\nclass Plain:\n    def run(self):\n" + INDENTED),
    "src/anon.js": ("export default function (v) {\n" + JS_BODY + "}\n\n"
                    "export const all = [1].map(function (v) {\n" + JS_BODY + "});\n"),
}
OVERRIDE = "src/late.py"
VARIABLE = "CRAPKIT_OVERRIDE_REASON"
SHELL_NAMES = {"Git Bash": ("bash",), "PowerShell": ("powershell", "pwsh"),
               "cmd.exe": ("cmd", "cmd-delayed")}
MANIFEST = "printed.json"


@dataclass(frozen=True)
class Printed:
    source: str
    text: str
    argv: tuple[str, ...] = ()
    shells: tuple[str, ...] = ()


def _cells(page: str) -> list[str]:
    return [html.unescape(code) for code in re.findall(r'<td class="cmd"><code>(.*?)</code>',
                                                       page, re.S)]


def _report(driver: drive.Driver, rows: list[dict]) -> list[Printed]:
    """Each report row's command; the page lists the rows in worklist order."""
    driver.run("report", "--out", "report.html")
    page = (driver.root / "report.html").read_bytes().decode("utf-8")
    return [Printed("report", text, runnable("explain", (), (row["path"], row["handle"])))
            for text, row in zip(_cells(page), rows)]


def meaning(argv) -> tuple:
    """(command, options, positionals) of an argv, read the POSIX way: a word
    that starts with `-` before a `--` is an option, `--` ends the options, and
    the rest are positionals in order."""
    head, tail = _at_end_of_options(list(argv[1:]))
    options = sorted(word for word in head if word.startswith("-"))
    positionals = [word for word in head if not word.startswith("-")] + tail
    return argv[0], tuple(options), tuple(positionals)


def _at_end_of_options(words: list[str]) -> tuple[list[str], list[str]]:
    cut = words.index("--") if "--" in words else len(words)
    return words[:cut], words[cut + 1:]


def runnable(command: str, options: tuple, positionals: tuple) -> tuple[str, ...]:
    """An argv any argparse CLI reads as meant: a `--` goes before the
    positionals when one of them starts with `-`."""
    cut = ("--",) if any(word.startswith("-") for word in positionals) else ()
    return (command, *options, *cut, *positionals)


def _brief(driver: drive.Driver, row: dict) -> list[Printed]:
    argv = runnable("brief", ("--json",), (row["path"], row["handle"]))
    commands = driver.run(*argv).json()["commands"]
    return [Printed("gate", commands["gate"], runnable("rescore", ("--gate",), (row["path"],))),
            Printed("scoped", commands["scoped_tests"], runnable("test-scoped", (),
                                                                 (row["path"],)))]


def next_step(stderr: str) -> str:
    return re.search(r"run `([^`]+)` first", stderr).group(1)


def python() -> Path:
    """The interpreter crapkit runs under: kit.drive's choice."""
    return Path(os.environ.get(drive.PYTHON_ENV) or sys.executable)


def refusal_next_step(source: str, interpreter: Path, root: Path) -> Printed:
    """The next step `<interpreter> -m crapkit worklist` names in a repo with no run."""
    done = subprocess.run([str(interpreter), "-m", "crapkit", "worklist"], cwd=root,
                          env=drive.child_env(), capture_output=True, text=True,
                          encoding="utf-8")
    return Printed(source, next_step(done.stderr), ("coverage",))


def unmeasured_repo(scratch: Path) -> Path:
    files = {"crapkit.toml": CONFIG, "src/a.py": "def f(x):\n    return x\n"}
    return repos.build(repos.Spec(steps=(repos.Commit(files=files),)), scratch / "unmeasured").root


def _install_root(executable: Path) -> Path:
    """A venv's root (it holds pyvenv.cfg), or the directory the interpreter is in."""
    venv = executable.parent.parent
    return venv if (venv / "pyvenv.cfg").is_file() else executable.parent


def spaced_interpreter(scratch: Path) -> Path:
    """The interpreter reached through a directory link whose name holds a space:
    a junction on Windows, which needs no privilege, a symlink elsewhere."""
    executable = python()
    root = _install_root(executable)
    link = scratch / "with space" / "python"
    link.parent.mkdir(parents=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(root)], check=True,
                       capture_output=True)
    else:
        link.symlink_to(root, target_is_directory=True)
    return link / executable.relative_to(root)


def unlink_spaced(scratch: Path) -> None:
    """Remove the link alone: never recurse into what it points at."""
    link = scratch / "with space" / "python"
    if os.name == "nt" and link.exists():
        os.rmdir(link)
    elif link.is_symlink():
        link.unlink()


_SPELLING = re.compile(r"`([^`]+)` in ([^,)]+)")


def clearing(receipt: str) -> list[Printed]:
    """Each spelling the receipt prints, with the shells it names for it; a
    receipt that names no shell (POSIX) prints one spelling for bash."""
    pairs = _SPELLING.findall(receipt)
    if pairs:
        return [Printed("clear", text, (), SHELL_NAMES[name.strip()]) for text, name in pairs]
    return [Printed("clear", re.search(r"`(unset [^`]+)`", receipt).group(1), (), ("bash",))]


def _override(driver: drive.Driver) -> list[Printed]:
    (driver.root / OVERRIDE).write_bytes(("def late(v):\n" + BODY).encode("utf-8"))
    repos.git(driver.root, "add", OVERRIDE)
    receipt = drive.Driver(driver.root, env={VARIABLE: "accepted for now"}).run("hook-precommit")
    repos.git(driver.root, "reset", "-q")
    (driver.root / OVERRIDE).unlink()
    return clearing(receipt.stdout + receipt.stderr)


def pip_line(stdout: str) -> str:
    """The install line the note puts in parentheses, however it quotes it."""
    return re.search(r"\((pip install \S+) when that is", stdout).group(1)


def _pip(scratch: Path) -> list[Printed]:
    """init's note, from a repo whose `python` cannot import pytest_cov."""
    bare = scratch / "bare-venv"
    subprocess.run([str(python()), "-m", "venv", "--without-pip", str(bare)], check=True)
    scripts = bare / ("Scripts" if os.name == "nt" else "bin")
    spec = repos.Spec(steps=(repos.Commit(files={
        "src/pkg/a.py": "def f(x):\n    return x\n", "tests/test_a.py": "def test_f():\n    pass\n",
        "pyproject.toml": '[project]\nname = "pkg"\nversion = "0"\n\n[tool.pytest.ini_options]\n'
                          'testpaths = ["tests"]\n'}),))
    root = repos.build(spec, scratch / "init-repo").root
    env = drive.child_env()
    env["PATH"] = os.pathsep.join((str(scripts), env["PATH"]))
    done = subprocess.run([str(python()), "-m", "crapkit", "init"], cwd=root, env=env,
                          capture_output=True, text=True, encoding="utf-8")
    return [Printed("pip", pip_line(done.stdout + done.stderr), ("install", "crapkit[py]"))]


@dataclass(frozen=True)
class PrintedRun:
    root: Path
    printed: tuple[Printed, ...]
    rows: tuple[dict, ...]


def _measure(work: Path) -> dict:
    root = repos.build(repos.Spec(steps=(repos.Commit(files=FILES),)), work / "repo").root
    printed = [refusal_next_step("next-step", python(), root)]
    driver = drive.Driver(root, date_now=repos.EPOCH + 86_400)
    driver.run("coverage")
    rows = driver.json("worklist")["active"]
    printed += _report(driver, rows)
    printed += [item for row in rows for item in _brief(driver, row)]
    printed += _override(driver) + _pip(work)
    return {"root": str(root), "rows": rows,
            "printed": [[p.source, p.text, list(p.argv), list(p.shells)] for p in printed]}


def golden_path(platform: str = sys.platform) -> Path:
    """The printed text differs by OS on purpose: Windows quotes for cmd.exe and
    PowerShell, POSIX for sh."""
    name = "win32.tsv" if platform == "win32" else "posix.tsv"
    return Path(__file__).resolve().parent / "goldens" / "printed" / name


def printed_text(run: PrintedRun) -> str:
    """source<TAB>text per printed line, the interpreter path replaced."""
    from accuracy.corpus_goldens import golden_runs
    lines = []
    for item in run.printed:
        text = item.text
        for spelling in golden_runs.interpreter_spellings():
            text = text.replace(spelling, "<python>")
        lines.append(f"{item.source}\t{text}")
    return "\n".join(lines) + "\n"


def measure(base: Path) -> PrintedRun:
    """The session's one measurement, shared by xdist workers through a lock."""
    work = base / "printed"
    base.mkdir(parents=True, exist_ok=True)
    with FileLock(str(work) + ".lock"):
        manifest = work / MANIFEST
        if not manifest.is_file():
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            manifest.write_text(json.dumps(_measure(work)), encoding="utf-8")
        data = json.loads(manifest.read_text(encoding="utf-8"))
    printed = tuple(Printed(s, t, tuple(a), tuple(h)) for s, t, a, h in data["printed"])
    return PrintedRun(Path(data["root"]), printed, tuple(data["rows"]))
