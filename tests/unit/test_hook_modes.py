"""A committed hook that is not executable in the index arms nothing on Unix.

Git silently skips a hook file whose mode is 100644, so `git config
core.hooksPath githooks` on Linux or macOS installs a gate that never runs. The
author on Windows sees it pass, because Windows ignores the bit. crapkit's own
repo shipped that way, and README's Route 2 tells adopters to do the same thing.
"""
from crapkit.doctor import non_executable_hooks


def test_a_hook_committed_without_the_bit_is_reported():
    modes = {"githooks/pre-commit": "100644"}

    assert non_executable_hooks(modes) == ("githooks/pre-commit",)


def test_an_executable_hook_is_clean():
    assert non_executable_hooks({"githooks/pre-commit": "100755"}) == ()


def test_every_offender_is_named_in_path_order():
    modes = {"h/pre-push": "100644", "h/pre-commit": "100644", "h/commit-msg": "100755"}

    assert non_executable_hooks(modes) == ("h/pre-commit", "h/pre-push")


def test_a_symlink_or_a_submodule_entry_is_not_a_missing_bit():
    """120000 is a symlink and 160000 a gitlink. Neither is a hook whose mode
    `git update-index --chmod=+x` would fix, and calling them broken sends the
    reader after a bit that is not the problem."""
    modes = {"h/pre-commit": "120000", "h/sub": "160000"}

    assert non_executable_hooks(modes) == ()


def test_no_hooks_path_means_nothing_to_report():
    assert non_executable_hooks({}) == ()
