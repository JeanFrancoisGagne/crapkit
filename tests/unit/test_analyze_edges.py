"""analyze at its edges: the cache and stamp files, the pool knobs and the words of each refusal.

A cached row is a list of exactly the record's field count, each field of its
type, and each refusal says which rule it broke. A stamp is written once a file
has held still for the settle time, to the nanosecond, into a directory made on
the way, with its keys sorted. The pool starts only past the threshold, and the
worker count rounds the source bytes up to whole workers. Every knob a caller
hands analyze_files reaches the jobs it runs.
"""
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import analyze, resources
from crapkit.merge import FunctionRecord, UnanalyzableFile

VALUES = ["a.c", "f", 1, 3, 2, 2, 2, 3, 0, 0, 0, 0, 0]


def refusal(call) -> str:
    with pytest.raises(ValueError) as caught:
        call()
    return str(caught.value)


def key(extension) -> tuple:
    return (getattr(extension, "__module__", "?"), getattr(extension, "__name__", type(extension).__name__))


def test_every_cached_row_refusal_names_the_rule_it_broke():
    assert [refusal(call) for call in (
        lambda: analyze._cached_record(tuple(VALUES)),
        lambda: analyze._cached_record(VALUES[:2] + ["1"] + VALUES[3:]),
        lambda: analyze._cached_rows({}),
        lambda: analyze._drained_records([]),
        lambda: analyze._cached_record(VALUES[:11] + [-1, 0]),
        lambda: analyze._cached_record(VALUES[:12] + [2]),
    )] == ["cached function fields must be a record list",
           "cached function fields have invalid types",
           "cached functions must be a list",
           "cached entries must be an object",
           "cached function occurrence must be nonnegative",
           "cached function inline_body must be 0 or 1"]


def test_a_cache_file_is_read_as_utf8_and_one_with_no_entries_holds_none(tmp_path):
    path = tmp_path / "cache.json"
    path.write_bytes('{"fp": "é"}'.encode("utf-8"))

    assert analyze.load_cache(path) == {"fp": "é", "entries": {}}


def test_a_cache_is_written_into_a_directory_made_on_the_way(tmp_path):
    path = tmp_path / "a" / "b" / "cache.json"

    analyze.save_cache(path, {"fp": "f", "entries": {}})

    assert path.read_text(encoding="utf-8") == '{"entries": {}, "fp": "f"}'


def test_stamps_are_written_sorted_into_a_directory_made_on_the_way_or_already_there(tmp_path):
    stamps = {"x.c": [1, 2, "h"], "a.c": [3, 4, "g"]}
    first, second = tmp_path / "a" / "b" / "s.json", tmp_path / "a" / "b" / "t.json"

    analyze._save_stamps(first, stamps, {})
    analyze._save_stamps(second, stamps, {})

    expected = '{"stamps": {"a.c": [3, 4, "g"], "x.c": [1, 2, "h"]}, "v": 1}'
    assert (first.read_text(encoding="utf-8"), second.read_text(encoding="utf-8")) == (expected, expected)


def test_a_stamp_file_of_another_version_is_no_index_and_one_of_this_version_reads_as_utf8(tmp_path):
    other, this = tmp_path / "other.json", tmp_path / "this.json"
    other.write_text('{"v": 2, "stamps": {"a.c": [1, 2, "h"]}}', encoding="utf-8")
    this.write_bytes('{"v": 1, "stamps": {"é.c": [1, 2, "h"]}}'.encode("utf-8"))

    assert analyze._load_stamps(other) == {}
    assert analyze._load_stamps(this) == {"é.c": [1, 2, "h"]}


def test_a_stamp_is_three_fields_an_int_an_int_and_a_string():
    stamps = ((1, 2, "h"), [1, 2, 3], ["x", 2, "h"], [1, "x", "h"], [1, 2, "h"])

    assert [analyze._valid_stamp(stamp) for stamp in stamps] == [False, False, False, False, True]


def test_a_file_still_exactly_the_settle_time_is_stamped():
    fresh = {}

    analyze._stamp(fresh, "a.c", (0, 5), "h", analyze._STAMP_SETTLE_NS)

    assert fresh == {"a.c": [0, 5, "h"]}


def test_a_visited_path_keeps_only_this_runs_stamp():
    assert analyze._kept_stamps({"a": 1, "c": 2}, {"b": 3}, {"b", "c"}) == {"a": 1, "b": 3}


def test_a_file_that_moved_while_it_was_hashed_gets_no_stamp(tmp_path, monkeypatch):
    path = tmp_path / "a.c"
    path.write_bytes(b"x")
    stats = iter([(1, 1), (2, 1)])
    monkeypatch.setattr(analyze, "_stat_of", lambda path: next(stats))

    assert analyze._path_hash(path, None) == (hashlib.sha256(b"x").hexdigest(), None)


def test_a_missing_file_weighs_nothing(tmp_path):
    assert analyze._job_size(str(tmp_path / "gone.c")) == 0


def test_a_batch_under_the_threshold_runs_in_process_and_a_zero_chunk_is_refused():
    with analyze._pool_for([0, 0, 0], 16, 2, 0, 1) as pool:
        assert pool is None

    assert refusal(lambda: analyze._pool_for([0] * 20, 16, 2, 0, 0)) == "chunksize must be >=1."


def test_the_worker_count_rounds_source_bytes_up_to_whole_workers(monkeypatch):
    per_worker = resources.DEFAULT_SOURCE_BYTES_PER_WORKER
    monkeypatch.setattr(resources, "default_chunks_per_worker", lambda: 2)
    counts = []
    for work in (3 * per_worker, 3 * per_worker + 1, 3 * per_worker + 2):
        monkeypatch.setattr(analyze, "_source_bytes", lambda jobs, work=work: work)
        counts.append(analyze._requested_workers(None, 6, []))

    assert counts == [3, 4, 4]


