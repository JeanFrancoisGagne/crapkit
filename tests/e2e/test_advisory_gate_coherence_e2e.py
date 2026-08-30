"""The advisory and the commit gate, asked about the same tree, in one test.

They are two programs reading two things. `claude-hook` reads the working tree
and `git diff HEAD`; `hook-precommit` reads staged blobs. They were also built
apart, and they disagreed: the same comment inside a marked function drew a
green advisory mid-session and exit 6 at `git commit`, because one honoured the
ratchet marks and the other did not. A session of green advisories ending at a
commit wall teaches an agent that the advisory means nothing.

So the pairing is the test. Each case stages one edit and asks both surfaces,
and the assertion is on the pair: green here has to mean green there, and the
function the advisory names has to be the function the gate refuses for.

What deliberately stays different is the wording. PostToolUse runs after the
write and cannot block, so the advisory says the edit landed; the gate says
decompose before committing, because there the commit really is refused.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

import crapkit
from conftest import run_cli

CONFIG = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]
"""

MARKS = ("# crapkit-analysis=5 lizard=1.24.0\n"
         "path\tlong_name\tcrap\n"
         "src/mod.py\tlegacy( n )\t63.6000\n")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   text=True, encoding="utf-8")


def write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def tangled(name: str) -> str:
    """ccn 8: seven decisions plus one, over the target of 6."""
    body = "".join(f"    if n > {i}:\n        n = n + 1\n" for i in range(1, 8))
    return f"def {name}(n):\n{body}    return n\n"


def commented(name: str) -> str:
    """The same function with a comment inside it: same ccn, same long name, and
    a changed range neither surface can help but see."""
    head, rest = tangled(name).split("\n", 1)
    return f"{head}\n    # explains the branch below\n{rest}"


def _child_overrides() -> dict:
    """The environment overrides that make both children import the crapkit this test imported, not an installed one."""
    src = str(Path(crapkit.__file__).resolve().parent.parent)
    inherited = os.environ.get("PYTHONPATH", "")
    return {"PYTHONPATH": os.pathsep.join(p for p in (src, inherited) if p),
            "CRAPKIT_OVERRIDE_REASON": None}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """ccn-8 `legacy` beside ccn-8 `newer`, committed, with `legacy` marked.

    Both are already over the ceiling at HEAD, which is what a repo looks like
    the day after `ratchet seed`. Only one of them carries a mark.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    write(root, "crapkit.toml", CONFIG)
    write(root, "crapkit-ratchet.tsv", MARKS)
    write(root, "src/mod.py", tangled("legacy") + "\n" + tangled("newer"))
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return root


def stage(repo: Path, source: str) -> None:
    """One edit, on disk AND in the index, so both surfaces judge one state."""
    write(repo, "src/mod.py", source)
    git(repo, "add", "src/mod.py")


def advisory(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """`claude-hook` on a PostToolUse payload for the edited file, run from
    outside the repo: its root comes from the file path, never from cwd."""
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "cwd": str(repo),
                          "tool_input": {"file_path": str(repo / "src/mod.py")}})
    return run_cli(tmp_path, "claude-hook", "--protocol", "1", stdin=payload,
                   timeout=300, encoding="utf-8", errors="replace",
                   env_extra=_child_overrides())


def gate(repo: Path) -> subprocess.CompletedProcess:
    return run_cli(repo, "hook-precommit", timeout=300, encoding="utf-8",
                   errors="replace", env_extra=_child_overrides())


def named(text: str) -> set[str]:
    """The functions a breach listing names. Both surfaces print the long name
    last on a `  ccn N  path:line  name` line, so one reader serves both."""
    return {line.rsplit("  ", 1)[-1] for line in text.splitlines()
            if line.startswith("  ccn ")}


# --- the same edit, both surfaces --------------------------------------------

def test_a_marked_function_is_green_on_both_surfaces(repo: Path, tmp_path: Path):
    """Debt the repo already signed for. The advisory subtracts marks because
    otherwise it fires on every edit in a seeded repo; the gate subtracts them
    for the same reason, and this is the pair that says they agree."""
    stage(repo, commented("legacy") + "\n" + tangled("newer"))

    mid_session, at_commit = advisory(repo, tmp_path), gate(repo)

    assert (mid_session.returncode, at_commit.returncode) == (0, 0), \
        mid_session.stderr + at_commit.stdout
    assert mid_session.stderr == ""


def test_an_unmarked_breach_is_red_on_both_surfaces(repo: Path, tmp_path: Path):
    """`newer` carries no mark. Exit 2 mid-session, exit 6 at the commit: two
    different codes for two different powers, one verdict."""
    stage(repo, tangled("legacy") + "\n" + commented("newer"))

    mid_session, at_commit = advisory(repo, tmp_path), gate(repo)

    assert (mid_session.returncode, at_commit.returncode) == (2, 6), \
        mid_session.stderr + at_commit.stdout


def test_both_surfaces_name_the_same_function(repo: Path, tmp_path: Path):
    """The stronger claim. Agreeing exit codes could still point at different
    functions: the advisory reads `git diff HEAD` against the working tree and
    the gate reads staged blobs, and `legacy` is over the ceiling in both."""
    stage(repo, tangled("legacy") + "\n" + commented("newer"))

    mid_session, at_commit = advisory(repo, tmp_path), gate(repo)

    assert named(mid_session.stderr) == named(at_commit.stdout) == {"newer( n )"}


def test_the_marks_file_is_what_makes_the_difference(tmp_path: Path):
    """Guards the pair above from the boring explanation: without the marks
    file, the same `legacy` edit is red on both surfaces. The exemption is doing
    the work, not some quirk of which function got touched."""
    root = tmp_path / "unmarked"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    write(root, "crapkit.toml", CONFIG)
    write(root, "src/mod.py", tangled("legacy") + "\n" + tangled("newer"))
    git(root, "add", "-A")
    git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    stage(root, commented("legacy") + "\n" + tangled("newer"))

    mid_session, at_commit = advisory(root, tmp_path), gate(root)

    assert (mid_session.returncode, at_commit.returncode) == (2, 6)
    assert named(mid_session.stderr) == named(at_commit.stdout) == {"legacy( n )"}


# --- what stays different, on purpose ----------------------------------------

def test_the_advisory_never_borrows_the_gates_wording(repo: Path, tmp_path: Path):
    """PostToolUse runs after the write. Telling an agent its landed edit was
    blocked, or to decompose before committing, describes a refusal that did not
    happen, on a file that is already on disk."""
    stage(repo, tangled("legacy") + "\n" + commented("newer"))

    mid_session, at_commit = advisory(repo, tmp_path), gate(repo)

    assert "the edit landed; nothing was blocked" in mid_session.stderr
    for forbidden in ("blocked by", "crapkit gate:", "decompose before committing"):
        assert forbidden not in mid_session.stderr, forbidden
    assert "decompose before committing" in at_commit.stdout
