"""Optional marks never undo worklist's admitted-row read."""
import json

import pytest

from cli_inproc_repo import git, repo, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.ratchet import RatchetEntry, dump_ratchet
from crapkit.snapshot import InventoryRow
from crapkit.store import SnapshotStore


@pytest.mark.parametrize("marked", [False, True])
def test_worklist_does_not_load_unadmitted_rows_for_absent_or_current_marks(
        repo, capsys, monkeypatch, marked):
    (repo / ".crapkit").mkdir()
    store = SnapshotStore(repo / ".crapkit/crap.sqlite")
    rows = [InventoryRow("src", "src/app.ts", "dispatch( kind )", 1, 12,
                         8, 8, 8, 12, 1, 0, 0, 1),
            InventoryRow("src", "src/app.ts", "plain( x )", 14, 19,
                         1, 1, 1, 6, 1, 0, 0, 1)]
    store.write_run(commit=git(repo, "rev-parse", "HEAD").strip(), tool_versions={},
                    rows=rows, kind="inventory")
    store._conn.close()
    if marked:
        (repo / "crapkit-ratchet.tsv").write_text(dump_ratchet(
            [RatchetEntry("src/app.ts", "dispatch( kind )", 30)], key_version=1), encoding="utf-8")
    loaded = []
    read = SnapshotStore.read_rows

    def observe(store, *args, **kwargs):
        result = read(store, *args, **kwargs)
        loaded.extend(result)
        return result

    monkeypatch.setattr(SnapshotStore, "read_rows", observe)
    assert main(["worklist", "--repo", str(repo), "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["active"][0]["function"] == "dispatch( kind )"
    assert output["active"][0]["ratchet_mark"] == (30 if marked else None)
    assert all(row.long_name != "plain( x )" for row in loaded)
