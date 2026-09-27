"""Override seam: all three records or nothing. Shell-adjacent, tested with tmp dirs."""
import sys

import pytest

from crapkit.errors import ConfigError, ToolError
from crapkit.override import record_override
from crapkit.ratchet import load_ratchet, metric_version
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
                    alert_command=alert_cmd, violations=[VIOLATION], reason="2am hotfix",
                    metric=metric_version())
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
                        alert_command=bad_alert, violations=[VIOLATION], reason="x",
                        metric=metric_version())
    assert not (tmp_path / "ratchet.tsv").is_file()
    assert store.read_overrides(run_id) == []


def test_missing_alert_command_refuses(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    with pytest.raises(ConfigError, match="alert_command"):
        record_override(store=store, run_id=1, root=tmp_path, ratchet_file="r.tsv",
                        alert_command="", violations=[VIOLATION], reason="x",
                        metric=metric_version())


def test_empty_reason_refuses(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    with pytest.raises(ConfigError, match="reason"):
        record_override(store=store, run_id=1, root=tmp_path, ratchet_file="r.tsv",
                        alert_command="echo", violations=[VIOLATION], reason="  ",
                        metric=metric_version())


def test_audit_store_failure_leaves_no_ratchet_grant(tmp_path, monkeypatch):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    monkeypatch.setattr(store, "write_overrides", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    alert_cmd, log = ok_alert(tmp_path)
    with pytest.raises(RuntimeError):
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=alert_cmd, violations=[VIOLATION], reason="x",
                        metric=metric_version())
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
                    raise_marks=False,
                    metric=metric_version())
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
                    raise_marks=False,
                    metric=metric_version())

    entries = {(e.path, e.long_name): e for e in
               load_ratchet((tmp_path / "ratchet.tsv").read_bytes().decode("utf-8-sig"))}
    assert entries[("src/a.ts", "f( )")].crap == 12.0, "the prior mark survives the BOM"


def test_an_override_on_a_utf16_marks_file_writes_it_back_as_utf16(tmp_path):
    """The marks read is the one reader's: a bare `Out-File` save reads as its
    rows, and the grant writes the file back behind its own byte-order mark,
    where a UTF-8 rewrite would read as cp1252 in PowerShell 5.1."""
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    (tmp_path / "ratchet.tsv").write_bytes(b"\xff\xfe" + "src/b.ts\tg( )\t12.0\n".encode("utf-16-le"))
    alert_cmd, _ = ok_alert(tmp_path)

    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                    metric=metric_version())

    written = (tmp_path / "ratchet.tsv").read_bytes()
    assert written.startswith(b"\xff\xfe")
    marks = {(e.path, e.long_name): e.crap for e in load_ratchet(written[2:].decode("utf-16-le"))}
    assert marks == {("src/a.ts", "f( )"): 90.0, ("src/b.ts", "g( )"): 12.0}


def test_an_override_on_a_cp1252_marks_file_refuses_before_its_alert(tmp_path):
    """The grant would save U+FFFD in place of `café( )`, so it refuses by the
    byte, and before the alert: a refused override leaves no alert line and no
    audit row claiming a grant that never landed."""
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    before = b"src/b.ts\tcaf\xe9( )\t12.0\n"
    (tmp_path / "ratchet.tsv").write_bytes(before)
    alert_cmd, log = ok_alert(tmp_path)

    with pytest.raises(ConfigError) as refused:
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                        metric=metric_version())

    assert str(refused.value).startswith("ratchet.tsv holds byte e9 at offset 12, which reads as "
                                         "U+FFFD")
    assert refused.value.exit_code == 3
    assert not log.exists(), "no alert for a grant that was refused"
    assert store.read_overrides(run_id) == []
    assert (tmp_path / "ratchet.tsv").read_bytes() == before


def test_hook_override_still_records_a_mark_for_a_new_function(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    alert_cmd, _ = ok_alert(tmp_path)
    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert_cmd, violations=[VIOLATION], reason="prod down",
                    raise_marks=False,
                    metric=metric_version())
    entries = load_ratchet((tmp_path / "ratchet.tsv").read_text(encoding="utf-8"))
    assert entries[0].crap == 90.0, "a function with no prior mark gets the synthesized one"


