"""A lone CR ends a line for the reader and not for git's diff.

crapkit reads source the way Python's compiler, coverage.py and ECMAScript read
it: `\\r\\n`, `\\r` and `\\n` each end a line (analyze.decode_source). git
numbers a diff's lines at LF only. The gates matched git's numbers against the
reader's function spans unmapped, so each lone CR above a function moved the
diff one line up from the function it changed:

- a new CR-only file is one line to git (`@@ -0,0 +1 @@`), and the gate judged
  only the function that touches line 1;
- an edit on a def line below one lone CR is git line 8 and reader line 9, and
  it touched no function at all.

Both committed a function at ccn 2 over a target of 1. verify, the advisory
hook and mutate read a working-tree diff the same way and missed the same line.
"""
import argparse
import io
import json
import subprocess
from pathlib import Path

from crapkit.cli import main
from crapkit.cli.analyses import _mutation_targets
from crapkit.cli.claude_hook import _advise
from crapkit.cli.scoring import _gate_candidates
from crapkit.config import load_config_text
from crapkit.hook import gate_staged
from crapkit.score import ScoredRow

CONFIG = ('[crapkit]\ntarget = 1\n\n[[scope]]\nname = "all"\npaths = ["."]\n'
          'languages = ["python"]\ncoverage_optional = true\n')
# Two functions of one `if` each: ccn 2, over the target of 1.
CR_ONLY = (b"def cafe(n):\r    if n:\r        return 1\r    return 2\r\r\r"
           b"def second(m):\r    if m:\r        return m\r    return 0\r")
# `x = 1` sits after a lone CR: git line 1, reader line 2. `target` is git lines
# 8-11 and reader lines 9-12.
LONE_CR = (b"# note\rx = 1\n" b"def first(a):\n    if a:\n        return 1\n    return 2\n\n\n"
           b"def target(b):\n    if b:\n        return 1\n    return 2\n")
EDITED = LONE_CR.replace(b"def target(b):", b"def target(b, c=0):")


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def committed(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    for rel, data in files.items():
        (repo / rel).write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    return repo


def gated(repo: Path) -> list[str]:
    verdict = gate_staged(repo, load_config_text(CONFIG))
    return sorted(violation.long_name for violation in verdict.violations)


def test_a_new_cr_only_file_gates_every_function(tmp_path):
    repo = committed(tmp_path, {})
    (repo / "m.py").write_bytes(CR_ONLY)
    git(repo, "add", "m.py")

    assert gated(repo) == ["cafe( n )", "second( m )"]


def test_the_hook_gates_an_edit_below_a_lone_cr(tmp_path):
    repo = committed(tmp_path, {"m.py": LONE_CR})
    (repo / "m.py").write_bytes(EDITED)
    git(repo, "add", "m.py")

    assert gated(repo) == ["target( b , c = 0 )"]


def row(name: str, start: int, end: int) -> ScoredRow:
    return ScoredRow("all", "m.py", name, start, end, 2, 2, 2, 4, 1, 0, 0.0, "measured", 6.0,
                     "decompose")


def test_rescore_gate_judges_an_edit_below_a_lone_cr(tmp_path):
    """The working tree is the diff's new side, so its bytes place git's lines."""
    repo = committed(tmp_path, {"m.py": LONE_CR})
    (repo / "m.py").write_bytes(EDITED)
    rows = [row("first( a )", 3, 6), row("target( b , c = 0 )", 9, 12)]

    assert [r.long_name for r in _gate_candidates(repo, rows)] == ["target( b , c = 0 )"]


def test_verify_gates_an_edit_below_a_lone_cr(tmp_path, capsys):
    """The diff since the baseline has the working tree for its new side."""
    repo = committed(tmp_path, {"m.py": LONE_CR})
    assert main(["coverage", "--repo", str(repo)]) == 0
    assert main(["ratchet", "seed", "--repo", str(repo)]) == 0
    git(repo, "add", "crapkit-ratchet.tsv")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    # A new signature is a new ratchet key, so no mark covers the edited function.
    (repo / "m.py").write_bytes(EDITED)
    capsys.readouterr()

    code = main(["verify", "--repo", str(repo)])

    out = capsys.readouterr()
    assert (code, "m.py:9" in out.out + out.err) == (6, True), out


def test_the_advisory_hook_judges_an_edit_below_a_lone_cr(tmp_path, capsys):
    repo = committed(tmp_path, {"m.py": LONE_CR})
    (repo / "m.py").write_bytes(EDITED)
    event = {"hook_event_name": "PostToolUse", "tool_input": {"file_path": str(repo / "m.py")}}

    code = _advise(argparse.Namespace(protocol="1"), io.StringIO(json.dumps(event)))

    assert (code, "target" in capsys.readouterr().err) == (2, True)


def test_mutate_targets_the_line_the_edit_changed(tmp_path):
    repo = committed(tmp_path, {"m.py": LONE_CR})
    (repo / "m.py").write_bytes(LONE_CR.replace(b"    if b:", b"    if b > 0:"))

    assert _mutation_targets(repo, None) == {"m.py": {10}}
