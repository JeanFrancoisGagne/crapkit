"""Analysis commands through the CLI: coupling, duplication, mutate, plus the
watch-mode mtime snapshot.

Every repo is built inline in tmp_path and driven as a subprocess, so the
assertions pin printed text and exit codes rather than any internal wiring.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crapkit.store import SnapshotStore
from crapkit.watch import snapshot_mtimes

from conftest import cli_runner

SRC_TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n'
)

# One clone per file, differing only on the def line: 11 normalized lines make
# 8 shingles each and 7 of them survive the rename, which is 0.875 containment.
CLONE = (
    "def NAME(rows):\n"
    "    total = 0\n"
    "    count = 0\n"
    "    names = []\n"
    "    for row in rows:\n"
    "        total = total + row['value']\n"
    "        count = count + 1\n"
    "        names.append(row['name'])\n"
    "    average = total / count\n"
    "    label = ', '.join(names)\n"
    "    return [total, count, average, label]\n"
)

# A closure factory: lizard scores `reach_tool` at 1..14 and `reach` at 2..12,
# and the inner body's normalized lines are all the outer body's too, so the two
# score a perfect 1.0 against each other. Issue #1: that pair used to top the
# report, and no one can deduplicate a function from the closure inside it.
FACTORY = (
    "def reach_tool(deps):\n"
    "    def reach(name, depth=1):\n"
    "        total = 0\n"
    "        for step in range(depth):\n"
    "            total = total + deps.weight(step)\n"
    "        label = deps.label(name)\n"
    "        parts = label.split('/')\n"
    "        head = parts[0]\n"
    "        tail = parts[-1]\n"
    "        size = len(parts)\n"
    "        return {'name': name, 'head': head, 'tail': tail,\n"
    "                'size': size, 'total': total}\n"
    "    deps.register(reach)\n"
    "    return reach\n"
)

RATE_BEFORE = "def rate(x):\n    return x\n"
RATE_AFTER = "def rate(x):\n    if x > 10:\n        return 10\n    return x\n"
# The suite for the mutate repo: a script, so the repo root lands on sys.path.
RATE_CHECK = "import calc\n\nassert calc.rate(11) == 10\n"
NOOP_TS = 'export const NAME = "noop";\n'


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


run_cli = cli_runner(timeout=300, encoding="utf-8",
                     # the mutant suite imports from its own cwd, and a stale
                     # .pyc would mask a mutant
                     env_extra={"PYTHONSAFEPATH": None,
                                "PYTHONDONTWRITEBYTECODE": "1"})


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def mutate_toml() -> str:
    command = f'"{sys.executable}" -B check.py'
    return (
        f"[crapkit]\ntarget = 6\nmutation_command = '{command}'\n\n"
        '[[scope]]\nname = "py"\npaths = ["."]\nlanguages = ["python"]\n'
    )


@pytest.fixture()
def coupled_repo(tmp_path: Path) -> Path:
    """a.py and b.py land in six shared commits; c.py always travels alone."""
    repo = tmp_path / "coupled"
    write(repo / "crapkit.toml", SRC_TOML)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "config")
    for i in range(6):
        write(repo / "a.py", f"A = {i}\n")
        write(repo / "b.py", f"B = {i}\n")
        commit_all(repo, f"pair {i}")
    for i in range(2):
        write(repo / "c.py", f"C = {i}\n")
        commit_all(repo, f"solo {i}")
    return repo


@pytest.fixture()
def renamed_repo(tmp_path: Path) -> Path:
    """old_name.py co-changed with stable.py six times, then git mv renamed it.
    The log keeps naming the old path; the index does not have it any more."""
    repo = tmp_path / "renamed"
    write(repo / "crapkit.toml", SRC_TOML)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "config")
    for i in range(6):
        write(repo / "src" / "old_name.py", f"A = {i}\n")
        write(repo / "src" / "stable.py", f"S = {i}\n")
        commit_all(repo, f"pair {i}")
    git(repo, "mv", "src/old_name.py", "src/new_name.py")
    commit_all(repo, "rename")
    for i in range(3):
        write(repo / "src" / "new_name.py", f"A = {i + 6}\n")
        write(repo / "src" / "stable.py", f"S = {i + 6}\n")
        commit_all(repo, f"after {i}")
    return repo


@pytest.fixture()
def accented_repo(tmp_path: Path) -> Path:
    """The co-changing pair carries a non-ASCII name. core.quotepath is pinned
    on — its default — so the log arrives quoted whatever the host's git config
    says, and the decode is the thing under test rather than the environment."""
    repo = tmp_path / "accented"
    write(repo / "crapkit.toml", SRC_TOML)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.quotepath", "true")
    commit_all(repo, "config")
    for i in range(6):
        write(repo / "src" / "alpha.py", f"A = {i}\n")
        write(repo / "src" / "bêta.py", f"B = {i}\n")
        commit_all(repo, f"pair {i}")
    return repo


@pytest.fixture()
def clone_repo(tmp_path: Path) -> Path:
    """Two real clones and one closure factory: the report has to find the first
    pair and say nothing about the second."""
    repo = tmp_path / "clones"
    write(repo / "crapkit.toml", SRC_TOML)
    write(repo / "src" / "dup_a.py", CLONE.replace("NAME", "summarize_alpha"))
    write(repo / "src" / "dup_b.py", CLONE.replace("NAME", "summarize_beta"))
    write(repo / "src" / "factory.py", FACTORY)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "clones")
    res = run_cli(repo, "inventory")
    assert res.returncode == 0, res.stdout + res.stderr
    return repo


@pytest.fixture()
def diff_repo(tmp_path: Path) -> Path:
    """calc.py is committed flat, then grows a guard in the working tree only."""
    repo = tmp_path / "diffed"
    write(repo / "crapkit.toml", mutate_toml())
    write(repo / "calc.py", RATE_BEFORE)
    write(repo / "check.py", RATE_CHECK)
    write(repo / "noop.ts", NOOP_TS)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo, "init")
    write(repo / "calc.py", RATE_AFTER)
    return repo


def test_coupling_json_reports_the_co_changing_pair(coupled_repo: Path):
    res = run_cli(coupled_repo, "coupling", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout) == {
        "schema": 1,
        "pairs": [{"files": ["a.py", "b.py"], "support": 6, "confidence": 1.0}],
        "window_months": 12,
    }


def test_coupling_plain_output_names_both_sides(coupled_repo: Path):
    res = run_cli(coupled_repo, "coupling")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "a.py  <->  b.py" in res.stdout
    assert "6x" in res.stdout
    assert "100%" in res.stdout
    assert "c.py" not in res.stdout, "two solo commits never pair with anything"


def test_coupling_says_when_nothing_clears_the_thresholds(coupled_repo: Path):
    res = run_cli(coupled_repo, "coupling", "--min-support", "99")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "no coupled pairs at support>=99 confidence>=0.5 in 12mo"


def test_coupling_never_recommends_a_path_git_stopped_tracking(renamed_repo: Path):
    """The dead pair outranks the live one on support and confidence, so without
    a tracked-set filter it is the only line printed."""
    tracked = subprocess.run(["git", "ls-files"], cwd=renamed_repo, check=True,
                             capture_output=True, text=True).stdout.split()
    assert "src/old_name.py" not in tracked and "src/new_name.py" in tracked

    res = run_cli(renamed_repo, "coupling", "--min-support", "3", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    pairs = json.loads(res.stdout)["pairs"]
    assert [p["files"] for p in pairs] == [["src/new_name.py", "src/stable.py"]]


def test_coupling_names_a_non_ascii_path_the_way_ls_files_spells_it(accented_repo: Path):
    """git writes src/bêta.py into --name-only as "src/b\\303\\252ta.py". The
    pair has to name the decoded path: that is what ls-files, the churn map and
    every scored row hold, and `brief` looks the partner up by it."""
    raw = subprocess.run(["git", "log", "--name-only", "--format="], cwd=accented_repo,
                         check=True, capture_output=True, text=True, encoding="utf-8")
    assert '"src/b\\303\\252ta.py"' in raw.stdout, "git stopped quoting; the test proves nothing"

    res = run_cli(accented_repo, "coupling", "--min-support", "3", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    (pair,) = json.loads(res.stdout)["pairs"]
    assert pair["files"] == ["src/alpha.py", "src/bêta.py"]


def test_duplication_json_pairs_the_two_clones(clone_repo: Path):
    res = run_cli(clone_repo, "duplication", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["run_id"] >= 1
    (pair,) = payload["pairs"]
    assert [f["path"] for f in pair["functions"]] == ["src/dup_a.py", "src/dup_b.py"]
    assert pair["similarity"] == 0.875
    assert pair["contained"] is False


def test_duplication_json_leaves_a_closure_out_of_its_factorys_pairs(clone_repo: Path):
    """The repo holds `reach_tool` and the `reach` defined inside it, a 1.0 pair
    by construction. Nothing in the payload names either one."""
    res = run_cli(clone_repo, "duplication", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    named = [f["long_name"] for p in json.loads(res.stdout)["pairs"] for f in p["functions"]]
    assert not [n for n in named if "reach" in n], named


def test_duplication_plain_output_shows_both_functions(clone_repo: Path):
    res = run_cli(clone_repo, "duplication")
    assert res.returncode == 0, res.stdout + res.stderr
    (line,) = res.stdout.splitlines()
    assert "src/dup_a.py:1" in line
    assert "src/dup_b.py:1" in line
    assert "summarize_alpha" in line
    assert "summarize_beta" in line
    assert "  ==  " in line


def test_duplication_says_when_no_function_is_long_enough(clone_repo: Path):
    res = run_cli(clone_repo, "duplication", "--min-lines", "50")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "no near-duplicate functions found"


def test_duplication_refuses_a_store_holding_only_hook_runs(tmp_path: Path):
    repo = tmp_path / "hooked"
    write(repo / "crapkit.toml", SRC_TOML)
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    store.write_run(commit="0" * 40, tool_versions={}, rows=[], kind="hook")
    res = run_cli(repo, "duplication")
    assert res.returncode == 1
    assert "run `crapkit inventory` first" in res.stderr


def pruneable_repo(tmp_path: Path) -> Path:
    """Three trusted runs where only the first and third share a lane set, so a
    keep-the-newest rule alone would take the half of the digest pair."""
    from crapkit.score import ScoredRow

    repo = tmp_path / "retention"
    write(repo / "crapkit.toml", SRC_TOML)
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    for commit, lanes, crap in (("c1", {"unit": {}}, 40.0),
                                ("c2", {"unit": {}, "py": {}}, 41.0),
                                ("c3", {"unit": {}}, 55.0)):
        store.write_run(commit=commit * 13, tool_versions={}, kind="coverage", lanes=lanes,
                        rows=[ScoredRow("src", "src/a.py", "f( )", 1, 9, 7, 7, 7, 5, 1, 1,
                                        0.25, "measured", crap, "add-tests")])
    return repo


def test_runs_prune_keeps_the_digest_pair_and_leaves_the_digest_unchanged(tmp_path: Path):
    repo = pruneable_repo(tmp_path)
    before = run_cli(repo, "digest")
    assert before.returncode == 0, before.stderr
    assert before.stdout.strip(), "the fixture must produce a loud digest"

    res = run_cli(repo, "runs", "prune", "--keep", "1", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout)
    assert payload["pruned_runs"] == 1 and payload["kept_runs"] == 2
    assert run_cli(repo, "digest").stdout == before.stdout


def test_runs_prune_drops_the_pruned_runs_from_runs_and_trend(tmp_path: Path):
    repo = pruneable_repo(tmp_path)
    trend_before = json.loads(run_cli(repo, "trend", "--json").stdout)["runs"]

    assert run_cli(repo, "runs", "prune", "--keep", "1").returncode == 0

    trend_after = json.loads(run_cli(repo, "trend", "--json").stdout)["runs"]
    kept = {r["run_id"] for r in trend_after}
    assert kept < {r["run_id"] for r in trend_before}
    assert all(r in trend_before for r in trend_after), "surviving rows report the same numbers"
    assert all(r["functions"] > 0 for r in trend_after), "no run reports a debt-free week it never had"
    assert [r["id"] for r in json.loads(run_cli(repo, "runs", "--json").stdout)["runs"]] == sorted(kept)


def test_runs_prune_refuses_a_keep_of_zero(tmp_path: Path):
    repo = pruneable_repo(tmp_path)
    res = run_cli(repo, "runs", "prune", "--keep", "0")
    assert res.returncode == 3, res.stdout + res.stderr
    assert "must be >= 1" in res.stderr
    assert len(json.loads(run_cli(repo, "runs", "--json").stdout)["runs"]) == 3


def test_runs_without_an_action_still_lists(tmp_path: Path):
    repo = pruneable_repo(tmp_path)
    res = run_cli(repo, "runs")
    assert res.returncode == 0, res.stderr
    assert len(res.stdout.strip().splitlines()) == 3


def test_mutate_scopes_to_the_working_tree_diff_and_caps_the_list(diff_repo: Path):
    before = (diff_repo / "calc.py").read_bytes()
    res = run_cli(diff_repo, "mutate", "--max-mutants", "1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "capping at 1 of 2 mutants" in res.stderr, "the guard line yields > -> >= and > -> <="
    header, survivor = res.stdout.splitlines()
    assert header == "mutation: 0/1 killed (0%)"
    assert survivor.strip().startswith("SURVIVED  calc.py:2  [> -> >=]")
    assert (diff_repo / "calc.py").read_bytes() == before, "the original always comes back"


def test_mutate_reports_nothing_to_mutate_for_absent_and_operator_free_files(diff_repo: Path):
    res = run_cli(diff_repo, "mutate", "--files", "ghost.py", "noop.ts")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.strip() == "mutation: 0/0 killed (n/a)"


def test_mutate_json_stays_well_formed_with_zero_mutants(diff_repo: Path):
    res = run_cli(diff_repo, "mutate", "--files", "noop.ts", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout) == {"mutants": 0, "killed": 0, "survived": 0,
                                      "survivors": [], "schema": 1}


def test_snapshot_mtimes_keeps_present_files_and_drops_missing_ones(tmp_path: Path):
    write(tmp_path / "here.py", "x = 1\n")
    snap = snapshot_mtimes(tmp_path, ["here.py", "gone.py"])
    assert list(snap) == ["here.py"]
    assert snap["here.py"] == (tmp_path / "here.py").stat().st_mtime


def test_snapshot_mtimes_ignores_directories_and_an_empty_file_list(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    assert snapshot_mtimes(tmp_path, ["pkg"]) == {}
    assert snapshot_mtimes(tmp_path, []) == {}
