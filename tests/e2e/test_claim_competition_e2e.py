"""The queue hands separate sessions separate functions, including twins."""
import json
from concurrent.futures import ThreadPoolExecutor

from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore
from conftest import cli_runner, git_init_repo, git_commit_all


run_cli = cli_runner(encoding="utf-8")


def seeded(repo):
    git_init_repo(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("def f():\n    pass\n" + "\n" * 17 + "def f():\n    pass\n")
    (repo / ".gitignore").write_text(".crapkit/\n")
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\nworklist_floor=1\n'
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n')
    git_commit_all(repo, "two functions")
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    rows = [ScoredRow("src", "src/a.py", "f( )", start, start + 1, 4, 4, 4,
                      2, 0, 1, 0.0, "measured", crap, "add-tests")
            for start, crap in ((1, 20.0), (20, 18.0))]
    store.write_run(commit="stored", tool_versions={}, rows=rows)
    return repo


def next_claim(repo):
    result = run_cli(repo, "next-item", "--claim")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_claiming_one_twin_leaves_the_other_available(tmp_path):
    repo = seeded(tmp_path)
    first = next_claim(repo)
    second = next_claim(repo)
    assert first["item"]["handle"] == "f#1"
    assert second["item"]["handle"] == "f#2"
    assert next_claim(repo)["empty"] is True


def test_competing_cli_sessions_emit_only_the_functions_they_acquired(tmp_path):
    repo = seeded(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        answers = list(pool.map(lambda _: next_claim(repo), range(3)))
    assigned = [a["item"]["handle"] for a in answers if not a["empty"]]
    assert sorted(assigned) == ["f#1", "f#2"]
