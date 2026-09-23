"""A legacy twin group on a file the tree deleted refuses no reader of another file.

The legacy mark proof covers the files whose marks a command compares. Only
`ratchet prune` compares a mark with a row of another file: it follows a git
rename, so a mark on a file the tree lost lands on the rename's destination.
Every reader added the marked files the tree lost to its proof too, so a legacy
group on a deleted file refused brief, explain and worklist of a file that never
collided. tests/unit/test_identity_proof_covers_marks_its_rows_lack.py keeps the
prune half: a rename source's legacy group still refuses prune.
"""
import json
import subprocess
from contextlib import closing

import pytest

from crapkit.cli import main
from crapkit.cli._shared import _proved_paths
from crapkit.ratchet import RatchetEntry, metric_version
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\nworklist_floor = 1\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')
SOURCE = "".join(f"// {n}\n" for n in range(1, 9))


def _row(path, name, start, occurrence):
    return ScoredRow("web", path, name, start, start, 9, 9, 9, 1, 0, 0,
                     0.0, "untested", 90.0, "decompose", 0, occurrence)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """Run 1 held src/gone.ts's twins on line 1; the tree then deleted the file,
    and run 2 scored only src/a.ts. The marks still name both files."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text(SOURCE, encoding="utf-8")
    (tmp_path / "crapkit-ratchet.tsv").write_text(
        f"# {metric_version()}\npath\tlong_name\tcrap\n"
        "src/a.ts\tf\t20.0000\nsrc/gone.ts\t(anonymous)#2\t99.0000\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        for rows in ([_row("src/a.ts", "f", 3, 0), _row("src/gone.ts", "(anonymous)", 1, 0),
                      _row("src/gone.ts", "(anonymous)", 1, 0)],
                     [_row("src/a.ts", "f", 3, 1)]):
            store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"}, rows=rows)
    return tmp_path


def test_brief_reads_a_mark_beside_a_deleted_files_legacy_group(repo, capsys):
    assert main(["brief", "src/a.ts", "f", "--json", "--repo", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)["gate_rule"]["ratchet_mark"] == 20.0


def test_explain_reads_a_mark_beside_a_deleted_files_legacy_group(repo, capsys):
    assert main(["explain", "src/a.ts", "f", "--json", "--repo", str(repo)]) == 0
    assert json.loads(capsys.readouterr().out)["functions"][0]["ratchet_mark"] == 20.0


def test_worklist_ranks_beside_a_deleted_files_legacy_group(repo, capsys):
    assert main(["worklist", "--json", "--repo", str(repo)]) == 0
    assert "legacy ratchet key identity" not in capsys.readouterr().err


def test_a_reader_proves_only_its_rows_files(tmp_path):
    marks = [RatchetEntry("src/a.ts", "f", 9.0), RatchetEntry("src/gone.ts", "g", 9.0)]

    assert _proved_paths(tmp_path, [_row("src/a.ts", "f", 3, 1)], marks) == {"src/a.ts"}


def test_prune_proves_the_marked_files_the_tree_lost(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/kept.ts").write_text("", encoding="utf-8")
    marks = [RatchetEntry("src/kept.ts", "f", 9.0), RatchetEntry("src/gone.ts", "g", 9.0)]

    assert _proved_paths(tmp_path, [], marks, moves_marks=True) == {"src/gone.ts"}
