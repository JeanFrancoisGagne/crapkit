"""A reader proves legacy mark identity for the files its rows cover, not the whole store.

explain, check_gate and the commit gate read one file or a few, and the marks
they use are that file's marks. The legacy proof still regrouped every run of
every file to find collision groups anywhere, so a legacy twin group in one
file refused explain on another, and each call paid for the whole history.
"""
import json
import subprocess
from contextlib import closing

from crapkit.cli import main
from crapkit.ratchet import metric_version
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')
SOURCE = "".join(f"// {n}\n" for n in range(1, 9))


def _row(path, name, start, occurrence):
    return ScoredRow("web", path, name, start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, occurrence)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


def _repo(root):
    """src/b.ts held legacy twins on line 1 in run 1; src/a.ts never collided."""
    _git(root, "init", "-q")
    (root / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "src").mkdir()
    for name in ("a.ts", "b.ts"):
        (root / "src" / name).write_text(SOURCE, encoding="utf-8")
    (root / "crapkit-ratchet.tsv").write_text(
        f"# {metric_version()}\npath\tlong_name\tcrap\n"
        "src/a.ts\tf\t20.0000\nsrc/b.ts\t(anonymous)#2\t99.0000\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    with closing(store._conn):
        for occurrence, second in ((0, 1), (1, 5)):
            store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"},
                            rows=[_row("src/a.ts", "f", 3, occurrence),
                                  _row("src/b.ts", "(anonymous)", 1, occurrence),
                                  _row("src/b.ts", "(anonymous)", second, occurrence)])


def test_explain_reads_a_mark_beside_another_files_legacy_group(tmp_path, capsys):
    _repo(tmp_path)
    assert main(["explain", "src/a.ts", "f", "--json", "--repo", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["functions"][0]["ratchet_mark"] == 20.0
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        assert store._conn.execute("SELECT COUNT(*) FROM run_collisions").fetchone() == (0,)


def test_explain_still_refuses_the_file_that_holds_the_legacy_group(tmp_path, capsys):
    _repo(tmp_path)
    assert main(["explain", "src/b.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 3
    assert "legacy ratchet key identity is ambiguous for src/b.ts: (anonymous)" in capsys.readouterr().err


def test_the_whole_run_readers_still_prove_every_file(tmp_path, capsys):
    _repo(tmp_path)
    assert main(["worklist", "--json", "--repo", str(tmp_path)]) == 3
    assert "legacy ratchet key identity is ambiguous for src/b.ts: (anonymous)" in capsys.readouterr().err
