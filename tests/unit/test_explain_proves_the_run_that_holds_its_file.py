"""explain on a file the newest run dropped proves only that file's marks.

explain names a position in the newest trusted run that holds the file. Its
legacy mark proof read the newest run of all, which held no rows for a dropped
file, and an empty proof proved every marked file. So a legacy twin group in
another marked file refused explain on a file that never collided.
tests/unit/test_identity_proof_covers_marks_its_rows_lack.py holds the other
half: a legacy group in the dropped file's own history still refuses.
"""
import json
import subprocess
from contextlib import closing

from crapkit.cli import main
from crapkit.cli._shared import _proved_paths
from crapkit.ratchet import RatchetEntry, metric_version
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')
SOURCE = "".join(f"// {n}\n" for n in range(1, 12))


def _row(path, start, occurrence, crap=12.0, name="(anonymous)"):
    return ScoredRow("web", path, name, start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", crap, "add-tests", 0, occurrence)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


def _repo(root):
    """Run 1 placed src/a.ts's twins and held src/c.ts's unplaced; run 2 scored only src/b.ts."""
    _git(root, "init", "-q")
    (root / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "src").mkdir()
    for name in ("a.ts", "b.ts", "c.ts"):
        (root / "src" / name).write_text(SOURCE, encoding="utf-8")
    (root / "crapkit-ratchet.tsv").write_text(
        f"# {metric_version()}\npath\tlong_name\tcrap\n"
        "src/a.ts\t(anonymous)#2\t99.0000\nsrc/c.ts\t(anonymous)#2\t50.0000\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    with closing(store._conn):
        for rows in ([_row("src/a.ts", 1, 1), _row("src/a.ts", 5, 1, 30.0),
                      _row("src/c.ts", 1, 0), _row("src/c.ts", 1, 0, 30.0)],
                     [_row("src/b.ts", 1, 1, name="f")]):
            store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"}, rows=rows)


def test_explain_on_a_dropped_file_reads_its_mark_beside_another_files_legacy_group(tmp_path, capsys):
    _repo(tmp_path)
    assert main(["explain", "src/a.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["functions"][0]["ratchet_mark"] == 99.0


def test_an_empty_proof_proves_only_the_marked_files_the_tree_lost(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/kept.ts").write_text("", encoding="utf-8")
    marks = [RatchetEntry("src/kept.ts", "f", 9.0), RatchetEntry("src/gone.ts", "g", 9.0)]

    assert _proved_paths(tmp_path, [], marks) == {"src/gone.ts"}
