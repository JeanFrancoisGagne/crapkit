"""Configuration is found upward (ADR 0002): `cd web && crapkit worklist` works.

The field shape: a monorepo whose root crapkit.toml claims `web/`, and a user
standing in `web/` or below it. On 0.4.15 every command there exited 3 with
`no crapkit.toml at .../web` while `--repo ..` worked, `brief src/grade.py
classify` from `web/` named a file the root never had, and `init` from `web/`
wrote a second configuration claiming the same files. Every case drives
`python -m crapkit` from the directory the user stands in.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

MAKE_COV = ('import json\n'
            'json.dump({"meta": {"branch_coverage": True}, "files": {}},'
            ' open("cov.json", "w"))\n')

CONFIG = """[crapkit]
target = 6
worklist_floor = 5
mutation_command = "python -c pass"

[[scope]]
name = "web"
paths = ["web"]
languages = ["python"]

[[lane]]
name = "py"
command = "python make_cov.py"
artifact = "cov.json"
parser = "coveragepy"
scopes = ["web"]
full_suite = false
"""

USING = "crapkit: using crapkit.toml at "

run_cli = cli_runner(timeout=180)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                   cwd=repo, check=True, capture_output=True)


def _source(ifs: int) -> str:
    body = "".join(f"    if a > {i}:\n        r += {i}\n" for i in range(1, ifs + 1))
    return f"def classify(a, b):\n    r = 0\n{body}    return r\n"


@pytest.fixture()
def mono(tmp_path: Path) -> Path:
    """The root configuration claims web/; api/ is tracked source no scope
    claims; the root .gitignore already ignores the store."""
    top = tmp_path / "mono"
    (top / "web" / "src").mkdir(parents=True)
    (top / "web" / "src" / "grade.py").write_text(_source(5), encoding="utf-8")
    (top / "api" / "src").mkdir(parents=True)
    (top / "api" / "src" / "app.py").write_text("def route(a):\n    return a or 0\n",
                                                encoding="utf-8")
    (top / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (top / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    (top / ".gitignore").write_text("node_modules/\ncoverage/\n.crapkit/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=top, check=True,
                   capture_output=True)
    _git(top, "add", "-A")
    _git(top, "commit", "-q", "-m", "scaffold monorepo")
    return top


def _scored(mono: Path) -> None:
    res = run_cli(mono, "coverage")
    assert res.returncode == 0, res.stdout + res.stderr


def _breach(mono: Path) -> Path:
    """web/src/grade.py rewritten to ccn 12 in the working tree; returns the
    directory the user stands in."""
    (mono / "web" / "src" / "grade.py").write_text(_source(11), encoding="utf-8")
    return mono / "web" / "src"


# --- the walk -----------------------------------------------------------------

def test_a_command_below_the_root_finds_the_configuration_and_says_so(mono: Path):
    res = run_cli(mono / "web", "coverage")
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"{USING}{mono}" in res.stderr, res.stderr

    res = run_cli(mono / "web" / "src", "worklist", "--json")

    assert res.returncode == 0, res.stdout + res.stderr
    assert f"{USING}{mono}" in res.stderr, res.stderr
    assert [e["path"] for e in json.loads(res.stdout)["active"]] == ["web/src/grade.py"]


def test_at_the_root_itself_nothing_is_said(mono: Path):
    _scored(mono)

    res = run_cli(mono, "worklist")

    assert res.returncode == 0, res.stdout + res.stderr
    assert USING not in res.stderr, res.stderr


def test_an_explicit_repo_names_an_exact_root_and_walks_nowhere(mono: Path):
    res = run_cli(mono, "worklist", "--repo", "web")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"no crapkit.toml at {mono / 'web'}" in res.stderr, res.stderr


def test_a_nested_repository_never_borrows_the_configuration_above_it(mono: Path):
    """A `.git` entry without a configuration stops the walk (ADR 0002)."""
    lib = mono / "lib"
    lib.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=lib, check=True,
                   capture_output=True)

    res = run_cli(lib, "worklist")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"no crapkit.toml at {lib}" in res.stderr, res.stderr
    assert USING not in res.stderr


def test_the_commit_gate_gates_from_below_the_root(mono: Path):
    """The reviewer's own transcript: `hook-precommit` from web/ exited 3."""
    stand = _breach(mono)
    _git(mono, "add", "web/src/grade.py")

    res = run_cli(stand, "hook-precommit")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "web/src/grade.py:1" in res.stdout


