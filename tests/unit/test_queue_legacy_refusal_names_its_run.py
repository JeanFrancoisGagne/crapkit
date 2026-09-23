"""brief and next-item name the stored run whose legacy twins they refuse.

explain already said `... in run 2; refresh analysis ...`. brief, `brief --batch`
and next-item printed the same refusal with no run, so a store holding a fresh
run beside the legacy one gave no hint which run the refusal read.
"""
import subprocess
from contextlib import closing

import pytest

from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\nworklist_floor = 1\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')


def _row(name, start, occurrence):
    return ScoredRow("web", "src/a.ts", name, start, start, 9, 9, 9, 1, 0, 0,
                     0.0, "untested", 90.0, "decompose", 0, occurrence)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """Run 1 placed its twins; run 2, the newest, holds them unplaced on line 1."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.ts").write_text("".join(f"// {n}\n" for n in range(1, 9)), encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        for occurrence, second in ((1, 5), (0, 1)):
            store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"},
                            rows=[_row(name, start, occurrence)
                                  for name in ("(anonymous)", "g") for start in (1, second)])
    return tmp_path


@pytest.mark.parametrize("args", [
    ["brief", "src/a.ts", "(anonymous)", "--json"],
    ["brief", "src/a.ts", "g#2", "--json"],
    ["brief", "--batch", "2", "--json"],
    ["next-item"],
])
def test_the_refusal_names_the_run_it_read(repo, capsys, args):
    assert main([*args, "--repo", str(repo)]) == 5
    err = capsys.readouterr().err
    assert "in run 2;" in err, err
