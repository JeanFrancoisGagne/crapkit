"""SwiftLint 0.65.1 cyclomatic_complexity, read per function.

SwiftLint counts one per if, guard, for, while, repeat, catch and switch case
(default included), minus one per fallthrough, from 0 rather than McCabe's 1.
It counts no `&&`, `||`, `?:` or `??`. With the warning level at 0 it reports
every function whose count is 1 or more, at the line where the declaration
starts; a function it leaves out counted 0.

complexity() takes a directory, the file paths under it and the function spans
tree-sitter lists ({(path, start): end}), starts one SwiftLint process for the
whole list with the JSON reporter, and answers {(path, start): count} for every
span. The transforms from SwiftLint's count to crapkit's documented one live in
test_swift_oracles.py, one rulings row each. No crapkit import.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import hang_guard

REASON = re.compile(r"currently complexity is (\d+)")
CONFIG = ("only_rules:\n  - cyclomatic_complexity\ncyclomatic_complexity:\n"
          "  warning: 0\n  error: 100000\n")


def reports(root: Path, paths: list) -> list:
    config = root / "swiftlint-every-function.yml"
    config.write_text(CONFIG, encoding="utf-8")
    done = hang_guard.run(["swiftlint", "lint", "--quiet", "--no-cache", "--config", str(config),
                           "--reporter", "json", *paths], cwd=root, text=True, encoding="utf-8",
                          errors="replace")
    assert done.stdout.strip().startswith("["), done.stdout + done.stderr
    return json.loads(done.stdout)


def complexity(root: Path, paths: list, spans: dict) -> dict:
    found = dict.fromkeys(spans, 0)
    for report in reports(root, paths):
        path = Path(report["file"]).resolve().relative_to(root.resolve()).as_posix()
        found[(path, report["line"])] = int(REASON.search(report["reason"])[1])
    return found
