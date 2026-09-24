"""A measured repo, then one thing done to a file under the lane's scope.

The lane-staleness tests ask the same question of many readers: after this
happened to `src/app.ts`, do the reuse warning, the dark-line note and the
report banner say the file moved, and only when its bytes did? Each event here
is one row of that matrix. `EVENTS[name]` builds the repo's git view first
(`view`), then does the thing (`act`); `moved` is the truth, the paths a right
answer names, and () when no byte a line number points into changed.

The lane is a python script that writes a fixed istanbul artifact for
`src/app.ts`, so a measurement costs one interpreter start and no test runner.
It runs through the coverage command's own lane runner, which writes the stamp
every reader judges. Real git throughout: git's view of the file is the input.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from hang_guard import HANG_SECONDS

REL = "src/app.ts"

APP_TS = """export function dispatch(kind: string): number {
  switch (kind) {
    case "a": return 1;
    case "b": return 2;
    case "c": return 3;
    case "d": return 4;
    case "e": return 5;
    case "f": return 6;
    default: return 0;
  }
}

export function plain(x: number): number {
  if (x > 10) {
    return x;
  }
  return -x;
}
"""

# Lines 9 and 17 never ran: dispatch's default arm and plain's negative return.
DARK = {9, 17}

_ARTIFACT = {REL: {
    "path": REL,
    "fnMap": {"0": {"name": "dispatch", "decl": {"start": {"line": 1}},
                    "loc": {"start": {"line": 1}, "end": {"line": 11}}},
              "1": {"name": "plain", "decl": {"start": {"line": 13}},
                    "loc": {"start": {"line": 13}, "end": {"line": 18}}}},
    "f": {"0": 3, "1": 1},
    "statementMap": {"0": {"start": {"line": 3}, "end": {"line": 3}},
                     "1": {"start": {"line": 9}, "end": {"line": 9}},
                     "2": {"start": {"line": 15}, "end": {"line": 15}},
                     "3": {"start": {"line": 17}, "end": {"line": 17}}},
    "s": {"0": 1, "1": 0, "2": 1, "3": 0},
    "branchMap": {}, "b": {}}}

# BYPRODUCT names a file the lane writes under its own scope, the way a python
# lane without PYTHONDONTWRITEBYTECODE writes src/__pycache__.
MAKE_COV = f"""import json, os
os.makedirs("coverage", exist_ok=True)
with open("coverage/coverage-final.json", "w", encoding="utf-8") as fh:
    json.dump({json.dumps(_ARTIFACT)}, fh)
by = os.environ.get("BYPRODUCT")
if by:
    os.makedirs(os.path.dirname(by), exist_ok=True)
    with open(by, "wb") as fh:
        fh.write(b"bytecode")
"""


def toml(byproduct: str = "") -> str:
    env = f'env = {{ BYPRODUCT = "{byproduct}" }}\n' if byproduct else ""
    command = f'"{Path(sys.executable).as_posix()}" make_cov.py'
    return (f'[crapkit]\ntarget = 6\n\n[[scope]]\nname = "src"\npaths = ["src"]\n'
            f'languages = ["typescript"]\n\n[exclude]\nglobs = ["make_cov.py"]\n\n'
            f"[[lane]]\nname = \"unit\"\ncommand = '{command}'\n"
            f'artifact = "coverage/coverage-final.json"\nparser = "istanbul"\n'
            f'scopes = ["src"]\n{env}')


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          cwd=repo, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=HANG_SECONDS)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {done.stderr}")
    return done.stdout


def write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def age(path: Path, seconds: float = 30.0) -> None:
    """An mtime in the past, as time passing would leave it, so a later touch
    lands on another tick and git's stat cache has to decide."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def touch(path: Path) -> None:
    """A new mtime, two minutes past the file's own, on the same bytes."""
    later = path.stat().st_mtime + 120
    os.utime(path, (later, later))


