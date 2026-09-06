"""Routing a file to its scope's isolated test command.

`{files}` used to be mandatory, which left the ordinary Python layout with no
working route at all: a top-level tests/ directory belongs to no scope, so a
test file is ambiguous once two scopes declare templates, and a source file gets
substituted into pytest's collection list, where it collects nothing (exit 5).
"""
import pytest

from crapkit.cli.verifying import _group_files_by_scope
from crapkit.errors import ConfigError






def test_the_multi_scope_routing_error_names_a_recipe_that_works():
    with pytest.raises(ConfigError) as err:
        _group_files_by_scope(["tests/test_curve.py"],
                              {"calc": ("calc",), "util": ("util",)},
                              {"calc": "a {files}", "util": "b {files}"})

    message = str(err.value)
    assert "calc" in message and "util" in message
    assert "{files}" in message, "the escape is a template with no {files} placeholder"
    assert "paths" in message, "the other route is test files under a scope path"
