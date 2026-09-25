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
import json
import os
from pathlib import Path
import re
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
