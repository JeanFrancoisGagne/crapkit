"""A run's shingle index is stored when inventory or coverage records the run.

brief and duplication read a run's index back from the store. When the first
reader on a run had to build it, that call shingled the repo and then wrote the
index: 9.4 s on a large consumer repo, where the same call cost about 5 s before
the index existed. A repo that briefs once per run paid that on every run.

inventory and coverage now store the index beside the rows they record, so the
first brief opens one file and duplication reads the owner lists back. verify
does not: it runs on every commit, and a large repo's commit gate would pay the
build and the write each time. On a verify run the first brief or duplication
report still builds and stores the index.
"""
import json
from pathlib import Path

from cli_inproc_repo import (APP_TS, commit_all, repo, seed_artifacts,  # noqa: F401
                             template_repo)

import pytest

from crapkit.cli import analyses, main, queue
from crapkit.mcp_server import tool_listing
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]


def store(repo) -> SnapshotStore:
    return SnapshotStore(repo / ".crapkit" / "crap.sqlite")


def indexed_runs(repo) -> list[tuple[int, int]]:
    return store(repo)._conn.execute("SELECT run_id, min_lines FROM twin_runs").fetchall()


def with_a_copy(repo) -> None:
    """dispatch again under another name: its first line differs, the other ten
    match. Eleven lines make eight windows and only the first holds line 1."""
    copy = APP_TS.split("\n\n")[0].replace("dispatch", "route")
    (repo / "src" / "copy.ts").write_text(copy + "\n", encoding="utf-8", newline="\n")
    commit_all(repo, "copy")


@pytest.fixture()
def reads(monkeypatch) -> list:
    """Every path set brief hands the file loader, in order."""
    seen: list = []
    real = queue._load_sources

    def recorded(root, paths):
        seen.append(set(paths))
        return real(root, paths)

    monkeypatch.setattr(queue, "_load_sources", recorded)
    return seen


def refuse_sources(root, paths):
    raise AssertionError(f"duplication read {len(paths)} files off a stored index")


def test_inventory_stores_the_index_of_the_run_it_records(repo, capsys):
    assert main(["inventory", "--json", "--repo", str(repo)]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]

    assert indexed_runs(repo) == [(run_id, 8)]


def test_duplication_after_inventory_reads_no_file(repo, capsys, monkeypatch):
    with_a_copy(repo)
    assert main(["inventory", "--repo", str(repo)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(analyses, "_load_sources", refuse_sources)

    assert main(["duplication", "--json", "--repo", str(repo)]) == 0
    pairs = json.loads(capsys.readouterr().out)["pairs"]

    assert [[f["long_name"] for f in p["functions"]] for p in pairs] == \
        [["dispatch ( kind )", "route ( kind )"]]
    assert pairs[0]["similarity"] == 0.875  # 7 of 8 windows


def test_the_first_brief_after_coverage_opens_only_its_own_file(repo, capsys, reads):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--json", "--repo", str(repo)]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert indexed_runs(repo) == [(run_id, 8)]

    assert main(["brief", "src/app.ts", "dispatch", "--json", "--repo", str(repo)]) == 0

    assert json.loads(capsys.readouterr().out)["path"] == "src/app.ts"
    assert reads == [{"src/app.ts"}]


def test_the_served_descriptions_say_twins_come_from_the_stored_index():
    """get_function_brief said every call shingles the repo, and
    list_duplicate_functions that it shingles source on every call. The tests
    above show both reading the index the run stored."""
    served = {t["name"]: t["description"] for t in tool_listing()}

    assert "the run's stored index" in served["get_function_brief"]
    assert "the run's stored index" in served["list_duplicate_functions"]


def test_the_json_page_says_where_the_index_is_written():
    """The page said no on-disk cache stood behind the batch saving."""
    text = " ".join((ROOT / "docs" / "agent-json.md").read_text(encoding="utf-8").split())

    assert "`inventory` and `coverage` write it as they record the run" in text
