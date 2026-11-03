"""Each coverage reader's number in the scope stamps a run records, and
the cc-only to measured flip a new lcov, Cobertura or JaCoCo lane brings.

Parametrized by format, so each reader that lands adds its row to READERS.
"""
import pytest

from crapkit import coverage_format
from crapkit.ratchet import ScopeStamp, stamp_conflict

from stamps import MARKS, lane, running, scope

# format -> (a language that format measures, a scope language with no reader)
READERS = {"lcov": ("rust", "go"), "cobertura": ("cpp", "go"), "jacoco": ("java", "go")}


@pytest.mark.parametrize("fmt", sorted(READERS))
def test_a_scope_a_reader_measures_records_its_mode_at_its_number(fmt):
    language, _ = READERS[fmt]

    now = running([scope("core", language)], [lane("core", fmt, "core")])

    assert now.scopes["core"] == ScopeStamp({language: 13}, {fmt: 1})
    assert coverage_format.READER_VERSIONS[fmt] == coverage_format.FIRST_READER_VERSION


@pytest.mark.parametrize("fmt", sorted(READERS))
def test_raising_a_readers_number_moves_only_the_scopes_it_measures(fmt, monkeypatch):
    language, _ = READERS[fmt]
    scopes = [scope("core", language), scope("api", "python")]
    lanes = [lane("core", fmt, "core"), lane("api", "coveragepy", "api")]
    before = running(scopes, lanes)

    monkeypatch.setitem(coverage_format.READER_VERSIONS, fmt, 2)
    after = running(scopes, lanes)

    conflict = stamp_conflict(before, after, frozenset({"core", "api"}))
    assert [move.scope for move in conflict.moved] == ["core"]
    assert after.scopes["api"] == before.scopes["api"]


def test_a_python_only_repo_s_stamp_does_not_move_with_the_lcov_reader():
    scopes, lanes = [scope("api", "python")], [lane("api", "coveragepy", "api")]

    assert running(scopes, lanes).scopes["api"] == ScopeStamp({"python": 13}, {"coveragepy": 1})


def test_a_js_scope_moving_from_istanbul_to_lcov_gets_a_coverage_conflict_naming_it():
    scopes = [scope("web", "typescript"), scope("api", "python")]
    before = running(scopes, [lane("web", "istanbul", "web"), lane("api", "coveragepy", "api")])
    after = running(scopes, [lane("web", "lcov", "web"), lane("api", "coveragepy", "api")])

    conflict = stamp_conflict(before, after, frozenset({"web", "api"}))

    assert [(move.scope, move.kind) for move in conflict.moved] == [("web", "coverage")]
    assert "web" in conflict.refusal(MARKS)


def test_a_cc_only_rust_scope_that_gains_an_lcov_lane_gets_the_flip_conflict():
    """One conflict naming the scope and its scoped reseed, not one exit 7
    per mark."""
    before = running([scope("core", "rust", optional=True)], [])
    after = running([scope("core", "rust")], [lane("core", "lcov", "core")])

    conflict = stamp_conflict(before, after, frozenset({"core"}))
    refusal = conflict.refusal(MARKS)

    assert [move.scope for move in conflict.moved] == ["core"]
    assert "core was cc-only and is now measured by its lcov lane(s)" in refusal
    assert "ratchet seed --scope core" in refusal


def test_a_cc_only_cpp_scope_that_gains_a_cobertura_lane_gets_the_flip_conflict():
    before = running([scope("core", "cpp", optional=True)], [])
    after = running([scope("core", "cpp")], [lane("core", "cobertura", "core")])

    conflict = stamp_conflict(before, after, frozenset({"core"}))
    refusal = conflict.refusal(MARKS)

    assert [move.scope for move in conflict.moved] == ["core"]
    assert "core was cc-only and is now measured by its cobertura lane(s)" in refusal
    assert "ratchet seed --scope core" in refusal


def test_a_cc_only_java_scope_that_gains_a_jacoco_lane_gets_the_flip_conflict():
    before = running([scope("core", "java", optional=True)], [])
    after = running([scope("core", "java")], [lane("core", "jacoco", "core")])

    conflict = stamp_conflict(before, after, frozenset({"core"}))
    refusal = conflict.refusal(MARKS)

    assert [move.scope for move in conflict.moved] == ["core"]
    assert "core was cc-only and is now measured by its jacoco lane(s)" in refusal
    assert "ratchet seed --scope core" in refusal
