"""A command asked to start in a directory that is not there never starts, and
says where it was asked to start.

Popen raised FileNotFoundError on POSIX and NotADirectoryError on Windows. Both
are OSErrors and neither is crapkit's own error, so the lane layer, which fails
one lane on a ToolError, let `crapkit coverage` die with a traceback. The start
failure is both: a ToolError to the lane layer, and still an OSError to the
doctor and init probes, which read one as a question that could not be put.
"""
from __future__ import annotations

import pytest

from crapkit.errors import ToolError
from crapkit.procs import CwdMissing, run_bounded, run_owned


@pytest.mark.parametrize("where", ["nope", "a/b", "file.txt"])
def test_run_bounded_names_the_cwd_that_is_not_a_directory(tmp_path, where):
    (tmp_path / "file.txt").write_text("", encoding="utf-8")
    cwd = tmp_path / where

    with pytest.raises(ToolError) as failed:
        run_bounded("echo never", 30, cwd=cwd)

    assert str(failed.value) == f"cwd {cwd} is not a directory, so the command never ran"
    assert isinstance(failed.value, OSError), "the probes read an OSError as no answer"
    assert isinstance(failed.value, CwdMissing), "the lane layer adds the lane's own fix"


def test_run_owned_refuses_the_same_way(tmp_path):
    with pytest.raises(ToolError, match="is not a directory, so the command never ran"):
        run_owned("echo never", 30, cwd=tmp_path / "nope")


def test_a_cwd_that_exists_still_runs(tmp_path):
    assert run_bounded("echo ran", 30, cwd=tmp_path) == 0
    assert run_bounded("echo ran", 30) == 0, "no cwd is the caller's own directory"
