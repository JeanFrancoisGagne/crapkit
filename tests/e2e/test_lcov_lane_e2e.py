"""An lcov lane end to end, from a tracefile cargo-llvm-cov 0.9.1 recorded.

The fixture under tests/fixtures/coverage/lcov is a Rust crate and the
tracefile cargo-llvm-cov wrote for it (its README names the command). Each
case builds a git repo around the crate, declares a lane with parser = "lcov"
whose command copies the tracefile into place, and runs the CLI: coverage
exits 0, worklist shows cov and add-tests, brief lists the DA-0 lines, and
explain --tests has no contexts to show. The SF keys are tried as recorded
(relative), as cargo-llvm-cov writes them (absolute, under the checkout) and
under path_prefix; keys from another checkout are refused with exit 5.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo
from crapkit.coverage_lcov import WRONG_TREE_FIX

run_cli = cli_runner(encoding="utf-8", errors="replace")

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
RECORDED = (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8")
CRATE = ("Cargo.toml", "drive.rs", "shapes.rs")
SHAPES = "rust/shapes.rs"

# The lane's command: copy the tracefile the test placed into the artifact path.
_COPY = ("from pathlib import Path\n"
         "Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
         "Path('.crapkit/cov/lcov.info').write_bytes(Path('tracefile.lcov').read_bytes())\n")


def _config(scope: str, path_prefix: str) -> str:
    prefix = f'path_prefix = "{path_prefix}"\n' if path_prefix else ""
    return ("[crapkit]\ntarget = 5\n\n"
            f'[[scope]]\nname = "core"\npaths = ["{scope}"]\nlanguages = ["rust"]\n\n'
            '[exclude]\nglobs = ["make_lcov.py"]\n\n'
            '[[lane]]\nname = "rust"\ncommand = "python make_lcov.py"\n'
            'artifact = ".crapkit/cov/lcov.info"\nparser = "lcov"\nscopes = ["core"]\n'
            + prefix)


def _repo(tmp_path: Path, sf_root, path_prefix: str = "") -> Path:
    """The crate under `path_prefix` (the repo root when empty), the recorded
    tracefile with each `SF:rust/` key respelled by `sf_root(repo)`."""
    repo = Path(os.path.realpath(tmp_path)) / "repo"
    crate = repo / path_prefix / "rust" if path_prefix else repo / "rust"
    crate.mkdir(parents=True)
    for name in CRATE:
        (crate / name).write_bytes((FIXTURE / "rust" / name).read_bytes())
    files = {"crapkit.toml": _config(path_prefix or "rust", path_prefix),
             "make_lcov.py": _COPY, ".gitignore": ".crapkit/\n",
             "tracefile.lcov": RECORDED.replace("SF:rust/", f"SF:{sf_root(repo)}rust/")}
    for rel, body in files.items():
        (repo / rel).write_text(body, encoding="utf-8", newline="\n")
    git_init_repo(repo)
    git_commit_all(repo, "a rust crate and its lcov lane")
    return repo


def _in_tree(repo: Path) -> str:
    return f"{repo.as_posix()}/"


KEYS = {"recorded": (lambda repo: "", ""), "absolute": (_in_tree, ""),
        "under-path-prefix": (lambda repo: "", "crates/core")}


@pytest.fixture(params=list(KEYS))
def measured(request, tmp_path):
    """A repo whose lcov lane ran: (repo, the shapes.rs key)."""
    sf_root, path_prefix = KEYS[request.param]
    repo = _repo(tmp_path, sf_root, path_prefix)
    res = run_cli(repo, "coverage", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["lane_failures"] == {}
    return repo, f"{path_prefix}/{SHAPES}" if path_prefix else SHAPES


def _worklist(repo: Path) -> dict:
    """The functions over the ceiling, by handle."""
    res = run_cli(repo, "worklist", "--json")
    assert res.returncode == 0, res.stderr
    return {row["handle"]: row for row in json.loads(res.stdout)["active"]}


def test_worklist_reads_cov_from_the_tracefile_and_hands_add_tests(measured):
    repo, key = measured
    rows = _worklist(repo)

    assert rows["untouched"]["path"] == key
    assert (rows["untouched"]["cov"], rows["untouched"]["remedy"]) == (0.0, "add-tests")
    assert rows["untouched"]["flag"] == "measured"


def test_brief_lists_the_da_lines_no_test_ran(measured):
    repo, key = measured
    res = run_cli(repo, "brief", key, "untouched", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["uncovered_lines"] == [32, 33, 34, 35, 36, 37]


def test_explain_tests_has_no_contexts_to_show(measured):
    repo, key = measured
    res = run_cli(repo, "explain", key, "if_else", "--tests", "--json")

    assert res.returncode == 0, res.stderr
    (function,) = json.loads(res.stdout)["functions"]
    assert function["tests"] is None
    assert function["tests_note"].startswith("no context data")


@pytest.mark.parametrize("path_prefix", ["", "crates/core"], ids=["no-prefix", "path-prefix"])
def test_sf_keys_from_another_checkout_are_refused_with_exit_5(tmp_path, path_prefix):
    elsewhere = "/other/checkout/"
    repo = _repo(tmp_path, lambda repo: elsewhere, path_prefix)

    res = run_cli(repo, "coverage", "--json")

    assert res.returncode == 5, res.stdout + res.stderr
    assert "lane 'rust' FAILED" in res.stderr
    assert WRONG_TREE_FIX in " ".join(res.stderr.split())
    assert f"{elsewhere}{SHAPES}" in res.stderr, "the key is quoted as the tracefile spelled it"
    assert json.loads(res.stdout)["error"]["exit"] == 5
