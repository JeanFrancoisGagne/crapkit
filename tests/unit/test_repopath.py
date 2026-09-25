r"""repopath: one set of rules for a path that did not come from git.

Each reader used to fold one spelling and compare the rest as text. A lane
committed from Windows (`.crapkit\cov.json`) opened no file on Linux, `SRC\a.py`
passed a gate its own file failed, and a report keyed `c:\...` failed a lane
over this very checkout. These tests pin the rules at their own seam; the tests
beside each reader feed the same spellings through that reader.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from crapkit.repopath import (Refused, Reported, declared, disk_spelling, entries,
                              file_separators, fragments, inside, native, on_a_share, typed,
                              typed_path)

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


@pytest.mark.parametrize("raw, expected", [
    (".crapkit\\cov.json", ".crapkit/cov.json"),
    ("web\\src/app.ts", "web/src/app.ts"),
    ("web/src/app.ts", "web/src/app.ts"),
    ("src/pkg/we\\ird.py", "src/pkg/we/ird.py"),
])
def test_a_path_a_file_carries_separates_on_every_os(raw, expected):
    """A crapkit.toml, a coverage report and a JUnit report travel between OSes,
    so one text reads as one path on Windows, Linux and macOS."""
    assert file_separators(raw) == expected


@only_posix
def test_a_posix_tree_holding_a_backslash_name_still_reads_a_separator(tmp_path, monkeypatch):
    r"""git on Linux can track `src/we\ird.py` as one file, and a report written
    on Windows says `src\we\ird.py` for src/we/ird.py. The text cannot tell the
    two apart, so a backslash separates directories on every OS whatever the
    tree holds: such a tracked name is unsupported, and doctor names it. No
    caller can hand in a tree to keep the literal name."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "we\\ird.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert file_separators("src/we\\ird.py") == "src/we/ird.py"
    with pytest.raises(TypeError):
        file_separators("src/we\\ird.py", tmp_path)


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


def test_a_given_lister_answers_every_folder_the_walk_reads(tmp_path):
    root = _tree(tmp_path)
    asked: list[Path] = []

    def listing(folder: Path) -> set[str]:
        asked.append(folder)
        return entries(folder)

    assert disk_spelling(root, "src/pkg/mod.py", listing) == "src/pkg/mod.py"
    assert asked == [root, root / "src", root / "src" / "pkg"]


REPORTED = {
    "relative": (lambda root: "src/pkg/mod.py", "src/pkg/mod.py"),
    "dot-slash": (lambda root: "./src/pkg/mod.py", "src/pkg/mod.py"),
    "backslash": (lambda root: "src\\pkg\\mod.py", "src/pkg/mod.py"),
    "absolute-native": (lambda root: str(root / "src" / "pkg" / "mod.py"), "src/pkg/mod.py"),
    "absolute-forward": (lambda root: (root / "src" / "pkg" / "mod.py").as_posix(),
                         "src/pkg/mod.py"),
    "climbs-out": (lambda root: "../other/mod.py", "../other/mod.py"),
    "dotted-module": (lambda root: "src.pkg.test_mod", "src.pkg.test_mod"),
}


@pytest.mark.parametrize("which", REPORTED)
def test_a_reported_path_reads_as_git_spells_the_file(tmp_path, which):
    """A JUnit file attribute or classname, in each spelling a runner writes."""
    spell, expected = REPORTED[which]
    root = _tree(tmp_path).resolve()

    assert Reported(root)(spell(root)) == expected


def test_a_reported_path_in_another_case_takes_the_listed_case(tmp_path):
    need_case_insensitive(tmp_path)

    assert Reported(_tree(tmp_path))("SRC\\Pkg\\mod.py") == "src/pkg/mod.py"


def test_a_reported_path_elsewhere_comes_back_folded(tmp_path):
    root = _tree(tmp_path / "repo").resolve()
    other = (tmp_path / "other" / "mod.py").resolve()

    assert Reported(root)(str(other)) == str(other).replace("\\", "/")


def test_an_absolute_path_through_a_linked_directory_lands_in_the_checkout(tmp_path):
    root = _tree(tmp_path / "repo")
    link_directory(tmp_path / "alias", root)

    assert inside(tmp_path / "alias" / "src" / "pkg" / "mod.py", root) == "src/pkg/mod.py"


def test_an_absolute_path_elsewhere_lands_nowhere(tmp_path):
    root = _tree(tmp_path / "repo")
    (tmp_path / "other").mkdir()

    assert inside(tmp_path / "other" / "mod.py", root) is None


@pytest.mark.parametrize("raw", ["src/pkg/mod.py", "C:/repo/src/pkg/mod.py"])
def test_a_path_with_no_root_this_os_reads_lands_nowhere(tmp_path, monkeypatch, raw):
    """Standing in the checkout, a relative name resolves into it. On POSIX
    `C:/repo/...` is such a name, and a report written on Windows would have
    been scored as this tree."""
    if os.name == "nt" and raw.startswith("C:"):
        pytest.skip("needs POSIX path rules")
    root = _tree(tmp_path)
    (root / "C:" / "repo" / "src" / "pkg").mkdir(parents=True)
    monkeypatch.chdir(root)

    assert inside(raw, root) is None


