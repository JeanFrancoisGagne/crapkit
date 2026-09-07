"""Release commands require current repository proof before side effects."""
import json
import shlex
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from test_release_tool import _tree, release


def git(root, *arguments):
    return subprocess.run(["git", *arguments], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def repo(tmp_path, *, bumped=False):
    root = _tree(tmp_path)
    (root / ".gitignore").write_text(".crapkit/\ndist/\n", encoding="utf-8")
    if bumped:
        release.bump(root, "0.5.2")
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Release Test")
    git(root, "config", "user.email", "release@example.test")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(root, "remote", "add", "origin", str(remote))
    git(root, "push", "-qu", "origin", "main")
    return root


def capture(monkeypatch, *, effect=None):
    commands = []

    def execute(command, root, dry_run):
        commands.append(command)
        if effect:
            effect(command, root)

    monkeypatch.setattr(release, "_execute", execute)
    return commands


@pytest.mark.parametrize("state", ["dirty", "branch", "unpushed"])
def test_stage1_refuses_before_any_release_command(tmp_path, monkeypatch, state):
    root = repo(tmp_path)
    if state == "dirty":
        (root / "unrelated.txt").write_text("keep me", encoding="utf-8")
    elif state == "branch":
        git(root, "switch", "-c", "feature")
    else:
        git(root, "commit", "--allow-empty", "-qm", "not pushed")
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage1", "0.5.2", root)
    assert commands == []


def test_publish_refuses_without_a_verify_receipt(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", "v0.5.2")
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", "0.5.2", root)
    assert commands == []


def test_contract_stage_never_takes_over_an_existing_tag(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    git(root, "tag", "v0.5.2")
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage2a", "0.5.2", root)
    assert commands == []
    assert git(root, "rev-parse", "v0.5.2") == git(root, "rev-parse", "HEAD")


def tag_command(command, root):
    words = shlex.split(command) if isinstance(command, str) else command
    if words[:2] == ["git", "tag"] or tuple(words[:2]) == ("git", "tag"):
        git(root, *words[1:])


def contracts(root, monkeypatch):
    capture(monkeypatch, effect=tag_command)
    release.run("stage2a", "0.5.2", root)


def ledger(root, *, head=None, kind="verify", ok=1, findings=0):
    path = root / ".crapkit" / "crap.sqlite"
    path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, "
                   "commit_sha TEXT, kind TEXT, verdict_ok INTEGER, findings INTEGER)")
        cursor = db.execute("INSERT INTO runs (commit_sha,kind,verdict_ok,findings) VALUES (?,?,?,?)",
                            (head or git(root, "rev-parse", "HEAD"), kind, ok, findings))
        return cursor.lastrowid


def test_successful_contracts_record_the_exact_release_tree(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    contracts(root, monkeypatch)
    receipt = json.loads((root / ".crapkit" / "release-receipt.json").read_text())
    assert receipt["head"] == git(root, "rev-parse", "HEAD")
    assert receipt["version"] == "0.5.2"
    assert receipt["contracts_sha256"]


@pytest.mark.parametrize("state", ["no-row", "failed", "partial", "wrong-head", "stale"])
def test_verify_zero_exit_is_insufficient_without_a_new_full_pass(tmp_path, monkeypatch, state):
    root = repo(tmp_path, bumped=True)
    contracts(root, monkeypatch)
    if state == "stale":
        ledger(root)

    def result(command, root):
        if state == "failed":
            ledger(root, ok=0, findings=1)
        elif state == "partial":
            ledger(root, kind="verify-diff")
        elif state == "wrong-head":
            ledger(root, head="a" * 40)

    capture(monkeypatch, effect=result)
    with pytest.raises(release.ReleaseError):
        release.run("verify", "0.5.2", root)


def verified(root, monkeypatch):
    contracts(root, monkeypatch)
    capture(monkeypatch, effect=lambda command, root: ledger(root))
    release.run("verify", "0.5.2", root)


def test_publish_accepts_only_the_recorded_full_verify_run(tmp_path, monkeypatch):
    from test_release_recovery import publish_adapter
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    adapter = publish_adapter(root, monkeypatch)
    release.run("stage2b", "0.5.2", root)
    assert "push" in adapter.events


@pytest.mark.parametrize("state", ["failed-later", "head-moved", "dirty", "tag-moved"])
def test_publish_rechecks_proof_at_its_own_boundary(tmp_path, monkeypatch, state):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)
    if state == "failed-later":
        ledger(root, ok=0, findings=1)
    elif state == "head-moved":
        git(root, "commit", "--allow-empty", "-qm", "new head")
    elif state == "dirty":
        (root / "extra.txt").write_text("concurrent edit", encoding="utf-8")
    else:
        git(root, "tag", "-d", "v0.5.2")
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", "0.5.2", root)
    assert commands == []


def test_release_command_arguments_reach_the_child_literally(tmp_path, monkeypatch):
    monkeypatch.setenv("RELEASE_VALUE", "expanded")
    values = ["%RELEASE_VALUE%", 'a" & echo no', "two words"]
    script = "import json,sys; from pathlib import Path; Path('args.json').write_text(json.dumps(sys.argv[1:]))"
    release._execute((sys.executable, "-c", script, *values), tmp_path, False)
    assert json.loads((tmp_path / "args.json").read_text()) == values


def test_an_unexpected_stage1_file_is_never_staged(tmp_path, monkeypatch):
    root = repo(tmp_path)

    def concurrent_change(command, root):
        (root / "unrelated.txt").write_text("another writer", encoding="utf-8")

    commands = capture(monkeypatch, effect=concurrent_change)
    with pytest.raises(release.ReleaseError):
        release.run("stage1", "0.5.2", root)
    assert not any("git add" in str(command) for command in commands)


def test_failed_verify_rerun_invalidates_previous_permission(tmp_path, monkeypatch):
    root = repo(tmp_path, bumped=True)
    verified(root, monkeypatch)

    def fail(command, root):
        raise subprocess.CalledProcessError(1, command)

    capture(monkeypatch, effect=fail)
    with pytest.raises(release.ReleaseError):
        release.run("verify", "0.5.2", root)
    commands = capture(monkeypatch)
    with pytest.raises(release.ReleaseError):
        release.run("stage2b", "0.5.2", root)
    assert commands == []


@pytest.mark.parametrize("replace_tag", [False, True])
def test_failed_contracts_remove_only_the_tag_the_stage_created(tmp_path, monkeypatch, replace_tag):
    root = repo(tmp_path, bumped=True)

    def fail_contracts(command, root):
        tag_command(command, root)
        if "pytest" in command:
            if replace_tag:
                git(root, "commit", "--allow-empty", "-qm", "new owner")
                git(root, "tag", "-f", "v0.5.2")
            raise subprocess.CalledProcessError(1, command)

    capture(monkeypatch, effect=fail_contracts)
    with pytest.raises(release.ReleaseError):
        release.run("stage2a", "0.5.2", root)
    assert bool(git(root, "tag", "--list", "v0.5.2")) is replace_tag
    assert not (root / ".crapkit" / "release-receipt.json").exists()


def test_distribution_arguments_expand_checked_files_literally(tmp_path):
    dist = tmp_path / ".crapkit/release-dist"
    dist.mkdir(parents=True)
    for name in ("pkg with spaces.whl", "pkg.tar.gz"):
        (dist / name).write_text("fixture", encoding="utf-8")
    script = "import json,sys; from pathlib import Path; Path('args.json').write_text(json.dumps(sys.argv[1:]))"
    release._execute((sys.executable, "-c", script, ".crapkit/release-dist/*"), tmp_path, False)
    actual = json.loads((tmp_path / "args.json").read_text())
    assert [Path(path).name for path in actual] == ["pkg with spaces.whl", "pkg.tar.gz"]


def test_distribution_reads_refuse_a_redirected_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.txt"
    keep.write_bytes(b"keep")
    try:
        (tmp_path / ".crapkit").mkdir()
        (tmp_path / ".crapkit/release-dist").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this account cannot create directory symlinks")
    with pytest.raises(release.ReleaseError):
        release._execute((sys.executable, "-c", "pass", ".crapkit/release-dist/*"), tmp_path, False)
    assert keep.read_bytes() == b"keep"


def test_stage1_stages_only_declared_release_files(tmp_path, monkeypatch):
    root = repo(tmp_path)
    commands = capture(monkeypatch)
    release.run("stage1", "0.5.2", root)
    staging = next(command for command in commands if command[:2] == ("git", "add"))
    assert staging == ("git", "add", "--", *release.RELEASE_FILES)
    assert all(command[0] == sys.executable for command in commands if command[0] != "git")


@pytest.mark.parametrize("state", ["wrong-version", "branch", "dirty"])
def test_contracts_require_the_requested_version_on_clean_main(tmp_path, monkeypatch, state):
    root = repo(tmp_path, bumped=state != "wrong-version")
    if state == "branch":
        git(root, "switch", "-c", "feature")
    if state == "dirty":
        (root / "README.md").write_text("dirty", encoding="utf-8")
    commands = capture(monkeypatch)
    assert release.main(["run", "stage2a", "0.5.2", "--repo", str(root)]) == 1
    assert commands == []
