"""Structural pattern matching (Python 3.10): a match with guarded and class patterns."""


def describe(value):
    match value:
        case None:
            return "nothing"
        case int() | float() if value < 0:
            return "negative"
        case [first, *rest]:
            return f"list of {1 + len(rest)} from {first}"
        case {"kind": kind}:
            return f"a {kind}"
        case _:
            return "something"
