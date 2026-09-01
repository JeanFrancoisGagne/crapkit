"""End-to-end: the istanbul half of the absolute-path refusal.

`tests/e2e/test_lane_absolute_paths_e2e.py` proves the coveragepy half three
ways and never builds an istanbul lane, so the branch of `_absolute_fix` a JS
consumer meets had nothing behind it. The two messages name different knobs,
and the istanbul one is the harder to write: the reader strips this checkout's
root off every measured path literally, so a path that stayed absolute means the
reporter spelled that root some other way, and no key on the lane can rebase it.

Staging it needs a root spelled two ways, because a reporter that spells the
root exactly as crapkit does is rebased and joins fine. The lane's script
reaches the checkout through its parent (`<parent>/mini-build/../mini/src`),
which is what a reporter given an unnormalized `root` or `cwd` writes into every
key. crapkit's own root is the normalized spelling, the literal strip misses,
and the key stays absolute while still resolving under the checkout. Case and
symlinks stage the same thing on one platform each; this spelling stages it on
both.

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


def test_an_istanbul_lane_is_refused_and_told_about_its_own_reporter(istanbul_absolute_repo):
    res = run_cli(istanbul_absolute_repo, "coverage", "--json")

    assert res.returncode == 5, res.stderr
    assert "lane 'unit' FAILED" in res.stderr
    assert "under this checkout" in res.stderr
    assert "cwd/root option" in res.stderr


def test_the_istanbul_refusal_carries_no_coveragepy_advice(istanbul_absolute_repo):
    """The bug this covers. `relative_files` is a coverage.py key no JS reporter
    reads, and a lane told to set it in pyproject.toml cannot act on the advice."""
    err = run_cli(istanbul_absolute_repo, "coverage", "--json").stderr

    assert "relative_files" not in err
    assert "[tool.coverage.run]" not in err and ".coveragerc" not in err
    assert "path_prefix" not in err, "a knob the istanbul reader never reads"
    assert "different tree" not in err, "the paths do resolve under this checkout"


def test_the_refused_istanbul_scope_reads_as_a_tooling_gap(istanbul_absolute_repo):
    """Same contract the coveragepy half earned: a refused lane leaves its scopes
    `no-lane`, never a grade assembled out of a path-spelling mistake."""
    summary = json.loads(run_cli(istanbul_absolute_repo, "coverage", "--json").stdout)

    assert summary["lane_failures"]["unit"]
    assert summary["no_lane"] > 0