def test_analyze_jobs_maps_through_the_pool_in_chunks_of_32_with_no_budget(monkeypatch):
    budgets, chunks = [], []

    class Pool:
        def map(self, worker, inputs, **kwargs):
            chunks.append(kwargs)
            return map(worker, inputs)

    def pool_for(jobs, threshold, workers, budget, chunksize):
        budgets.append(budget)
        return nullcontext(Pool())

    monkeypatch.setattr(analyze, "_pool_for", pool_for)

    assert analyze.analyze_jobs([]) == {}
    assert (budgets, chunks) == ([0], [{"chunksize": 32}])


def test_the_misses_are_parsed_with_the_callers_knobs_and_no_notes(monkeypatch):
    calls = []
    monkeypatch.setattr(analyze, "analyze_jobs", lambda jobs, **kwargs: calls.append(kwargs) or {})

    assert analyze._analyze_misses(Path("r"), [], {}, {"h": "1"}, 3, 7) == {}
    assert calls == [{"workers": 3, "hashes": {"h": "1"}, "worker_budget": 7, "notes": False}] * 2


def test_analyze_files_hands_its_knobs_down_and_its_prior_stamps_back(tmp_path, monkeypatch):
    saved, missed = [], []
    monkeypatch.setattr(analyze, "_save_stamps", lambda path, stamps, prior: saved.append((stamps, prior)))
    monkeypatch.setattr(analyze, "_analyze_misses", lambda *args: missed.append(args[4:]) or {})

    analyze.analyze_files(tmp_path, [], cache={}, workers=3)
    analyze.analyze_files(tmp_path, [], cache={})

    assert saved == [({}, {}), ({}, {})]
    assert missed == [(3, 0), (None, 0)]


def test_five_names_are_listed_whole_and_more_are_counted():
    assert analyze._listed(list("abcde")) == "a, b, c, d, e"
    assert analyze._listed(list("abcdefg")) == "a, b, c, d, e and 2 more name(s)"


def test_exactly_five_noted_files_print_no_count_line(capsys):
    twins = [FunctionRecord(*VALUES), FunctionRecord(*VALUES)]
    refused = {f"r{index}.c": UnanalyzableFile(f"why {index}") for index in range(5)}

    analyze._note_twin_files({f"t{index}.c": twins for index in range(5)})
    analyze._note_unanalyzable(refused)

    err = capsys.readouterr().err
    assert "more file(s)" not in err
    assert err.endswith("crapkit: 5 file(s) could not be tokenized; each is scored as zero functions "
                        "and stays unranked:\n" + "".join(f"crapkit:   why {i}\n" for i in range(5)))


def test_a_function_lizard_gave_no_extra_attributes_reads_zero_for_each():
    """nesting is the cognitive pass's depth in every language, so a Python path
    and a C path read the same 0 when the pass left no attribute."""
    fn = SimpleNamespace(cyclomatic_complexity=2, long_name="f", start_line=1, end_line=3, nloc=3,
                         parameters=[])

    assert analyze._record("a.py", fn) == FunctionRecord("a.py", *VALUES[1:])
    assert analyze._record("a.c", fn) == FunctionRecord(*VALUES)


def test_the_unread_defs_are_named_comma_separated():
    unread = [SimpleNamespace(start_line=1, long_name="f"), SimpleNamespace(start_line=4, long_name="g")]

    assert "no body for 2 def(s): a.py:1 f, a.py:4 g; " in analyze._unread_reason("a.py", unread)


def test_a_lone_cr_ends_a_line():
    assert analyze.decode_source(b"a\rb\r\nc") == "a\nb\nc"


def test_a_path_no_reader_claims_is_keyed_under_the_c_reader():
    """lizard answers a suffix no reader declares with its CLikeReader, which
    lizardclike.register() rebinds to crapkit's own C-family reader."""
    assert analyze._analysis_key("notes.txt", "d").startswith("crapkit.lizardclike.CLikeReader:")


def test_the_decoder_rebinds_lizards_read_and_refuses_a_lizard_without_one(monkeypatch):
    monkeypatch.setattr(analyze.lizard, "auto_read", None)
    analyze._install_decoder()
    bound = analyze.lizard.auto_read
    monkeypatch.delattr(analyze.lizard, "auto_read")

    with pytest.raises(RuntimeError) as caught:
        analyze._install_decoder()

    assert bound is analyze.read_source
    assert str(caught.value) == (
        f"crapkit.analyze._install_decoder() found no lizard.auto_read to rebind: lizard "
        f"{analyze.lizard.version} reads source some other way. Rewrite this against the new "
        f"mechanism; leaving it undone makes every non-ASCII file score by the machine's locale.")


def test_a_chain_built_again_matches_the_one_built_at_import():
    assert [key(extension) for extension in analyze._chain(0)] == [key(e) for e in analyze._EXTENSIONS]
    assert [key(extension) for extension in analyze._chain(1)] == [
        key(e) for e in analyze._PREPROCESSED_EXTENSIONS]


def test_the_creation_order_hands_the_context_its_own_hook_back():
    context = SimpleNamespace(current_function=None)

    def start(name):
        context.current_function = SimpleNamespace(name=name)

    context.try_new_function = start

    def tokens():
        context.try_new_function("f")
        yield "a"

    assert list(analyze._CreationOrder()(tokens(), SimpleNamespace(context=context))) == ["a"]
    assert context.try_new_function is start