def build(root: Path, *, gitcfg: dict | None = None, attrs: str = "", app: str = APP_TS,
          extra: dict | None = None, byproduct: str = "") -> Path:
    """A committed repo with one lane over `src`, not measured yet."""
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    for key, value in (gitcfg or {}).items():
        git(root, "config", key, value)
    files = {".gitignore": ".crapkit/\ncoverage/\n", "crapkit.toml": toml(byproduct),
             "make_cov.py": MAKE_COV, REL: app, **(extra or {})}
    if attrs:
        files[".gitattributes"] = attrs
    for rel, text in files.items():
        write(root / rel, text)
        age(root / rel)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def config(root: Path):
    from crapkit.config import load_config_text

    return load_config_text((root / "crapkit.toml").read_text(encoding="utf-8"), root=root)


def run_lanes(root: Path, *, reuse: bool = False):
    """The coverage command's lane runner: runs (or reuses) every lane and
    writes the stamps."""
    from crapkit.cli.scoring import _run_lanes

    cfg = config(root)
    return _run_lanes(root, cfg.lanes, reuse, cfg.scope_paths)


def measure(root: Path) -> Path:
    run_lanes(root)
    return root


def reverted_checkout(root: Path, rel: str = REL) -> None:
    """Delete and check the file out again, then let the index settle, so git
    writes the bytes its current settings write."""
    (root / rel).unlink()
    git(root, "checkout", "--", rel)
    age(root / rel)
    git(root, "update-index", "-q", "--refresh", check=False)


def clone_with_state(origin: Path, dest: Path, *, depth: int | None = None) -> Path:
    """A clone with crapkit's ignored state copied in, the way a CI cache
    restore brings .crapkit/ and coverage/ into a fresh checkout."""
    url = "file:///" + str(origin).replace("\\", "/").lstrip("/")
    shallow = ["--depth", str(depth)] if depth else []
    subprocess.run(["git", "clone", "-q", *shallow, url, str(dest)], check=True,
                   capture_output=True, timeout=HANG_SECONDS)
    for state in (".crapkit", "coverage"):
        if (origin / state).exists():
            shutil.copytree(origin / state, dest / state)
    return dest


@dataclass
class Event:
    """One matrix row: how the repo is set up, what happens after measuring,
    and which paths moved (the paths a right answer names)."""
    name: str
    act: Callable[[Path], Path | None]
    moved: tuple[str, ...] = ()
    gitcfg: dict = field(default_factory=dict)
    attrs: str = ""
    app: str = APP_TS

    def prepare(self, tmp: Path) -> Path:
        """The measured repo with this event applied; the returned root is the
        one to read (a clone for the clone rows)."""
        root = measure(build(tmp / "repo", gitcfg=self.gitcfg, attrs=self.attrs, app=self.app))
        return self.act(root) or root


def _norefresh(root: Path) -> None:
    git(root, "config", "diff.autoRefreshIndex", "false")


def _crlf(root: Path, mode: str = "true") -> None:
    git(root, "config", "core.autocrlf", mode)
    reverted_checkout(root)
    assert b"\r\n" in (root / REL).read_bytes(), "the CRLF checkout did not land"


def _crlf_bytes(root: Path) -> None:
    path = root / REL
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def _same_size_one_tick(root: Path) -> None:
    """Same-length new content under the old mtime: two writes inside one
    filesystem clock tick, or a copy that keeps times (cp -p, tar -x)."""
    path = root / REL
    old, stat = path.read_bytes(), path.stat()
    path.write_bytes(old.replace(b'case "a": return 1;', b'case "a": return 7;'))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def _content(root: Path) -> None:
    write(root / REL, APP_TS + "\nexport function extra(y: number): number { return y; }\n")


def _rename(root: Path) -> None:
    (root / REL).rename(root / "src/moved.ts")


def _case_rename(root: Path) -> None:
    os.rename(root / REL, root / "src/tmp_case.ts")
    os.rename(root / "src/tmp_case.ts", root / "src/App.ts")


def _symlink(root: Path) -> None:
    write(root / "src/other.ts", "export function other(): number { return 2; }\n")
    os.symlink("other.ts", root / "src/link.ts")


