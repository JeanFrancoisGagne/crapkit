"""The legacy mark proof covers every file whose marks a command compares.

A reader proves identity for the files its rows cover, so a legacy group in a
file it never reads cannot refuse it. Two commands compare a mark with a row of
another file than the one the mark names. `ratchet prune` follows a git rename,
so a mark on the rename source lands on a row of the destination. `explain`
reads the newest run that holds the file, while its proof rows come from the
newest run of all, which may hold nothing of that file. A legacy run with
same-line twins in either file must still refuse the unstamped mark, because
no run can say which twin it named.
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
SOURCE = "".join(f"// {n}\n" for n in range(1, 12))
VERSIONS = {"analysis_version": "10"}


def _row(path, start, occurrence, crap=12.0, name="(anonymous)"):
    return ScoredRow("web", path, name, start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", crap, "add-tests", 0, occurrence)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(root, files, marks):
    _git(root, "init", "-q")
    _git(root, "config", "core.autocrlf", "false")
    (root / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (root / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (root / "src").mkdir()
    for path in files:
        (root / path).write_text(SOURCE, encoding="utf-8", newline="\n")
    (root / "crapkit-ratchet.tsv").write_text(
        f"# {metric_version()}\npath\tlong_name\tcrap\n{marks}", encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    (root / ".crapkit").mkdir()


def _write_run(root, rows):
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    with closing(store._conn):
        store.write_run(commit=_git(root, "rev-parse", "HEAD"), tool_versions=VERSIONS, rows=rows)


def _renamed(root, first_run):
    """Run 1 scored src/old.ts, git moved it to src/new.ts, run 2 positions its twins there."""
    _repo(root, ["src/old.ts"], "src/old.ts\t(anonymous)#2\t20.0000\n")
    _write_run(root, first_run)
    _git(root, "mv", "src/old.ts", "src/new.ts")
    _git(root, "commit", "-q", "-m", "rename")
    _write_run(root, [_row("src/new.ts", 1, 1), _row("src/new.ts", 5, 1, 20.0)])


def _marks(root):
    return (root / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


def test_prune_refuses_to_carry_a_legacy_mark_through_a_rename(tmp_path, capsys):
    _renamed(tmp_path, [_row("src/old.ts", 1, 0), _row("src/old.ts", 1, 0, 20.0)])
    before = _marks(tmp_path)
    assert main(["ratchet", "prune", "--repo", str(tmp_path)]) == 3
    assert "legacy ratchet key identity is ambiguous for src/old.ts: (anonymous)" in capsys.readouterr().err
    assert _marks(tmp_path) == before


def test_prune_follows_a_rename_whose_old_runs_placed_the_twins(tmp_path):
    _renamed(tmp_path, [_row("src/old.ts", 1, 1), _row("src/old.ts", 5, 1, 20.0)])
    assert main(["ratchet", "prune", "--repo", str(tmp_path)]) == 0
    assert "src/new.ts\t(anonymous)#2\t20.0000" in _marks(tmp_path)


def _dropped(root, runs):
    """src/a.ts's runs, then a newest run that scored only src/b.ts."""
    _repo(root, ["src/a.ts", "src/b.ts"], "src/a.ts\t(anonymous)#2\t99.0000\n")
    for rows in runs:
        _write_run(root, rows)
    _write_run(root, [_row("src/b.ts", 1, 1, name="f")])


def test_explain_refuses_a_legacy_mark_on_a_file_the_newest_run_dropped(tmp_path, capsys):
    _dropped(tmp_path, [[_row("src/a.ts", 1, 0), _row("src/a.ts", 1, 0, 30.0)],
                        [_row("src/a.ts", 1, 1), _row("src/a.ts", 5, 1, 30.0)]])
    assert main(["explain", "src/a.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 3
    assert "legacy ratchet key identity is ambiguous for src/a.ts: (anonymous)" in capsys.readouterr().err


def test_explain_reads_the_mark_on_a_dropped_file_whose_runs_placed_the_twins(tmp_path, capsys):
    _dropped(tmp_path, [[_row("src/a.ts", 1, 1), _row("src/a.ts", 5, 1, 30.0)]])
    assert main(["explain", "src/a.ts", "(anonymous)#2", "--json", "--repo", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["functions"][0]["ratchet_mark"] == 99.0
