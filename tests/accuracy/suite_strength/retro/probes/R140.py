"""R140: a marks-file write took the running metric whenever its caller named
none, so an override grant added its number to marks another metric recorded
and relabeled the whole file with the running metric, and verify's stamp
refusal went quiet.

    <retro venv python> R140.py WORKTREE

verdict_model's check runs prune and move. At the fix commit prune still took
the running metric (a later fix, R56, moved it onto the rule) and move already
kept the stamp before the fix, so the check reads the same at both commits.
This probe asks the grant, crapkit.override.record_override, which both
commits have with these keywords, to grant one violation into a marks file an
older metric recorded, naming no metric: the file must keep the stamp it
recorded.
"""
# source: docs/ratchet.md "The metric stamp": "Every write to the marks file sets the stamp by where its numbers came from", and only `ratchet seed` "replaces a recorded metric stamp"; the file below records `crapkit-analysis=7 lizard=1.17.10`
from __future__ import annotations

from pathlib import Path
import sys
import tempfile

STAMP = "crapkit-analysis=7 lizard=1.17.10"
MARKS = f"# {STAMP}\n# crapkit-keys=1\npath\tlong_name\tcrap\nsrc/a.py\tf( )\t50.0000\n"


def _alert(scratch: Path) -> str:
    log = scratch / "alert.log"
    return (f'"{sys.executable}" -c "import sys,pathlib; '
            f'pathlib.Path(r\'{log}\').open(\'a\').write(sys.stdin.read())"')


def _grant(scratch: Path) -> str:
    from crapkit.override import record_override
    from crapkit.store import SnapshotStore
    from crapkit.verify import GateViolation

    (scratch / "marks.tsv").write_bytes(MARKS.encode())
    store = SnapshotStore(scratch / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    violation = GateViolation("src/b.py", "g( )", 3, 9, 0.0, 90.0, "decompose")
    record_override(store=store, run_id=run_id, root=scratch, ratchet_file="marks.tsv",
                    alert_command=_alert(scratch), violations=[violation], reason="hotfix")
    return (scratch / "marks.tsv").read_bytes().decode()


def main(argv: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="crapkit-r140-", ignore_cleanup_errors=True) as scratch:
        first = _grant(Path(scratch)).splitlines()[0]
    assert first == f"# {STAMP}", f"the grant relabeled the marks file {first!a}; it recorded {STAMP!a}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
