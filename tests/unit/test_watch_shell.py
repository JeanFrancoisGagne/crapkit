"""The watch command's shell: what one poll rescores, what the banner promises,
and how the loop is bounded. The mtime diffing behind it is pure and lives in
test_contexts_and_watch.py.

subprocess.run is recorded rather than run: the argv IS the contract here (the
rescore has to leave the watcher's own process, so a half-saved syntax error
cannot take the loop down with it), and a real child would only re-prove what
tests/e2e/test_watch_cycles_e2e.py proves against a real repo.
"""
import subprocess
import sys

from crapkit.cli import _watch_banner, _watch_cycles, _watch_rescore


def _recorded(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv))
    return calls


def test_a_poll_whose_every_moved_path_is_gone_rescores_nothing(tmp_path, capsys, monkeypatch):
    """Deleting a file moves it too, and there is no blob left to hand lizard."""
    calls = _recorded(monkeypatch)

    _watch_rescore(tmp_path, ["deleted.py"])

    assert calls == []
    assert capsys.readouterr().out == "", "nothing rescored, nothing announced"


def test_a_poll_names_every_move_but_rescores_only_the_survivors(tmp_path, capsys, monkeypatch):
    (tmp_path / "kept.py").write_text("x = 1\n", encoding="utf-8")
    calls = _recorded(monkeypatch)

    _watch_rescore(tmp_path, ["deleted.py", "kept.py"])

    assert capsys.readouterr().out == "--- changed: deleted.py, kept.py\n", \
        "the line reports the change; the rescore is what has to skip the corpse"
    assert calls == [[sys.executable, "-m", "crapkit", "rescore", "kept.py",
                      "--repo", str(tmp_path)]]


def test_an_unbounded_watch_never_runs_out_of_polls():
    cycles = _watch_cycles(None)

    assert [next(cycles) for _ in range(3)] == [0, 1, 2]


def test_a_bounded_watch_yields_exactly_the_polls_it_was_given():
    assert list(_watch_cycles(2)) == [0, 1]
    assert list(_watch_cycles(0)) == [], "zero polls is a legal, immediate exit"


def test_the_banner_of_an_unbounded_run_says_how_to_stop_it():
    assert _watch_banner(12, 2.0, None) == "watching 12 tracked files every 2.0s — ctrl-c to stop"


def test_the_banner_of_a_bounded_run_names_its_own_end_instead():
    assert _watch_banner(12, 0.5, 3) == \
        "watching 12 tracked files every 0.5s — 3 poll(s) then stop"
