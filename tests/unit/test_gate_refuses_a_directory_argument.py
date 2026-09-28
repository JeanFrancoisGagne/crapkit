"""A file argument that names a directory is refused, never judged as nothing.

`rescore --gate src` placed the directory, assigned it to no scope, judged 0
functions and passed at exit 0 while src/app.ts held an uncommitted function
over its ceiling that fails the gate when named. `src/`, `.` and `""` did the
same, and the MCP tool `check_gate`, which runs the same command, answered
`gate.ok: true` with `judged: 0` for an argument its schema calls a source file.
Every spelling of a directory now exits 3 with the directory named, as a path
that does not exist does, and `--json` carries the error object.
"""
import json

import pytest
from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit import mcp_server
from crapkit.cli import main

REFUSAL = "is a directory; name the source files in it"


def run(root, capsys, *argv: str) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(root)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def breached(repo, capsys):  # noqa: F811 (the imported fixture)
    """A measured repo whose src/app.ts holds an uncommitted breach."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    capsys.readouterr()
    return repo


def test_the_file_itself_fails_the_gate(breached, capsys):
    """The control: named directly, the breach fails the gate."""
    code, _, err = run(breached, capsys, "rescore", "--gate", "src/app.ts")

    assert code == 6 and "knotty" in err, err


@pytest.mark.parametrize("spelling, named", [("src", "src"), ("src/", "src"), (".", "."),
                                             ("", ".")])
def test_rescore_gate_refuses_a_directory(breached, capsys, spelling, named):
    code, out, err = run(breached, capsys, "rescore", "--gate", spelling)

    assert code == 3, out + err
    assert f"{named} {REFUSAL}" in err, err
    assert "gate:" not in out, out


def test_a_directory_among_files_is_refused_too(breached, capsys):
    code, _, err = run(breached, capsys, "rescore", "--gate", "src/app.ts", "web")

    assert code == 3 and f"web {REFUSAL}" in err, err


def test_a_directory_named_like_a_source_file_is_refused_not_crashed(breached, capsys):
    (breached / "src" / "odd.ts").mkdir()

    code, _, err = run(breached, capsys, "rescore", "--gate", "src/odd.ts")

    assert code == 3, err
    assert f"src/odd.ts {REFUSAL}" in err and "Traceback" not in err, err


def test_the_json_refusal_is_the_error_object(breached, capsys):
    code, out, _ = run(breached, capsys, "rescore", "--gate", "src", "--json")

    error = json.loads(out)["error"]
    assert (code, error["exit"], error["kind"]) == (3, 3, "config"), out
    assert error["message"] == f"src {REFUSAL}"


@pytest.mark.parametrize("path", ["src", "."])
def test_check_gate_answers_a_directory_with_an_error_not_ok(breached, path):
    result = mcp_server._call_tool(breached, "check_gate", {"path": path})

    text = result["content"][0]["text"]
    assert result["isError"] is True, text
    assert REFUSAL in text and '"ok": true' not in text, text


def test_explain_refuses_a_directory_the_same_way(breached, capsys):
    code, _, err = run(breached, capsys, "explain", "src", "dispatch")

    assert code == 3 and f"src {REFUSAL}" in err, err
