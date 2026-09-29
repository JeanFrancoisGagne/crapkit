"""R112: lane staleness read git through a pathspec built from the lanes that
had a stamp when the reads started, so a lane a concurrent `crapkit coverage`
stamped before the verdict was judged against reads that left out its scope
paths: an uncommitted edit under its scope read as fresh.

    <retro venv python> R112.py WORKTREE

verdict_model's check puts the stamp back at the seam where the reads have
started, in its own process, and a retro replay spawns the commit's crapkit, so
the seam never fires there. This probe runs the commit's crapkit in its own
process. It builds a committed repo with two scopes and two pytest lanes (a on
src/, b on lib/), runs `crapkit coverage`, takes lane b's artifact and stamp
away, appends a line to lib/util.py, wraps crapkit.lanes.staleness_reads so
the stamp comes back once the reads have started, and runs `crapkit next-item
--top 50` in process: lib/util.py's uncovered lines must be withheld, with a
note that names lane 'b'.
"""
# requires: pytest==9.1.1 pytest-cov==7.1.0 coverage==7.16.1
# source: docs/agent-json.md "`uncovered_lines`: null is not `[]`": null when no artifact can answer, with a note like "lane 'py': files in its scopes changed since .crapkit/cov/py.json was written (uncommitted edits count), so its line numbers are stale"; lib/util.py sits in lane b's scope and holds an uncommitted edit
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

LANE = """[[lane]]
name = "{name}"
command = '"{python}" -m pytest {folder} -p no:cacheprovider -p no:randomly --cov={folder} --cov-branch --cov-report=json:cov-{name}.json -q'
artifact = "cov-{name}.json"
parser = "coveragepy"
scopes = ["{scope}"]
full_suite = false
"""
SCOPE = '[[scope]]\nname = "{scope}"\npaths = ["{folder}"]\nlanguages = ["python"]\n'
FILES = {
    "src/app.py": "def f(x):\n    if x:\n        return 1\n    return 0\n",
    "src/test_app.py": "from app import f\n\n\ndef test_f():\n    assert f(1) == 1\n",
    # ccn 8, one test that takes the first arm only: far over the ceiling, so next-item lists it
    "lib/util.py": "def g(y):\n" + "".join(f"    if y == {n}:\n        return {n}\n" for n in range(7))
                   + "    return 9\n",
    "lib/test_util.py": "from util import g\n\n\ndef test_g():\n    assert g(0) == 3\n",
    ".gitignore": ".crapkit/\ncov-*.json\n.coverage\n__pycache__/\n",
}


def _config() -> str:
    lanes = [("a", "src", "app"), ("b", "lib", "lib")]
    return ("[crapkit]\ntarget = 6\n\n[exclude]\nglobs = [\"**/test_*.py\"]\n\n"
            + "\n".join(SCOPE.format(scope=scope, folder=folder) for _, folder, scope in lanes) + "\n"
            + "\n".join(LANE.format(name=name, folder=folder, scope=scope, python=sys.executable)
                        for name, folder, scope in lanes))


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(base: Path) -> Path:
    repo = base / "repo"
    for path, text in {**FILES, "crapkit.toml": _config()}.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_bytes(text.encode())
    _git(repo, "init", "-q")
    for key, value in (("user.name", "probe"), ("user.email", "probe@example.invalid"),
                       ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        _git(repo, "config", key, value)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "probe")
    return repo


def _unstamp_lane_b(repo: Path):
    """Take lane b's artifact and stamp away; return the call that puts both back."""
    stamps, artifact = repo / ".crapkit" / "artifacts.json", repo / "cov-b.json"
    saved, saved_artifact = stamps.read_bytes(), artifact.read_bytes()
    kept = {key: entry for key, entry in json.loads(saved).items() if entry.get("lane") != "b"}
    stamps.write_bytes(json.dumps(kept).encode())
    artifact.unlink()

    def restore():
        artifact.write_bytes(saved_artifact)
        stamps.write_bytes(saved)
    return restore


def _stamp_once_reads_start(restore) -> list:
    import crapkit.lanes as lanes
    original, fired = lanes.staleness_reads, []

    @contextmanager
    def stamped_meanwhile(*args, **kwargs):
        with original(*args, **kwargs) as facts:
            restore()
            fired.append(True)
            yield facts

    lanes.staleness_reads = stamped_meanwhile
    return fired


def _next_item(repo: Path) -> dict:
    from crapkit.cli import main as crapkit_main
    printed = io.StringIO()
    os.chdir(repo)
    with redirect_stdout(printed):
        try:
            crapkit_main(["next-item", "--top", "50"])
        except SystemExit:
            pass
    return json.loads(printed.getvalue())


def _measure(repo: Path) -> None:
    measured = subprocess.run([sys.executable, "-m", "crapkit", "coverage"], cwd=repo,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True)
    if measured.returncode != 0:
        raise RuntimeError(f"crapkit coverage exited {measured.returncode}: "
                           f"{(measured.stdout + measured.stderr).decode(errors='replace')[-400:]}")


def _lib_item(scratch: Path) -> dict:
    """next-item's lib/util.py item, after lane b is stamped once the reads start."""
    repo = _repo(scratch)
    _measure(repo)
    fired = _stamp_once_reads_start(_unstamp_lane_b(repo))
    (repo / "lib" / "util.py").write_bytes((repo / "lib" / "util.py").read_bytes() + b"# edited\n")
    items = _next_item(repo)["items"]
    os.chdir(scratch)
    if not fired:
        raise RuntimeError("crapkit never called lanes.staleness_reads, so the stamp never came back")
    (lib,) = [item for item in items if item["path"] == "lib/util.py"]
    return lib


def main(argv: list[str]) -> int:
    # crapkit's in-process store stays open until exit, which Windows will not delete under
    with tempfile.TemporaryDirectory(prefix="crapkit-r112-", ignore_cleanup_errors=True) as scratch:
        lib = _lib_item(Path(scratch))
    note = lib.get("uncovered_lines_note") or ""
    assert lib["uncovered_lines"] is None and "lane 'b'" in note, (
        f"an uncommitted edit under lane b's scope read as fresh: {lib['uncovered_lines']}, {note!a}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
