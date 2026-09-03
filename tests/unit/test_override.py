"""Override seam: all three records or nothing. Shell-adjacent, tested with tmp dirs."""
import sys

import pytest

from crapkit.errors import ConfigError, ToolError
from crapkit.override import record_override
from crapkit.ratchet import load_ratchet
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation

VIOLATION = GateViolation("src/a.ts", "f( )", 3, 9, 0.0, 90.0, "decompose")


def ok_alert(tmp_path):
    log = tmp_path / "alert.log"
    return f'"{sys.executable}" -c "import sys,pathlib; pathlib.Path(r\'{log}\').open(\'a\').write(sys.stdin.read())"', log


def test_override_writes_all_three_records(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    alert_cmd, log = ok_alert(tmp_path)
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="2am hotfix")
    assert "OVERRIDE (2am hotfix)" in log.read_text(encoding="utf-8")
    entries = load_ratchet((tmp_path / "ratchet.tsv").read_text(encoding="utf-8"))
    assert entries[0].crap == 90.0
    assert store.read_overrides(run_id) == [("src/a.ts", "f( )", 90.0, "2am hotfix")]


def test_failed_alert_grants_nothing(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    bad_alert = f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    with pytest.raises(ToolError, match="no alert, no override"):
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=bad_alert, violations=[VIOLATION], reason="x")
    assert not (tmp_path / "ratchet.tsv").is_file()
    assert store.read_overrides(run_id) == []


def test_missing_alert_command_refuses(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    with pytest.raises(ConfigError, match="alert_command"):
        record_override(store=store, run_id=1, root=tmp_path, ratchet_file="r.tsv",
                        alert_command="", violations=[VIOLATION], reason="x")


def test_empty_reason_refuses(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    with pytest.raises(ConfigError, match="reason"):
        record_override(store=store, run_id=1, root=tmp_path, ratchet_file="r.tsv",
                        alert_command="echo", violations=[VIOLATION], reason="  ")


def test_audit_store_failure_leaves_no_ratchet_grant(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    monkeypatch.setattr(store, "write_overrides", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    alert_cmd, log = ok_alert(tmp_path)
    with pytest.raises(RuntimeError):
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=alert_cmd, violations=[VIOLATION], reason="x")
    assert not (tmp_path / "ratchet.tsv").is_file(), "the debt grant is the LAST step; a failed audit grants nothing"


def test_hook_override_never_raises_an_existing_mark(tmp_path):
    # The hook synthesizes worst-case crap (no coverage data); letting it raise
    # a measured mark to ccn^2+ccn would blind the ratchet to a later real
    # coverage collapse. Marks only ever tighten.
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    (tmp_path / "ratchet.tsv").write_text("src/a.ts\tf( )\t12.0\n", encoding="utf-8")
    alert_cmd, _ = ok_alert(tmp_path)
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                    raise_marks=False)
    entries = {(e.path, e.long_name): e for e in
               load_ratchet((tmp_path / "ratchet.tsv").read_text(encoding="utf-8"))}
    assert entries[("src/a.ts", "f( )")].crap == 12.0, "the prior tighter mark must survive"


def test_an_override_reads_a_marks_file_that_starts_with_a_bom(tmp_path):
    """PowerShell 5.1 writes a BOM in front of a file it saves as UTF-8; every
    other reader of the marks file tolerates it, and the override's own read
    raised ValueError on the header line after verify had already passed."""
    import codecs

    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    (tmp_path / "ratchet.tsv").write_bytes(codecs.BOM_UTF8 + b"src/a.ts\tf( )\t12.0\n")
    alert_cmd, _ = ok_alert(tmp_path)

    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                    raise_marks=False)

    entries = {(e.path, e.long_name): e for e in
               load_ratchet((tmp_path / "ratchet.tsv").read_bytes().decode("utf-8-sig"))}
    assert entries[("src/a.ts", "f( )")].crap == 12.0, "the prior mark survives the BOM"


def test_hook_override_still_records_a_mark_for_a_new_function(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    alert_cmd, _ = ok_alert(tmp_path)
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                    raise_marks=False)
    entries = load_ratchet((tmp_path / "ratchet.tsv").read_text(encoding="utf-8"))
    assert entries[0].crap == 90.0, "a function with no prior mark gets the synthesized one"