def _shallow(root: Path, *, scope_moves: bool) -> Path:
    """A depth-1 clone one commit past the measured one: the stamp's commit is
    not in it, and .crapkit/ arrives from a cache."""
    if scope_moves:
        _content(root)
    else:
        write(root / "README.md", "docs only\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "second")
    return clone_with_state(root, root.parent / "clone", depth=1)


def _without_git(root: Path) -> None:
    """Content moves, then git leaves PATH (the caller's monkeypatch)."""
    _content(root)


EVENTS = {event.name: event for event in (
    Event("norefresh-control", _norefresh),
    Event("touch", lambda root: touch(root / REL)),
    Event("touch-norefresh", lambda root: (_norefresh(root), touch(root / REL)) and None),
    Event("crlf-autocrlf-true", _crlf),
    Event("crlf-touch-norefresh",
          lambda root: (_crlf(root), _norefresh(root), touch(root / REL)) and None),
    Event("autocrlf-input-crlf-bytes",
          lambda root: (git(root, "config", "core.autocrlf", "input"), _crlf_bytes(root)) and None),
    Event("autocrlf-input-crlf-bytes-norefresh",
          lambda root: (git(root, "config", "core.autocrlf", "input"), _crlf_bytes(root),
                        _norefresh(root)) and None),
    Event("eol-attr-touch", lambda root: (reverted_checkout(root), touch(root / REL)) and None,
          attrs="*.ts text eol=crlf\n"),
    Event("eol-attr-touch-norefresh",
          lambda root: (reverted_checkout(root), _norefresh(root), touch(root / REL)) and None,
          attrs="*.ts text eol=crlf\n"),
    Event("ident-filter-touch", lambda root: (reverted_checkout(root), touch(root / REL)) and None,
          attrs="*.ts ident\n", app="// $Id$\n" + APP_TS),
    Event("ident-filter-touch-norefresh",
          lambda root: (reverted_checkout(root), _norefresh(root), touch(root / REL)) and None,
          attrs="*.ts ident\n", app="// $Id$\n" + APP_TS),
    Event("mode-change-staged", lambda root: git(root, "update-index", "--chmod=+x", REL) and None),
    Event("detached-head", lambda root: git(root, "checkout", "-q", "--detach") and None),
    Event("case-only-rename", _case_rename),
    Event("fresh-clone", lambda root: clone_with_state(root, root.parent / "clone")),
    Event("shallow-clone-scope-unchanged", lambda root: _shallow(root, scope_moves=False)),
    Event("amend-message-only",
          lambda root: git(root, "commit", "-q", "--amend", "-m", "reworded") and None),
    Event("sibling-commit-same-scope-bytes",
          lambda root: (git(root, "checkout", "-q", "-b", "alt", "HEAD"),
                        write(root / "NOTES.md", "two\n"), git(root, "add", "-A"),
                        git(root, "commit", "-q", "--amend", "-m", "sibling")) and None),
    # Under core.autocrlf=false the bytes a commit would take differ, and yet
    # no line moved: the artifact still describes every line of the file.
    Event("autocrlf-false-crlf-bytes",
          lambda root: (git(root, "config", "core.autocrlf", "false"), _crlf_bytes(root)) and None),
    Event("same-size-one-tick", _same_size_one_tick, moved=(REL,)),
    Event("content-change", _content, moved=(REL,)),
    Event("delete", lambda root: (root / REL).unlink(), moved=(REL,)),
    Event("rename", _rename, moved=(REL, "src/moved.ts")),
    Event("add-in-scope",
          lambda root: write(root / "src/added.ts", "export const added = 1;\n"),
          moved=("src/added.ts",)),
    Event("symlink-add", _symlink, moved=("src/link.ts", "src/other.ts")),
    Event("shallow-clone-scope-changed", lambda root: _shallow(root, scope_moves=True),
          moved=(REL,)),
    Event("git-missing", _without_git, moved=(REL,)),
)}


def symlinks_work(tmp: Path) -> bool:
    """os.symlink needs developer mode or elevation on Windows."""
    try:
        os.symlink("nowhere", tmp / "probe-link")
    except OSError:
        return False
    return True


def case_preserving_rename(root: Path) -> bool:
    return "App.ts" in os.listdir(root / "src")


def stamp(root: Path) -> dict:
    return json.loads((root / ".crapkit" / "artifacts.json").read_text(encoding="utf-8"))
