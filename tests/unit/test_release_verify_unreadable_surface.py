"""`release.py verify` prints a row for a surface it cannot read, never a traceback.

Two readers let an exception escape `main`, which catches only ReleaseError: the
GitHub release row ran `gh` with no guard, so a shell without gh died with
FileNotFoundError, and the PyPI row parsed whatever PyPI answered, so an HTML
error page died with JSONDecodeError and a JSON body without `info` with KeyError.
A gh that ran and failed read as `none` whatever its exit code, so a logged-out gh
claimed the release did not exist.
"""
import json
import subprocess
from pathlib import Path

import pytest

from test_release_guards import git, repo
from test_release_tool import release

VERSION = "0.5.2"


def _tagged(tmp_path):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", "v" + VERSION)
    return root, git(root, "rev-parse", "HEAD")


def _surfaces(commit, pypi):
    """Every live surface answering for the release, PyPI with `pypi`."""
    entry = {"server": {"name": release.REGISTRY_NAME, "version": VERSION,
                        "repository": {"url": release.REGISTRY_REPOSITORY},
                        "packages": [{"registryType": "pypi", "identifier": "crapkit", "version": VERSION}]},
             "_meta": {release.REGISTRY_META: {"isLatest": True}}}
    answers = {"https://pypi.org/": pypi,
               release.REGISTRY_SEARCH: json.dumps({"servers": [entry], "metadata": {}}),
               release.PAGES_LATEST: json.dumps({"status": "built", "commit": commit})}
    listing = "uses: JeanFrancoisGagne/crapkit@v" + VERSION  # the server listing's README pin
    return lambda url: next((body for prefix, body in answers.items() if url.startswith(prefix)), listing)


def _gh(monkeypatch, answer):
    """`gh` answers with `answer(argv)`; every other command runs for real."""
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if Path(argv[0]).stem.lower() == "gh":
            return answer(argv)
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", run)


def _missing(argv):
    raise FileNotFoundError(2, "The system cannot find the file specified")


def _released(argv):
    return subprocess.CompletedProcess(argv, 0, stdout=f"https://github.com/r/releases/tag/v{VERSION}\n",
                                       stderr="")


def _logged_out(argv):
    """What gh 2.85.0 printed with no login and no GH_TOKEN: exit 4."""
    return subprocess.CompletedProcess(argv, 4, stdout="", stderr=(
        "To get started with GitHub CLI, please run:  gh auth login\n"
        "Alternatively, populate the GH_TOKEN environment variable with a GitHub API "
        "authentication token.\n"))


def _silent(argv):
    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")


def _absent(argv):
    """What gh 2.85.0 printed for a tag with no release: exit 1."""
    return subprocess.CompletedProcess(argv, 1, stdout="", stderr="release not found\n")


def _github_line(root, capsys):
    """The one MISMATCH line `verify` prints, which must be the GitHub release row."""
    code = release.main(["verify", VERSION, "--repo", str(root)])
    out = capsys.readouterr().out
    assert code == 1
    (github,) = [line for line in out.splitlines() if line.startswith("MISMATCH")]
    assert github.startswith("MISMATCH GitHub release")
    return github


@pytest.mark.parametrize("answer, observed", [
    (_missing, "observed unconfirmed (cannot run gh:"),
    (_logged_out, "observed unconfirmed (gh failed: To get started with GitHub CLI, please run:  gh auth login)"),
    (_silent, "observed unconfirmed (gh failed: exit 1)"),
], ids=["no-gh", "logged-out", "silent-failure"])
def test_a_gh_that_cannot_answer_gets_an_unconfirmed_github_row(tmp_path, monkeypatch, capsys, answer, observed):
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, json.dumps({"info": {"version": VERSION}})))
    _gh(monkeypatch, answer)

    assert observed in _github_line(root, capsys)


def test_a_release_gh_cannot_find_reads_as_none(tmp_path, monkeypatch, capsys):
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, json.dumps({"info": {"version": VERSION}})))
    _gh(monkeypatch, _absent)

    assert _github_line(root, capsys).endswith("observed none")


@pytest.mark.parametrize("answer", ["<html>503 Service Unavailable</html>",
                                    json.dumps({"message": "Not Found"}),
                                    json.dumps({"info": None})])
def test_a_pypi_answer_that_is_not_the_version_json_reads_as_unreachable(tmp_path, monkeypatch, capsys, answer):
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, answer))
    _gh(monkeypatch, _released)

    code = release.main(["verify", VERSION, "--repo", str(root)])

    out = capsys.readouterr().out
    assert code == 1
    (pypi,) = [line for line in out.splitlines() if line.startswith("MISMATCH")]
    assert pypi.startswith("MISMATCH PyPI")
    assert "observed unreachable (" in pypi


def test_every_surface_reads_ok_when_each_one_answers_for_the_release(tmp_path, monkeypatch, capsys):
    """The tests above change one answer each; this is the answer set they start from."""
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, json.dumps({"info": {"version": VERSION}})))
    _gh(monkeypatch, _released)

    assert release.main(["verify", VERSION, "--repo", str(root)]) == 0, capsys.readouterr().out
