"""The coupling cache through the CLI, on a real repo with real git.

Three things have to hold at once: a repeat run is byte-identical, it really
came off disk, and it goes stale when it should. The middle one is proved twice
over, because the deflated churn log already removes git's walk and a spawn
census alone would pass on a cache nothing reads: the log pair is deleted before
the warm run, so a run that still answers without a window walk answered from
the ranking file, and a doctored support count planted in that file is watched
surfacing in the output.

The stale side is where a coupling cache differs from a churn map. ls-files
reads the index, so `git rm --cached` moves the tracked set with HEAD standing
still, and the ranking has to fall with it.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git, git_commit_all, git_init_repo

CRAPKIT = Path(".crapkit")
CACHE = CRAPKIT / "coupling-cache-v1.json"
LOG_Z = CRAPKIT / "churn-log-v2.z"
LOG_KEY = CRAPKIT / "churn-log-v2.json"

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

UTIL_PY = """def widen(kind, n):
    if kind == "a":
        return n + 1
    if kind == "b":
        return n + 2
    if kind == "c":
        return n + 3
    return n
"""

EXTRA_PY = """def clamp(n, low, high):
    if n < low:
        return low
    if n > high:
        return high
    return n
"""

# Stand-in for `pytest --cov`: a fixed coverage.py artifact, so brief and
# worklist have a scored run without this file owning a real suite.
LANE_SCRIPT = '''import json
from pathlib import Path

def fn(start, covered, statements, branches, taken):
    return {"start_line": start, "executed_lines": list(range(start, start + covered)),
            "missing_lines": list(range(start + covered, start + statements)),
            "summary": {"covered_lines": covered, "num_statements": statements,
                        "num_branches": branches, "covered_branches": taken}}

report = {"meta": {"branch_coverage": True}, "files": {
    "src/app.py": {"functions": {"plain": fn(1, 4, 4, 0, 0), "pick": fn(7, 2, 6, 4, 1)}},
    "src/util.py": {"functions": {"widen": fn(1, 2, 8, 6, 1)}},
    "src/extra.py": {"functions": {"clamp": fn(1, 2, 7, 4, 1)}},
}}
Path("cov.json").write_text(json.dumps(report), encoding="utf-8")
'''

TOML = (
    '[crapkit]\ntarget = 6\nworklist_floor = 1\nchurn_window_months = 12\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python lane.py"\nartifact = "cov.json"\n'
    'parser = "coveragepy"\nscopes = ["src"]\n'
)

run_cli = cli_runner(timeout=180, encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def head(repo: Path) -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def _argvs(trace_dir: Path) -> list[list[str]]:
    out = []
    for path in sorted(trace_dir.glob("*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            event = json.loads(line)
            if event.get("event") == "start":
                out.append(event["argv"])
    return out


def _subcommand(argv: list[str]) -> str:
    """The git subcommand past git's own flags: crapkit spawns every git with
    `-c diff.relative=true`, so the subcommand is not argv[1]."""
    i = 1
    while argv[i:i + 1] == ["-c"]:
        i += 2
    return argv[i] if i < len(argv) else ""


def window_walks(repo: Path, tmp_path: Path, tag: str, *args: str):
    """Run the CLI and report the history walks it spawned, if any."""
    trace = tmp_path / f"trace-{tag}"
    trace.mkdir()
    res = run_cli(repo, *args, env_extra={"GIT_TRACE2_EVENT": str(trace)})
    walks = [argv for argv in _argvs(trace)
             if _subcommand(argv) == "log" and any(a.startswith("--since") for a in argv)]
    return res, walks


def cache_doc(repo: Path) -> dict:
    return json.loads((repo / CACHE).read_text(encoding="utf-8"))


def rewrite_cache(repo: Path, doc: dict) -> None:
    (repo / CACHE).write_text(json.dumps(doc), encoding="utf-8")


def drop_the_log(repo: Path) -> None:
    """Take away the only other thing a warm run could have read the pairs from."""
    (repo / LOG_Z).unlink()
    (repo / LOG_KEY).unlink()


@pytest.fixture()
def coupled_repo(tmp_path: Path) -> Path:
    """app.py and util.py land in six commits together, extra.py in five, so all
    three pairs clear the default support of 5 and the ranking has an order."""
    repo = tmp_path / "coupled"
    write(repo / "crapkit.toml", TOML)
    write(repo / "lane.py", LANE_SCRIPT)
    write(repo / ".gitignore", ".crapkit/\ncov.json\n__pycache__/\n")
    git_init_repo(repo)
    for i in range(6):
        write(repo / "src" / "app.py", APP_PY + f"\nBUILD = {i}\n")
        write(repo / "src" / "util.py", UTIL_PY + f"\nBUILD = {i}\n")
        if i:
            write(repo / "src" / "extra.py", EXTRA_PY + f"\nBUILD = {i}\n")
        git_commit_all(repo, f"edit {i}")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    return repo


COUPLING = ("coupling", "--json")


def test_a_warm_coupling_reads_neither_git_nor_the_log(coupled_repo, tmp_path):
    cold, cold_walks = window_walks(coupled_repo, tmp_path, "cold", *COUPLING)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert cold_walks, "the cold run is the walk this cache exists to save"
    assert (coupled_repo / CACHE).is_file(), "and it must lay the ranking down"

    drop_the_log(coupled_repo)
    warm, warm_walks = window_walks(coupled_repo, tmp_path, "warm", *COUPLING)

    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout, "same tree, same answer, byte for byte"
    assert warm_walks == [], "with no log on disk, the pairs can only be the cached ones"


def test_coupling_really_reports_pairs(coupled_repo):
    """A byte-identity claim over an empty list would pass on a broken cache."""
    pairs = json.loads(run_cli(coupled_repo, *COUPLING).stdout)["pairs"]

    assert [p["files"] for p in pairs] == [
        ["src/app.py", "src/util.py"],
        ["src/app.py", "src/extra.py"],
        ["src/extra.py", "src/util.py"],
    ]
    assert [p["support"] for p in pairs] == [6, 5, 5]


def test_the_pairs_come_off_the_cache_file(coupled_repo):
    """Plant a support count git could never produce; if it surfaces, it was read."""
    run_cli(coupled_repo, *COUPLING)
    doc = cache_doc(coupled_repo)
    doc["pairs"][0][2] = 999
    rewrite_cache(coupled_repo, doc)

    pairs = json.loads(run_cli(coupled_repo, *COUPLING).stdout)["pairs"]
    assert pairs[0]["support"] == 999


def test_top_truncates_the_stored_order_and_still_reads_the_cache(coupled_repo):
    run_cli(coupled_repo, *COUPLING)
    doc = cache_doc(coupled_repo)
    doc["pairs"][0][2] = 999
    rewrite_cache(coupled_repo, doc)

    pairs = json.loads(run_cli(coupled_repo, *COUPLING, "--top", "1").stdout)["pairs"]
    assert [p["support"] for p in pairs] == [999], "a cut of the cached total order"


def test_off_default_thresholds_recompute_and_leave_the_cache_alone(coupled_repo):
    """The file holds the ranking at support>=5 confidence>=0.5 and nothing else.
    Served to `--min-support 1`, it would answer a wider question with a
    narrower list and hide every pair the wider threshold was asked for."""
    run_cli(coupled_repo, *COUPLING)
    doc = cache_doc(coupled_repo)
    doc["pairs"][0][2] = 999
    rewrite_cache(coupled_repo, doc)

    wide = json.loads(run_cli(coupled_repo, *COUPLING, "--min-support", "1").stdout)
    assert 999 not in [p["support"] for p in wide["pairs"]]
    assert cache_doc(coupled_repo)["pairs"][0][2] == 999, \
        "a bypass must not overwrite the default ranking with its own"


def test_a_new_commit_discards_the_cache(coupled_repo):
    run_cli(coupled_repo, *COUPLING)
    doc = cache_doc(coupled_repo)
    doc["pairs"][0][2] = 999
    rewrite_cache(coupled_repo, doc)

    write(coupled_repo / "src" / "app.py", APP_PY + "\nBUILD = 9\n")
    write(coupled_repo / "src" / "util.py", UTIL_PY + "\nBUILD = 9\n")
    git_commit_all(coupled_repo, "move head")

    pairs = json.loads(run_cli(coupled_repo, *COUPLING).stdout)["pairs"]
    assert pairs[0]["support"] == 7, "the doctored ranking must not outlive its HEAD"
    assert cache_doc(coupled_repo)["key"]["head"] == head(coupled_repo)


def test_untracking_a_file_discards_the_cache_at_an_unmoved_head(coupled_repo):
    """`git rm --cached` moves the index, not HEAD. Every pair naming util.py
    is now a recommendation to open a file git no longer tracks."""
    run_cli(coupled_repo, *COUPLING)
    before = head(coupled_repo)
    git(coupled_repo, "rm", "--cached", "-q", "--", "src/util.py")

    pairs = json.loads(run_cli(coupled_repo, *COUPLING).stdout)["pairs"]

    assert head(coupled_repo) == before, "the sha the churn map keys on never moved"
    assert [p["files"] for p in pairs] == [["src/app.py", "src/extra.py"]]


def test_a_cache_written_before_the_tracked_digest_is_ignored(coupled_repo):
    """A file under this name whose key predates the index digest: rebuilt, and
    its pairs never reach the output."""
    run_cli(coupled_repo, *COUPLING)
    doc = cache_doc(coupled_repo)
    del doc["key"]["tracked"]
    doc["pairs"] = [["src/gone.py", "src/vanished.py", 41, 1.0]]
    rewrite_cache(coupled_repo, doc)

    pairs = json.loads(run_cli(coupled_repo, *COUPLING).stdout)["pairs"]
    assert "src/gone.py" not in [f for p in pairs for f in p["files"]]
    assert "tracked" in cache_doc(coupled_repo)["key"]


BRIEF = ("brief", "src/app.py", "pick", "--json")
BATCHES = ("worklist", "--batches", "2", "--top", "10", "--json")


def test_brief_and_batches_share_one_ranking(coupled_repo, tmp_path):
    cold = run_cli(coupled_repo, *BRIEF)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert [p["path"] for p in json.loads(cold.stdout)["coupling"]] == \
        ["src/util.py", "src/extra.py"]

    drop_the_log(coupled_repo)
    batches, walks = window_walks(coupled_repo, tmp_path, "batches", *BATCHES)

    assert batches.returncode == 0, batches.stdout + batches.stderr
    assert walks == [], "batches must read brief's ranking, not walk for a rival one"
    assert sorted(p.name for p in (coupled_repo / CRAPKIT).glob("coupling*")) == \
        ["coupling-cache-v1.json"]


def test_a_warm_brief_reads_the_ranking_off_disk(coupled_repo, tmp_path):
    cold, _ = window_walks(coupled_repo, tmp_path, "cold", *BRIEF)
    assert cold.returncode == 0, cold.stdout + cold.stderr

    drop_the_log(coupled_repo)
    warm, walks = window_walks(coupled_repo, tmp_path, "warm", *BRIEF)

    assert warm.returncode == 0, warm.stdout + warm.stderr
    assert warm.stdout == cold.stdout
    assert walks == []


def test_a_torn_ranking_is_ignored_and_rebuilt(coupled_repo, tmp_path):
    cold = run_cli(coupled_repo, *COUPLING)
    (coupled_repo / CACHE).write_text("{ truncated garbage", encoding="utf-8")

    again, walks = window_walks(coupled_repo, tmp_path, "torn", *COUPLING)
    assert again.returncode == 0, again.stdout + again.stderr
    assert again.stdout == cold.stdout
    assert walks == [], "the log still stands, so a torn ranking costs the pairing only"

    drop_the_log(coupled_repo)
    healed, healed_walks = window_walks(coupled_repo, tmp_path, "healed", *COUPLING)
    assert healed.stdout == cold.stdout, "and it is replaced, not left torn"
    assert healed_walks == []
