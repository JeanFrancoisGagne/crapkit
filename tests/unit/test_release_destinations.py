"""Release commands and receipt readbacks address the same public services."""
import json
import subprocess

import pytest

from test_release_guards import git, repo, verified
from test_release_recovery import VERSION, publish_adapter, receipt
from test_release_tool import _tree, release


UPLOAD = "https://upload.pypi.org/legacy/"
REPOSITORY = "github.com/JeanFrancoisGagne/crapkit"


def option(command, flag, default):
    return command[command.index(flag) + 1] if flag in command else default


@pytest.mark.parametrize("surface", ["pypi", "github", "pages"])
def test_stage2b_ignores_external_cli_destination_overrides(tmp_path, monkeypatch, surface):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    monkeypatch.setenv("TWINE_REPOSITORY_URL", "https://packages.example.invalid/legacy/")
    monkeypatch.setenv("GH_REPO", "example.invalid/other/project")
    monkeypatch.setenv("GH_HOST", "example.invalid")
    writes = []

    def execute(command, root, dry_run):
        target = None
        expected = None
        if surface == "pypi" and "twine" in command and "upload" in command:
            target = option(command, "--repository-url", "https://packages.example.invalid/legacy/")
            expected = UPLOAD
        if surface == "github" and command[:2] == ("gh", "release"):
            target = option(command, "--repo", "example.invalid/other/project")
            expected = REPOSITORY
        if surface == "pages" and command[:2] == ("gh", "api"):
            target = option(command, "--hostname", "example.invalid")
            expected = "github.com"
        if target is not None:
            writes.append(target)
            if target != expected:
                return  # The other service accepts bytes; canonical readback stays absent.
        adapter.execute(command, root, dry_run)

    monkeypatch.setattr(release, "_execute", execute)
    release.run("stage2b", VERSION, root)
    assert writes == [{"pypi": UPLOAD, "github": REPOSITORY, "pages": "github.com"}[surface]] * {
        "pypi": 2, "github": 3, "pages": 1}[surface]
    assert receipt(root)["pending"] == []


@pytest.mark.parametrize("mode", ["fetch", "push", "multiple-push", "instead-of", "push-instead-of"])
def test_stage2b_refuses_git_destinations_outside_readback_repository(tmp_path, monkeypatch, mode):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    # The Git identity reads are real. No publication command may reach the adapter.
    if mode == "fetch":
        git(root, "remote", "set-url", "origin", "https://github.com/other/project.git")
    elif mode in ("instead-of", "push-instead-of"):
        git(root, "remote", "set-url", "origin", "https://github.com/JeanFrancoisGagne/crapkit.git")
        option_name = "insteadOf" if mode == "instead-of" else "pushInsteadOf"
        git(root, "config", "url.https://github.com/other/." + option_name,
            "https://github.com/JeanFrancoisGagne/")
    else:
        git(root, "remote", "set-url", "origin", "https://github.com/JeanFrancoisGagne/crapkit.git")
        git(root, "remote", "set-url", "--push", "origin", "https://github.com/other/project.git")
        if mode == "multiple-push":
            git(root, "remote", "set-url", "--add", "--push", "origin",
                "git@github.com:JeanFrancoisGagne/crapkit.git")
    current_git = release._git

    def read(root, *args):
        return git(root, *args) if args[:2] == ("remote", "get-url") else current_git(root, *args)

    monkeypatch.setattr(release, "_git", read)
    monkeypatch.setattr(release, "_execute", lambda *args: pytest.fail("command ran before destination admission"))
    with pytest.raises(release.ReleaseError, match="origin.*github.com/JeanFrancoisGagne/crapkit"):
        release.run("stage2b", VERSION, root)
    assert adapter.events == []


@pytest.mark.parametrize("url", [
    "https://github.com/JeanFrancoisGagne/crapkit.git",
    "https://github.com/jeanfrancoisgagne/crapkit",
    "git@github.com:JeanFrancoisGagne/crapkit.git",
    "ssh://git@github.com/JeanFrancoisGagne/crapkit.git",
])
def test_stage2b_accepts_canonical_https_and_ssh_git_identity(tmp_path, monkeypatch, url):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    current_git = release._git
    monkeypatch.setattr(release, "_git", lambda root, *args:
                        url if args[:2] == ("remote", "get-url") else current_git(root, *args))
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("push") == 1


def test_printed_plan_pins_the_same_service_destinations():
    steps = {step.name: step for step in release.plan(VERSION)}
    assert option(steps["pypi"].commands[0], "--repository-url", None) == UPLOAD
    for command in steps["github release"].commands:
        assert option(command, "--repo", None) == REPOSITORY
    assert option(steps["pages"].commands[0], "--hostname", None) == "github.com"


def test_verify_queries_the_canonical_github_repository(tmp_path, monkeypatch):
    root = _tree(tmp_path, version=VERSION)
    monkeypatch.setenv("GH_REPO", "example.invalid/other/project")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, f"https://{REPOSITORY}/releases/tag/v{VERSION}\n", "")

    monkeypatch.setattr(release.subprocess, "run", run)
    rows = release.verify(root, VERSION, git_tag=lambda: "v" + VERSION,
                          fetch=lambda url: json.dumps({"info": {"version": VERSION}, "servers": []}))
    assert rows[1].ok
    assert option(commands[0], "--repo", None) == REPOSITORY
