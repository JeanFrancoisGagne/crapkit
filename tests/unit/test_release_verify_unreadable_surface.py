"""`release.py verify` prints a row for a surface it cannot read, never a traceback.

Two readers let an exception escape `main`, which catches only ReleaseError: the
GitHub release row ran `gh` with no guard, so a shell without gh died with
FileNotFoundError, and the PyPI row parsed whatever PyPI answered, so an HTML
error page died with JSONDecodeError and a JSON body without `info` with KeyError.
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


def _without_gh(monkeypatch):
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if Path(argv[0]).stem.lower() == "gh":
            raise FileNotFoundError(2, "The system cannot find the file specified")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", run)


def _gh_answers(monkeypatch):
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if Path(argv[0]).stem.lower() == "gh":
            return subprocess.CompletedProcess(argv, 0, stdout=f"https://github.com/r/releases/tag/v{VERSION}\n")
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(release.subprocess, "run", run)


def test_a_shell_without_gh_gets_an_unconfirmed_github_row(tmp_path, monkeypatch, capsys):
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, json.dumps({"info": {"version": VERSION}})))
    _without_gh(monkeypatch)

    code = release.main(["verify", VERSION, "--repo", str(root)])

    out = capsys.readouterr().out
    assert code == 1
    (github,) = [line for line in out.splitlines() if "GitHub release" in line]
    assert github.startswith("MISMATCH GitHub release")
    assert "observed unconfirmed (cannot run gh:" in github
    assert [line for line in out.splitlines() if line.startswith("MISMATCH")] == [github]


def test_every_surface_reads_ok_when_each_one_answers_for_the_release(tmp_path, monkeypatch, capsys):
    """The tests above change one answer each; this is the answer set they start from."""
    root, commit = _tagged(tmp_path)
    monkeypatch.setattr(release, "_urlopen", _surfaces(commit, json.dumps({"info": {"version": VERSION}})))
    _gh_answers(monkeypatch)

    assert release.main(["verify", VERSION, "--repo", str(root)]) == 0, capsys.readouterr().out
