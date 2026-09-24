"""Text the OS hands over (argv, the environment, a host or directory name).

Python decodes a POSIX byte that is not UTF-8 as a lone surrogate, and a
Windows command line or environment can carry one too. sqlite and
`str.encode("utf-8")` refuse a surrogate, so `explain` and `brief` on such a
path argument, an override reason in such bytes, and a host or checkout
directory named in Latin-1 ended with a UnicodeEncodeError traceback. The
override's alert had already gone out, so its three-record audit was left
half written. Each value now passes through repotext.os_text (text a store
can hold) or os_bytes (the bytes a hash or a lock key needs).
"""
import hashlib
import os
import socket
import sys

import pytest

from hand_scored_repo import make_repo, run, scored, write_run

from crapkit import lanes, repotext, resources
from crapkit.cli._shared import _repo_relative
from crapkit.override import record_override
from crapkit.ratchet import metric_version
from crapkit.store import SnapshotStore
from crapkit.verify import GateViolation

# A POSIX byte comes back as that one byte; on Windows the same surrogate is
# three bytes of broken UTF-16, each read as U+FFFD.
BYTE = "�" * (3 if sys.platform == "win32" else 1)
SURROGATE = [
    # id, a value as Python hands it over, the text crapkit keeps
    ("argv-invalid-utf8", "src/caf\udce9.py", f"src/caf{BYTE}.py"),
    ("argv-lone-surrogate-windows", "src/caf\ud800.py", "src/caf���.py"),
    ("argv-valid-accent", "src/café.py", "src/café.py"),
    ("argv-cjk-emoji", "src/渡辺\U0001f680.py", "src/渡辺\U0001f680.py"),
]
IDS = [row[0] for row in SURROGATE]


@pytest.mark.parametrize("given, kept", [row[1:] for row in SURROGATE], ids=IDS)
def test_a_file_argument_becomes_text_every_reader_can_hold(given, kept):
    assert _repo_relative(given) == kept


@pytest.mark.parametrize("given", [row[1] for row in SURROGATE], ids=IDS)
@pytest.mark.parametrize("command", ["explain", "brief"])
def test_explain_and_brief_answer_a_path_argument_in_any_bytes_with_a_sentence(
        tmp_path, capsys, command, given):
    root = make_repo(tmp_path)
    write_run(root, [scored("f( x )", 1, 3, ccn=2, cov=1.0, crap=2.0, remedy="ok")])

    code, _, err = run(root, capsys, command, given, "f")

    assert code == 1, err
    assert err.startswith("crapkit: no function") and "Traceback" not in err


VIOLATION = GateViolation("src/a.ts", "f( )", 3, 9, 0.0, 90.0, "decompose")
REASONS = [
    ("reason-invalid-utf8-bytes", "caf\udce9 hotfix", f"caf{BYTE} hotfix"),
    ("reason-lone-surrogate-windows", "caf\ud800 hotfix", "caf��� hotfix"),
    ("reason-ascii", "hotfix 123", "hotfix 123"),
    ("reason-valid-accent", "correctif café", "correctif café"),
    ("reason-cjk-emoji", "修正 \U0001f680", "修正 \U0001f680"),
]


@pytest.mark.parametrize("given, kept", [row[1:] for row in REASONS], ids=[row[0] for row in REASONS])
def test_an_override_reason_in_any_bytes_lands_all_three_records(tmp_path, given, kept):
    store = SnapshotStore(tmp_path / "db.sqlite")
    run_id = store.write_run(commit="c", tool_versions={}, rows=[])
    log = tmp_path / "alert.log"
    alert = (f'"{sys.executable}" -c "import sys,pathlib; '
             f'pathlib.Path(r\'{log}\').write_bytes(sys.stdin.buffer.read())"')

    record_override(store=store, run_id=run_id, root=tmp_path, ratchet_file="ratchet.tsv",
                    alert_command=alert, violations=[VIOLATION], reason=given, metric=metric_version())

    assert store.read_overrides(run_id) == [("src/a.ts", "f( )", 90.0, kept)]
    assert f"OVERRIDE ({kept})".encode() in log.read_bytes()
    assert (tmp_path / "ratchet.tsv").is_file()


HOSTS = [
    # id, the name Python hands over, the bytes it keys on here
    ("hostname-invalid-utf8", "caf\udce9", b"caf\xed\xb3\xa9" if sys.platform == "win32" else b"caf\xe9"),
    ("hostname-valid-accent", "café", "café".encode()),
    ("hostname-ascii", "build-01", b"build-01"),
]


@pytest.mark.parametrize("host, raw", [row[1:] for row in HOSTS], ids=[row[0] for row in HOSTS])
def test_a_host_name_in_any_bytes_keys_the_worker_budget_and_the_output_lock(
        tmp_path, monkeypatch, host, raw):
    monkeypatch.setattr(socket, "gethostname", lambda: host)
    monkeypatch.delenv("CRAPKIT_RESOURCE_DIR", raising=False)

    assert resources._budget_directory().name == hashlib.sha256(raw).hexdigest()[:16]
    assert lanes._output_lock(tmp_path / "cov.json").parent.name == resources._budget_directory().name


@pytest.mark.skipif(sys.platform == "win32",
                    reason="needs a POSIX directory name whose bytes are not UTF-8")
def test_an_output_under_a_directory_named_in_latin1_takes_a_lock(tmp_path):
    lock = lanes._output_lock(tmp_path / os.fsdecode(b"caf\xe9") / "cov.json")

    assert lock.name.startswith("measurement-") and lock.suffix == ".lock"


@pytest.mark.parametrize("value", ["caf\udce9", "caf\ud800", "café", "x"])
def test_os_text_and_os_bytes_never_raise(value):
    assert repotext.os_text(value).encode("utf-8")
    assert repotext.os_bytes(value)
