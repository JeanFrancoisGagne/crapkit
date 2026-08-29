"""Defects the second cold-start onboarding run found, pinned before fixing.

Each test failed against 2d980ea for the reason the audit recorded; together
they keep the onboarding path from regressing to guesswork.
"""
from crapkit.lanes import Lane, _raise_no_artifact
from crapkit.scaffold import DEFAULT_EXCLUDES, LaneSpec, _template_lines, gitignore_entries
from crapkit.errors import ConfigError, ToolError

import pytest


def _lane(**kw):
    base = dict(name="py", command="python -m pytest --cov", artifact="coverage-py.json",
                parser="coveragepy", scopes=("calc",))
    base.update(kw)
    return Lane(**base)


def test_default_excludes_cover_runner_config_files_at_any_depth():
    """The vitest recipe the docs hand out creates vitest.config.ts, which made
    doctor FAIL as an unclaimed file. Globs are whole-path, so the root form and
    the nested form are both needed."""
    for ext in ("ts", "js", "mts"):
        assert f"*.config.{ext}" in DEFAULT_EXCLUDES
        assert f"**/*.config.{ext}" in DEFAULT_EXCLUDES


def test_coveragepy_lane_ignores_the_pytest_droppings():
    entries = gitignore_entries((LaneSpec(name="py", command="python -m pytest --cov",
                                          artifact="coverage-py.json", parser="coveragepy",
                                          languages=("python",)),))
    assert ".coverage" in entries
    assert "__pycache__/" in entries


def test_lane_templates_carry_a_placeholder_scope_not_a_real_one():
    """init wrote the python project's real scope into the ISTANBUL template,
    pointing a TS lane at python sources."""
    lines = "\n".join(_template_lines(set(), {"calc": ("python",)}))
    assert '"<your-scope>"' in lines
    assert '"calc"' not in lines


def test_a_repo_with_no_coverable_language_gets_no_lane_template_at_all():
    """Neither parser reads Go, so both templates would tell a Go repo to
    declare a lane before running coverage — which is the step it has to skip."""
    assert _template_lines(set(), {"cmd": ("go",)}) == []


def test_missing_pytest_cov_failure_names_the_package():
    with pytest.raises(ToolError) as err:
        _raise_no_artifact(_lane(), _FakeLog("ERROR: usage: python -m pytest\n"
                                             "python -m pytest: error: unrecognized "
                                             "arguments: --cov --cov-branch"), 4)
    assert "pip install pytest-cov" in str(err.value)


class _FakeLog:
    """Stands in for the lane log path: read_text is all _log_tail needs."""

    def __init__(self, text: str):
        self._text = text

    def is_file(self) -> bool:
        return True

    def read_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self._text


def test_stale_artifact_note_names_uncommitted_edits_and_the_remedy():
    from crapkit import uncovered
    import inspect

    src = inspect.getsource(uncovered._artifact_state)
    assert "uncommitted" in src, "the note must name the real cause"
    assert "commit" in src


def test_test_files_route_to_the_only_templated_scope():
    from crapkit.cli import _group_files_by_scope

    grouped = _group_files_by_scope(["tests/test_grade.py"],
                                    {"calc": ("calc",)},
                                    {"calc": "python -m pytest {files} -q"})
    assert grouped == {"calc": ["tests/test_grade.py"]}


def test_test_files_with_several_templates_error_naming_them():
    from crapkit.cli import _group_files_by_scope

    with pytest.raises(ConfigError) as err:
        _group_files_by_scope(["tests/test_grade.py"],
                              {"calc": ("calc",), "web": ("web",)},
                              {"calc": "a {files}", "web": "b {files}"})
    assert "calc" in str(err.value) and "web" in str(err.value)


def test_every_json_flagged_mcp_tool_has_a_real_json_flag():
    """The next_item tool shipped appending --json to a command with no such
    flag. Parity between the registry and argparse keeps that class dead."""
    import argparse
    from crapkit.cli import build_parser
    from crapkit.mcp_server import TOOLS

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for tool in TOOLS:
        if not tool["json_flag"]:
            continue
        cmd_parser = sub.choices[tool["argv"][0]]
        flags = {s for a in cmd_parser._actions for s in a.option_strings}
        assert "--json" in flags, f"tool {tool['name']} appends --json but {tool['argv'][0]} lacks it"
