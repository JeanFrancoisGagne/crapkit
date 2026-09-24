r"""repopath: one set of rules for a path that did not come from git.

Each reader used to fold one spelling and compare the rest as text. A lane
committed from Windows (`.crapkit\cov.json`) opened no file on Linux, `SRC\a.py`
passed a gate its own file failed, and a report keyed `c:\...` failed a lane
over this very checkout. These tests pin the rules at their own seam; the tests
beside each reader feed the same spellings through that reader.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from crapkit.repopath import disk_spelling, file_separators, inside, is_unc, native

from path_spellings import (admin_share, link_directory, lower_drive, need_case_insensitive,
                            need_case_sensitive, only_posix, only_windows)


@pytest.mark.parametrize("raw, drive", [
    ("/c/Users/dev/repo", "C:/Users/dev/repo"),
    ("/mnt/c/Users/dev/repo", "C:/Users/dev/repo"),
    ("/D/work", "D:/work"),
    ("\\\\?\\C:\\Users\\dev\\repo", "C:\\Users\\dev\\repo"),
    ("\\\\?\\UNC\\server\\share\\repo", "\\\\server\\share\\repo"),
    ("\\\\?\\UNC\\localhost\\C$\\repo", "C:\\repo"),
    ("\\\\localhost\\c$\\repo", "C:\\repo"),
    ("//127.0.0.1/C$/repo", "C:/repo"),
])
def test_windows_reads_a_shell_or_share_spelling_as_its_drive(raw, drive):
    r"""Git Bash hands a script `/c/...`, WSL `/mnt/c/...`, Python's resolve()
    can hand back `\\?\C:\...`, and the admin share names the same disk. cmd.exe
    cannot start a lane in any UNC directory, so these come back to the drive."""
    assert native(raw, windows=True) == drive


@pytest.mark.parametrize("raw", ["src/a.py", "C:\\repo", "/mnt/data/x", "\\\\server\\share\\repo",
                                 "/cache/x"])
def test_windows_leaves_every_other_spelling_alone(raw):
    assert native(raw, windows=True) == raw


@pytest.mark.parametrize("raw", ["/c/Users/dev/repo", "/mnt/c/x", "\\\\?\\C:\\x"])
def test_posix_reads_every_path_as_given(raw):
    assert native(raw, windows=False) == raw


@pytest.mark.parametrize("raw, windows, expected", [
    (".crapkit\\cov.json", True, ".crapkit/cov.json"),
    (".crapkit\\cov.json", False, ".crapkit/cov.json"),
    ("web\\src/app.ts", False, "web/src/app.ts"),
    ("web/src/app.ts", False, "web/src/app.ts"),
])
def test_a_path_a_file_carries_separates_on_every_os(raw, windows, expected):
    """A crapkit.toml, a coverage report and a JUnit report travel between OSes."""
    assert file_separators(raw, None, windows=windows) == expected


@only_posix
def test_a_posix_tree_holding_the_literal_name_keeps_the_backslash(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "we\\ird.py").write_text("x = 1\n", encoding="utf-8")

    assert file_separators("src/we\\ird.py", tmp_path) == "src/we\\ird.py"
    assert file_separators("src\\other.py", tmp_path) == "src/other.py"


def _tree(root: Path) -> Path:
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("typed", ["SRC/pkg/mod.py", "src/PKG/mod.py", "src/pkg/MOD.PY",
                                   "Src/Pkg/Mod.py"])
def test_a_case_insensitive_disk_hands_back_the_listed_case(tmp_path, typed):
    need_case_insensitive(tmp_path)

    assert disk_spelling(_tree(tmp_path), typed) == "src/pkg/mod.py"


@pytest.mark.parametrize("typed", ["SRC/pkg/mod.py", "src/pkg/MOD.PY"])
def test_a_case_sensitive_disk_keeps_the_case_given(tmp_path, typed):
    """On ext4 `SRC` is another directory, which does not exist, and folding it
    onto `src` would name a file the runner could never open."""
    need_case_sensitive(tmp_path)

    assert disk_spelling(_tree(tmp_path), typed) == typed


def test_a_path_naming_nothing_keeps_its_unmatched_tail(tmp_path):
    need_case_insensitive(tmp_path)

    assert disk_spelling(_tree(tmp_path), "SRC/new/Thing.py") == "src/new/Thing.py"


def test_an_absolute_path_through_a_linked_directory_lands_in_the_checkout(tmp_path):
    root = _tree(tmp_path / "repo")
    link_directory(tmp_path / "alias", root)

    assert inside(tmp_path / "alias" / "src" / "pkg" / "mod.py", root) == "src/pkg/mod.py"


def test_an_absolute_path_elsewhere_lands_nowhere(tmp_path):
    root = _tree(tmp_path / "repo")
    (tmp_path / "other").mkdir()

    assert inside(tmp_path / "other" / "mod.py", root) is None


@only_windows
@pytest.mark.parametrize("spell", [lower_drive, lambda p: str(p.resolve()).upper()])
def test_windows_places_a_root_spelled_in_another_case(tmp_path, spell):
    root = _tree(tmp_path / "repo")

    assert inside(spell(root) + "\\src\\pkg\\mod.py", root) == "src/pkg/mod.py"


@only_windows
def test_windows_places_the_checkout_reached_through_its_admin_share(tmp_path):
    root = _tree(tmp_path / "repo")
    alias = admin_share(root)

    assert inside(alias + "\\src\\pkg\\mod.py", root) == "src/pkg/mod.py"
    assert is_unc(alias) and not is_unc(root)


def test_folds_case_says_what_the_disk_under_the_root_does(tmp_path):
    from path_spellings import case_insensitive
    from crapkit.repopath import folds_case

    root = _tree(tmp_path / "Repo")

    assert folds_case(root) is case_insensitive(root)
