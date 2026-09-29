"""PowerShell switch arms are counted only inside a switch statement's body.

about_Switch: a switch statement is `switch (...) { arm { } ... }`, one
decision per arm, and `default` none. The `;` that ends a statement, or the
`]` of a `[switch]` type, shows the word before it named no switch statement,
so the blocks after it are an `if`'s and hold no arms. Hand-counted: two `if`s
make ccn 3, the inner one nested for cognitive 1 + 2.
"""
from crapkit.analyze import analyze_source


def test_a_statement_end_arms_no_switch():
    code = "function F($x, $y) {\n    $a = 1;\n    if ($x) {\n        if ($y) { 1 }\n    }\n}\n"

    assert [(r.long_name, r.start, r.end, r.ccn_std, r.cognitive) for r in
            analyze_source("src/a.ps1", code)] == [("F $x , $y", 1, 6, 3, 3)]
