"""`crapkit mcp --repo` holding a variable the client never expanded serves
where the server was started, and says so.

Cursor's docs wire an MCP server with `${workspaceFolder}`. The Cursor agent
CLI expands only `${NAME}`, `${env:NAME}` and `${NAME:-default}`, so the server
received `--repo ${workspaceFolder}` literally, read it as a directory below its
working directory, and answered every call `no crapkit.toml in
<cwd>/${workspaceFolder}` while the client listed it as ready. The server now
drops a `--repo` that holds `${...}`, serves as if none was given, and names
the variable on stderr, which clients keep as the server's log.
"""
import argparse
from pathlib import Path

import pytest

from crapkit import mcp_server
from crapkit.cli import analyses


def _served(monkeypatch, cwd: Path, repo: str | None) -> Path:
    served = []
    monkeypatch.setattr(mcp_server, "serve", lambda root, **_: served.append(root) or 0)
    monkeypatch.chdir(cwd)
    assert analyses.cmd_mcp(argparse.Namespace(repo=repo)) == 0
    return served[0]


def _measured(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n',
        encoding="utf-8")
    return path


@pytest.mark.parametrize("placeholder", ["${workspaceFolder}", "${workspaceFolder}/web",
                                         "${userHome}${/}src", "${env:PROJECT}"])
def test_an_unexpanded_variable_serves_the_directory_the_client_started_in(
        monkeypatch, tmp_path, capsys, placeholder):
    repo = _measured(tmp_path / "repo")

    assert _served(monkeypatch, repo, placeholder) == repo.resolve()

    err = capsys.readouterr().err
    assert f"--repo {placeholder!r}" in err
    assert "the MCP client did not expand" in err
    assert "absolute path" in err


def test_the_walk_up_still_applies_below_the_root(monkeypatch, tmp_path, capsys):
    repo = _measured(tmp_path / "repo")
    (repo / "web").mkdir()

    assert _served(monkeypatch, repo / "web", "${workspaceFolder}") == repo.resolve()


def test_an_expanded_repo_is_still_an_exact_root(monkeypatch, tmp_path, capsys):
    repo = _measured(tmp_path / "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert _served(monkeypatch, elsewhere, str(repo)) == repo.resolve()
    assert "did not expand" not in capsys.readouterr().err
