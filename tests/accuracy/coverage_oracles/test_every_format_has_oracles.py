"""Every coverage format crapkit reads has hand-worked recordings and a crapkit-free reader.

`coverage_format._FORMATS` names each lane `parser`. For each one, ground_table
works at least one committed recording read in that format (probe_repo.PRODUCERS
names each recording's parser; a recording ground_table has no family for is not
worked by hand), and counts_table reads that format with no crapkit import, in a
function named after it. A seventh reader fails here, naming its key, until it
brings both.
"""
from __future__ import annotations

import inspect

import pytest

from accuracy.coverage_oracles import counts_table, ground_table, probe_repo
from crapkit import coverage_format


def _worked(producer: str) -> bool:
    try:
        ground_table.family(producer)
    except KeyError:
        return False
    return True


def _recordings() -> dict[str, list[str]]:
    """Each parser's hand-worked recordings whose `call` artifact is committed."""
    found: dict[str, list[str]] = {}
    for producer, (parser, _) in probe_repo.PRODUCERS.items():
        if _worked(producer) and probe_repo.artifact(producer, "call").is_file():
            found.setdefault(parser, []).append(producer)
    return found


def _readers() -> set[str]:
    """The formats counts_table reads: the prefix of each public function's name."""
    return {name.split("_", 1)[0] for name, member in inspect.getmembers(counts_table)
            if inspect.isfunction(member) and not name.startswith("_")
            and member.__module__ == counts_table.__name__}


def _gaps() -> list[str]:
    recordings, readers = _recordings(), _readers()
    formats = sorted(coverage_format._FORMATS)
    return ([f"{key}: no recording ground_table works is read with parser {key!r}"
             for key in formats if key not in recordings]
            + [f"{key}: counts_table has no {key}_* reader" for key in formats if key not in readers])


def test_every_format_has_a_worked_recording_and_a_counts_table_reader():
    gaps = _gaps()
    assert not gaps, "\n".join(gaps)


def test_a_seventh_format_fails_naming_its_key(monkeypatch):
    monkeypatch.setitem(coverage_format._FORMATS, "no-such-format", object())

    with pytest.raises(AssertionError) as failed:
        test_every_format_has_a_worked_recording_and_a_counts_table_reader()

    message = str(failed.value)
    assert "no-such-format: no recording ground_table works is read with parser 'no-such-format'" \
        in message
    assert "no-such-format: counts_table has no no-such-format_* reader" in message
    assert [key for key in probe_repo.TEXT if key in message] == []
