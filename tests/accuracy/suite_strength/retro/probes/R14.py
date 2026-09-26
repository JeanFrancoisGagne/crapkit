"""R14: crapkit read a Rust `match` as one decision, so a seven-arm dispatch
scored ccn 2 where the if/else-if chain doing the same work scores 8.

    <retro venv python> R14.py WORKTREE

analysis_oracles' check measures Rust through `crapkit inventory`, and neither
commit reads Rust through the CLI: Rust joined the language table later, on the
corrected reader. This probe asks the reader question below the CLI. It measures
a seven-arm match and the equivalent seven-if chain with lizard, the analyzer
crapkit runs, after registering the Rust reader the commit ships
(crapkit.lizardrust.register(); a commit that ships none leaves lizard's own
reader in place, which is what crapkit read Rust with there). The chain's score
is lizard's stock count, so the expected value comes from outside the fix.
"""
# source: NIST SP 500-235 sec. 4.1: seven arms plus the `_` default are eight paths, as seven ifs chained by else are; lizard 1.24.0 scores the chain 8 with its stock Rust reader; lizard issue #494 reports the stock reader counting match arms zero times
from __future__ import annotations

import importlib
import sys

CODE = """pub fn seven_arms(a: i32) -> i32 {
    match a {
        1 => 10,
        2 => 20,
        3 => 30,
        4 => 40,
        5 => 50,
        6 => 60,
        7 => 70,
        _ => 0,
    }
}

pub fn seven_ifs(a: i32) -> i32 {
    if a == 1 {
        10
    } else if a == 2 {
        20
    } else if a == 3 {
        30
    } else if a == 4 {
        40
    } else if a == 5 {
        50
    } else if a == 6 {
        60
    } else if a == 7 {
        70
    } else {
        0
    }
}
"""
PATHS = 8


def _register_the_commit_s_reader() -> None:
    """crapkit's corrected Rust reader, when this commit ships one."""
    try:
        lizardrust = importlib.import_module("crapkit.lizardrust")
    except ModuleNotFoundError:
        return
    lizardrust.register()


def _scores() -> dict[str, int]:
    import lizard
    info = lizard.analyze_file.analyze_source_code("equivalence.rs", CODE)
    return {fn.name: fn.cyclomatic_complexity for fn in info.function_list}


def main(argv: list[str]) -> int:
    _register_the_commit_s_reader()
    scores = _scores()
    if scores.get("seven_ifs") != PATHS:
        raise RuntimeError(f"lizard scored the if chain {scores}, so the oracle does not hold")
    assert scores["seven_arms"] == PATHS, f"seven match arms scored {scores['seven_arms']}, expected {PATHS}"
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
