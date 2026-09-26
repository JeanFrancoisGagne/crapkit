r"""End-to-end: an istanbul report that spells this checkout another way joins.

`tests/e2e/test_lane_absolute_paths_e2e.py` proves the coveragepy half of the
absolute-path refusal: coverage.py has a switch to write relative paths, and
the refusal names it. An istanbul reporter always writes absolute paths, and
its reader rebases every one that lands in this checkout. It used to strip the
root as literal text, so a key that named this checkout in another spelling
stayed absolute and the lane FAILED, told to point the reporter at the
checkout it had measured: a report made from a shell standing in `c:\...`, one
reached through a junction or symlink, one keyed `\\?\C:\...`.

Staging it needs a root spelled two ways. The lane's script reaches the
checkout through its parent (`<parent>/mini-build/../mini/src`), which is what
a reporter given an unnormalized `root` or `cwd` writes into every key. Case,
drive-letter case and symlinks stage the same thing on one platform each
(tests/unit/test_coverage_istanbul.py feeds each of them to the reader); this
spelling stages it on both.

The artifact cannot be committed: its keys carry the tmp directory the fixture
is copied into. The lane writes it, the way the coveragepy test's lane does.
"""
import json
import shutil
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")

# An istanbul coverage-final.json about THIS checkout's src/app.ts, keyed
# through a sibling directory and back — the shape a reporter writes when its
# root option was joined rather than resolved.
ABSOLUTE_ISTANBUL = (
    "import json, os\n"
    "root = os.getcwd()\n"
    "parent, name = os.path.split(root)\n"
    "sibling = os.path.join(parent, name + '-build')\n"
    "os.makedirs(sibling, exist_ok=True)\n"
    "app = os.path.join(sibling, '..', name, 'src', 'app.ts')\n"
    "artifact = {app: {'path': app,\n"
    "    'fnMap': {'0': {'name': 'dispatch', 'decl': {'start': {'line': 1}},\n"
    "                    'loc': {'start': {'line': 1}, 'end': {'line': 10}}}},\n"
    "    'f': {'0': 3},\n"
    "    'branchMap': {'0': {'loc': {'start': {'line': 2}},\n"
    "                        'locations': [{'start': {'line': 2}}]}},\n"
    "    'b': {'0': [1]}}}\n"
    "os.makedirs(os.path.join(root, 'coverage'), exist_ok=True)\n"
    "with open(os.path.join(root, 'coverage', 'coverage-final.json'), 'w',\n"
    "          encoding='utf-8') as fh:\n"
    "    json.dump(artifact, fh)\n"
)

# The python lane's artifact, written relative and reaching its scope, so the
# only lane that fails is the istanbul one and the only advice on stderr is the
# advice under test.
RELATIVE_COV = (
    "import json\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {'pylib/mod.py': {'functions': {\n"
    "    'guarded': {'start_line': 1, 'executed_lines': [1, 2], 'missing_lines': [],\n"
    "                'summary': {'covered_lines': 2, 'num_statements': 2,\n"
    "                            'num_branches': 2, 'covered_branches': 2}}}}}}\n"
    "open('coverage-py.json', 'w', encoding='utf-8').write(json.dumps(report))\n"
)

LANES = """[[lane]]
name = "unit"
command = "python make_abs_istanbul.py"
artifact = "coverage/coverage-final.json"
parser = "istanbul"
scopes = ["src"]
full_suite = false

[[lane]]
name = "py"
command = "python make_rel_cov.py"
artifact = "coverage-py.json"
parser = "coveragepy"
scopes = ["py"]
full_suite = false
"""


def _swap_lanes(config: str) -> str:
    """The fixture's two real lanes, replaced by the artifact writers. They are
    the tail of the file, so truncating at the first one is the whole edit."""
    head, marker, _ = config.partition("[[lane]]")
    assert marker, "the fixture no longer declares a lane"
    return head + LANES


@pytest.fixture()
def istanbul_absolute_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini"
    shutil.copytree(FIXTURES / "mini_repo", repo)
    config = repo / "crapkit.toml"
    config.write_text(_swap_lanes(config.read_text(encoding="utf-8")),
                      encoding="utf-8", newline="\n")
    (repo / "make_abs_istanbul.py").write_text(ABSOLUTE_ISTANBUL,
                                               encoding="utf-8", newline="\n")
    (repo / "make_rel_cov.py").write_text(RELATIVE_COV, encoding="utf-8", newline="\n")
    (repo / ".gitignore").write_text(
        ".crapkit/\ncoverage/\ncoverage-py.json\njunit.xml\n__pycache__/\n",
        encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "init")
    return repo


def test_an_istanbul_report_keyed_through_another_spelling_of_the_root_joins(
        istanbul_absolute_repo):
    res = run_cli(istanbul_absolute_repo, "coverage", "--json")

    assert res.returncode == 0, res.stderr
    assert "FAILED" not in res.stderr, res.stderr


def test_the_rebased_istanbul_scope_is_measured(istanbul_absolute_repo):
    """The lane's scope reads as measured, not as a tooling gap: the key named
    this checkout, so its function carries the coverage the report recorded."""
    summary = json.loads(run_cli(istanbul_absolute_repo, "coverage", "--json").stdout)

    assert "unit" not in summary.get("lane_failures", {}), summary
    assert summary["no_lane"] == 0, summary


def test_the_rebased_istanbul_lane_carries_no_refusal_advice(istanbul_absolute_repo):
    err = run_cli(istanbul_absolute_repo, "coverage", "--json").stderr

    assert "relative_files" not in err and "cwd/root option" not in err, err
    assert "different tree" not in err, "the paths do resolve under this checkout"
