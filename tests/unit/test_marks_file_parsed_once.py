"""A read command parses a legacy marks file once.

The lenient reader parsed the marks, then the legacy identity proof parsed the
same text four more times: its reader-version check, its emptiness check, and
the key-group check with its own reader-version check. On a 39,496-mark file
one parse takes 71 ms, so explain and brief paid about 0.35 s for the marks
alone. The proof now takes the marks the reader already parsed.
"""
import json
import subprocess
from contextlib import closing

import pytest

from crapkit import ratchet
from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\nworklist_floor = 1\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')
SOURCE = "".join(f"// {n}\n" for n in range(1, 9))


def _row(path, name):
    return ScoredRow("web", path, name, 3, 3, 9, 9, 9, 1, 0, 0,
                     0.0, "untested", 90.0, "decompose", 0, 1)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """Unstamped marks, so every reader runs the legacy identity proof."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    for name in ("a.ts", "b.ts"):
        (tmp_path / "src" / name).write_text(SOURCE, encoding="utf-8")
    (tmp_path / "crapkit-ratchet.tsv").write_text(
        "path\tlong_name\tcrap\nsrc/a.ts\tf\t20.0000\nsrc/b.ts\tg\t30.0000\n", encoding="utf-8",
        newline="\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"},
                        rows=[_row("src/a.ts", "f"), _row("src/b.ts", "g")])
    return tmp_path


@pytest.fixture
def parses(monkeypatch):
    """Every text a marks parse was handed. brief also parses the file's past
    versions out of git log for a mark's age; those are other texts."""
    seen = []
    real = ratchet.read_ratchet

    def counted(text):
        seen.append(text)
        return real(text)

    monkeypatch.setattr(ratchet, "read_ratchet", counted)
    return seen


def _of_the_file(repo, parses) -> int:
    return parses.count((repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8"))


def test_explain_parses_the_marks_once(repo, capsys, parses):
    assert main(["explain", "src/a.ts", "f", "--json", "--repo", str(repo)]) == 0

    assert json.loads(capsys.readouterr().out)["functions"][0]["ratchet_mark"] == 20.0
    assert _of_the_file(repo, parses) == 1


def test_a_batch_over_two_files_parses_the_marks_once(repo, capsys, parses):
    assert main(["brief", "--batch", "2", "--json", "--repo", str(repo)]) == 0

    marks = {p["path"]: p["gate_rule"]["ratchet_mark"]
             for p in json.loads(capsys.readouterr().out)["packets"]}
    assert marks == {"src/a.ts": 20.0, "src/b.ts": 30.0}
    assert _of_the_file(repo, parses) == 1
