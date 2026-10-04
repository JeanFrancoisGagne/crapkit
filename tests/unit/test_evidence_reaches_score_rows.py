"""A reader that hands per-file evidence and no function records is scored end
to end: its read() fills FileEvidence, run_lane carries it, the lane fold
gathers it per lane, and score_rows joins it to the inventory's spans.

This is the path an lcov reader takes. The adapter here is a fake registered in
coverage_format._FORMATS for one test: it keeps no producer records and fills
hit_lines, so every number the score reads came through the join.
"""
import hashlib
from array import array
from types import SimpleNamespace

from cli_inproc_repo import repo, template_repo  # noqa: F401

import pytest

from crapkit import coverage_format, lanes
from crapkit.cli.scoring import _scored_run
from crapkit.config import load_config_text
from crapkit.score import FileEvidence
from crapkit.uncovered import _artifact_key

ARTIFACT = "coverage/lines.info"
# src/app.ts in the template repo has 18 lines: the inventory spans dispatch over
# lines 1 to 13 and plain over 13 to 18. Line 30 sits in no span.
PLAIN = FileEvidence(hit_lines=array("I", [14, 17]), missed_lines=array("I", [15, 30]),
                     branches=((14, 2, 1),))


def _fake_adapter(evidence: dict, walks: list) -> SimpleNamespace:
    def read(lane, root, artifact, *, unplaced=None):
        walks.append(artifact.name)
        return {}, dict(evidence), hashlib.sha256(artifact.read_bytes()).hexdigest()

    return SimpleNamespace(
        WRONG_TREE_FIX="rerun it here", ABSOLUTE_FIX="write relative paths",
        UNMEASURED_READING="or the suite measured another part of the tree",
        TAKES_PATH_PREFIX=False, read=read,
        missing=lambda lane, root, artifact: {path: set(ev.missed_lines)
                                              for path, ev in evidence.items()},
        contexts=lambda lane, root, artifact, source_path: {})


@pytest.fixture
def fake(repo, monkeypatch):  # noqa: F811
    """The template repo with its `unit` lane read by the fake adapter."""
    walks: list = []
    monkeypatch.setitem(coverage_format._FORMATS, "lines",
                        _fake_adapter({"src/app.ts": PLAIN}, walks))
    (repo / "coverage").mkdir()
    (repo / ARTIFACT).write_text("SF:src/app.ts\nend_of_record\n", encoding="utf-8")
    cfg = load_config_text((repo / "crapkit.toml").read_text(encoding="utf-8"), root=repo)
    lane = cfg.lanes[0]._replace(parser="lines", artifact=ARTIFACT)
    return SimpleNamespace(root=repo, cfg=cfg, lane=lane, walks=walks)


def test_run_lane_carries_the_evidence_the_reader_filled(fake):
    outcome = lanes.run_lane(fake.root, fake.lane, reuse_artifact=True)

    assert outcome.coverage == {}
    assert outcome.evidence == {"src/app.ts": PLAIN}
    assert fake.walks == ["lines.info"]


def test_a_reader_with_no_records_scores_through_the_join(fake, capsys):
    """Worked by hand from PLAIN. plain(x) spans 13-18 with ccn 2 (one `if`).
    Every evidence line from 14 on is inside it and inside no other span, so
    it owns hit lines 14 and 17, missed line 15, and the branch triple on 14;
    line 30 sits in no span and is dropped. Branches decide when there are
    any: 1 of 2, so cov = 0.5, and CRAP = 2^2 * (1 - 0.5)^3 + 2 = 4 * 0.125 + 2
    = 2.5. dispatch owns no evidence line, so the join builds it no record and
    it scores untested: cov 0."""
    run = _scored_run(fake.root, fake.cfg, [fake.lane], reuse_artifacts=True)

    by_name = {row.long_name.split("(")[0].strip(): row for row in run.scored
               if row.path == "src/app.ts"}
    plain, dispatch = by_name["plain"], by_name["dispatch"]
    assert (plain.start, plain.end, dispatch.start) == (13, 18, 1)
    assert (plain.ccn, plain.cov, plain.flag, plain.crap) == (2, 0.5, "measured", 2.5)
    assert (dispatch.cov, dispatch.flag) == (0.0, "untested")
    assert run.lane_errors == {}
    assert fake.walks == ["lines.info"]


def test_the_dead_line_fold_reads_the_evidence_the_same_walk_filled(fake):
    run = _scored_run(fake.root, fake.cfg, [fake.lane], reuse_artifacts=True)

    missing, _ = run.dead_lines.take({_artifact_key(fake.root / ARTIFACT)})

    assert missing == {"src/app.ts": {15, 30}}
