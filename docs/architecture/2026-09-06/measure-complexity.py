"""Check every changed production function against the fixed review baseline."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
BASE = "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6"
sys.path.insert(0, str(ROOT / "src"))
from crapkit.analyze import analyze_source, decode_source
from crapkit.diffparse import changed_ranges


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


diff = git("diff", "--no-ext-diff", "--unified=0", BASE, "--", "src", "tools")
ranges = changed_ranges(diff.decode("utf-8"))
new = git("ls-files", "--others", "--exclude-standard", "-z", "--", "src", "tools")
for name in new.decode("utf-8").split("\0"):
    if name:
        ranges[name] = [(1, sys.maxsize)]

rows = []
for name, spans in sorted(ranges.items()):
    path = ROOT / name
    if not path.is_file() or path.suffix != ".py":
        continue
    for row in analyze_source(name, decode_source(path.read_bytes())):
        if any(row.start <= end and row.end >= start for start, end in spans):
            rows.append({"path": name, "name": row.long_name, "start": row.start,
                         "end": row.end, "ccn": row.ccn})

violations = [row for row in rows if row["ccn"] > 6]
result = {"baseline": BASE, "ceiling": 6, "changed_functions": len(rows),
          "violations": violations, "functions": rows}
(Path(__file__).parent / "complexity-measurements.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({key: value for key, value in result.items() if key != "functions"}, indent=2))
raise SystemExit(bool(violations))
