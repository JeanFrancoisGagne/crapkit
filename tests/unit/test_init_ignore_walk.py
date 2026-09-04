"""init's ignore walk at its own seam: where git stops reading .gitignore files.

The four shapes the walk distinguishes, in one process each, so crapkit's own
coverage lane measures every branch (the e2e cases run the CLI in a child and
are not measured)."""
from pathlib import Path

from crapkit.cli import admin

IGNORE = ".crapkit/" + chr(10)


def test_a_root_that_is_a_repository_top_reads_no_ancestor(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(IGNORE, encoding="utf-8")
    root = tmp_path / "lib"
    (root / ".git").mkdir(parents=True)

    assert admin._store_ignored_above(root) is False


def test_an_ancestor_that_ignores_the_store_answers_true(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(IGNORE, encoding="utf-8")
    root = tmp_path / "web"
    root.mkdir()

    assert admin._store_ignored_above(root) is True


def test_the_walk_stops_at_the_first_repository_top_above(tmp_path: Path):
    (tmp_path / ".gitignore").write_text(IGNORE, encoding="utf-8")
    top = tmp_path / "mono"
    (top / ".git").mkdir(parents=True)
    root = top / "web"
    root.mkdir()

    assert admin._store_ignored_above(root) is False


def test_no_ancestor_ignores_it(tmp_path: Path):
    root = tmp_path / "a" / "b"
    root.mkdir(parents=True)

    assert admin._store_ignored_above(root) is False
