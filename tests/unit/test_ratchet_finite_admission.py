"""A malformed numeric mark cannot excuse debt or enter a merged ratchet."""
import subprocess
import sys

import pytest

from crapkit.ratchet import RatchetEntry, load_ratchet, read_ratchet
from crapkit.score import ScoredRow
from crapkit.verify import evaluate


NONFINITE = ("nan", "NaN", "inf", "+Infinity", "-Infinity", "1e999")


@pytest.mark.parametrize("value", NONFINITE)
def test_nonfinite_marks_are_reported_and_cannot_excuse_changed_debt(value):
    text = f"path\tlong_name\tcrap\napp.ts\tg( )\t12\napp.ts\tf( )\t{value}\n"
    marks, complaints = read_ratchet(text)
    assert marks == [RatchetEntry("app.ts", "g( )", 12.0)]
    assert len(complaints) == 1 and "line 3" in complaints[0]
    row = ScoredRow("web", "app.ts", "f( )", 20, 20, 9, 9, 9, 1, 0, 0,
                    0.0, "untested", 90.0, "decompose")
    result = evaluate(fresh=[row], changed_ranges={"app.ts": [(20, 20)]}, ratchet=marks,
                      baseline_failures=set(), fresh_failures=set(), target=6)
    assert not result.ok
    with pytest.raises(ValueError, match="ratchet line 3"):
        load_ratchet(text)


@pytest.mark.parametrize("value", ["0", "-1", "6.125", "1e3"])
def test_existing_finite_mark_representations_still_load(value):
    assert load_ratchet(f"app.ts\tf( )\t{value}\n")[0].crap == float(value)


@pytest.mark.parametrize("value", NONFINITE)
def test_merge_command_refuses_nonfinite_input_without_rewriting_ours(tmp_path, value):
    paths = [tmp_path / name for name in ("base.tsv", "ours.tsv", "theirs.tsv")]
    for path, mark in zip(paths, ("50", "60", value)):
        path.write_text(f"app.ts\tf( )\t{mark}\n", encoding="utf-8")
    before = paths[1].read_bytes()
    result = subprocess.run([sys.executable, "-B", "-m", "crapkit", "ratchet", "merge",
                             *(str(path) for path in paths)], cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 3
    assert "unreadable mark" in result.stderr and "theirs.tsv" in result.stderr
    assert paths[1].read_bytes() == before
