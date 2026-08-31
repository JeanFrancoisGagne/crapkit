"""The twin-key note is printed by the parent process, never by an analysis worker.

`_reconfigure_streams` makes the CLI's pipes UTF-8 on Windows, where a pipe
otherwise gets the legacy codepage. A `ProcessPoolExecutor` child builds its own
`sys.stderr` over the inherited handle and never sees that reconfigure, so a
note printed from `analyze_one` reached a UTF-8 reader as cp1252 bytes (#31: the
em dash arrived as 0x97 on a stream whose other lines were UTF-8). Workers
return records; the parent is the only process that says anything.
"""
import crapkit.analyze as analyze_module
from crapkit.analyze import analyze_jobs, analyze_one, analyze_source

TWINS = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"


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
    class InProcessPool:
        def __init__(self, max_workers=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, jobs, chunksize=1):
            return [fn(job) for job in jobs]

    monkeypatch.setattr(analyze_module, "ProcessPoolExecutor", InProcessPool)

    fresh = analyze_jobs([_twins_job(tmp_path)], pool_threshold=1)

    assert len(fresh["twins.py"]) == 2
    assert capsys.readouterr().err.count("more than once") == 1


def test_analyze_source_keeps_the_note_because_it_runs_in_the_parent(capsys):
    """The hooks analyze staged blobs and edits through this seam, in-process."""
    records = analyze_source("twins.py", TWINS)

    assert len(records) == 2
    assert "more than once" in capsys.readouterr().err
