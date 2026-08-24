"""crapkit's own crapkit.toml is the first consumer of everything it ships.

Two things it has to get right before asking any other repo to: a scoped test
command for the scope its lane measures, and the traps it learned written where
a payload can read them instead of in comments the parser throws away.
"""
from pathlib import Path

from crapkit.config import load_config_text
from crapkit.doctor import scoped_test_gaps

ROOT = Path(__file__).resolve().parent.parent.parent


def _own_config():
    return load_config_text((ROOT / "crapkit.toml").read_text(encoding="utf-8"))


def test_every_laned_scope_has_a_scoped_test_command():
    cfg = _own_config()

    assert scoped_test_gaps(cfg.lanes, cfg.scoped_tests) == ()


def test_the_src_template_runs_a_suite_not_the_named_file():
    """A {files} template would hand pytest src/crapkit/store.py, which holds no
    tests, and pytest exits 5 on an empty collection."""
    template = dict(_own_config().scoped_tests)["src"]

    assert "{files}" not in template
    assert "pytest" in template


def test_the_repo_writes_its_traps_down_as_notes():
    assert _own_config().notes


def test_the_src_scope_writes_its_own():
    assert _own_config().scope_notes["src"]
