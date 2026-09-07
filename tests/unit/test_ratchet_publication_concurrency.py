"""Concurrent commands may not overwrite another command's admitted marks."""
from pathlib import Path
import subprocess
import sys
import pytest

from cli_inproc_repo import add_knotty, repo, template_repo  # noqa: F401
from crapkit.ratchet import RatchetEntry, dump_ratchet, load_ratchet
from state_concurrency_worker import wait_for
from test_cli_verifying_inproc import baselined, marked_debt  # noqa: F401


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_public_merge_requires_each_input_without_changing_ours(tmp_path, capsys, missing):
    from crapkit.cli import main

    paths = [tmp_path / name for name in ("base.tsv", "ours.tsv", "theirs.tsv")]
    text = dump_ratchet([RatchetEntry("src/a.py", "f( )", 50)])
    for index, path in enumerate(paths):
        if index != missing:
            path.write_text(text, encoding="utf-8")
    result = main(["ratchet", "merge", *map(str, paths)])
    assert result != 0
    assert "missing" in capsys.readouterr().err
    assert paths[1].read_text(encoding="utf-8") == text if missing != 1 else not paths[1].exists()


def test_two_public_moves_from_one_prior_preserve_the_first_committed_change(repo):
    path = repo / "crapkit-ratchet.tsv"
    path.write_text(dump_ratchet([RatchetEntry("src/a.py", "f( )", 50)], key_version=1),
                    encoding="utf-8")
    workers = {name: subprocess.Popen([
        sys.executable, str(Path(__file__).with_name("state_concurrency_worker.py")),
        f"move-{name}", str(repo)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for name in ("first", "second")}
    try:
        for name in workers:
            wait_for(repo / f"{name}-ready")
        (repo / "first-go").touch()
        stdout, stderr = workers["first"].communicate(timeout=15)
        assert workers["first"].returncode == 0, (stdout, stderr)
        (repo / "second-go").touch()
        stdout, stderr = workers["second"].communicate(timeout=15)
        assert workers["second"].returncode == 3, (stdout, stderr)
        assert b"changed" in stderr and b"rerun" in stderr
        assert load_ratchet(path.read_text(encoding="utf-8")) == [
            RatchetEntry("src/first.py", "f( )", 50)]
    finally:
        for name, worker in workers.items():
            (repo / f"{name}-go").touch()
            if worker.poll() is None:
                worker.communicate(timeout=15)


def test_verify_refuses_an_intervening_move_and_keeps_its_run_unsettled(marked_debt, capsys):
    from crapkit.cli import main
    from crapkit.store import SnapshotStore, is_trusted

    path = marked_debt / "crapkit-ratchet.tsv"
    path.write_text(dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 100)],
                                 key_version=1), encoding="utf-8")
    worker = subprocess.Popen([
        sys.executable, str(Path(__file__).with_name("state_concurrency_worker.py")),
        "verify", str(marked_debt)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        wait_for(marked_debt / "verify-ready")
        assert main(["ratchet", "move", "src/app.ts", "src/renamed.ts",
                     "--repo", str(marked_debt)]) == 0
        moved = path.read_bytes()
        (marked_debt / "verify-go").touch()
        stdout, stderr = worker.communicate(timeout=15)
        assert worker.returncode == 3, (stdout, stderr)
        assert b"changed during the command" in stderr
        assert path.read_bytes() == moved
        store = SnapshotStore(marked_debt / ".crapkit/crap.sqlite")
        latest = store.list_runs()[-1]
        assert latest["kind"] == "verify" and latest["verdict_ok"] is None
        assert not is_trusted(latest)
    finally:
        (marked_debt / "verify-go").touch()
        if worker.poll() is None:
            worker.communicate(timeout=15)


@pytest.mark.parametrize("prior", [None, "", "src/a.py\tf( )\t50\n"])
@pytest.mark.parametrize("intervening", ["", "src/a.py\tf( )\t90\n"])
def test_saved_publication_preserves_an_intervening_creation_grant_or_deletion(tmp_path, prior,
                                                                            intervening):
    from crapkit.errors import ConfigError
    from crapkit.ratchetfile import RatchetFile

    path = tmp_path / "marks.tsv"
    if prior is not None:
        path.write_text(prior, encoding="utf-8")
    saved = RatchetFile.read(path)
    RatchetFile.read(path).publish(intervening)
    if intervening != prior:
        with pytest.raises(ConfigError, match="file left unchanged"):
            saved.publish("src/a.py\tf( )\t10\n")
        assert path.read_text(encoding="utf-8") == intervening
    else:
        assert saved.publish("src/a.py\tf( )\t10\n")
        assert path.read_text(encoding="utf-8") == "src/a.py\tf( )\t10\n"


def test_failed_atomic_replace_keeps_original_and_cleans_temporary_file(tmp_path, monkeypatch):
    from crapkit.errors import ToolError
    from crapkit import ratchetfile

    path = tmp_path / "marks.tsv"
    path.write_bytes(b"\xef\xbb\xbfsrc/a.py\tf( )\t50\r\n")
    saved = ratchetfile.RatchetFile.read(path)
    before = path.read_bytes()

    def denied(*args):
        raise PermissionError("test denied replace")

    monkeypatch.setattr(ratchetfile.os, "replace", denied)
    with pytest.raises(ToolError, match="cannot publish ratchet marks.tsv: test denied replace"):
        saved.publish("src/a.py\tf( )\t10\n")
    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_fresh_noop_preserves_bytes_and_mtime_after_publication(tmp_path):
    from crapkit.ratchetfile import RatchetFile

    path = tmp_path / "marks.tsv"
    text = dump_ratchet([RatchetEntry("src/a.py", "f( )", 10)], key_version=1)
    assert RatchetFile.read(path).publish(text)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert not RatchetFile.read(path).publish(text)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_verify_does_not_adopt_marks_changed_while_lanes_run(marked_debt, capsys, monkeypatch):
    from crapkit.cli import main, verifying

    path = marked_debt / "crapkit-ratchet.tsv"
    path.write_text(dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 100)],
                                 key_version=1), encoding="utf-8")
    changed = dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 90)],
                           stamp="crapkit-analysis=999 lizard=1.24.0", key_version=1)
    original = verifying._scored_run

    def replace_marks(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text(changed, encoding="utf-8")
        return result

    monkeypatch.setattr(verifying, "_scored_run", replace_marks)
    assert main(["verify", "--reuse-artifacts", "--repo", str(marked_debt)]) == 3
    assert "changed during the command" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == changed


def test_verify_override_cannot_adopt_a_marks_file_created_during_lanes(baselined, capsys,
                                                                     monkeypatch):
    from crapkit.cli import main, verifying
    from crapkit.store import SnapshotStore

    add_knotty(baselined)
    path = baselined / "crapkit-ratchet.tsv"
    changed = dump_ratchet([RatchetEntry("src/other.ts", "other ( )", 90)], key_version=1)
    original = verifying._scored_run

    def replace_marks(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_text(changed, encoding="utf-8")
        return result

    monkeypatch.setattr(verifying, "_scored_run", replace_marks)
    assert main(["verify", "--reuse-artifacts", "--override", "accepted debt",
                 "--repo", str(baselined)]) == 3
    assert "changed during the command" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == changed
    store = SnapshotStore(baselined / ".crapkit/crap.sqlite")
    latest = store.list_runs()[-1]
    assert latest["verdict_ok"] is None
    assert store.read_overrides(latest["id"]), "a failed publication still has its audit"
    store._conn.close()


def test_verify_receipt_hashes_the_exact_admitted_bom_bytes(marked_debt, capsys):
    import hashlib
    import json
    from crapkit.cli import main

    path = marked_debt / "crapkit-ratchet.tsv"
    text = dump_ratchet([RatchetEntry("src/app.ts", "knotty ( n )", 100)], key_version=1)
    before = b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8")
    path.write_bytes(before)

    assert main(["verify", "--reuse-artifacts", "--json", "--repo", str(marked_debt)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["ratchet_sha256"] == hashlib.sha256(before).hexdigest()
    assert output["ratchet_changes"] == {"dropped": 0, "tightened": 1}
    assert load_ratchet(path.read_text(encoding="utf-8"))[0].crap == 16
