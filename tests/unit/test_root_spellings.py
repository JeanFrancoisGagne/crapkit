r"""A root on a network share is refused before any child starts, and a mapped
drive keeps its letter.

cmd.exe cannot stand in a UNC directory. Handed one, it prints `UNC paths are
not supported. Defaulting to Windows directory.` and runs the command in
C:\Windows: a pytest lane there collected C:\Windows for 42 s of CPU before it
was killed. `--repo \\server\share\repo`, a session standing in a share, and a
repo on a mapped network drive, which resolve() turns into the share behind
the letter, all rooted a run on such a path. This machine's own admin share
comes back to its drive (repopath.native, pinned in test_cli_path_spellings);
a share on another host has no drive to come back to, so crapkit says to map
one.

No host here serves a remote share, so the share is `\\fileserver.invalid\share`,
a name that fails at once, and a mapped drive is a local checkout whose
resolve() answers with that share (path_spellings.mapped_drive). POSIX has no
UNC root, and sh starts a command in any directory: every row here is Windows
only.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401
from crapkit import mcp_server
from crapkit.cli import main
from crapkit.cli._shared import _command_root

from path_spellings import REMOTE_SHARE, mapped_drive, only_windows

pytestmark = only_windows

REMOTE_ROOT = REMOTE_SHARE + "\\repo"

# id -> how a root on another host's share is spelled
NETWORK_ROOTS = {
    "unc": REMOTE_ROOT,
    "unc-forward": REMOTE_ROOT.replace("\\", "/"),
    "unc-extended": "\\\\?\\UNC\\" + REMOTE_ROOT[2:],
    "wsl": "\\\\wsl.localhost\\Ubuntu\\tmp\\repo",
}

# The commands that start a child through the shell, one argv each.
COMMANDS = {
    "test-scoped": ["test-scoped", "src/app.ts"],
    "mutate": ["mutate"],
    "coverage": ["coverage"],
    "verify": ["verify"],
    "doctor": ["doctor"],
    "init": ["init"],
}


@pytest.fixture()
def no_child(monkeypatch):
    """Every child crapkit starts, git included, goes through Popen."""
    def refuse(self, args, *a, **kw):
        raise AssertionError(f"a child started before the refusal: {args!r}")

    monkeypatch.setattr(subprocess.Popen, "__init__", refuse)


def _refused(capsys, argv: list[str]) -> str:
    code = main(argv)
    err = capsys.readouterr().err
    assert code == 3, err
    return err


def _says_map_the_share(err: str, share: str) -> None:
    assert "network share" in err and "cmd.exe" in err, err
    assert f"net use Z: {share}" in err, err


@pytest.mark.parametrize("which", NETWORK_ROOTS)
@pytest.mark.parametrize("command", COMMANDS)
def test_a_repo_flag_naming_a_network_share_is_refused_before_any_child(no_child, capsys,
                                                                        which, command):
    err = _refused(capsys, [*COMMANDS[command], "--repo", NETWORK_ROOTS[which]])

    share = "\\\\wsl.localhost\\Ubuntu" if which == "wsl" else REMOTE_SHARE
    _says_map_the_share(err, share)


@pytest.mark.parametrize("command", COMMANDS)
def test_a_session_standing_in_a_network_share_is_refused_before_any_child(no_child,
                                                                           monkeypatch,
                                                                           capsys, command):
    monkeypatch.setattr(os, "getcwd", lambda: REMOTE_ROOT)

    err = _refused(capsys, COMMANDS[command])

    _says_map_the_share(err, REMOTE_SHARE)
    assert "Z:\\repo" in err, err


def _share_dropped(stat):
    """os.stat as a share answers once its connection drops: WinError 64, which
    pathlib's is_file does not read as a missing file."""
    def dropped(path, *args, **kwargs):
        if str(path).startswith("\\\\"):
            raise OSError(0, "The specified network name is no longer available", str(path), 64)
        return stat(path, *args, **kwargs)

    return dropped


