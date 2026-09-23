"""brief reads the file it is about, and looks the rest of the repo up.

A packet needs one function's source and that function's twins. It used to
read every scored file to get both: 12,705 files and 115 MB on a large consumer
repo, 0.8 s before any shingling started. The twins now come from the run's
stored shingle index, so once that index exists a packet opens exactly one file.
"""
import json
import subprocess

import pytest

from crapkit import packet
from crapkit.cli import main
from crapkit.cli import queue
from crapkit.dup import find_twins
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
coverage_optional = true
"""

BODY = "\n".join(f"    step_{i} = compute({i}) + offset" for i in range(10))
FILES = {
    "src/a.py": "def alpha():\n" + BODY + "\n",
    "src/b.py": "def beta():\n" + BODY + "\n" + "\n".join(
        f"    extra_{i} = more({i})" for i in range(4)) + "\n",
    "src/c.py": "def gamma():\n" + "\n".join(f"    other_{i} = load({i})" for i in range(10)) + "\n",
}


def scored(path: str, name: str, end: int) -> ScoredRow:
    return ScoredRow("src", path, name, 1, end, 3, 3, 3, end, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, 1)


ROWS = [scored("src/a.py", "alpha( )", 11), scored("src/b.py", "beta( )", 15),
        scored("src/c.py", "gamma( )", 11)]


def git(repo, *args) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "crapkit.toml").write_text(TOML, encoding="utf-8", newline="\n")
    for path, text in FILES.items():
        (root / path).write_text(text, encoding="utf-8", newline="\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "seed")
    (root / ".crapkit").mkdir()
    SnapshotStore(root / ".crapkit" / "crap.sqlite").write_run(
        commit="a" * 40, tool_versions={}, rows=ROWS)
    return root


@pytest.fixture()
def reads(monkeypatch) -> list:
    """Every path set a brief hands the file loader, in order."""
    seen: list = []
    real = queue._load_sources

    def recorded(root, paths):
        seen.append(set(paths))
        return real(root, paths)

    monkeypatch.setattr(queue, "_load_sources", recorded)
    return seen


def brief(repo, capsys, path: str = "src/a.py", name: str = "alpha") -> dict:
    assert main(["brief", path, name, "--json", "--repo", str(repo)]) == 0
    return json.loads(capsys.readouterr().out)


def test_a_brief_on_a_run_with_a_stored_index_opens_only_its_own_file(repo, capsys, reads):
    first = brief(repo, capsys)
    reads.clear()

    second = brief(repo, capsys)

    assert reads == [{"src/a.py"}]
    assert second == first
    assert [t["path"] for t in second["duplication_twins"]] == ["src/b.py"]


def test_the_first_brief_on_a_run_reads_the_repo_once_and_stores_the_index(repo, capsys, reads):
    brief(repo, capsys)

    assert reads == [set(FILES), {"src/a.py"}]
    store = SnapshotStore(repo / ".crapkit" / "crap.sqlite")
    stored = store.twin_index(1, lambda: pytest.fail("the index was not stored"))
    assert stored.min_lines == 8


def test_the_packet_twins_are_what_a_build_of_every_file_finds(repo, capsys):
    brief(repo, capsys, "src/c.py", "gamma")  # the build, on another target

    packet_twins = brief(repo, capsys, "src/b.py", "beta")["duplication_twins"]

    rows = SnapshotStore(repo / ".crapkit" / "crap.sqlite").read_rows(1)
    assert packet_twins == packet.with_contained(find_twins(ROWS[1], rows, FILES))
    assert [t["path"] for t in packet_twins] == ["src/a.py"]


def test_a_packet_source_is_read_once_per_file_across_a_batch(repo, capsys, reads):
    brief(repo, capsys)
    reads.clear()

    assert main(["brief", "--batch", "3", "--repo", str(repo)]) == 0
    packets = json.loads(capsys.readouterr().out)["packets"]

    assert sorted(map(sorted, reads)) == [["src/a.py"], ["src/b.py"], ["src/c.py"]]
    assert {p["path"] for p in packets} == set(FILES)


def test_a_target_file_gone_since_the_run_has_no_source_and_no_twins(repo, capsys):
    brief(repo, capsys, "src/c.py", "gamma")
    (repo / "src" / "a.py").unlink()

    out = brief(repo, capsys)

    assert out["duplication_twins"] == []
    assert out["source"] is None
