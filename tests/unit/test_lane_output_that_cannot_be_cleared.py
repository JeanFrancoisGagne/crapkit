"""A declared output crapkit cannot clear fails its lane, not the command.

Before each attempt the runner drops what an earlier attempt left at a declared
path (lane_outputs.Outputs.clear). The unlink had no OSError handling, so a lane
whose artifact names a directory (`artifact = "coverage"`, vitest's report
directory) ended `crapkit coverage` and `verify` in a traceback at exit 1, with
no --json object and no run stored, and the other lanes' measurements went with
it. A retry on Windows did the same while an editor held the previous
attempt's report open. 0.8.0 failed only that lane. doctor passed the config.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cli_inproc_repo import istanbul, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.config import load_config_text
from crapkit.errors import ConfigError, ToolError
from crapkit.lane_outputs import owned


def test_a_directory_at_a_declared_path_is_a_refusal_that_names_it(tmp_path):
    (tmp_path / "coverage").mkdir()
    with owned(tmp_path, "unit", ("coverage",)) as outputs:
        with pytest.raises(ToolError, match=r"coverage is a directory"):
            outputs.clear()
    assert (tmp_path / "coverage").is_dir(), "nothing of the reader's is removed"


def test_a_file_something_holds_open_is_a_refusal_that_says_to_close_it(tmp_path, monkeypatch):
    """Windows refuses the unlink of a file another process holds (WinError 32);
    the retry of a lane whose first attempt wrote its junit hit that."""
    with owned(tmp_path, "unit", ("junit.xml",)) as outputs:
        (tmp_path / "junit.xml").write_text("<testsuites/>", encoding="utf-8")

        def held(self, missing_ok=False):
            raise PermissionError(32, "The process cannot access the file because it is being "
                                      "used by another process")

        monkeypatch.setattr(Path, "unlink", held)
        with pytest.raises(ToolError, match=r"cannot clear junit.xml .*close whatever holds it"):
            outputs.clear()
        monkeypatch.undo()


def _unit_artifact(root: Path, artifact: str) -> None:
    toml = root / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('artifact = "coverage/unit.json"', f'artifact = "{artifact}"'),
                    encoding="utf-8")


def _ui_lane_writes_its_report(root: Path) -> None:
    """Lane 'ui' copies a canned report into place, so it measures this run."""
    istanbul(root, "ui.seed.json", "web/ui.ts", {"render": (1, 3, 2)})
    (root / "copy_ui.py").write_text(
        "import shutil\nshutil.copy('ui.seed.json', 'coverage/ui.json')\n", encoding="utf-8")
    toml = root / "crapkit.toml"
    text = toml.read_text(encoding="utf-8")
    toml.write_text(text.replace('command = "python -c pass"\nartifact = "coverage/ui.json"',
                                 'command = "python copy_ui.py"\nartifact = "coverage/ui.json"'),
                    encoding="utf-8")


def test_coverage_fails_the_lane_whose_artifact_is_a_directory_and_scores_the_rest(repo, capsys):
    seed_artifacts(repo)
    _unit_artifact(repo, "coverage")
    _ui_lane_writes_its_report(repo)

    assert main(["coverage", "--json", "--repo", str(repo)]) == 5

    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "partial"
    assert "coverage is a directory" in payload["lane_failures"]["unit"]


@pytest.mark.parametrize("field, value", [
    ("artifact", ""), ("artifact", "."), ("artifact", "./"), ("artifact", "cov/.."),
    ("results_artifact", "."), ("results_artifact", "cov/.."),
])
def test_a_lane_output_that_names_the_root_is_refused_at_load(field, value):
    lane = {"artifact": "cov.json", field: value}
    text = ('[[scope]]\nname = "py"\npaths = ["pkg"]\nlanguages = ["python"]\n'
            '[[lane]]\nname = "py"\ncommand = "python -c pass"\nparser = "coveragepy"\n'
            'scopes = ["py"]\nfull_suite = false\n'
            + "".join(f'{key} = "{spelled}"\n' for key, spelled in lane.items()))

    with pytest.raises(ConfigError, match=rf"lane 'py': {field} names the directory crapkit.toml"):
        load_config_text(text)


def test_doctor_fails_an_artifact_that_names_a_directory(repo, capsys):
    seed_artifacts(repo)
    _unit_artifact(repo, "coverage")

    assert main(["doctor", "--json", "--repo", str(repo)]) == 1

    failures = json.loads(capsys.readouterr().out)["problems"]
    assert [f for f in failures if "directory" in f] == [
        "lane 'unit': artifact 'coverage' names a directory, and a lane output is one file; "
        "point it at the report file the command writes inside it"]


@pytest.mark.skipif(os.name != "nt", reason="POSIX deletes a file another process holds open")
def test_a_file_held_open_on_windows_is_the_same_refusal(tmp_path):
    with owned(tmp_path, "unit", ("junit.xml",)) as outputs:
        (tmp_path / "junit.xml").write_text("<testsuites/>", encoding="utf-8")
        with open(tmp_path / "junit.xml", encoding="utf-8"):
            with pytest.raises(ToolError, match=r"cannot clear junit.xml .*WinError 32"):
                outputs.clear()