# --- what the alert command is handed and hands back ---------------------------

def _copy_stdin(tmp_path, exit_code: int = 0, noise: bytes = b""):
    """An alert command that keeps the bytes it was handed, writes `noise` to
    its own stdout, and exits `exit_code`."""
    log = tmp_path / "alert.bin"
    script = tmp_path / "alert.py"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(r'{log}').write_bytes(sys.stdin.buffer.read())\n"
        f"sys.stdout.buffer.write({noise!r})\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    return f'"{sys.executable}" "{script}"', log


def test_a_non_ascii_reason_and_name_reach_the_alert_as_utf8_and_the_audit_as_written(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    command, log = _copy_stdin(tmp_path, noise=bytes([0xFF, 0xFE]))
    violation = GateViolation("src/café.ts", "gräde_世( )", 3, 9, 0.0, 90.0, "decompose")

    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=command, violations=[violation], reason="déploiement 世",
                    metric=metric_version())

    sent = log.read_bytes().decode("utf-8")
    assert "OVERRIDE (déploiement 世)" in sent and "src/café.ts:3 gräde_世( )" in sent
    assert store.read_overrides(run_id) == [("src/café.ts", "gräde_世( )", 90.0, "déploiement 世")]


def test_an_alert_that_fails_writing_bytes_that_are_not_utf8_grants_nothing(tmp_path):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    command, _ = _copy_stdin(tmp_path, exit_code=3, noise=b"caf" + bytes([0xE9]))

    with pytest.raises(ToolError, match="override alert command failed \\(exit 3\\)"):
        record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                        alert_command=command, violations=[VIOLATION], reason="hotfix",
                        metric=metric_version())

    assert store.read_overrides(run_id) == []
    assert not (tmp_path / "ratchet.tsv").exists()


# --- the alert_command a refusal hands the user ------------------------------------

def _example_value() -> str:
    """The command the refusal's crapkit.toml line sets, read the way config loads it."""
    import tomllib

    from crapkit.override import alert_example

    return tomllib.loads("[crapkit]\n" + alert_example())["crapkit"]["alert_command"]


def test_the_refusal_names_an_alert_command_line_to_paste(tmp_path):
    """`set [crapkit] alert_command` named the key and no value; a user then
    guessed one, and `cat` does not exist in the cmd.exe crapkit runs it with
    on Windows."""
    from crapkit.override import alert_example

    with pytest.raises(ConfigError) as refused:
        record_override(store=SnapshotStore(tmp_path / "db.sqlite"), run_id=1, root=tmp_path,
                        ratchet_file="r.tsv", alert_command="", violations=[VIOLATION], reason="x",
                        metric=metric_version())

    assert str(refused.value).endswith(f"set [crapkit] alert_command in crapkit.toml, for example {alert_example()}")


def test_the_example_alert_command_appends_the_override_line_on_this_platform(tmp_path):
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])

    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=_example_value(), violations=[VIOLATION], reason="hotfix 412",
                    metric=metric_version())

    logged = (tmp_path / ".crapkit" / "alerts.log").read_text(encoding="utf-8")
    assert logged.splitlines() == ["crapkit OVERRIDE (hotfix 412): src/a.ts:3 f( ) crap=90.0"]


def test_digest_alert_with_no_command_names_the_same_line(tmp_path):
    from types import SimpleNamespace

    from crapkit.cli.reports import _send_digest_alert
    from crapkit.override import alert_example

    with pytest.raises(ConfigError) as refused:
        _send_digest_alert(tmp_path, SimpleNamespace(alert_command=" "), {"id": 1}, {"id": 2}, ["x"])

    assert str(refused.value) == ("digest --alert needs [crapkit] alert_command in crapkit.toml, "
                                  f"for example {alert_example()}")


@pytest.mark.parametrize("page", ["docs/configuration.md", "docs/ratchet.md"])
def test_the_config_and_override_pages_give_both_platforms_example(page):
    from pathlib import Path

    from crapkit.override import POSIX_ALERT_EXAMPLE, WINDOWS_ALERT_EXAMPLE

    text = (Path(__file__).resolve().parents[2] / page).read_text(encoding="utf-8")

    assert POSIX_ALERT_EXAMPLE in text and WINDOWS_ALERT_EXAMPLE in text, page
