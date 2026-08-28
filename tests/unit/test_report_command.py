"""`report` as a subcommand: the parser entry, the handler it resolves to, and
the path it writes.

The dispatch half is already covered generically by test_cli_lazy_families, which
parametrizes over the parser's own choices. What is pinned here is the part that
generic test cannot see: the flags the command takes, the default output path,
and that the page it writes is the renderer's output rather than a second one.
"""
import argparse
from pathlib import Path

import pytest

from crapkit.cli import build_parser
from crapkit.errors import ConfigError


def _subparsers() -> dict:
    subs = [a for a in build_parser()._actions if isinstance(a, argparse._SubParsersAction)]
    return dict(subs[0].choices)


def _flags(name: str) -> set[str]:
    return {opt for action in _subparsers()[name]._actions for opt in action.option_strings}


def test_the_parser_defines_report():
    assert "report" in _subparsers()


def test_report_takes_repo_and_out():
    assert {"--repo", "--out"} <= _flags("report")


def test_report_defaults_to_the_page_the_docs_name():
    args = build_parser().parse_args(["report"])
    assert args.out == ".crapkit/report.html"
    assert args.repo == "."


def test_report_resolves_its_handler_from_the_reports_family():
    from crapkit.cli import _OWNER

    assert _OWNER["cmd_report"] == "reports"


def test_the_handler_is_importable_by_name():
    from crapkit.cli import cmd_report

    assert callable(cmd_report)


# --- the writer --------------------------------------------------------------

def test_the_writer_puts_the_rendered_page_on_disk_and_prints_where(tmp_path, capsys):
    from crapkit.cli.reports import _write_report

    out = _write_report(tmp_path, ".crapkit/report.html", "<!DOCTYPE html>\n<html></html>\n")

    assert out == tmp_path / ".crapkit" / "report.html"
    assert out.read_text(encoding="utf-8") == "<!DOCTYPE html>\n<html></html>\n"
    assert capsys.readouterr().out.strip() == str(out)


def test_the_writer_creates_the_directory_it_writes_into(tmp_path):
    from crapkit.cli.reports import _write_report

    out = _write_report(tmp_path, "build/pages/debt.html", "<html></html>")

    assert out.is_file()


def test_the_page_is_written_with_lf_on_every_host(tmp_path):
    """The page is an artifact people diff and publish; it must not pick up the
    host's line separator the way a bare write does on Windows."""
    from crapkit.cli.reports import _write_report

    out = _write_report(tmp_path, "r.html", "<html>\n<body>\n</body>\n</html>\n")

    assert b"\r\n" not in out.read_bytes()


def test_the_writer_refuses_a_path_outside_the_repo(tmp_path):
    """`--out` is repo-relative like `--export` and `--sarif`. An absolute path
    or a climb out of the tree writes somewhere nobody asked for."""
    from crapkit.cli.reports import _write_report

    with pytest.raises(ConfigError):
        _write_report(tmp_path, "../escaped.html", "<html></html>")


def test_a_second_run_overwrites_rather_than_appends(tmp_path):
    from crapkit.cli.reports import _write_report

    _write_report(tmp_path, "r.html", "<html>first</html>")
    out = _write_report(tmp_path, "r.html", "<html>second</html>")

    assert out.read_text(encoding="utf-8") == "<html>second</html>"


# --- the lane states the banner reads ----------------------------------------
#
# `load_uncovered` already joins these into one note and throws the per-lane
# detail away. The banner needs the list, so the list is what the module answers
# with and the joined note is built from it.

def _config(*lanes: tuple[str, str]) -> object:
    from crapkit.config import load_config_text

    scope = '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n\n'
    blocks = "".join(f'[[lane]]\nname = "{name}"\ncommand = "x"\nartifact = "{artifact}"\n'
                     f'parser = "coveragepy"\nscopes = ["src"]\n\n'
                     for name, artifact in lanes)
    return load_config_text(scope + blocks)


class _CleanGit:
    """A tree where nothing moved since the stamp: the fresh-artifact case."""

    def is_ancestor(self, commit: str) -> bool:
        return True

    def diff_names_since(self, commit: str) -> list[str]:
        return []

    def status_names(self) -> list[str]:
        return []


def test_lane_states_names_every_declared_lane(tmp_path):
    """The banner is only as good as the list behind it: a lane missing here is
    a lane whose staleness the page never mentions."""
    from crapkit.uncovered import lane_states

    states = lane_states(tmp_path, _config(("a", "a.json"), ("b", "b.json")), _CleanGit())

    assert [name for name, _ in states] == ["a", "b"]
    assert all("no artifact" in note for _, note in states), "neither artifact exists"


def test_lane_states_reports_no_note_for_an_artifact_that_still_describes_the_tree(tmp_path):
    from crapkit.lanes import write_stamps
    from crapkit.uncovered import lane_states

    (tmp_path / "cov.json").write_text("{}", encoding="utf-8")
    write_stamps(tmp_path, {"cov.json": {"commit": "abc123", "lane": "a", "seconds": 1.0}})

    assert lane_states(tmp_path, _config(("a", "cov.json")), _CleanGit()) == [("a", "")]


def test_the_joined_note_load_uncovered_reports_is_built_from_the_same_states(tmp_path):
    """One source of truth: a lane the list calls fresh cannot show up in the
    note that blacks out every line number repo-wide."""
    from crapkit.uncovered import _staleness_note, lane_states

    cfg = _config(("a", "a.json"), ("b", "b.json"))

    note = _staleness_note(tmp_path, cfg, _CleanGit())

    assert note == "; ".join(n for _, n in lane_states(tmp_path, cfg, _CleanGit()) if n)
    assert "lane 'a'" in note and "lane 'b'" in note
