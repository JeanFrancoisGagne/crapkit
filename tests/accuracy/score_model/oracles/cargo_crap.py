"""cargo-crap 0.5.0 as a CRAP oracle: the Rust tool's own score, at the
(ccn, covered, total) inputs crapkit scores.

It imports no crapkit. Each case becomes one Rust function whose cyclomatic
complexity is ccn (ccn - 1 one-line `if`s) and whose body holds at least
`total` lines, and one LCOV DA record for each of `total` body lines, the
first `covered` of them hit. cargo-crap reads both and reports, per function,
the complexity and percent it read and the CRAP it computed from them. The
caller checks the first two equal the case before it reads the third.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil

import hang_guard
from accuracy.kit import oracles

SECONDS = 600


def function_lines(index: int, ccn: int, total: int) -> list[str]:
    """`f<index>`: ccn - 1 `if`s, then filler, so the body has `total` lines or more."""
    ifs = [f"    if x > {k} {{ y += {k}; }}" for k in range(ccn - 1)]
    filler = ["    y += 1;"] * max(0, total - len(ifs))
    return [f"pub fn f{index}(x: i32) -> i32 {{", "    let mut y = 0;", *ifs, *filler, "    y", "}"]


def crate(cases: list[tuple[int, int, int]], root: Path) -> None:
    """src/lib.rs and lcov.info under root, one function per case."""
    source, lcov, line = [], ["SF:src/lib.rs"], 1
    for index, (ccn, covered, total) in enumerate(cases):
        lines = function_lines(index, ccn, total)
        body = range(line + 2, line + 2 + total)
        lcov += [f"DA:{number},{int(k < covered)}" for k, number in enumerate(body)]
        source += lines
        line += len(lines)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "lib.rs").write_text("\n".join(source) + "\n", encoding="utf-8")
    (root / "lcov.info").write_text("\n".join([*lcov, "end_of_record"]) + "\n", encoding="utf-8")


def scores(cases: list[tuple[int, int, int]], root: Path) -> list[dict | None]:
    """cargo-crap's entry (cyclomatic, coverage, crap) for each case, None where it
    reported no function."""
    program = shutil.which("cargo-crap")
    if program is None:
        raise oracles.OracleMissing("cargo-crap is not on PATH")
    crate(cases, root)
    done = hang_guard.run([program, "--path", str(root), "--lcov", str(root / "lcov.info"),
                           "--format", "json", "--no-default-excludes"],
                          timeout=SECONDS, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-2000:]
    found = {entry["function"]: entry for entry in json.loads(done.stdout)["entries"]}
    return [found.get(f"f{index}") for index in range(len(cases))]
