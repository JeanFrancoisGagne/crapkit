r"""A lane with path_prefix is refused another checkout's report, as one without it is.

The coverage.py reader glues path_prefix onto every key, an absolute one too,
and the wrong-tree check asked whether a scope reached the glued key:
`backend/` + `/other/checkout/pkg/mod.py` sits under the `backend` scope. So a
monorepo lane fed a report from another checkout scored every function in its
scope untested with exit 0, while the same lane without path_prefix failed with
the wrong-tree refusal. The check now asks only keys the runner wrote
relative to this checkout.

Real git, real CLI, the prefix spelled each way crapkit.toml reads it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

run_cli = cli_runner(encoding="utf-8", errors="replace")

# A coverage.py report keying pkg/mod.py the way argv[1] says, written where
# the lane's artifact says.
_GEN = (
    "import json, sys\n"
    "report = {'meta': {'branch_coverage': True}, 'files': {sys.argv[1]: {'functions': {\n"
    "    'f': {'start_line': 1, 'executed_lines': [1, 2, 3], 'missing_lines': [4],\n"
    "          'summary': {'covered_lines': 3, 'num_statements': 4,\n"
    "                      'num_branches': 2, 'covered_branches': 1}}}}}}\n"
    "open('cov.json', 'w', encoding='utf-8').write(json.dumps(report))\n"
)

# How crapkit.toml may spell the prefix: each reads as `backend`.
PREFIXES = ["backend", "backend/", "backend\\", "./backend", ".\\backend\\"]

# Another checkout's pkg/mod.py, as a runner on either OS writes it.
ELSEWHERE = ["/other/checkout/backend/pkg/mod.py", "C:/other/checkout/backend/pkg/mod.py"]


def _repo(tmp_path: Path, prefix: str, key: str) -> Path:
    repo = tmp_path / "repo"
    files = {
        "backend/pkg/__init__.py": "",
        "backend/pkg/mod.py": "def f(x):\n    if x:\n        return 1\n    return 2\n",
        "gen_cov.py": _GEN,
        ".gitignore": ".crapkit/\ncov.json\n",
        "crapkit.toml": (
            "[[scope]]\nname = 'backend'\npaths = ['backend']\nlanguages = ['python']\n\n"
            "[exclude]\nglobs = ['gen_cov.py']\n\n"
            "[[lane]]\nname = 'py'\nparser = 'coveragepy'\nscopes = ['backend']\n"
            f"path_prefix = '{prefix}'\nartifact = 'cov.json'\nfull_suite = false\n"
            f"command = 'python gen_cov.py {key}'\n"),
    }
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body, encoding="utf-8")
    git_init_repo(repo)
    git_commit_all(repo, "sources")
    return repo


@pytest.mark.parametrize("key", ELSEWHERE)
@pytest.mark.parametrize("prefix", PREFIXES)
def test_a_prefixed_lane_fed_another_checkouts_report_fails(tmp_path, prefix, key):
    res = run_cli(_repo(tmp_path, prefix, key), "coverage", "--json")

    assert res.returncode == 5, res.stdout + res.stderr
    assert "lane 'py' FAILED" in res.stderr
    assert "describes a different tree" in res.stderr
    assert key in res.stderr, "the path is quoted as the runner wrote it"
    assert json.loads(res.stdout)["error"]["exit"] == 5


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_same_lane_scores_a_report_about_this_checkout(tmp_path, prefix):
    """The control: the key the runner writes from backend/ joins its file."""
    res = run_cli(_repo(tmp_path, prefix, "pkg/mod.py"), "coverage", "--json")

    assert res.returncode == 0, res.stderr
    assert "describes a different tree" not in res.stderr
    assert json.loads(res.stdout)["lane_failures"] == {}
