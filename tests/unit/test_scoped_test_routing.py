"""Routing a file to its scope's isolated test command.

`{files}` used to be mandatory, which left the ordinary Python layout with no
working route at all: a top-level tests/ directory belongs to no scope, so a
test file is ambiguous once two scopes declare templates, and a source file gets
substituted into pytest's collection list, where it collects nothing (exit 5).
"""
import pytest

from crapkit.cli import _group_files_by_scope, _scoped_command
from crapkit.errors import ConfigError


def test_a_template_with_files_still_substitutes_the_quoted_list():
    cmd = _scoped_command("python -m pytest {files} -q", ["util/a.py", "util/b.py"])

    assert cmd == 'python -m pytest "util/a.py" "util/b.py" -q'


def test_a_template_without_files_runs_verbatim_as_the_scopes_whole_suite():
    cmd = _scoped_command("python -m pytest tests/util -q", ["util/stats.py"])

    assert cmd == "python -m pytest tests/util -q", \
        "the coarse escape: the scope's own suite, whatever file was named"


def test_the_multi_scope_routing_error_names_a_recipe_that_works():
    with pytest.raises(ConfigError) as err:
        _group_files_by_scope(["tests/test_curve.py"],
                              {"calc": ("calc",), "util": ("util",)},
                              {"calc": "a {files}", "util": "b {files}"})

    message = str(err.value)
    assert "calc" in message and "util" in message
    assert "{files}" in message, "the escape is a template with no {files} placeholder"
    assert "paths" in message, "the other route is test files under a scope path"