@pytest.mark.parametrize("command", COMMANDS)
def test_a_session_in_a_share_whose_stat_fails_is_refused_before_any_read(no_child, monkeypatch,
                                                                         capsys, command):
    """The walk up to the nearest crapkit.toml would read the share, and every
    root it could find there is refused, so it never starts."""
    monkeypatch.setattr(os, "getcwd", lambda: REMOTE_ROOT)
    monkeypatch.setattr(os, "stat", _share_dropped(os.stat))

    err = _refused(capsys, COMMANDS[command])

    _says_map_the_share(err, REMOTE_SHARE)


@pytest.mark.parametrize("tail, drive_path", [("\\team\\app", "Z:\\team\\app"),
                                              ("", "Z:\\"), ("\\", "Z:\\")],
                         ids=["nested", "share-root", "share-root-trailing"])
def test_the_refusal_names_the_drive_path_to_run_from(no_child, capsys, tail, drive_path):
    err = _refused(capsys, ["coverage", "--repo", REMOTE_SHARE + tail])

    assert f"{REMOTE_SHARE}{tail}" in err, err
    assert err.rstrip().endswith(f"run crapkit from {drive_path}"), err


@pytest.mark.parametrize("origin", ["repo-flag", "working-directory"])
def test_a_root_on_a_mapped_drive_keeps_its_letter(repo, monkeypatch, origin):  # noqa: F811
    mapped_drive(monkeypatch, repo)
    assert str(repo.resolve()).startswith(REMOTE_SHARE)  # the stub holds
    monkeypatch.chdir(repo)

    root = _command_root(str(repo) if origin == "repo-flag" else None)

    assert root == repo


def test_a_directory_below_a_mapped_drive_root_finds_its_config_on_the_drive(repo,  # noqa: F811
                                                                            monkeypatch):
    mapped_drive(monkeypatch, repo)
    monkeypatch.chdir(repo / "src")

    assert _command_root(None) == repo


RECORD_CWD = "import os, sys; open(sys.argv[1], 'w').write(os.getcwd())"


def _record_cwd_scoped_tests(root: Path) -> Path:
    """The scoped-test command writes where it started into cwd.txt."""
    out = root / "cwd.txt"
    command = f'"{sys.executable}" -c "{RECORD_CWD}" "{out}"'
    toml = (root / "crapkit.toml").read_text(encoding="utf-8")
    (root / "crapkit.toml").write_text(
        toml.replace('src = "python -c pass"', f"src = '''{command}'''"), encoding="utf-8")
    return out


@pytest.mark.parametrize("origin", ["repo-flag", "working-directory"])
def test_a_lane_on_a_mapped_drive_starts_on_the_drive(repo, monkeypatch, capsys,  # noqa: F811
                                                      origin):
    out = _record_cwd_scoped_tests(repo)
    mapped_drive(monkeypatch, repo)
    monkeypatch.chdir(repo)
    argv = ["test-scoped", "src/app.ts"] + (["--repo", str(repo)] if origin == "repo-flag" else [])

    code = main(argv)

    assert code == 0, capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == str(repo)


def test_an_mcp_call_naming_a_mapped_drive_repo_is_served_on_the_drive(repo,  # noqa: F811
                                                                       monkeypatch):
    r"""The server walked the call's `repo` from its resolved spelling, so the
    CLI it spawned got `--repo \\server\share\...` for a checkout the user
    opened as a drive letter, and refused it."""
    calls: list = []
    monkeypatch.setattr(mcp_server, "run_owned", lambda argv, **kw: calls.append(argv)
                        or subprocess.CompletedProcess(argv, 0, '{"schema": 1}', ""))
    mapped_drive(monkeypatch, repo)

    reply = mcp_server._call_tool(repo, "list_runs", {"repo": str(repo / "src")})

    assert reply.get("isError") is not True, reply
    assert calls[0][-2:] == ["--repo", str(repo)], calls
