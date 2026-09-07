"""Real subprocess actors for state transaction regressions."""
from pathlib import Path
import subprocess
import sys
import time

from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore


def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for {path.name}")
        time.sleep(0.01)


def row():
    return ScoredRow("src", "src/app.ts", "f( )", 1, 2, 4, 4, 4, 2, 0, 0,
                     0.5, "measured", 6, "ok", 0, 1)


def trend_writer(root: Path) -> None:
    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    (root / "writer-ready").touch()
    wait_for(root / "writer-go")
    store.write_run(commit="new", tool_versions={}, rows=[row()])
    store._conn.close()
    (root / "writer-done").touch()


def alert(root: Path) -> None:
    (root / "alert-ready").write_text(sys.stdin.read(), encoding="utf-8")
    wait_for(root / "alert-go")


def override_writer(root: Path) -> None:
    from crapkit.override import record_override
    from crapkit.verify import GateViolation

    store = SnapshotStore(root / ".crapkit/crap.sqlite")
    run_id = store.write_run(commit="hook", tool_versions={}, rows=[], kind="hook")
    command = subprocess.list2cmdline([sys.executable, str(Path(__file__)), "alert", str(root)])
    record_override(store=store, run_id=run_id, root=root, ratchet_file="crapkit-ratchet.tsv",
                    alert_command=command, violations=[GateViolation(
                        "src/app.ts", "f( )", 1, 9, 0, 90, "decompose")],
                    reason="concurrency regression", raise_marks=False, key_version=1)
    store._conn.close()


def move_writer(root: Path, name: str) -> None:
    from crapkit.cli import main
    from crapkit import ratchet

    original = ratchet.move_marks

    def pause(entries, old, new):
        moved = original(entries, old, new)
        (root / f"{name}-ready").touch()
        wait_for(root / f"{name}-go")
        return moved

    ratchet.move_marks = pause
    sys.exit(main(["ratchet", "move", "src/a.py", f"src/{name}.py", "--repo", str(root)]))


def verify_writer(root: Path) -> None:
    from crapkit.cli import main
    from crapkit import ratchet

    original = ratchet.update_ratchet

    def pause(*args, **kwargs):
        result = original(*args, **kwargs)
        (root / "verify-ready").touch()
        wait_for(root / "verify-go")
        return result

    ratchet.update_ratchet = pause
    sys.exit(main(["verify", "--reuse-artifacts", "--repo", str(root)]))


if __name__ == "__main__":
    {"trend": trend_writer, "alert": alert, "override": override_writer, "verify": verify_writer,
     "move-first": lambda root: move_writer(root, "first"),
     "move-second": lambda root: move_writer(root, "second")}[sys.argv[1]](Path(sys.argv[2]))
