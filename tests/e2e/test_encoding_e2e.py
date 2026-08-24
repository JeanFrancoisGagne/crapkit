"""A hostile console encoding must never crash crapkit or eat its exit code.

Git hooks and CI runners pipe output through whatever codec the host picked
(cp1252 on Windows, sometimes ascii). Python's stderr is backslashreplace by
default, but STDOUT under PYTHONIOENCODING=ascii is strict — a report line
with typographic punctuation would turn a clean run into a traceback."""
import os
import subprocess
import sys
from pathlib import Path


def _run(repo: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=60, env=env)


def test_stdout_reports_survive_an_ascii_only_console(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                             encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)
    assert _run(tmp_path, "init").returncode == 0

    # doctor's no-lane note carries an em dash; strict ascii stdout would explode
    res = _run(tmp_path, "doctor", env_extra={"PYTHONIOENCODING": "ascii"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "note" in res.stdout
    assert "Traceback" not in res.stderr