# --- relative path arguments, rebased from where the user stands --------------

def test_rescore_gate_judges_the_file_named_from_where_the_user_stands(mono: Path):
    """From web/src, `grade.py` is web/src/grade.py. Without the rebase the gate
    scored nothing and passed, a false pass over a ccn 12 function."""
    _scored(mono)
    stand = _breach(mono)

    res = run_cli(stand, "rescore", "--gate", "grade.py")

    assert res.returncode == 6, res.stdout + res.stderr
    assert "web/src/grade.py:1" in res.stdout


def test_brief_and_explain_take_the_path_from_where_the_user_stands(mono: Path):
    _scored(mono)
    stand = mono / "web" / "src"

    brief = run_cli(stand, "brief", "grade.py", "classify", "--json")
    explain = run_cli(stand, "explain", "grade.py", "classify", "--json")

    assert brief.returncode == 0, brief.stdout + brief.stderr
    assert json.loads(brief.stdout)["path"] == "web/src/grade.py"
    assert explain.returncode == 0, explain.stdout + explain.stderr
    assert json.loads(explain.stdout)["path"] == "web/src/grade.py"


def test_mutate_files_are_rebased_the_same_way(mono: Path):
    stand = _breach(mono)

    res = run_cli(stand, "mutate", "--files", "grade.py", "--json", "--max-mutants", "1")

    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["mutants"] == 1
    assert "outside the scored corpus" not in res.stderr


def test_a_path_climbing_out_of_the_root_is_refused(mono: Path):
    _scored(mono)
    (mono.parent / "outside.py").write_text("def f():\n    return 1\n", encoding="utf-8")

    res = run_cli(mono / "web" / "src", "rescore", "--gate", "../../../outside.py")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"is outside the repo at {mono}" in res.stderr, res.stderr


# --- init under an ancestor configuration -------------------------------------

@pytest.mark.parametrize("stand, claimed", [("web", "web"), ("web/src", "web/src")])
def test_init_refuses_where_an_ancestor_configuration_already_claims_the_directory(
        mono: Path, stand: str, claimed: str):
    res = run_cli(mono / stand, "init")

    assert res.returncode == 3, res.stdout + res.stderr
    assert f"crapkit.toml at {mono} already claims {claimed} (scope 'web')" in res.stderr, \
        res.stderr
    assert not (mono / stand / "crapkit.toml").exists(), "a second configuration was written"


def test_init_writes_a_nested_configuration_where_no_scope_claims_the_directory(mono: Path):
    """api/ is tracked source the root configuration never claimed, so a nested
    configuration there shadows nothing; the root .gitignore already ignores
    `.crapkit/` at every depth, so no api/.gitignore is written for it."""
    res = run_cli(mono / "api", "init")

    assert res.returncode == 0, res.stdout + res.stderr
    assert (mono / "api" / "crapkit.toml").is_file()
    assert USING not in res.stderr, "init writes where the user stands; it adopts nothing"
    assert not (mono / "api" / ".gitignore").exists(), "the store is ignored one level up"
    assert "added to .gitignore" not in res.stdout


# --- the MCP server walks the same way ----------------------------------------

def _rpc(msg_id, method, params=None) -> str:
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg)


def _call(msg_id, name, arguments=None) -> str:
    return _rpc(msg_id, "tools/call", {"name": name, "arguments": arguments or {}})


def _serve(cwd: Path, requests: list[str]) -> dict:
    proc = run_cli(cwd, "mcp", stdin="\n".join(requests) + "\n")
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    return {m["id"]: m for m in map(json.loads, proc.stdout.strip().splitlines())}


def test_a_server_started_below_the_root_serves_the_configuration_above_it(mono: Path):
    """A globally registered client starts the server in the directory it
    opened, which in a monorepo is a workspace."""
    _scored(mono)

    replies = _serve(mono / "web", [
        _rpc(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {}}),
        _call(2, "worklist"),
        _call(3, "worklist", {"repo": str(mono / "web" / "src")}),
    ])

    for msg_id in (2, 3):
        call = replies[msg_id]["result"]
        assert call["isError"] is False, call
        assert [e["path"] for e in call["structuredContent"]["active"]] == ["web/src/grade.py"]
