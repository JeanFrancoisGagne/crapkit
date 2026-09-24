r"""End-to-end: a file name git or a shell spells specially joins its own score.

git hands crapkit every tracked name through `ls-files -z`, `diff -z` and diff
headers it C-quotes (a non-ASCII byte, a double quote, a tab). The name then
meets its coverage row, its churn and the staged diff the commit gate reads. A
name with a space, `[id]`, `%PATH%`, `c^a!r`, a leading dash, `$HOME` or a
backtick is one file at each of those steps, so the gate names it and brief
reads it measured with its churn.

A tracked POSIX name holding a backslash is the one exception, as ruled: every
report crapkit reads folds `\` into a separator on every OS, so the coverage
key for `src/we\ird.py` reads `src/we/ird.py`, and the file scores untested.
git's side still names it, so the commit gate still sees the breach.

One repo holds every name, measured once by a lane that writes an istanbul
report keyed by absolute path, the way jest and vitest key it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git, git_commit_all, git_init_repo

run_cli = cli_runner(timeout=300, encoding="utf-8", errors="replace")

WINDOWS = os.name == "nt"

EVERY_OS = {
    "space": "src/sp ace/mod.py",
    "non-ascii": "src/ünï/mod.py",
    "glob-brackets": "src/[id]/mod.py",
    "apostrophe-ampersand": "src/o'k&b/mod.py",
    "percent-hash": "src/p%20#x/mod.py",
    "percent-pair": "src/%PATH%/mod.py",
    "caret-bang": "src/c^a!r/mod.py",
    "leading-dash": "src/-dash.py",
}
POSIX_ONLY = {
    "double-quote": 'src/q"uote/mod.py',
    "tab": "src/t\tab.py",
    "star-question": "src/*?/mod.py",
    "dollar-backtick": "src/$HOME`x`/mod.py",
    "backslash": "src/we\\ird.py",
}
NAMES = {**EVERY_OS, **({} if WINDOWS else POSIX_ONLY)}

CLEAN = "def f(n):\n    if n > 1:\n        n = n + 1\n    return n\n"
TANGLED = ("def f(n):\n" + "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(7))
           + "    return n\n")

# Every function fully covered, keyed by absolute path. The key for a POSIX
# name holding a backslash keeps it, and the reader folds it.
GEN_COV = """import json, os
root = os.getcwd()
report = {}
for rel in open('sources.txt', encoding='utf-8').read().split('\\0'):
    if not rel:
        continue
    key = os.path.join(root, *rel.split('/'))
    report[key] = {'path': key,
        'fnMap': {'0': {'name': 'f', 'decl': {'start': {'line': 1}},
                        'loc': {'start': {'line': 1}, 'end': {'line': 4}}}},
        'f': {'0': 1},
        'branchMap': {'0': {'loc': {'start': {'line': 2}},
                            'locations': [{'start': {'line': 2}}, {'start': {'line': 2}}]}},
        'b': {'0': [1, 1]}}
os.makedirs('coverage', exist_ok=True)
with open(os.path.join('coverage', 'coverage-final.json'), 'w', encoding='utf-8') as fh:
    json.dump(report, fh)
"""

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "unit"
command = "python gen_cov.py"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
full_suite = false
"""


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture(scope="module")
def joined(tmp_path_factory) -> dict:
    """brief's packet for each name after one measured run, then the commit
    gate over every name staged with a breach."""
    repo = tmp_path_factory.mktemp("names") / "repo"
    repo.mkdir()
    git_init_repo(repo)
    git(repo, "config", "core.autocrlf", "false")
    _write(repo, ".gitignore", "coverage/\n.crapkit/\n")
    _write(repo, "crapkit.toml", TOML)
    _write(repo, "gen_cov.py", GEN_COV)
    _write(repo, "sources.txt", "".join(rel + "\0" for rel in NAMES.values()))
    for rel in NAMES.values():
        _write(repo, rel, CLEAN)
    git_commit_all(repo, "one file per name")

    measured = run_cli(repo, "coverage", "--json")
    assert measured.returncode == 0, measured.stderr
    briefs = {rel: run_cli(repo, "brief", rel, "f", "--json") for rel in NAMES.values()}
    for rel in NAMES.values():
        _write(repo, rel, TANGLED)
    git(repo, "add", "-A")
    return {"briefs": briefs, "hook": run_cli(repo, "hook-precommit")}


@pytest.mark.parametrize("which", NAMES)
def test_the_commit_gate_names_the_staged_breach_in_each_name(joined, which):
    hook = joined["hook"]

    assert hook.returncode == 6, hook.stderr
    assert NAMES[which] in hook.stdout + hook.stderr


@pytest.mark.parametrize("which", [name for name in NAMES if name != "backslash"])
def test_brief_reads_each_name_measured_with_its_churn(joined, which):
    brief = joined["briefs"][NAMES[which]]
    assert brief.returncode == 0, brief.stderr
    packet = json.loads(brief.stdout)

    assert packet["scored"]["flag"] == "measured"
    assert packet["churn"]["commits"] >= 1


@pytest.mark.skipif(WINDOWS, reason="needs a POSIX filesystem: NTFS refuses \\ in a name")
def test_a_tracked_backslash_name_reads_untested_by_design(joined):
    """The report's key folds to src/we/ird.py, which git does not hold."""
    brief = joined["briefs"][POSIX_ONLY["backslash"]]
    assert brief.returncode == 0, brief.stderr
    packet = json.loads(brief.stdout)

    assert packet["scored"]["flag"] == "untested"
    assert packet["churn"]["commits"] >= 1
