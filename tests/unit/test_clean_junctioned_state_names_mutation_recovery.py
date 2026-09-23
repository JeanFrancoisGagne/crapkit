"""A repository whose .crapkit is a directory link hears clean refuse over the
feature that uses the link, never over test evidence it has not got."""
import os
from pathlib import Path

from crapkit.cli import main


CONFIG = '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\ncoverage_optional=true\n'


def _link_directory(link: Path, target: Path) -> None:
    """A junction on Windows needs no symlink privilege; elsewhere a symlink."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def test_clean_on_a_linked_state_directory_names_the_mutation_path(tmp_path, capsys):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "crapkit.toml").write_text(CONFIG, encoding="utf-8")
    elsewhere = tmp_path / "state-elsewhere"
    elsewhere.mkdir()
    _link_directory(root / ".crapkit", elsewhere)

    code = main(["clean", "--repo", str(root), "--dry-run", "--json"])

    error = capsys.readouterr().err
    assert "test evidence" not in error, error
    assert f"mutation path {root / '.crapkit'} is a link" in error, error
    assert code == 5
