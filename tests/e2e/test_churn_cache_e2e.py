"""The churn cache through the CLI, on a real repo with real git.

Two things have to hold at once: a repeat read is byte-identical to the cold
one, and it really came off disk. The second is proved by planting a doctored
count in the cache and watching it surface in the output — a stopwatch would
prove nothing about correctness, and a mock proves nothing about the wiring.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner
from repo_templates import copy_of, template

CACHE = Path(".crapkit") / "churn-cache-v2.json"

APP_PY = """def plain(x):
    a = x + 1
    b = a + 2
    return a + b


def pick(kind):
    if kind == "a":
        return 1
    if kind == "b":
        return 2
    return 0
"""

# Stand-in for `pytest --cov`: a fixed coverage.py artifact, so next-item has a
# scored run without this file owning a real suite.
LANE_SCRIPT = '''import json
from pathlib import Path

report = {"meta": {"branch_coverage": True}, "files": {"src/app.py": {"functions": {
    "plain": {"start_line": 1, "executed_lines": [1, 2, 3, 4], "missing_lines": [],
              "summary": {"covered_lines": 4, "num_statements": 4,
                          "num_branches": 0, "covered_branches": 0}},
    "pick": {"start_line": 7, "executed_lines": [7, 8], "missing_lines": [9, 10, 11, 12],
             "summary": {"covered_lines": 2, "num_statements": 6,
                         "num_branches": 4, "covered_branches": 1}},
}}}}
Path("cov.json").write_text(json.dumps(report), encoding="utf-8")
'''

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "coveragepy"\nscopes = ["src"]\n'
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", message],
                   cwd=repo, check=True, capture_output=True)


def head(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def cache_doc(repo: Path) -> dict:
    return json.loads((repo / CACHE).read_text(encoding="utf-8"))


def _build_churned_repo(repo: Path) -> None:
    write(repo / "crapkit.toml", TOML)
    write(repo / "lane.py", LANE_SCRIPT)
    write(repo / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    for i in range(3):
        write(repo / "src" / "app.py", APP_PY + f"\nBUILD = {i}\n")
        commit(repo, f"edit {i}")
    res = run_cli(repo, "inventory")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture()
def churned_repo(tmp_path: Path) -> Path:
    """src/app.py lands in three commits, so it has churn in any window."""
    built = template(tmp_path, "churn-cache", _build_churned_repo)
    return copy_of(built, tmp_path / "churned")


def test_a_repeat_worklist_is_byte_identical_and_leaves_a_head_keyed_cache(churned_repo: Path):
    cold = run_cli(churned_repo, "worklist", "--json")
    assert cold.returncode == 0, cold.stdout + cold.stderr
    warm = run_cli(churned_repo, "worklist", "--json")
    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout, "same tree, same answer, byte for byte"

    doc = cache_doc(churned_repo)
    assert doc["key"]["head"] == head(churned_repo)
    assert doc["key"]["months"] == 12
    assert doc["files"]["src/app.py"][0] == 3, "three commits touched it"


def test_the_worklist_answers_from_the_cache_file(churned_repo: Path):
    """Plant a count git could never produce; if it surfaces, the cache was read."""
    run_cli(churned_repo, "worklist", "--json")
    doc = cache_doc(churned_repo)
    doc["files"]["src/app.py"] = [999, 7, 42.0]
    (churned_repo / CACHE).write_text(json.dumps(doc), encoding="utf-8")

    res = run_cli(churned_repo, "worklist", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    entry = json.loads(res.stdout)["active"][0]
    assert (entry["commits"], entry["authors"], entry["weight"]) == (999, 7, 42.0)


def test_a_new_commit_discards_the_cache(churned_repo: Path):
    run_cli(churned_repo, "worklist", "--json")
    doc = cache_doc(churned_repo)
    doc["files"]["src/app.py"] = [999, 7, 42.0]
    (churned_repo / CACHE).write_text(json.dumps(doc), encoding="utf-8")

    write(churned_repo / "src" / "app.py", APP_PY + "\nBUILD = 9\n")
    commit(churned_repo, "move head")

    res = run_cli(churned_repo, "worklist", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    entry = json.loads(res.stdout)["active"][0]
    assert entry["commits"] == 4, "the doctored map must not outlive its HEAD"
    assert cache_doc(churned_repo)["key"]["head"] == head(churned_repo)


def test_next_item_and_worklist_share_one_cache(churned_repo: Path):
    assert run_cli(churned_repo, "coverage", "--json").returncode == 0

    res = run_cli(churned_repo, "next-item")
    assert res.returncode == 0, res.stdout + res.stderr
    written_by_next_item = cache_doc(churned_repo)

    assert run_cli(churned_repo, "worklist", "--json").returncode == 0
    assert cache_doc(churned_repo) == written_by_next_item, \
        "worklist must read next-item's cache, not write a rival one"
    assert sorted(p.name for p in (churned_repo / ".crapkit").glob("churn*")) == \
        ["churn-cache-v2.json", "churn-commits-v1.json"]


def test_coupling_is_byte_identical_across_runs(churned_repo: Path):
    cold = run_cli(churned_repo, "coupling", "--json", "--min-support", "1")
    assert cold.returncode == 0, cold.stdout + cold.stderr
    warm = run_cli(churned_repo, "coupling", "--json", "--min-support", "1")
    assert warm.stdout == cold.stdout


OTHER_PY = "def other(x):\n    return x\n"


def _deepening_clone(tmp_path: Path) -> Path:
    """A depth-1 clone of a history where src/app.py and src/other.py change
    together in six commits and HEAD touches neither. The clone sees one
    commit; `git fetch --unshallow` brings the other six under the same HEAD."""
    source = tmp_path / "history"
    write(source / "crapkit.toml", TOML)
    write(source / "lane.py", LANE_SCRIPT)
    write(source / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True,
                   capture_output=True)
    for i in range(6):
        write(source / "src" / "app.py", APP_PY + f"\nBUILD = {i}\n")
        write(source / "src" / "other.py", OTHER_PY + f"\nBUILD = {i}\n")
        commit(source, f"co-change {i}")
    write(source / "README.md", "readme\n")
    commit(source, "docs")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", source.as_uri(), str(clone)],
                   check=True, capture_output=True)
    return clone


def _app_commits(repo: Path) -> int:
    assert run_cli(repo, "inventory").returncode == 0
    res = run_cli(repo, "worklist", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return next(e["commits"] for e in json.loads(res.stdout)["active"]
                if e["path"] == "src/app.py")


def _pair_support(repo: Path) -> int:
    res = run_cli(repo, "coupling", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    return next((p["support"] for p in json.loads(res.stdout)["pairs"]
                 if p["files"] == ["src/app.py", "src/other.py"]), 0)


@pytest.mark.parametrize("read, shallow, full", [
    (_app_commits, 1, 6),
    (_pair_support, 0, 6),
], ids=["churn", "coupling"])
def test_a_deepened_clone_answers_from_its_whole_history_the_same_day(
        tmp_path: Path, read, shallow, full):
    """HEAD does not move when a shallow clone is deepened, so a key of HEAD,
    window and date served the one-commit answer until the UTC date rolled. The
    key holds git's shallow boundary now, and no cache has to be deleted."""
    clone = _deepening_clone(tmp_path)
    before = head(clone)
    assert read(clone) == shallow, "a depth-1 clone sees one commit"

    subprocess.run(["git", "fetch", "-q", "--unshallow"], cwd=clone, check=True,
                   capture_output=True)

    assert head(clone) == before
    assert read(clone) == full, "the cached shallow answer outlived the deepen"
