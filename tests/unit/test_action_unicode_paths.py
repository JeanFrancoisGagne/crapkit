"""The Action's changed-file command preserves the worklist's path identity."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from hang_guard import HANG_SECONDS


ROOT = Path(__file__).resolve().parents[2]
CHANGED = [
    # id, one record `git diff --name-only -z` wrote, the path the comment matches (None: none)
    ("changed-path-invalid-utf8", b"docs/r\xe9sum\xe9.md", None),
    ("changed-path-cp1252-smart-quote", b"src/o\x92brien.py", None),
    ("deleted-in-pr-path-invalid-utf8", b"src/caf\xe9.py", None),
    ("changed-path-valid-accent", "src/café.py".encode(), "src/café.py"),
    ("changed-path-cjk-emoji", "src/日本\U0001f680.py".encode(), "src/日本\U0001f680.py"),
    ("changed-path-newline", b"src/a\nb.py", "src/a\nb.py"),
]


@pytest.mark.parametrize("record, path", [row[1:] for row in CHANGED], ids=[row[0] for row in CHANGED])
def test_the_comment_is_built_whatever_bytes_a_changed_name_holds(tmp_path, record, path):
    """A pull request that adds, touches or deletes a file named in bytes that
    are not UTF-8 failed the comment step, so no comment was posted. crapkit
    leaves such a file out of every run, so no worklist row can name it."""
    changed = tmp_path / "changed"
    changed.write_bytes(b"src/app.py\0" + record + b"\0")
    rows = [{"path": name, "function": f"row_{i}", "ccn": 7, "risk": 7, "remedy": "decompose"}
            for i, name in enumerate(["src/app.py", *([path] if path else [])])]
    worklist = tmp_path / "worklist.json"
    worklist.write_text(json.dumps({"active": rows}), encoding="utf-8")
    out = tmp_path / "comment.md"

    done = subprocess.run([sys.executable, str(ROOT / "tools/action/comment.py"),
                           "--changed-z", str(changed), "--worklist", str(worklist),
                           "--top", "10", "--out", str(out)], capture_output=True, timeout=HANG_SECONDS)

    assert done.returncode == 0, done.stderr
    text = out.read_text(encoding="utf-8")
    assert "row_0" in text and ("row_1" in text) is bool(path)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True, encoding="utf-8").strip()


@pytest.mark.parametrize("filename", ["café.py", "世界.py", " leading.py", "line\u2028break.py"])
def test_action_changed_files_match_unicode_worklist_paths(tmp_path, filename):
    git(tmp_path, "init")
    git(tmp_path, "config", "core.quotePath", "true")
    path = tmp_path / filename
    path.write_text("before\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
        "commit", "-qm", "before")
    base = git(tmp_path, "rev-parse", "HEAD")
    path.write_text("after\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
        "commit", "-qm", "after")
    command = next(line.strip().split(" > ")[0]
                   for line in (ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
                   if 'diff --name-only "$BASE_SHA...HEAD"' in line)
    argv = [part.replace("$BASE_SHA", base) for part in shlex.split(command)]
    actual = subprocess.check_output(argv, cwd=tmp_path).decode("utf-8").split("\0")[:-1]
    assert actual == [filename]


# --- path bytes that are not UTF-8 --------------------------------------------
#
# A git tree can hold any bytes but `/` and NUL in a name, and a Linux checkout
# writes them as they are. `git diff -z` hands them over unconverted, and the
# builder decoded them as strict UTF-8: one such name failed "build the
# comment", and the composite stopped before posting and before the gate.

def _commit_names(repo: Path, names: list, parent=None) -> str:
    """A commit holding one empty blob under each raw byte name, made through
    the index so no checkout has to spell the name on this filesystem."""
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=b"x\n",
                          capture_output=True, check=True).stdout.decode().strip()
    listing = b"".join(b"100644 " + blob.encode() + b"\t" + name + b"\n" for name in names)
    index = {"GIT_INDEX_FILE": str(repo / f"index-{len(names)}"), "PATH": os.environ["PATH"],
             "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    subprocess.run(["git", "-c", "core.protectNTFS=false", "update-index", "--index-info"], cwd=repo,
                   input=listing, env=index, check=True)
    tree = subprocess.run(["git", "write-tree"], cwd=repo, env=index, capture_output=True,
                          check=True).stdout.decode().strip()
    parents = ["-p", parent] if parent else []
    return git(repo, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
               "commit-tree", tree, *parents, "-m", "names")


def _changed_z(tmp_path, names: list) -> Path:
    """The changed-file list the action's own `git diff -z` line writes for a
    branch that adds `names` on top of a commit holding one ASCII file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    base = _commit_names(repo, [b"base.txt"])
    head = _commit_names(repo, [b"base.txt", *names], base)
    command = next(line.strip().split(" > ")[0]
                   for line in (ROOT / "action.yml").read_text(encoding="utf-8").splitlines()
                   if 'diff --name-only "$BASE_SHA...HEAD"' in line)
    argv = [part.replace("$BASE_SHA", base).replace("HEAD", head) for part in shlex.split(command)]
    out = tmp_path / "changed.z"
    out.write_bytes(subprocess.run(argv, cwd=repo, capture_output=True, check=True).stdout)
    return out


def _builder():
    import importlib.util

    builder = ROOT / "tools" / "action" / "comment.py"
    spec = importlib.util.spec_from_file_location("crapkit_action_comment", builder)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_NAMES = {
    "latin1-byte": ([b"src/caf\xe9.py"], "1 changed file"),
    "cp1252-quotes": ([b"src/\x93quoted\x94.py"], "1 changed file"),
    "0xff-in-a-directory": ([b"\xffdir/a.py"], "1 changed file"),
    "ascii": ([b"src/a.py"], "1 changed file"),
    "utf8-non-ascii": (["src/café.py".encode()], "1 changed file"),
    "markdown-pipe-and-backtick": ([b"src/a|b`c.py"], "1 changed file"),
    "no-changed-file": ([], "the whole repository"),
    "5000-changed-files": ([f"src/m{i}.py".encode() for i in range(5000)], "5000 changed files"),
}


@pytest.mark.parametrize("name", list(_NAMES))
def test_the_builder_reads_every_changed_name_git_hands_it(tmp_path, name):
    """Every name counts toward the heading, whatever its bytes; a name that
    is not UTF-8 matches no worklist row, since crapkit scores none."""
    names, heading = _NAMES[name]
    changed = _changed_z(tmp_path, names)
    out = tmp_path / "comment.md"

    code = _builder().main(["--changed-z", str(changed), "--out", str(out)])

    assert code == 0
    assert heading in out.read_text(encoding="utf-8")
