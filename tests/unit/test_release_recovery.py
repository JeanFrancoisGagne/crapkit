"""Release recovery preserves the checked distribution bytes across retries."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from test_release_guards import repo, verified
from test_release_tool import release

VERSION = "0.5.2"
ARTIFACTS = {"crapkit-0.5.2-py3-none-any.whl": b"wheel fixture", "crapkit-0.5.2.tar.gz": b"source fixture"}


def receipt(root):
    return json.loads((root / ".crapkit/release-receipt.json").read_text())


def test_build_and_check_finish_before_any_push(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    publish_adapter(root, monkeypatch)
    commands = []

    def execute(command, root, dry_run):
        commands.append(command)
        if "build" in command:
            output = root / (command[command.index("--outdir") + 1] if "--outdir" in command else "dist")
            output.mkdir(parents=True)
            for name, raw in ARTIFACTS.items():
                (output / name).write_bytes(raw)
        if "check" in command:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(release, "_execute", execute)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    assert not any(c[:2] == ("git", "push") for c in commands)
    assert any("build" in c for c in commands) and any("check" in c for c in commands)
    assert "artifacts" not in receipt(root)


class PublicationAdapter:
    """A disposable repository plus in-memory publication responses."""
    def __init__(self, root, fail_after=None):
        self.root = root
        self.fail_after = fail_after
        self.events = []
        self.pypi = {}
        self.github = None
        self.pages = {"commit": "old", "status": "built"}

    def event(self, name, command):
        self.events.append(name)
        if self.fail_after == name:
            self.fail_after = None
            raise subprocess.CalledProcessError(1, command)

    def execute(self, command, root, dry_run):
        from test_release_guards import git
        command = tuple(value for arg in command for value in (
            [str(p.relative_to(root)) for p in sorted(root.glob(arg))] if arg.endswith("/*") else [arg]))
        if "--repo" in command:
            index = command.index("--repo")
            command = command[:index] + command[index + 2:]
        if "build" in command:
            output = root / command[command.index("--outdir") + 1]
            output.mkdir(parents=True)
            for name, raw in ARTIFACTS.items():
                (output / name).write_bytes(raw)
            self.event("build", command)
        elif command[:2] == ("git", "push"):
            git(root, *command[1:])
            self.event("push", command)
        elif "twine" in command and "upload" in command:
            for value in command[command.index("--non-interactive") + 1:]:
                self.pypi[Path(value).name] = hashlib.sha256((root / value).read_bytes()).hexdigest()
                self.event("pypi:" + Path(value).name, command)
        elif command[:3] == ("gh", "release", "create"):
            self.github = {"tag_name": "v" + VERSION, "draft": False, "assets": []}
            self.event("github:create", command)
        elif command[:3] == ("gh", "release", "upload"):
            for value in command[4:]:
                digest = hashlib.sha256((root / value).read_bytes()).hexdigest()
                self.github["assets"].append({"name": Path(value).name, "digest": "sha256:" + digest})
                self.event("github:" + Path(value).name, command)
        elif command[:3] == ("claude", "plugin", "update"):
            self.event("plugin", command)
        elif command[:2] == ("gh", "api"):
            self.pages = {"commit": receipt(root)["head"], "status": "built"}
            self.event("pages", command)
        elif "check" in command:
            self.event("check", command)
        else:
            raise AssertionError(command)

    def remote_json(self, url, *, absent=False):
        if url.startswith("https://pypi.org/"):
            if not self.pypi:
                return None
            return {"info": {"version": VERSION}, "urls": [
                {"filename": name, "digests": {"sha256": digest}} for name, digest in self.pypi.items()]}
        if "/releases/tags/" in url:
            return self.github
        if "/pages/builds/latest" in url:
            return self.pages
        raise AssertionError(url)


def publish_adapter(root, monkeypatch, fail_after=None):
    adapter = PublicationAdapter(root, fail_after)
    git_read = release._git
    # Model the public repository identity; actual refs and pushes stay in the local bare fixture.
    monkeypatch.setattr(release, "_git", lambda root, *args:
                        "https://github.com/JeanFrancoisGagne/crapkit.git"
                        if args[:2] == ("remote", "get-url") else git_read(root, *args))
    monkeypatch.setattr(release, "_execute", adapter.execute)
    monkeypatch.setattr(release, "_remote_json", adapter.remote_json, raising=False)
    return adapter


def test_retry_reuses_artifacts_and_uploads_only_missing_files(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    first = sorted(ARTIFACTS)[0]
    adapter = publish_adapter(root, monkeypatch, "pypi:" + first)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    expected = {name: hashlib.sha256(raw).hexdigest() for name, raw in ARTIFACTS.items()}
    assert receipt(root)["artifacts"] == expected
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("build") == 1
    assert adapter.events.count("push") == 1
    assert adapter.events.count("pypi:" + first) == 1
    assert adapter.pypi == expected
    assert {a["name"]: a["digest"].removeprefix("sha256:") for a in adapter.github["assets"]} == expected


@pytest.mark.parametrize("stop", ["push", *("pypi:" + name for name in sorted(ARTIFACTS)),
                                   "github:create", *("github:" + name for name in sorted(ARTIFACTS)), "pages"])
def test_failure_after_each_publication_resumes_without_republishing(tmp_path, monkeypatch, stop):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch, stop)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("build") == adapter.events.count("check") == 1
    assert adapter.events.count(stop) == 1
    assert not receipt(root)["pending"]
    before = list(adapter.events)
    release.run("stage2b", VERSION, root)
    assert adapter.events == before


@pytest.mark.parametrize("change", ["replace", "delete", "extra"])
def test_changed_confirmed_local_bytes_refuse_before_publication(tmp_path, monkeypatch, change):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch, "push")
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    path = root / ".crapkit/release-dist" / sorted(ARTIFACTS)[0]
    if change == "replace":
        path.write_bytes(b"unconfirmed replacement")
    elif change == "delete":
        path.unlink()
    else:
        (path.parent / "extra.whl").write_bytes(b"not in the receipt")
    before = list(adapter.events)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    assert adapter.events == before


@pytest.mark.parametrize("surface", ["pypi", "github"])
def test_published_digest_mismatch_never_overwrites_bytes(tmp_path, monkeypatch, surface):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    name = sorted(ARTIFACTS)[0]
    if surface == "pypi":
        adapter.pypi[name] = "0" * 64
    else:
        adapter.github = {"tag_name": "v" + VERSION, "draft": False,
                          "assets": [{"name": name, "digest": "sha256:" + "0" * 64}]}
    with pytest.raises(release.ReleaseError, match="digest mismatch"):
        release.run("stage2b", VERSION, root)
    assert not any(e.startswith(surface + ":") for e in adapter.events)


def test_uncertain_upload_stays_pending_and_retry_waits_for_readback(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    execute = adapter.execute
    first = sorted(ARTIFACTS)[0]
    calls = []

    def lost_response(command, root, dry_run):
        if "upload" in command:
            calls.append(command)
            raise subprocess.CalledProcessError(1, command)
        execute(command, root, dry_run)

    monkeypatch.setattr(release, "_execute", lost_response)
    with pytest.raises(release.ReleaseError, match="unconfirmed"):
        release.run("stage2b", VERSION, root)
    monkeypatch.setattr(release, "_execute", execute)
    before = list(adapter.events)
    with pytest.raises(release.ReleaseError, match="unconfirmed"):
        release.run("stage2b", VERSION, root)
    assert adapter.events == before
    assert len(calls) == 1
    adapter.pypi[first] = hashlib.sha256(ARTIFACTS[first]).hexdigest()
    release.run("stage2b", VERSION, root)
    assert not receipt(root)["pending"]
    assert "pypi:" + first not in adapter.events


def test_pages_pending_waits_without_a_second_post(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    execute = adapter.execute

    def queued(command, root, dry_run):
        execute(command, root, dry_run)
        if command[:2] == ("gh", "api"):
            adapter.pages["status"] = "building"

    monkeypatch.setattr(release, "_execute", queued)
    with pytest.raises(release.ReleaseError, match="Pages build is pending"):
        release.run("stage2b", VERSION, root)
    with pytest.raises(release.ReleaseError, match="Pages build is pending"):
        release.run("stage2b", VERSION, root)
    assert adapter.events.count("pages") == 1
    adapter.pages["status"] = "built"
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("pages") == 1


def test_definitively_failed_pages_build_can_be_retried_once(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    execute = adapter.execute

    def queued(command, root, dry_run):
        execute(command, root, dry_run)
        if command[:2] == ("gh", "api"):
            adapter.pages["status"] = "building"

    monkeypatch.setattr(release, "_execute", queued)
    with pytest.raises(release.ReleaseError, match="Pages build is pending"):
        release.run("stage2b", VERSION, root)
    adapter.pages["status"] = "errored"
    monkeypatch.setattr(release, "_execute", execute)
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("pages") == 2
    assert adapter.events.count("build") == 1
    assert not receipt(root)["pending"]


def test_http_json_and_legacy_github_asset_bytes_confirm_the_release(tmp_path, monkeypatch):
    import io
    import urllib.error
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    real_reader = release._remote_json
    adapter = publish_adapter(root, monkeypatch)
    adapter.github = {"tag_name": "v" + VERSION, "draft": False, "assets": [
        {"name": name, "digest": None, "browser_download_url": "https://assets.example.test/" + name}
        for name in ARTIFACTS]}
    fetched = []

    def response(url, timeout):
        fetched.append(url)
        if url.startswith("https://assets.example.test/"):
            return io.BytesIO(ARTIFACTS[url.rsplit("/", 1)[1]])
        data = adapter.remote_json(url, absent=True)
        if data is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(json.dumps(data).encode())

    monkeypatch.setattr(release, "_remote_json", real_reader)
    monkeypatch.setattr(release.urllib.request, "urlopen", response)
    release.run("stage2b", VERSION, root)
    assert not any(event.startswith("github:") for event in adapter.events)
    assert all("https://assets.example.test/" + name in fetched for name in ARTIFACTS)


@pytest.mark.parametrize("bad", ["nonobject", "urls-object", "bad-digest", "timeout", "401", "bad-json"])
def test_uncertain_http_metadata_cannot_admit_an_upload(tmp_path, monkeypatch, bad):
    import io
    import urllib.error
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    real_reader = release._remote_json
    adapter = publish_adapter(root, monkeypatch)

    def response(url, timeout):
        if bad == "timeout":
            raise TimeoutError("response lost")
        if bad == "401":
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        values = {"nonobject": b"[]", "bad-json": b"{", "urls-object": b'{"info":{"version":"0.5.2"},"urls":{}}',
                  "bad-digest": b'{"info":{"version":"0.5.2"},"urls":[{"filename":"x","digests":{"sha256":"bad"}}]}'}
        return io.BytesIO(values[bad])

    monkeypatch.setattr(release, "_remote_json", real_reader)
    monkeypatch.setattr(release.urllib.request, "urlopen", response)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    assert not any(event.startswith("pypi:") for event in adapter.events)


def test_initial_preparation_replaces_only_its_unconfirmed_output(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    output = root / ".crapkit/release-dist"
    output.mkdir()
    (output / "old.whl").write_bytes(b"unconfirmed")
    other = root / "dist"
    other.mkdir()
    (other / "keep.whl").write_bytes(b"ordinary build")
    release.run("stage2b", VERSION, root)
    assert set(p.name for p in output.iterdir()) == set(ARTIFACTS)
    assert (other / "keep.whl").read_bytes() == b"ordinary build"
    assert adapter.events.count("build") == 1


def test_local_plugin_failure_can_retry_without_republishing(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch, "plugin")
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("plugin") == 2
    assert all(adapter.events.count("pypi:" + name) == 1 for name in ARTIFACTS)
    assert all(adapter.events.count("github:" + name) == 1 for name in ARTIFACTS)


def test_retry_allows_main_to_advance_after_confirmed_release_push(tmp_path, monkeypatch):
    from test_release_guards import git
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch, "pypi:" + sorted(ARTIFACTS)[0])
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", VERSION, root)
    release_head = git(root, "rev-parse", "HEAD")
    other = tmp_path / "other"
    git(tmp_path, "clone", "--quiet", "--branch", "main", str(tmp_path / "remote.git"), str(other))
    (other / "later.txt").write_text("ordinary next commit\n")
    git(other, "add", "later.txt")
    git(other, "-c", "user.name=Release Test", "-c", "user.email=release@example.test", "commit", "-qm", "later")
    git(other, "push", "--quiet", "origin", "main")
    git(other, "merge-base", "--is-ancestor", release_head, "HEAD")
    release.run("stage2b", VERSION, root)
    assert adapter.events.count("push") == 1
    assert set(adapter.pypi) == set(ARTIFACTS)


def test_artifact_change_during_check_refuses_before_push(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)

    def changed_after_check(command, root, dry_run):
        adapter.execute(command, root, dry_run)
        if "check" in command:
            (root / ".crapkit/release-dist" / sorted(ARTIFACTS)[0]).write_bytes(b"unchecked replacement")

    monkeypatch.setattr(release, "_execute", changed_after_check)
    with pytest.raises(release.ReleaseError, match="changed during"):
        release.run("stage2b", VERSION, root)
    assert "push" not in adapter.events
    assert "artifacts" not in receipt(root)


def test_conflicting_remote_tag_refuses_before_any_push(tmp_path, monkeypatch):
    from test_release_guards import git
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    other = git(root, "commit-tree", "HEAD^{tree}", "-m", "different release")
    git(root, "push", "--quiet", "origin", other + ":refs/heads/other")
    git(tmp_path / "remote.git", "update-ref", "refs/tags/v" + VERSION, other)
    adapter = publish_adapter(root, monkeypatch)
    with pytest.raises(release.ReleaseError, match="remote release tag points to another commit"):
        release.run("stage2b", VERSION, root)
    assert "push" not in adapter.events
    assert not adapter.pypi
    assert git(tmp_path / "remote.git", "rev-parse", "refs/tags/v" + VERSION) == other
