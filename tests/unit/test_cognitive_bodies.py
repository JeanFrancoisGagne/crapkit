"""Where a structure's body starts and what it nests, with or without braces.

Sonar Cognitive Complexity v1.7 (App. B1-B3) charges an else-if link a flat +1
and raises the nesting level for everything inside an if, an else or a loop.
Zig puts a payload between an `else` and the `if` it continues, `else |err|
if (...)`, and the pass decided at the payload's first `|` that the `else` was
a plain one: the `if` after it paid +1 of its own and the chain cost one more
than the same chain with no payload.
"""
import pytest

from crapkit.analyze import analyze_source

ZIG_ELSE_CHAINS = [  # (label, source, Sonar value)
    ("a payload else before an if with no braces",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n"
     "    } else |err| if (err != error.Boom) return 1;\n    return 0;\n}\n", 2),
    ("a payload else-if chain that ends in an else",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n"
     "    } else |err| if (err == error.A) {\n        return 1;\n    } else {\n"
     "        return 2;\n    }\n}\n", 3),
    ("a pointer payload before an if",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |*v| {\n        return v.*;\n"
     "    } else |*err| if (err.* == error.A) {\n        return 1;\n    }\n    return 0;\n}\n", 2),
    ("a payload else whose block holds an if (control)",
     "pub fn f(x: anyerror!u8) u8 {\n    if (x) |v| {\n        return v;\n    } else |err| {\n"
     "        if (err == error.Boom) {\n            return 1;\n        }\n    }\n    return 0;\n}\n", 4),
    ("an else-if with no payload (control)",
     "pub fn f(a: bool, b: bool) u8 {\n    if (a) {\n        return 1;\n    } else if (b) {\n"
     "        return 2;\n    }\n    return 0;\n}\n", 2),
]


@pytest.mark.parametrize("label,source,want", ZIG_ELSE_CHAINS, ids=[c[0] for c in ZIG_ELSE_CHAINS])
def test_a_zig_payload_else_continues_its_chain(label, source, want):
    rows = analyze_source("a.zig", source, note=False)
    assert [r.cognitive for r in rows] == [want], label
