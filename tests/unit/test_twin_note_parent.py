"""The twin-key note is printed by the parent process, never by an analysis worker.

`_reconfigure_streams` makes the CLI's pipes UTF-8 on Windows, where a pipe
otherwise gets the legacy codepage. A `ProcessPoolExecutor` child builds its own
`sys.stderr` over the inherited handle and never sees that reconfigure, so a
note printed from `analyze_one` reached a UTF-8 reader as cp1252 bytes (#31: the
em dash arrived as 0x97 on a stream whose other lines were UTF-8). Workers
return records; the parent is the only process that says anything.
"""
from pathlib import Path

from crapkit import _analysis_pool
from crapkit.analyze import analyze_files, analyze_jobs, analyze_one, analyze_source

TWINS = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
ROOT = Path(__file__).resolve().parents[2]


def _twins_job(tmp_path) -> tuple[str, str]:
    src = tmp_path / "twins.py"
    src.write_text(TWINS, encoding="utf-8")
    return str(src), "twins.py"


def test_a_worker_returns_records_and_prints_nothing(tmp_path, capsys):
    rel_path, records = analyze_one(_twins_job(tmp_path))

    assert (rel_path, len(records)) == ("twins.py", 2)
    assert capsys.readouterr().err == ""


def test_the_parent_prints_the_note_once_per_file_after_collecting(tmp_path, capsys):
    fresh = analyze_jobs([_twins_job(tmp_path)], pool_threshold=10**6)

    assert len(fresh["twins.py"]) == 2
    err = capsys.readouterr().err
    assert err.count("more than once") == 1 and "twins.py" in err, err


def test_the_pooled_path_prints_from_the_parent_too(tmp_path, capsys, monkeypatch):
    """A stand-in pool runs the jobs in-process: the question is which side of
    the pool prints, not whether a child was spawned."""
    built = []
    class InProcessPool:
        def __init__(self, workers=None, worker_budget=0):
            built.append(workers)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, jobs, chunksize=1):
            return [fn(job) for job in jobs]

    monkeypatch.setattr(_analysis_pool, "analysis_pool", InProcessPool)

    plain = tmp_path / "plain.py"
    plain.write_text("def plain():\n    return 1\n", encoding="utf-8")
    fresh = analyze_jobs([_twins_job(tmp_path), (str(plain), "plain.py")],
                         pool_threshold=1, chunksize=1, workers=2)
    assert built == [2]

    assert len(fresh["twins.py"]) == 2
    assert capsys.readouterr().err.count("more than once") == 1


def test_analyze_source_keeps_the_note_because_it_runs_in_the_parent(capsys):
    """The hooks analyze staged blobs and edits through this seam, in-process."""
    records = analyze_source("twins.py", TWINS)

    assert len(records) == 2
    assert "more than once" in capsys.readouterr().err


def _twin_notes(err: str) -> list[str]:
    return [line for line in err.splitlines() if "more than once" in line]


def test_a_batch_names_five_twin_files_and_counts_the_rest(tmp_path, capsys):
    """The first run after an analysis upgrade analyzes every file again. On a
    large consumer repo that printed 1,021 of these notes, uncapped, over the
    lane progress lines; the tokenize-failure list in the same run stops at five."""
    jobs = []
    for n in range(8):
        src = tmp_path / f"twins{n}.py"
        src.write_text(TWINS, encoding="utf-8")
        jobs.append((str(src), f"twins{n}.py"))

    analyze_jobs(jobs, pool_threshold=10**6)

    notes = _twin_notes(capsys.readouterr().err)
    assert [note.split()[1] for note in notes[:5]] == [f"twins{n}.py" for n in range(5)], notes
    assert notes[5:] == ["crapkit: ... and 3 more file(s) define a name more than once"], notes


def test_copies_of_one_analyzed_file_share_the_same_cap(tmp_path, capsys):
    """Files with the same content are analyzed once and noted per path."""
    paths = [f"copy{n}.py" for n in range(7)]
    for path in paths:
        (tmp_path / path).write_text(TWINS, encoding="utf-8")

    analyze_files(tmp_path, paths, cache={})

    notes = _twin_notes(capsys.readouterr().err)
    assert len(notes) == 6 and notes[-1].endswith("and 2 more file(s) define a name more than once"), notes


def test_the_pages_quote_the_count_line_a_batch_prints(tmp_path, capsys):
    jobs = []
    for n in range(6):
        src = tmp_path / f"twins{n}.py"
        src.write_text(TWINS, encoding="utf-8")
        jobs.append((str(src), f"twins{n}.py"))
    analyze_jobs(jobs, pool_threshold=10**6)
    printed = _twin_notes(capsys.readouterr().err)[-1]
    assert " and 1 more file(s) " in printed, printed

    def flat(page: str) -> str:
        return " ".join((ROOT / "docs" / page).read_text(encoding="utf-8").split())

    assert f"`{printed.replace(' 1 more', ' 1016 more')}`" in flat("ratchet.md")
    assert f"`{printed.removeprefix('crapkit: ').replace(' 1 more', ' N more')}`" in flat("upgrading.md")
