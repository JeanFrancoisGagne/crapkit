"""doctor names a marks file whose merge driver this clone never defined.

docs/ratchet.md installs the driver in two steps: a committed .gitattributes
line, and a `git config` line every clone runs, because git takes no driver
command from a committed file. A clone that skipped the second step merges
crapkit-ratchet.tsv as text: git finds no driver by that name and falls back
without a word, and the conflict lands in the one file the docs say never to
resolve by hand. doctor said nothing. WARN, never FAIL: nothing has merged yet.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit.cli import admin
from crapkit.doctor import undefined_merge_driver

DRIVER = "crapkit ratchet merge %O %A %B"


def test_a_driver_the_clone_never_defined_is_one_warn_naming_the_config_line():
    (finding,) = undefined_merge_driver("crapkit-ratchet.tsv", "crapkit-ratchet", "")

    assert finding.level == "WARN"
    assert finding.text == (
        "crapkit-ratchet.tsv has merge=crapkit-ratchet in its git attributes, but "
        "merge.crapkit-ratchet.driver is not set in this clone, so git merges the marks file "
        "as text and leaves its conflicts to be resolved by hand; run `git config "
        "merge.crapkit-ratchet.driver \"crapkit ratchet merge %O %A %B\"` "
        "(docs/ratchet.md#the-git-merge-driver)")


@pytest.mark.parametrize("driver, command", [
    ("crapkit-ratchet", DRIVER),
    ("unspecified", ""),
    ("unset", ""),
    ("set", ""),
    ("text", ""),
    ("binary", ""),
    ("union", ""),
], ids=["defined", "no-attribute", "unset", "set", "text", "binary", "union"])
def test_a_defined_driver_no_attribute_or_a_built_in_one_says_nothing(driver, command):
    assert undefined_merge_driver("crapkit-ratchet.tsv", driver, command) == ()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A repo whose committed .gitattributes routes the marks file to the
    driver, and a git config this test owns."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / ".gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "clone"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitattributes").write_text("crapkit-ratchet.tsv merge=crapkit-ratchet\n", encoding="utf-8")
    return root


CFG = SimpleNamespace(ratchet_file="crapkit-ratchet.tsv")


def test_the_attribute_without_the_config_line_warns_and_the_named_line_clears_it(clone):
    (finding,) = admin._doctor_merge_driver(clone, CFG)
    assert "merge.crapkit-ratchet.driver is not set in this clone" in finding.text

    _git(clone, "config", "merge.crapkit-ratchet.driver", DRIVER)
    assert admin._doctor_merge_driver(clone, CFG) == []


def test_a_driver_set_globally_counts(clone):
    _git(clone, "config", "--global", "merge.crapkit-ratchet.driver", DRIVER)

    assert admin._doctor_merge_driver(clone, CFG) == []


def test_a_renamed_marks_file_is_the_one_asked_about(clone):
    (clone / ".gitattributes").write_text("debt/marks.tsv merge=crapkit-ratchet\n", encoding="utf-8")

    assert admin._doctor_merge_driver(clone, CFG) == []
    (finding,) = admin._doctor_merge_driver(clone, SimpleNamespace(ratchet_file="debt/marks.tsv"))
    assert finding.text.startswith("debt/marks.tsv has merge=crapkit-ratchet")


def test_a_crapkit_root_below_the_git_top_reads_the_attribute_from_its_own_directory(clone):
    (clone / "packages" / "api").mkdir(parents=True)
    (clone / "packages" / "api" / ".gitattributes").write_text(
        "crapkit-ratchet.tsv merge=crapkit-ratchet\n", encoding="utf-8")
    (clone / ".gitattributes").unlink()

    (finding,) = admin._doctor_merge_driver(clone / "packages" / "api", CFG)
    assert finding.text.startswith("crapkit-ratchet.tsv has merge=crapkit-ratchet")


def test_no_attribute_at_all_says_nothing(clone):
    (clone / ".gitattributes").unlink()

    assert admin._doctor_merge_driver(clone, CFG) == []


def test_outside_a_repository_nothing_is_read(tmp_path):
    assert admin._doctor_merge_driver(tmp_path, CFG) == []
