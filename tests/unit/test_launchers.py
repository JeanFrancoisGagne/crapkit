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

from crapkit.launchers import (ephemeral_runner, install_line, path_launchers, reinstall_command,
                                upgrade_command)

WINDOWS = os.name == "nt"
BIN = "Scripts" if WINDOWS else "bin"


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


def uv_cache(root: Path) -> Path:
    """A uv cache directory, tagged the way uv tags its root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55",
                                       encoding="ascii")
    return root


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


def test_entries_in_an_environment_built_for_one_command_are_left_out(tmp_path):
    """`uv run --with crapkit crapkit doctor` puts two environments from uv's
    cache on PATH: the one uv built for the run (builds-v0, this process's
    sys.prefix) and the `--with` layer (archive-v0). The plugin's hooks inherit
    neither. Only the launcher that outlives the command is counted."""
    cache = uv_cache(tmp_path / "uv-cache")
    run, layer = cache / "builds-v0" / ".tmp4THQRy" / BIN, cache / "archive-v0" / "JLLMYkrV" / BIN
    shim(run), shim(layer)
    pipx_run = shim(tmp_path / ".local" / "pipx" / ".cache" / "3c2c1d4e" / BIN).parent
    kept = shim(tmp_path / "tools")

    assert path_launchers(joined(run, layer, pipx_run, tmp_path / "tools")) == [str(kept)]


def test_a_quoted_entry_is_read_without_its_quotes(tmp_path):
    only = shim(tmp_path / "with space")

    assert path_launchers(f'"{tmp_path / "with space"}"') == [str(only)]


# --- environments a runner builds for one command ---------------------------------
#
# uv keeps every environment it builds for one command in a bucket of its cache:
# archive-v0 for uvx and `uv tool run`, builds-v0 for the environment `uv run
# --with` runs in. uv tags the cache root with CACHEDIR.TAG wherever UV_CACHE_DIR
# puts it, so the tag, not a bucket's name, marks the environment. pipx 1.17 on
# its uv backend hands `pipx run crapkit ...` to `uv tool run`, so nothing in
# that environment says pipx started it. It is named by the tool that built it.

@pytest.mark.parametrize("parts, runner", [
    (("custom-uv-cache", "archive-v0", "ePz6wC7F"), "uv"),
    (("custom-uv-cache", "builds-v0", ".tmp4THQRy"), "uv"),
    (("custom-uv-cache", "environments-v2", "crapkit-8a9f0c"), "uv"),
    ((".cache", "pipx", "3c2c1d4e"), "pipx"),
    ((".local", "pipx", ".cache", "3c2c1d4e"), "pipx"),
    (("AppData", "Local", "pipx", "pipx", "Cache", "3c2c1d4e"), "pipx"),
    ((".local", "share", "uv", "tools", "crapkit"), None),
    ((".local", "share", "pipx", "venvs", "crapkit"), None),
    (("repo", ".venv"), None),
])
def test_an_environment_built_for_one_command_is_named_by_the_tool_that_built_it(tmp_path, parts,
                                                                                runner):
    uv_cache(tmp_path / "custom-uv-cache")
    (tmp_path / ".local" / "share" / "uv" / "tools").mkdir(parents=True)

    assert ephemeral_runner(str(tmp_path.joinpath(*parts))) == runner


def test_each_builder_names_the_install_that_stays():
    """A uv-built environment came from uvx, `uv tool run`, or `pipx run` on
    pipx's uv backend, so its line names both installs."""
    assert install_line("uv") == ("`uv tool install crapkit`, or `pipx install crapkit` if you "
                                  "ran doctor through `pipx run`")
    assert install_line("pipx") == "`pipx install crapkit`"


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

    assert upgrade_command(str(launcher), quote) == f"<{python}> -P -m pip install --upgrade crapkit"


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

    assert upgrade_command(str(launcher), quote) == f"<{python}> -P -m pip install --upgrade crapkit"


def test_a_script_s_shebang_names_its_interpreter(tmp_path):
    """pip --user on Linux: ~/.local/bin/crapkit starts /usr/bin/python3."""
    python = tmp_path / "usr" / "bin" / "python3.12"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    launcher = tmp_path / "home" / ".local" / "bin" / "crapkit"
    launcher.parent.mkdir(parents=True)
    launcher.write_text(f"#!{python}\nfrom crapkit.cli import main\n", encoding="utf-8")

    assert upgrade_command(str(launcher), quote) == f"<{python}> -P -m pip install --upgrade crapkit"


def test_a_shebang_naming_a_shell_is_not_the_interpreter(tmp_path):
    """pip writes `#!/bin/sh` and an exec line when the interpreter path holds a
    space; /bin/sh is not what runs pip."""
    launcher = tmp_path / "bin" / "crapkit"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\n'''exec' \"/opt/my env/bin/python\" \"$0\" \"$@\"\n", encoding="utf-8")

    assert upgrade_command(str(launcher), quote) == "python -P -m pip install --upgrade crapkit"


def test_a_launcher_that_cannot_be_read_falls_back_to_plain_pip(tmp_path):
    assert upgrade_command(str(tmp_path / "gone" / "crapkit"), quote) == (
        "python -P -m pip install --upgrade crapkit")


# --- the reinstall that repairs a launcher that cannot answer ----------------------
#
# A launcher whose environment lost its python answers nothing. Each installer's
# upgrade leaves it broken (`pipx upgrade` says to reinstall, `uv tool upgrade`
# and `pip install --upgrade` see crapkit current); these reinstalls brought a
# broken launcher back in the deploy image.

def test_a_uv_tool_launcher_is_reinstalled_with_force(tmp_path):
    launcher = shim(tmp_path / "uv" / "tools" / "crapkit" / "bin")

    assert reinstall_command(str(launcher), quote) == "uv tool install --force crapkit"


def test_a_pipx_launcher_is_reinstalled_by_pipx(tmp_path):
    launcher = shim(tmp_path / "pipx" / "venvs" / "crapkit" / "bin")

    assert reinstall_command(str(launcher), quote) == "pipx reinstall crapkit"


@pytest.mark.parametrize("uv_made", [False, True], ids=["pip-venv", "uv-venv"])
def test_a_venv_launcher_is_reinstalled_through_the_python_beside_it(tmp_path, uv_made):
    scripts = tmp_path / "venv" / BIN
    launcher = shim(scripts)
    python = scripts / ("python.exe" if WINDOWS else "python3")
    python.write_bytes(b"")
    if uv_made:
        (tmp_path / "venv" / "pyvenv.cfg").write_text("uv = 0.9.2\n", encoding="utf-8")

    expected = (f"uv pip install --python <{python}> --force-reinstall crapkit" if uv_made
                else f"<{python}> -P -m pip install --force-reinstall crapkit")
    assert reinstall_command(str(launcher), quote) == expected


def test_a_launcher_with_no_python_found_is_reinstalled_through_python(tmp_path):
    launcher = tmp_path / "bin" / "crapkit"
    launcher.parent.mkdir()
    launcher.write_text("#!/bin/sh\nexit 2\n", encoding="utf-8")

    assert reinstall_command(str(launcher), quote) == "python -P -m pip install --force-reinstall crapkit"
