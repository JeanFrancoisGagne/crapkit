"""SnapshotStore.close releases the store's connection.

A caller that opens a store for one read had to reach into the private
connection to close it (`closing(store._conn)`), which ties every such caller
to how the store holds its database. close() is that release, so
`contextlib.closing(store)` works.
"""
import os
import sqlite3
from contextlib import closing

import pytest

from crapkit.store import SnapshotStore


def test_closing_a_store_releases_its_database_file(tmp_path):
    db = tmp_path / "crap.sqlite"
    with closing(SnapshotStore(db)) as store:
        assert store.list_runs() == []

    os.replace(db, tmp_path / "moved.sqlite")  # Windows refuses to move an open database
    with pytest.raises(sqlite3.ProgrammingError):
        store.list_runs()
