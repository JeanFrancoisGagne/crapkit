"""The stamp file read once into explicit states, and the refusal query every reader asks.

Four modules indexed .crapkit/artifacts.json by hand and read an unreadable
file, a mangled entry and a missing entry alike as "no stamp". lane_stamps
answers each shape with a named state, and the refusal query answers for each:
the file on disk is a failed attempt's leftover, is not, or crapkit cannot tell.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crapkit.lane_stamps import (ABSENT, LEGACY, MANGLED, RECORDED, UNREADABLE, file_sha256,
                                 read, write)

ART = "cov/a.json"


def _stamps_file(root: Path, text: str) -> None:
    (root / ".crapkit").mkdir(parents=True, exist_ok=True)
    (root / ".crapkit" / "artifacts.json").write_text(text, encoding="utf-8")


def _artifact(root: Path, text: str = "{}") -> str:
    path = root / ART
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return file_sha256(path)


# (stamp file text or None for no file, the state of ART, why it cannot be read)
SHAPES = {
    "no-file": (None, ABSENT, ""),
    "no-entry": (json.dumps({"other.json": {"commit": "c"}}), ABSENT, ""),
    "cut-short": ('{"cov/a.json": {"commit"', UNREADABLE, "it does not parse as JSON"),
    "top-level-list": ("[1]", UNREADABLE, "its top level is not an object"),
    "not-utf8": (None, UNREADABLE, "it does not parse as JSON"),
    "mangled-entry": (json.dumps({ART: "garbage"}), MANGLED,
                      "its entry for cov/a.json is not an object"),
    "legacy-entry": (json.dumps({ART: {"commit": "c", "lane": "a"}}), LEGACY, ""),
    "recorded-entry": (json.dumps({ART: {"commit": "c", "blobs": {"src/a.ts": "b" * 40}}}),
                       RECORDED, ""),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_shape_of_the_file_reads_as_one_named_state(shape, tmp_path):
    text, state, why = SHAPES[shape]
    if text is not None:
        _stamps_file(tmp_path, text)
    if shape == "not-utf8":
        (tmp_path / ".crapkit").mkdir()
        (tmp_path / ".crapkit" / "artifacts.json").write_bytes(b'{"\xff": 1}')

    stamps = read(tmp_path)

    assert stamps.state(ART) == state
    assert stamps.why_unreadable(ART) == why
    assert stamps.commit(ART) == ("c" if state in (LEGACY, RECORDED) else "")
    assert (stamps.blobs(ART) is not None) == (state == RECORDED)


# (stamp file text, refusal kind for the file on disk)
REFUSALS = {
    "no-file": (None, ""),
    "cut-short": ('{"cov/a.json": {"commit"', "unknown"),
    "mangled-entry": (json.dumps({ART: "garbage"}), "unknown"),
    "legacy-entry": (json.dumps({ART: {"commit": "c"}}), ""),
    "refused-by-sha256": (None, "leftover"),
    "refused-other-bytes": (json.dumps({ART: {"refused_sha256": "0" * 64}}), ""),
}


@pytest.mark.parametrize("shape", sorted(REFUSALS))
def test_the_refusal_query_answers_for_every_state(shape, tmp_path):
    digest = _artifact(tmp_path)
    text, kind = REFUSALS[shape]
    if shape == "refused-by-sha256":
        text = json.dumps({ART: {"lane": "a", "refused_sha256": digest}})
    if text is not None:
        _stamps_file(tmp_path, text)

    assert read(tmp_path).refusal(ART).kind == kind


def test_no_file_on_disk_is_never_refused(tmp_path):
    _stamps_file(tmp_path, json.dumps({ART: {"refused_sha256": "0" * 64}}))

    assert read(tmp_path).refusal(ART).kind == ""


def test_the_store_answers_when_the_stamps_file_is_gone_or_unreadable(tmp_path):
    digest = _artifact(tmp_path)
    write(tmp_path, {ART: {"lane": "a", "refused_sha256": digest}})

    for text in (None, "not json"):
        if text is None:
            (tmp_path / ".crapkit" / "artifacts.json").unlink()
        else:
            _stamps_file(tmp_path, text)
        assert read(tmp_path).refusal(ART).kind == "leftover", text


def test_a_fresh_stamp_clears_the_store_s_refusal(tmp_path):
    digest = _artifact(tmp_path)
    write(tmp_path, {ART: {"lane": "a", "refused_sha256": digest}})
    write(tmp_path, {ART: {"lane": "a", "commit": "c"}})
    (tmp_path / ".crapkit" / "artifacts.json").unlink()

    assert read(tmp_path).refusal(ART).kind == ""


def test_a_write_with_no_refusal_creates_no_store(tmp_path):
    write(tmp_path, {ART: {"lane": "a", "commit": "c"}})

    assert not (tmp_path / ".crapkit" / "crap.sqlite").exists()


def test_a_write_over_an_unreadable_file_keeps_only_what_it_can_read(tmp_path):
    _stamps_file(tmp_path, "{cut")

    write(tmp_path, {ART: {"lane": "a", "commit": "c"}})

    assert read(tmp_path).raw == {ART: {"lane": "a", "commit": "c"}}


def test_byproducts_are_read_per_artifact_and_across_every_stamp(tmp_path):
    _stamps_file(tmp_path, json.dumps({ART: {"byproducts": [".coverage", 3]},
                                       "b.json": {"byproducts": ["src/__pycache__/x.pyc"]},
                                       "c.json": "garbage"}))
    stamps = read(tmp_path)

    assert stamps.byproducts(ART) == {".coverage"}
    assert stamps.byproducts() == {".coverage", "src/__pycache__/x.pyc"}
    assert stamps.mangled() == ["c.json"]


# (stamp file text, the cause the refusal gives for the file on disk)
CAUSES = {
    "not-refused": (json.dumps({ART: {"commit": "c"}}), ""),
    "leftover": (None, "its last attempt wrote no artifact, and the cov/a.json on disk predates it"),
    "unknown": ('{"cov/a.json": {"commit"',
                ".crapkit/artifacts.json cannot be read (it does not parse as JSON), so crapkit "
                "cannot tell whether the cov/a.json on disk is the file a failed attempt left"),
}


@pytest.mark.parametrize("shape", sorted(CAUSES))
def test_each_refusal_names_its_cause_in_one_sentence(shape, tmp_path):
    """doctor's lane refusal, the dark-line note and reuse's refusal of an
    unreadable record each built this sentence, twice in two wordings."""
    digest = _artifact(tmp_path)
    text, cause = CAUSES[shape]
    _stamps_file(tmp_path, text if text is not None else json.dumps({ART: {"refused_sha256": digest}}))

    assert read(tmp_path).refusal(ART).cause(ART) == cause


SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"


@pytest.mark.parametrize("sentence", [
    "wrote no artifact, and the",
    "is the file a failed attempt left",
    '".crapkit/artifacts.json"',
])
def test_only_the_stamp_module_says_what_the_stamp_file_holds(sentence):
    """doctor parsed .crapkit/artifacts.json a second time under a constant of
    its own, beside the unreadable state lane_stamps.read gives every reader."""
    homes = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py")
                   if sentence in path.read_text(encoding="utf-8"))

    assert homes == ["lane_stamps.py"]
