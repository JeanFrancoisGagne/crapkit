"""git display settings and colour variables leave every git read crapkit parses unchanged.

crapkit reads git output as data at the churn log (worklist), `git log -L`
(explain --history), the co-change walk (coupling) and the diff rescore --gate
judges. gitio pins the settings that change a parsed read (core.quotePath,
diff.relative, and --no-color with fixed prefixes on a patch), and git colours
a pipe only when the user's config says `always`. These rows set that config
through GIT_CONFIG_COUNT, which every git crapkit spawns reads, plus the colour,
width, locale and time-zone variables a CI job or a shell sets, and hold every
`--json` answer to the bytes of the run with none of them.

Each row reads a fresh copy of one measured repo with crapkit's git-derived
caches removed, so every command asks git again under the row's settings.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from repo_templates import copy_of, template

run_cli = cli_runner(encoding="utf-8", errors="replace")

ESC = "\x1b"
TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]

[[lane]]
name = "unit"
command = "python make_cov.py"
artifact = "cov.json"
parser = "istanbul"
scopes = ["src"]
env = { COVERAGE_PROCESS_CONFIG = "", COV_CORE_DATAFILE = "" }
"""
MAKE_COV = """import json, os
app = os.path.join(os.getcwd(), "src", "app.ts")
cov = {app: {"path": app,
             "fnMap": {"0": {"name": "tiny", "decl": {"start": {"line": 1}},
                             "loc": {"start": {"line": 1}, "end": {"line": 1}}}},
             "f": {"0": 1}, "branchMap": {}, "b": {}}}
with open("cov.json", "w", encoding="utf-8") as fh:
    json.dump(cov, fh)
"""
APP = "export function tiny(a: number) { return a; }\n"


def _tangled(k: int, step: str = "r += 3") -> str:
    return ("export function tangled(a: number, b: number): number {\n"
            f"  let r = {k};\n"
            "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
            f"  if (a > 10 && b > 10) {{ {step}; }}\n"
            "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
            "  return r;\n}\n")


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _build(repo: Path) -> None:
    """Six commits that edit `tangled` and `tiny` together, so churn, coupling
    and `log -L` all have rows; one scored run; then a dirty edit for the gate."""
    for rel, text in {"crapkit.toml": TOML, "make_cov.py": MAKE_COV, "src/app.ts": APP,
                      "src/tangled.ts": _tangled(0), ".gitignore": ".crapkit/\ncov.json\n"}.items():
        _write(repo, rel, text)
    git_init_repo(repo)
    git_commit_all(repo, "init")
    for i in range(1, 7):
        _write(repo, "src/tangled.ts", _tangled(i))
        _write(repo, "src/app.ts", APP.replace("a;", f"a + {i};"))
        git_commit_all(repo, f"change {i}\n\nbody line {i}")
    result = run_cli(repo, "coverage")
    assert result.returncode == 0, result.stdout + result.stderr
    _write(repo, "src/tangled.ts", _tangled(9, "r += 4"))


def _git_config(**pairs: str) -> dict:
    env = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (key, value) in enumerate(pairs.items()):
        env.update({f"GIT_CONFIG_KEY_{i}": key.replace("__", "."), f"GIT_CONFIG_VALUE_{i}": value})
    return env


ROWS = {
    "color.ui=always": _git_config(color__ui="always"),
    "color.diff=always color.status=always": _git_config(color__diff="always", color__status="always"),
    "color.ui=always TERM=dumb": {**_git_config(color__ui="always"), "TERM": "dumb"},
    "color.ui=always log.decorate log.showRoot log.abbrevCommit": _git_config(
        color__ui="always", log__decorate="full", log__showRoot="true", log__abbrevCommit="true"),
    "diff.noprefix diff.mnemonicPrefix diff.renames=copies diff.relative=false": _git_config(
        diff__noprefix="true", diff__mnemonicPrefix="true", diff__renames="copies", diff__relative="false"),
    "format.pretty=oneline log.date=relative core.quotePath=true": _git_config(
        format__pretty="oneline", log__date="relative", core__quotePath="true"),
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb": {"TERM": "dumb"},
    "NO_COLOR=1": {"NO_COLOR": "1"},
    "COLUMNS=40": {"COLUMNS": "40"},
    "LANG=C LC_ALL=C": {"LANG": "C", "LC_ALL": "C"},
    "LANG=fr_FR.UTF-8 LC_ALL=fr_FR.UTF-8 LANGUAGE=fr": {"LANG": "fr_FR.UTF-8", "LC_ALL": "fr_FR.UTF-8",
                                                        "LANGUAGE": "fr"},
    "TZ=Pacific/Kiritimati": {"TZ": "Pacific/Kiritimati"},
}
_KNOBS = ("FORCE_COLOR", "NO_COLOR", "PY_COLORS", "PYTHON_COLORS", "TERM", "CLICOLOR_FORCE", "COLUMNS",
          "LANG", "LC_ALL", "LANGUAGE", "TZ", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")
COMMANDS = {
    "worklist": ("worklist", "--json"),
    "explain --history": ("explain", "src/tangled.ts", "tangled", "--history", "--json"),
    "coupling": ("coupling", "--min-support", "2", "--json"),
    "rescore --gate": ("rescore", "src/tangled.ts", "--gate", "--json"),
}


def _fresh(tmp_path: Path, name: str) -> Path:
    """A copy of the measured repo holding its store and artifact, and none of
    the caches a git read fills, so each row's commands ask git themselves."""
    repo = copy_of(template(tmp_path, "git-display", _build), tmp_path / name)
    for cached in (repo / ".crapkit").glob("*"):
        if cached.name.startswith(("churn-", "coupling-")):
            cached.unlink()
    return repo


def _answers(repo: Path, env: dict) -> dict:
    extra = {**dict.fromkeys(_KNOBS), **env}
    return {name: run_cli(repo, *argv, env_extra=extra) for name, argv in COMMANDS.items()}


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """The answers with no setting, over a repo whose reads all have rows."""
    answers = _answers(_fresh(tmp_path_factory.mktemp("plain"), "repo"), {})
    for name, result in answers.items():
        assert result.returncode in (0, 6), f"{name}: {result.stdout}{result.stderr}"
    assert json.loads(answers["coupling"].stdout)["pairs"], "coupling found a pair to read"
    assert json.loads(answers["explain --history"].stdout)["functions"][0]["commits"], "log -L had commits"
    return answers


@pytest.mark.parametrize("row", list(ROWS), ids=list(ROWS))
def test_git_reads_answer_the_same_bytes_under_display_settings(tmp_path, plain, row):
    answers = _answers(_fresh(tmp_path, "repo"), ROWS[row])

    for name, result in answers.items():
        assert ESC not in result.stdout + result.stderr, f"{row}: {name} printed an escape code"
        assert (result.returncode, result.stdout) == (plain[name].returncode, plain[name].stdout), (
            f"{row}: {name} answered differently from the run with no setting")


def test_the_colour_rows_do_colour_a_git_pipe(tmp_path):
    """What makes the rows above a test: under color.ui=always git colours a
    pipe, so a read gitio left unpinned would carry the codes."""
    repo = _fresh(tmp_path, "repo")
    env = {**os.environ, **ROWS["color.ui=always"]}
    diff = subprocess.run(["git", "diff"], cwd=repo, env=env, capture_output=True, text=True, check=True)

    assert ESC in diff.stdout
