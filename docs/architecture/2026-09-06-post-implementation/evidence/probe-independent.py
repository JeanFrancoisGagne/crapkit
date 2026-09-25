"""Independent root replay of the deterministic analysis and state interleavings."""
import json
from pathlib import Path
import runpy
import tempfile

here = Path(__file__).resolve().parent
analysis = runpy.run_path(str(here / "analysis-probe.py"), run_name="review_analysis")
state = runpy.run_path(str(here / "state-probes.py"), run_name="review_state")
result = {"analysis_cache": analysis["cache_race"]()}
assert result["analysis_cache"]["wrong_metrics"] is True
assert result["analysis_cache"]["second_cache_hits"] == 1
with tempfile.TemporaryDirectory(prefix="crapkit-review-independent-") as temp:
    root = Path(temp)
    result["ratchet_writers"] = state["stale_ratchet_write"](root)
    result["trend_snapshot"] = state["mixed_trend_read"](root)
assert [r["disk_mark"] for r in result["ratchet_writers"]] == [10, 20]
latest = result["trend_snapshot"]["runs"][-1]
assert latest["functions"] == 0 and latest["by_scope"]["src"]["functions"] == 1
(here / "root-evidence" / "independent-replay.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print("Confirmed wrong cache hit, non-monotone concurrent ratchet writes, and mixed trend snapshot.")
