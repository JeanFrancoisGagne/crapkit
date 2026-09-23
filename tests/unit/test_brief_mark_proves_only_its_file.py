"""brief proves legacy mark identity for the file of the function it packets.

A packet reads one mark, the one on its own function, so the proof it owes
covers that function's file. brief proved every file the run held, so a legacy
twin group in src/b.ts refused a brief of src/a.ts, which explain on the same
function answered.
"""
import json
import subprocess
from contextlib import closing

from crapkit.cli import main
from crapkit.ratchet import metric_version
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


def _repo(root, marks: str, runs: list) -> None:
    _git(root, "init", "-q")
    (root / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "src").mkdir()
    for name in ("a.ts", "b.ts", "c.ts"):
        (root / "src" / name).write_text(SOURCE, encoding="utf-8")
    (root / "crapkit-ratchet.tsv").write_text(
        f"# {metric_version()}\npath\tlong_name\tcrap\n{marks}", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    with closing(store._conn):
        for rows in runs:
            store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"}, rows=rows)


def _legacy_b(root) -> None:
    """src/b.ts held legacy twins on line 1 in run 1; src/a.ts never collided."""
    _repo(root, "src/a.ts\tf\t20.0000\nsrc/b.ts\t(anonymous)#2\t99.0000\n",
          [[_row("src/a.ts", "f", 3, occurrence),
            _row("src/b.ts", "(anonymous)", 1, occurrence),
            _row("src/b.ts", "(anonymous)", second, occurrence)]
           for occurrence, second in ((0, 1), (1, 5))])


def test_brief_reads_a_mark_beside_another_files_legacy_group(tmp_path, capsys):
    _legacy_b(tmp_path)
    assert main(["brief", "src/a.ts", "f", "--json", "--repo", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["gate_rule"]["ratchet_mark"] == 20.0


def test_brief_still_refuses_the_file_that_holds_the_legacy_group(tmp_path, capsys):
    _legacy_b(tmp_path)
    assert main(["brief", "src/b.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 3
    assert "legacy ratchet key identity is ambiguous for src/b.ts: (anonymous)" in \
        capsys.readouterr().err


def test_a_batch_over_two_files_warns_once_about_an_unreadable_mark(tmp_path, capsys):
    _repo(tmp_path, "src/a.ts\tf\t20.0000\nsrc/c.ts\th\n",
          [[_row("src/a.ts", "f", 3, 1), _row("src/c.ts", "h", 3, 1)]])

    assert main(["brief", "--batch", "2", "--json", "--repo", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert sorted(p["path"] for p in json.loads(captured.out)["packets"]) == ["src/a.ts", "src/c.ts"]
    assert captured.err.count("skipped an unreadable mark in crapkit-ratchet.tsv") == 1, captured.err
