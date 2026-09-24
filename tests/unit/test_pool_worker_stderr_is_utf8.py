"""An analysis worker writes UTF-8 to stderr, the encoding the parent promised.

`cli.parser._reconfigure_streams` makes the CLI's pipes UTF-8 before any command
runs. A spawned `ProcessPoolExecutor` worker builds its own `sys.stderr` over
the inherited handle and never runs that line, so it writes in the ANSI code
page on Windows and in PYTHONIOENCODING's codec wherever that is set. crapkit's
own twin-key note already moved to the parent (#31), but lizard still writes
from the worker: a TypeScript file nested 3000 braces deep makes it print
`[skip] fail to process 'src/café.ts' with RecursionError`, and the `é` reached
a strict UTF-8 reader as the lone byte 0xe9.

Windows spawns workers on every Python; Linux spawns them through forkserver
from 3.14 on, and fork before that hands the child the parent's reconfigured
stream. Each row runs the real CLI in a private resource directory, so the pool
never falls back to the serial path because another run holds the slots.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import hang_guard
from crapkit.resources import available_cpus

TOML = ('[crapkit]\ntarget = 6\n{workers}\n[[scope]]\nname = "src"\npaths = ["src"]\n'
        'languages = ["typescript"]\n')
DEEP = "export function deep(a: number) { " + "{" * 3000 + "}" * 3000 + " return a; }\n"

# (label, argv, files, analysis_workers); 0 workers is automatic sizing.
POOLED = [("inventory, 40 files, 4 workers", "inventory", 40, 4),
          ("inventory, 140 files, automatic", "inventory", 140, 0),
          ("hook-precommit, 20 staged files", "hook-precommit", 20, 0)]
SERIAL = [("inventory, 5 files", "inventory", 5, 0),
          ("hook-precommit, 5 staged files", "hook-precommit", 5, 0)]
# What a Windows console or a CI runner leaves in the environment. None of them
# may change the bytes crapkit writes to a pipe.
LEGACY = [{}, {"PYTHONIOENCODING": "cp1252"}, {"PYTHONIOENCODING": "latin-1"}]
UTF8 = [{"PYTHONIOENCODING": "utf-8"}, {"PYTHONUTF8": "1"}]

needs_a_pool = pytest.mark.skipif(available_cpus()[0] < 2,
                                  reason="one CPU sizes every pool to the serial path")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _repo(root: Path, argv: str, files: int, workers: int) -> Path:
    """A committed TypeScript repo whose one deep file has a non-ASCII name.
    The hook reads staged blobs, so its rows stage an edit to every file."""
    (root / "src").mkdir(parents=True)
    workers_line = f"analysis_workers = {workers}\n" if workers else ""
    (root / "crapkit.toml").write_text(TOML.format(workers=workers_line), encoding="utf-8")
    (root / "src" / "café.ts").write_text(DEEP, encoding="utf-8")
    for n in range(files - 1):
        (root / "src" / f"m{n:03d}.ts").write_text(
            f"export function f{n}(a: number) {{ return a + {n}; }}\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    if argv == "hook-precommit":
        for path in (root / "src").iterdir():
            path.write_bytes(path.read_bytes() + b"// staged edit\n")
        _git(root, "add", "-A")
    return root


def _stderr(repo: Path, argv: str, env_extra: dict, slots: Path) -> bytes:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env.update(env_extra, CRAPKIT_RESOURCE_DIR=str(slots))
    result = hang_guard.run([sys.executable, "-m", "crapkit", argv], cwd=repo, env=env)
    return result.stderr


def _skip_line(stderr: bytes) -> str:
    """The stderr as a strict UTF-8 reader gets it, down to lizard's line."""
    text = stderr.decode("utf-8")  # raises on the byte a legacy codec wrote
    return next((line for line in text.splitlines() if "[skip]" in line), text)


def _label(env: dict) -> str:
    return " ".join(f"{key}={value}" for key, value in env.items()) or "no-env"


@needs_a_pool
@pytest.mark.parametrize("env", LEGACY + UTF8, ids=_label)
@pytest.mark.parametrize("label, argv, files, workers", POOLED, ids=[row[0] for row in POOLED])
def test_a_pooled_workers_stderr_decodes_as_utf8(tmp_path, label, argv, files, workers, env):
    repo = _repo(tmp_path / "repo", argv, files, workers)

    line = _skip_line(_stderr(repo, argv, env, tmp_path / "slots"))

    assert "[skip] fail to process" in line and "café.ts" in line, line


@pytest.mark.parametrize("env", LEGACY, ids=_label)
@pytest.mark.parametrize("label, argv, files, workers", SERIAL, ids=[row[0] for row in SERIAL])
def test_the_serial_path_already_wrote_utf8(tmp_path, label, argv, files, workers, env):
    """The control: under the pool threshold lizard runs in the parent, whose
    stderr went through `_reconfigure_streams`."""
    repo = _repo(tmp_path / "repo", argv, files, workers)

    line = _skip_line(_stderr(repo, argv, env, tmp_path / "slots"))

    assert "[skip] fail to process" in line and "café.ts" in line, line


class _Stream:
    """A text stream that records how it was reconfigured."""

    def __init__(self, tty: bool):
        self.tty = tty
        self.calls = []

    def isatty(self) -> bool:
        return self.tty

    def reconfigure(self, **kwargs) -> None:
        self.calls.append(kwargs)


def test_a_worker_keeps_a_terminals_encoding_and_pins_a_pipe_to_utf8(monkeypatch):
    """The parent's rule, in the worker: a terminal keeps its own encoding and
    only degrades errors; a pipe is UTF-8. A stream without `reconfigure`
    (None under pythonw, a StringIO in a test) is left alone."""
    from crapkit import _analysis_pool

    terminal, pipe = _Stream(tty=True), _Stream(tty=False)
    monkeypatch.setattr(sys, "stdout", terminal)
    monkeypatch.setattr(sys, "stderr", pipe)
    _analysis_pool._utf8_output()
    monkeypatch.setattr(sys, "stdout", None)
    _analysis_pool._utf8_output()

    assert terminal.calls == [{"errors": "replace"}]
    assert pipe.calls == [{"encoding": "utf-8", "errors": "replace"}] * 2
