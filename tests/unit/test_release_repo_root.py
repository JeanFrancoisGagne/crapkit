"""Release verification reads Git and GitHub from the explicitly named repository."""
import json
import subprocess

from test_release_tool import _tree, release


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def tagged(root, version):
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Probe", "-c", "user.email=probe@example.test",
        "commit", "-qm", "fixture")
    git(root, "tag", f"v{version}")


def test_verify_uses_repo_for_git_and_github_even_when_cwd_names_another_repo(tmp_path, monkeypatch):
    target = _tree(tmp_path / "target", version="9.9.0")
    elsewhere = _tree(tmp_path / "elsewhere", version="1.0.0")
    tagged(target, "9.9.0")
    tagged(elsewhere, "1.0.0")
    monkeypatch.chdir(elsewhere)
    real_run = release.subprocess.run
    github_directories = []

    def run(argv, **kwargs):
        if argv[0] != "gh":
            return real_run(argv, **kwargs)
        github_directories.append(kwargs.get("cwd"))
        return subprocess.CompletedProcess(argv, 0, "https://example.test/releases/tag/v9.9.0\n", "")

    def fetch(url):
        return json.dumps({"info": {"version": "9.9.0"}} if "pypi.org" in url else {"servers": []})

    monkeypatch.setattr(release.subprocess, "run", run)
    rows = release.verify(target, "9.9.0", fetch=fetch)
    assert rows[0].observed == "v9.9.0"
    assert rows[0].ok
    assert github_directories == [target]
