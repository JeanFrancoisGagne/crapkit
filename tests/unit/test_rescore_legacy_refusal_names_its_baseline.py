"""rescore's legacy-identity refusal names the baseline run it read.

rescore joins fresh complexity onto the newest trusted run's coverage. When
that run holds same-line twins it cannot place, the join refuses, and the
sentence said only the file and the name, where explain and brief say which
run they read.
"""
import subprocess
from contextlib import closing

from crapkit.cli import main
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

CFG = ('[crapkit]\ntarget = 6\n[[scope]]\nname = "web"\npaths = ["src"]\n'
       'languages = ["typescript"]\ncoverage_optional = true\n')


def _row(start, occurrence):
    return ScoredRow("web", "src/a.ts", "(anonymous)", start, start, 3, 3, 3, 1, 0, 0,
                     0.0, "untested", 12.0, "add-tests", 0, occurrence)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
                    *args], check=True, capture_output=True)


def test_rescore_names_the_baseline_run_that_holds_the_legacy_twins(tmp_path, capsys):
    _git(tmp_path, "init", "-q")
    (tmp_path / "crapkit.toml").write_text(CFG, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.ts").write_text("export function f(a) {\n  return a ? 1 : 2;\n}\n",
                                       encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "fixture")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    with closing(store._conn):
        store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"},
                        rows=[_row(1, 1), _row(5, 1)])
        legacy = store.write_run(commit="c0ffee", tool_versions={"analysis_version": "10"},
                                 rows=[_row(1, 0), _row(1, 0)])

    assert main(["rescore", "src/a.ts", "--json", "--repo", str(tmp_path)]) == 5

    assert (f"ambiguous legacy function identity in src/a.ts: (anonymous) in run {legacy}; "
            "refresh analysis") in capsys.readouterr().err