def test_placing_places_each_folder_once_and_keeps_the_name_as_written(tmp_path, monkeypatch):
    """A report names thousands of files in a few hundred folders."""
    import crapkit.repopath as repopath

    root = _tree(tmp_path / "repo").resolve()
    asked: list[str] = []
    real = repopath.inside

    def counted(path, top):
        asked.append(str(path))
        return real(path, top)

    monkeypatch.setattr(repopath, "inside", counted)
    placing = repopath.Placing(root)
    names = [placing(str(root / "src" / "pkg" / f"m{i}.py")) for i in range(5)]

    assert names == [f"src/pkg/m{i}.py" for i in range(5)]
    assert len(asked) == 1
    assert placing(str(tmp_path / "other" / "m.py")) is None


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
    assert on_a_share(alias) and not on_a_share(root)


# --- the declared entry --------------------------------------------------------
# What crapkit.toml holds, read by the kind its key's row in config._PATH_KEYS
# names. test_config_path_spellings feeds each key through the loader and the
# reader that consumes it; these rows pin the entry.

def _declared_tree(root: Path) -> Path:
    for folder in ("web/dist", "api", "backend", ".crapkit"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.parametrize("kind, raw, expected", [
    ("file", ".crapkit\\cov.json", ".crapkit/cov.json"),
    ("file", "./.crapkit/cov.json", ".crapkit/cov.json"),
    ("file", ".\\api\\", "api/"),
    ("scope", "web", "web"),
    ("scope", "./web/", "web"),
    ("scope", ".\\web\\", "web"),
    ("scope", "/web", "web"),
    ("scope", ".", "."),
    ("prefix", ".", ""),
    ("prefix", "api\\", "api"),
    ("prefix", "/api/", "api"),
    ("input", ".\\backend", "backend"),
    ("input", "./", "."),
    ("glob", "web/dist/", "web/dist/**"),
    ("glob", "web\\dist\\**", "web/dist/**"),
    ("glob", "/web/dist/**", "web/dist/**"),
    ("glob", "**\\dist\\**", "**/dist/**"),
])
def test_the_declared_entry_reads_a_key_as_its_kind_says(tmp_path, kind, raw, expected):
    assert declared(raw, kind, _declared_tree(tmp_path)) == expected


@pytest.mark.parametrize("kind, raw, says", [
    ("scope", "../web", "can never match a tracked file"),
    ("scope", "src/../web", "can never match a tracked file"),
    ("scope", "C:/web", "can never match a tracked file"),
    ("scope", "", "can never match a tracked file"),
    ("scope", "/elsewhere/web", "names nothing under the root as 'elsewhere/web'"),
    ("input", "../shared", "is not a path inside the root"),
    ("input", "/abs", "is not a path inside the root"),
    ("input", "D:\\x", "is not a path inside the root"),
    ("input", "src/*.ts", "is a glob"),
])
def test_the_declared_entry_refuses_a_value_that_can_never_name_a_tracked_file(tmp_path, kind,
                                                                               raw, says):
    with pytest.raises(Refused, match=re.escape(says)):
        declared(raw, kind, _declared_tree(tmp_path))


def test_a_declared_scope_path_in_this_checkout_is_refused_with_its_relative_spelling(tmp_path):
    """Written from `/`: on POSIX the path itself, on Windows Git Bash's `/c/...`."""
    root = _declared_tree(tmp_path).resolve()
    web = (root / "web").as_posix()
    written = web if web.startswith("/") else "/" + web[0].lower() + web[2:]

    with pytest.raises(Refused, match=re.escape(": write 'web'")):
        declared(written, "scope", root)


@pytest.mark.parametrize("kind", ["scope", "prefix", "input"])
def test_a_declared_directory_takes_the_case_its_directory_lists(tmp_path, kind):
    need_case_insensitive(tmp_path)

    assert declared("BACKEND", kind, _declared_tree(tmp_path)) == "backend"
    assert declared("BACKEND", kind) == "BACKEND"


def test_a_fragment_folds_case_where_the_disk_under_the_root_does(tmp_path):
    from path_spellings import case_insensitive
    from crapkit.repopath import fragments

    root = _tree(tmp_path / "Repo")

    assert [f.folds for f in fragments(["x", "y"], root)] == [case_insensitive(root)] * 2
    assert fragments([], root) == []


# id -> (what the host needs, a typed fragment of src/pkg, whether it is within)
FRAGMENTS = {
    "as-git": ("", "src/pkg", True),
    "dot-slash": ("", "./src/pkg", True),
    "file-part": ("", "pkg/mod", True),
    "backslash": ("windows", "src\\pkg", True),
    "dot-backslash": ("windows", ".\\src\\pkg", True),
    "posix-backslash": ("posix", "src\\pkg", False),
    "dir-case": ("case", "SRC/Pkg", True),
    "case-on-ext4": ("case-sensitive", "SRC/Pkg", False),
    "elsewhere": ("", "lib/pkg", False),
}


# need -> a skip naming it where this host lacks it
NEEDS = {
    "": lambda folder: None,
    "windows": lambda folder: os.name == "nt" or pytest.skip("needs Windows path rules"),
    "posix": lambda folder: os.name != "nt" or pytest.skip("needs POSIX path rules"),
    "case": need_case_insensitive,
    "case-sensitive": need_case_sensitive,
}


@pytest.mark.parametrize("which", FRAGMENTS)
def test_the_fragment_entry_matches_a_typed_piece_of_the_path_git_spells(tmp_path, which):
    need, raw, within = FRAGMENTS[which]
    NEEDS[need](tmp_path)
    [piece] = fragments([raw], _tree(tmp_path))

    assert piece.within("src/pkg/mod.py") is within
    assert piece.typed == raw


# --- the typed entry ----------------------------------------------------------
# A path a person or agent typed: a CLI or MCP file argument, `--repo`, the
# working directory, a hook payload's `file_path` or `cwd`. The commands feed
# these spellings through their own flags (test_cli_path_spellings,
# test_claude_hook_path_spellings, test_root_spellings); these rows pin the
# entry they all call.

from path_spellings import SPELLINGS, spelled  # noqa: E402


def _app_tree(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text("export const a = 1;\n", encoding="utf-8")
    return root


@pytest.mark.parametrize("which", SPELLINGS)
def test_the_typed_entry_reads_a_file_argument_as_git_spells_the_file(tmp_path, which):
    root = _app_tree(tmp_path / "repo")

    assert typed(spelled(which, root), root) == "src/app.ts"


@pytest.mark.parametrize("raw, stand, expected", [
    ("app.ts", "src", "src/app.ts"),
    ("./app.ts", "src", "src/app.ts"),
    ("../src/app.ts", "src", "src/app.ts"),
    ("src/app.ts", ".", "src/app.ts"),
    ("src/app.ts", "..", "src/app.ts"),
    ("../../elsewhere.ts", "src", None),
], ids=["below", "below-dot", "below-climbs-back", "at-root", "above-root", "climbs-out"])
def test_the_typed_entry_reads_a_relative_argument_from_where_the_user_stands(tmp_path, raw,
                                                                             stand, expected):
    """ADR 0002: below the root a relative argument is read from where the user
    stands; at the root or above it, it is root-relative."""
    root = _app_tree(tmp_path / "repo").resolve()

    assert typed(raw, root, root / stand) == expected


def test_the_typed_entry_names_nothing_for_a_file_outside_the_root(tmp_path):
    root = _app_tree(tmp_path / "repo")
    (tmp_path / "other.ts").write_text("", encoding="utf-8")

    assert typed(str(tmp_path / "other.ts"), root) is None
    assert typed("/other.ts", root) is None


@only_posix
def test_the_typed_entry_keeps_a_posix_backslash_as_a_filename_character(tmp_path):
    r"""A shell on POSIX hands over the name it was given: `src\app.ts` is one
    file there, not src/app.ts."""
    assert typed("src\\app.ts", _app_tree(tmp_path / "repo")) == "src\\app.ts"


def _msys(root: Path, prefix: str) -> str:
    return prefix + root.drive[0].lower() + root.as_posix()[2:]


# id -> (what the host needs, a typed spelling of the directory `root`)
PLACES = {
    "native": ("", lambda root: str(root)),
    "forward": ("", lambda root: root.as_posix()),
    "msys": ("windows", lambda root: _msys(root, "/")),
    "wsl": ("windows", lambda root: _msys(root, "/mnt/")),
    "extended-length": ("windows", lambda root: "\\\\?\\" + str(root)),
    "admin-share": ("windows", admin_share),
    "lower-drive": ("windows", lower_drive),
}


@pytest.mark.parametrize("which", PLACES)
def test_the_typed_entry_opens_a_typed_place_where_this_os_opens_it(tmp_path, which):
    """`--repo`, the working directory and a hook payload's `cwd`: each is a
    place a lane starts in, so a share alias of a local drive comes back to the
    drive, and a relative `file_path` is read against the typed `cwd`."""
    need, spell = PLACES[which]
    if need == "windows" and os.name != "nt":
        pytest.skip("needs Windows path rules")
    root = _app_tree(tmp_path / "repo").resolve()

    place = typed_path(spell(root))

    assert place.resolve() == root and not on_a_share(place)
    assert typed_path("src/app.ts", spell(root)).resolve() == root / "src" / "app.ts"
    assert typed_path(str(root / "src" / "app.ts"), "elsewhere") == root / "src" / "app.ts"


@pytest.mark.parametrize("path, windows, shared", [
    ("\\\\server\\share\\repo", True, True),
    ("//server/share/repo", True, True),
    ("C:\\repo", True, False),
    ("\\\\server\\share\\repo", False, False),
], ids=["unc", "unc-forward", "drive", "posix"])
def test_a_network_path_is_a_share_on_windows_alone(path, windows, shared):
    """cmd.exe cannot start a command in a UNC directory; sh starts one anywhere."""
    assert on_a_share(path, windows=windows) is shared

