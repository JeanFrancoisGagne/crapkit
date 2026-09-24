"""A changed file no reader could read fails every gate, on the shape it was found on.

The probe: a cc-only TypeScript scope at ceiling 6, and `src/a.ts` holding a
handler table whose arrow body the reader refuses beside `big`, a ccn-8
function. On 0.8.0 the commit hook printed `1 file(s) could not be tokenized`
and exited 0, claude-hook exited 0 in silence, rescore --gate passed and
verify printed `verify OK`: zero records read as nothing over the ceiling.

Each gate is asked here through the CLI, over a real git repo: the commit hook
and rescore exit 6 naming the file and the fix, verify fails, claude-hook
exits 2, and with an override reason neither override writes or stages the
marks file. The Action's comment, built from verify's own --json, counts the
file as a gate violation. A coverage run over an unreadable file no change
touched still exits 0.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git, git_commit_all, git_init_repo

run_cli = cli_runner()

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"
FIX = "wrap that arrow body in parentheses or a block"

TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["typescript"]
coverage_optional = true
"""

CLEAN = "export function small(n: number): number {\n  return n;\n}\n"
# An old file with the same refused arrow that no change below touches.
OLD = "export const old = {\n  pick: ({ x }) => new Set<string>([x]).has(x),\n};\n"
PROBE = (CLEAN + "export const handlers = {\n"
         "  supportsAction: ({ action }) => new Set<string>([action]).has(action),\n"
         "};\n"
         "export function big(a: number, b: number, c: number, d: number, e: number, "
         "f: number, g: number): number {\n  let n = 0;\n"
         + "".join(f"  if ({v}) {{ n += {i}; }}\n" for i, v in enumerate("abcdefg", 1))
         + "  return n;\n}\n")
NO_REASON = {"CRAPKIT_OVERRIDE_REASON": None}


@pytest.fixture()
def measured(tmp_path: Path) -> Path:
    """The repo at its first commit, scored once: src/old.ts is already
    unreadable, and nothing has touched it since."""
    repo = git_init_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\n", encoding="utf-8")
    (repo / "src" / "a.ts").write_text(CLEAN, encoding="utf-8")
    (repo / "src" / "old.ts").write_text(OLD, encoding="utf-8")
    git_commit_all(repo, "base")
    res = run_cli(repo, "coverage", env_extra=NO_REASON)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "src/old.ts" in res.stderr and "the commit gate refuses these files" in res.stderr
    return repo


@pytest.fixture()
def staged(measured: Path) -> Path:
    (measured / "src" / "a.ts").write_text(PROBE, encoding="utf-8")
    git(measured, "add", "src/a.ts")
    return measured


def test_the_commit_hook_refuses_the_probe_naming_the_file_and_the_fix(staged):
    res = run_cli(staged, "hook-precommit", env_extra=NO_REASON)

    assert res.returncode == 6, res.stdout + res.stderr
    assert "  UNREAD  src/a.ts: " in res.stdout and FIX in res.stdout, res.stdout
    assert "src/old.ts" not in res.stdout, res.stdout


def test_rescore_gate_refuses_the_probe(staged):
    res = run_cli(staged, "rescore", "src/a.ts", "--gate", "--json", env_extra=NO_REASON)

    assert res.returncode == 6, res.stdout + res.stderr
    assert [u["path"] for u in json.loads(res.stdout)["gate"]["unread"]] == ["src/a.ts"]


def test_claude_hook_names_the_probe_after_the_edit(staged):
    event = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": str(staged),
             "tool_input": {"file_path": str(staged / "src" / "a.ts")}}

    res = run_cli(staged, "claude-hook", "--protocol", "1", stdin=json.dumps(event),
                  env_extra=NO_REASON)

    assert res.returncode == 2, res.stderr
    assert res.stderr.startswith("crapkit advisory: src/a.ts could not be read"), res.stderr
    assert FIX in res.stderr and res.stdout == ""


def comment_module():
    spec = importlib.util.spec_from_file_location("crapkit_action_comment",
                                                  ROOT / "tools" / "action" / "comment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verify_fails_the_probe_and_the_action_counts_it(staged):
    git_commit_all(staged, "the probe")

    res = run_cli(staged, "verify", "--json", env_extra=NO_REASON)

    payload = json.loads(res.stdout)
    assert res.returncode == 6, res.stdout + res.stderr
    assert [u["path"] for u in payload["unread_files"]] == ["src/a.ts"]
    counts = comment_module().verdict_line(payload, res.returncode).splitlines()[-1]
    # big is in the file no reader could read, so no function of it was scored:
    # the one gate finding is the file.
    assert "1 changed file: 1 gate violation (1 unread file)" in counts, counts


def test_the_env_override_refuses_the_probe_and_leaves_the_marks_file_alone(staged):
    res = run_cli(staged, "hook-precommit", env_extra={"CRAPKIT_OVERRIDE_REASON": "ship it"})

    assert res.returncode == 6, res.stdout + res.stderr
    assert "override refused: 1 unread file (src/a.ts: " in res.stdout, res.stdout
    assert "override granted" not in res.stdout
    assert not (staged / MARKS).exists()
    staged_names = git_output(staged, "diff", "--cached", "--name-only").split()
    assert staged_names == ["src/a.ts"]


def test_verify_override_refuses_the_probe_and_writes_no_mark(staged):
    git_commit_all(staged, "the probe")

    res = run_cli(staged, "verify", "--override", "ship it", "--json", env_extra=NO_REASON)

    assert res.returncode == 6, res.stdout + res.stderr
    assert json.loads(res.stdout)["overridden"] == []
    assert "override refused: 1 unread file (src/a.ts: " in res.stderr, res.stderr
    assert not (staged / MARKS).exists()


def test_an_unreadable_file_no_change_touched_blocks_nothing(measured):
    (measured / "src" / "a.ts").write_text(CLEAN + "export const two = 2;\n", encoding="utf-8")
    git(measured, "add", "src/a.ts")

    hook = run_cli(measured, "hook-precommit", env_extra=NO_REASON)
    git_commit_all(measured, "a readable change")
    verify = run_cli(measured, "verify", env_extra=NO_REASON)

    assert hook.returncode == 0, hook.stdout + hook.stderr
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "UNREAD" not in hook.stdout + verify.stdout


def git_output(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout
