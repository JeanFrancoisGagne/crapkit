"""Which `crapkit` launchers a PATH holds, and which installer owns each.

A user who keeps 0.7.6 in one environment and upgrades into another has two
launchers on PATH. The shell, a git hook, the plugin's hooks and an MCP client
each start whichever their own PATH lists first, and doctor named neither. And
`doctor --plugin-root` told every version gap to run `pip install -U crapkit`,
which does not reach a uv tool or pipx install.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crapkit.launchers import ephemeral_runner, install_line, path_launchers, upgrade_command

WINDOWS = os.name == "nt"


def shim(directory: Path, version: str = "0.8.0") -> Path:
    """A `crapkit` launcher that answers `--version`, spelled the way this
    platform's PATH lookup finds one."""
    directory.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        path = directory / "crapkit.bat"
        path.write_text(f"@echo crapkit {version}\n", encoding="utf-8")
        return path
    path = directory / "crapkit"
    path.write_text(f'#!/bin/sh\necho "crapkit {version}"\n', encoding="utf-8")
    path.chmod(0o755)
    return path


def joined(*dirs) -> str:
    return os.pathsep.join(str(d) for d in dirs)


# --- every launcher on PATH, once, in order ---------------------------------------

def test_every_launcher_on_path_comes_back_in_path_order(tmp_path):
    new, old = shim(tmp_path / "new"), shim(tmp_path / "old")
    (tmp_path / "empty").mkdir()

    assert path_launchers(joined(tmp_path / "new", tmp_path / "empty", tmp_path / "old")) == [
        str(new), str(old)]


def test_a_directory_listed_twice_counts_once(tmp_path):
    only = shim(tmp_path / "bin")

    assert path_launchers(joined(tmp_path / "bin", "", tmp_path / "bin")) == [str(only)]


@pytest.mark.skipif(WINDOWS, reason="a directory symlink needs a privilege Windows CI lacks")
def test_a_directory_linked_to_another_counts_once(tmp_path):
    """/bin -> /usr/bin on a merged-usr system lists one launcher twice."""
    real = shim(tmp_path / "usr" / "bin")
    (tmp_path / "bin").symlink_to(tmp_path / "usr" / "bin")

    assert path_launchers(joined(tmp_path / "usr" / "bin", tmp_path / "bin")) == [str(real)]


def test_entries_under_the_skipped_environment_are_left_out(tmp_path):
    cached = tmp_path / "cache" / "uv" / "archive-v0" / "abc"
    shim(cached / ("Scripts" if WINDOWS else "bin"))
    kept = shim(tmp_path / "tools")

    path = joined(cached / ("Scripts" if WINDOWS else "bin"), tmp_path / "tools")
    assert path_launchers(path, skip=str(cached)) == [str(kept)]
    assert len(path_launchers(path)) == 2


def test_a_quoted_entry_is_read_without_its_quotes(tmp_path):
    only = shim(tmp_path / "with space")

    assert path_launchers(f'"{tmp_path / "with space"}"') == [str(only)]


# --- environments a runner builds for one command ---------------------------------

@pytest.mark.parametrize("prefix, runner", [
    ("/home/u/.cache/uv/archive-v0/ePz6wC7FqYPz2zfC", "uvx"),
    (r"C:\Users\u\AppData\Local\uv\cache\archive-v0\ePz6wC7F", "uvx"),
    ("/home/u/.cache/pipx/3c2c1d4e", "pipx run"),
    ("/home/u/.local/pipx/.cache/3c2c1d4e", "pipx run"),
    (r"C:\Users\u\AppData\Local\pipx\pipx\Cache\3c2c1d4e", "pipx run"),
    ("/home/u/.local/share/uv/tools/crapkit", None),
    ("/home/u/.local/share/pipx/venvs/crapkit", None),
    ("/home/u/repo/.venv", None),
])
def test_an_environment_built_for_one_command_is_named_by_its_runner(prefix, runner):
    assert ephemeral_runner(prefix) == runner


def test_each_runner_names_the_install_that_stays():
    assert install_line("uvx") == "uv tool install crapkit"
    assert install_line("pipx run") == "pipx install crapkit"


# --- the installer that owns a launcher --------------------------------------------

def quote(word: str) -> str:
    return f"<{word}>"


def test_a_uv_tool_launcher_upgrades_through_uv(tmp_path):
    launcher = shim(tmp_path / "uv" / "tools" / "crapkit" / "bin")

    assert upgrade_command(str(launcher), quote) == "uv tool upgrade crapkit"


def test_a_pipx_launcher_upgrades_through_pipx(tmp_path):
    launcher = shim(tmp_path / "pipx" / "venvs" / "crapkit" / "bin")

    assert upgrade_command(str(launcher), quote) == "pipx upgrade crapkit"


def test_a_copied_windows_launcher_is_read_for_the_interpreter_it_embeds(tmp_path):
    """uv tool and pipx put a copy of the launcher in ~/.local/bin on Windows;
    its bytes still name the tool environment's interpreter."""
    launcher = tmp_path / ".local" / "bin" / "crapkit.exe"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"MZ\x90\x00" + b"\x00" * 64
                         + rb"C:\Users\u\AppData\Roaming\uv\tools\crapkit\Scripts\python.exe" + b"UVSC")

    assert upgrade_command(str(launcher), quote) == "uv tool upgrade crapkit"


@pytest.mark.skipif(WINDOWS, reason="a launcher symlink needs a privilege Windows CI lacks")
def test_a_linked_launcher_is_owned_by_where_its_link_points(tmp_path):
    """uv tool on Linux links ~/.local/bin/crapkit into its tools directory."""
    real = shim(tmp_path / "share" / "uv" / "tools" / "crapkit" / "bin")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "crapkit").symlink_to(real)

    assert upgrade_command(str(tmp_path / "bin" / "crapkit"), quote) == "uv tool upgrade crapkit"


def test_a_venv_launcher_upgrades_through_the_python_beside_it(tmp_path):
    scripts = tmp_path / "venv" / ("Scripts" if WINDOWS else "bin")
    launcher = shim(scripts)
    python = scripts / ("python.exe" if WINDOWS else "python3")
    python.write_bytes(b"")

    assert upgrade_command(str(launcher), quote) == f"<{python}> -m pip install --upgrade crapkit"


def test_a_venv_uv_made_upgrades_through_uv_pip_because_it_holds_no_pip(tmp_path):
    scripts = tmp_path / "venv" / ("Scripts" if WINDOWS else "bin")
    launcher = shim(scripts)
    python = scripts / ("python.exe" if WINDOWS else "python3")
    python.write_bytes(b"")
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\nuv = 0.9.2\nversion_info = 3.12.7\n",
                                                  encoding="utf-8")

    assert upgrade_command(str(launcher), quote) == f"uv pip install --python <{python}> --upgrade crapkit"


def test_a_windows_install_s_scripts_launcher_upgrades_through_the_python_above_it(tmp_path):
    launcher = shim(tmp_path / "Python312" / "Scripts")
    python = tmp_path / "Python312" / ("python.exe" if WINDOWS else "python3")
    python.write_bytes(b"")

    assert upgrade_command(str(launcher), quote) == f"<{python}> -m pip install --upgrade crapkit"


def test_a_script_s_shebang_names_its_interpreter(tmp_path):
    """pip --user on Linux: ~/.local/bin/crapkit starts /usr/bin/python3."""
    python = tmp_path / "usr" / "bin" / "python3.12"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    launcher = tmp_path / "home" / ".local" / "bin" / "crapkit"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(f"#!{python}\nfrom crapkit.cli import main\n", encoding="utf-8")

    assert upgrade_command(str(launcher), quote) == f"<{python}> -m pip install --upgrade crapkit"


def test_a_shebang_naming_a_shell_is_not_the_interpreter(tmp_path):
    """pip writes `#!/bin/sh` and an exec line when the interpreter path holds a
    space; /bin/sh is not what runs pip."""
    launcher = tmp_path / "bin" / "crapkit"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n'''exec' \"/opt/my env/bin/python\" \"$0\" \"$@\"\n", encoding="utf-8")

    assert upgrade_command(str(launcher), quote) == "python -m pip install --upgrade crapkit"


def test_a_launcher_that_cannot_be_read_falls_back_to_plain_pip(tmp_path):
    assert upgrade_command(str(tmp_path / "gone" / "crapkit"), quote) == (
        "python -m pip install --upgrade crapkit")
