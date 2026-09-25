"""Drive shapes.py for one scenario: `call` makes exactly the calls ground_truth.tsv's
arms_taken column is worked from; `idle` only imports the module."""
import sys

import shapes


def call():
    shapes.if_else(True)
    shapes.if_no_else(-1)
    shapes.for_loop([1, 2])
    shapes.while_loop(0)
    shapes.match_case("go")
    shapes.match_case("halt")
    shapes.and_or(1, 0)
    shapes.comprehension_if([1, -1])
    try:
        shapes.branchless([])
    except ValueError:
        pass
    shapes.outer(False)
    shapes.run_async(True)
    shapes.Box().method(5)
    shapes.one_line(1)
    shapes.body_on_signature(1, 2)
    shapes.excluded(1)
    shapes.exclude_also(3)


if __name__ == "__main__":
    if sys.argv[1:] == ["call"]:
        call()
