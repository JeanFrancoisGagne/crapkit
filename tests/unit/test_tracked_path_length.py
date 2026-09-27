"""No tracked path is longer than 110 characters.

A plugin marketplace add clones this repository, and Git for Windows leaves
core.longpaths off, so a checkout that writes a path of 260 characters or more
fails with "Filename too long". Claude Code clones under
<CLAUDE_CONFIG_DIR>\\plugins\\marketplaces\\ plus a temporary directory name,
so the clone fits while CLAUDE_CONFIG_DIR and the longest tracked path add up
to 204 characters or fewer. A 139-character evidence path failed the add for
every CLAUDE_CONFIG_DIR of 66 characters or more (65 installed), which the
default ~\\.claude reaches under a user profile path of 58, and against
github.com Claude Code then reported only an SSH error. At 110 characters a
CLAUDE_CONFIG_DIR of 94 still fits.
"""
from pathlib import Path

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
LONGEST = 110


def _tracked() -> list[str]:
    listed = hang_guard.run(["git", "ls-files", "-z"], cwd=ROOT)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    return [path.decode("utf-8") for path in listed.stdout.split(b"\0") if path]


def test_no_tracked_path_is_longer_than_a_windows_clone_can_write():
    paths = _tracked()
    over = sorted((len(path), path) for path in paths if len(path) > LONGEST)

    assert paths, "git ls-files listed nothing, so the cap checked nothing"
    assert over == [], f"{len(over)} tracked path(s) over {LONGEST} characters, longest first: " \
                       f"{sorted(over, reverse=True)[:3]}"
