"""The PowerShell reader, pinned at the seam lizard resolves and the seam crapkit calls.

Every number here is hand-counted from the source above it. lizard has no
PowerShell support at all, so nothing in this file is a regression guard against
an upstream change: it is the specification.

The probe battery below (P1..P10) is the one this reader was built against, and
the three real scripts at the end come from a 106-file corpus in which it reports
227 functions.
"""
import pathlib

import lizard
import lizard_languages
import pytest

from crapkit.analyze import analyze_source
from crapkit.keys import bare_name
from crapkit.lizardpowershell import PowerShellReader, register

# P1: if + 5 elseif + else. Six decision points, base 1.
P1_CHAIN = """function Get-P1Chain {
    param([int]$x)
    if ($x -eq 1) {
        return 10
    } elseif ($x -eq 2) {
        return 20
    } elseif ($x -eq 3) {
        return 30
    } elseif ($x -eq 4) {
        return 40
    } elseif ($x -eq 5) {
        return 50
    } elseif ($x -eq 6) {
        return 60
    } else {
        return 0
    }
}
"""
P1_CCN = 7

# P2: six arms plus default. C convention counts the six, base 1.
P2_SWITCH = """function Get-P2Switch {
    param([int]$x)
    switch ($x) {
        1 { return 10 }
        2 { return 20 }
        3 { return 30 }
        4 { return 40 }
        5 { return 50 }
        6 { return 60 }
        default { return 0 }
    }
}
"""
P2_CCN = 7

# P3: while + for + foreach + a nested if, base 1.
P3_LOOPS = """function Get-P3Loops {
    param($items, [int]$n)
    $total = 0
    $i = 0
    while ($i -lt $n) {
        $total += $i
        $i++
    }
    for ($j = 0; $j -lt $n; $j++) {
        $total += $j
    }
    foreach ($it in $items) {
        if ($it -gt 0) {
            $total += $it
        }
    }
    return $total
}
"""
P3_CCN = 5

# P4: if + two -and + one -or, base 1.
P4_LOGIC = """function Test-P4Logic {
    param($a, $b, $c, $d)
    if ($a -and $b -and $c -or $d) {
        return $true
    }
    return $false
}
"""
P4_CCN = 5

# P5: catch is a decision point, base 1.
P5_TRY = """function Invoke-P5TryCatch {
    param($path)
    try {
        Get-Content -Path $path
    } catch {
        return $null
    }
}
"""

# P6: every trap at once. A line comment, a block comment, two here-strings and a
# backtick-escaped quote, all holding keywords and unbalanced braces. Base 1 and
# nothing else.
P6_TRAPS = '''# if elseif while for switch foreach   <- line comment, must not count
function Get-P6Traps {
<#
.SYNOPSIS
    if while for foreach switch elseif -and -or
    A brace here would break naive brace counting: {
#>
    $plain = "if elseif while for switch foreach"
    $escaped = "He said `"if (`$z) { }`" and left"
    $hereD = @"
if ($true) { Write-Output 'x' }
while ($true) { break }
"@
    $hereS = @'
if ($true) { Write-Output 'x' }
foreach ($q in $r) { }
'@
    return $plain
}
'''

# P7: a function declared inside another function's body.
P7_NESTED = """function Get-P7Outer {
    param([int]$x)
    function Get-P7Inner {
        param([int]$y)
        if ($y -gt 0) { return $y }
        return 0
    }
    if ($x -gt 0) { return Get-P7Inner -y $x }
    return 0
}
"""

# P8: `filter`, PowerShell's other function-shaped keyword.
P8_FILTER = """filter Select-P8Positive {
    if ($_ -gt 0) { $_ }
}
"""

# P9: an anonymous script block bound to a variable. Reported by nothing.
P9_BLOCK = """$p9 = {
    param($v)
    if ($v -gt 0) { 1 } else { 0 }
}
"""

# P10: the advanced-function shape. `[switch]$Force` is a parameter declaration,
# and the whole param() block is signature, not branches. Base 1 + the one if.
P10_ADVANCED = """function Set-P10Advanced {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [switch]$Force
    )
    if ($Force) {
        return $Name
    }
    return ""
}
"""
P10_CCN = 2


def _functions(code, name="probe.ps1"):
    """Through lizard's own pipeline, so the reader is reached the way lizard
    reaches it: filename -> get_reader_for -> tokenize -> extensions -> reader."""
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    return analyzer.analyze_source_code(name, code).function_list


def _only(code, name="probe.ps1"):
    (fn,) = _functions(code, name)
    return fn


# --- registration: lizard has to resolve .ps1 to this reader -------------------

def test_lizard_resolves_ps1_to_the_powershell_reader():
    assert lizard_languages.get_reader_for("deploy.ps1") is PowerShellReader


def test_lizard_resolves_psm1_to_the_powershell_reader():
    assert lizard_languages.get_reader_for("Tools.psm1") is PowerShellReader


def test_lizard_shipped_no_reader_for_ps1_at_all():
    from crapkit.lizardpowershell import _stock_languages

    assert not [r for r in _stock_languages() if "ps1" in getattr(r, "ext", [])]


def test_register_is_idempotent():
    before = lizard_languages.languages()

    register()
    register()

    assert lizard_languages.languages() == before


def test_two_wrapped_readers_compose_without_duplicating_either():
    """The defect a second wrapper exposed. Each `register()` used to STAMP the
    function it installed and check that stamp on `lizard_languages.languages`,
    which only ever answers for the OUTERMOST wrapper: with PowerShell
    registered on top of shell, shell's stamp was no longer visible and a second
    `register_shell()` appended ShellReader again. A reader listed twice is not
    inert — `get_reader_for` walks the list — and the list grows once per call
    for the rest of the process."""
    from crapkit.lizardshell import ShellReader
    from crapkit.lizardshell import register as register_shell

    for _ in range(2):
        register_shell()
        register()

    listed = lizard_languages.languages()
    assert (listed.count(ShellReader), listed.count(PowerShellReader)) == (1, 1)


def test_registration_leaves_the_stock_readers_alone():
    assert lizard_languages.get_reader_for("a.java").__name__ == "JavaReader"
    assert lizard_languages.get_reader_for("a.ts").__name__ == "TypeScriptReader"


def test_what_a_powershell_script_costs_when_it_is_read_as_c():
    """The price of skipping registration, since lizard answers rather than
    fails: `(get_reader_for(filename) or CLikeReader)`. Feeding the same source
    through the C reader is what an unregistered .ps1 gets. It loses the
    declaration entirely and reports the `if (...)` line as a function named
    `if`, so the wrong answer arrives shaped like a right one."""
    assert [fn.name for fn in _functions(P4_LOGIC, name="probe.c")] == ["if"]
    assert [fn.name for fn in _functions(P4_LOGIC)] == ["Test-P4Logic"]


# --- ccn convention ------------------------------------------------------------

def test_an_elseif_chain_counts_every_link():
    assert _only(P1_CHAIN).cyclomatic_complexity == P1_CCN


def test_the_four_loop_keywords_and_a_nested_if():
    assert _only(P3_LOOPS).cyclomatic_complexity == P3_CCN


def test_the_powershell_logical_operators_count():
    assert _only(P4_LOGIC).cyclomatic_complexity == P4_CCN


def test_a_dash_prefixed_comparison_is_not_a_condition():
    """`-eq`, `-gt`, `-match` and friends tokenize exactly like `-and`. Only the
    three connectives are conditions; a reader that counted every `-word` would
    charge a point for every cmdlet parameter in the file."""
    code = "function f {\n  if ($a -eq 1 -and $b -match 'x') { return 1 }\n}\n"

    assert _only(code).cyclomatic_complexity == 3  # base, if, -and


def test_catch_is_a_condition_and_try_is_not():
    assert _only(P5_TRY).cyclomatic_complexity == 2


def test_until_closes_a_do_loop_and_counts():
    code = "function f {\n  do {\n    $i++\n  } until ($i -gt 3)\n}\n"

    assert _only(code).cyclomatic_complexity == 2


# --- the switch decision: arms, not the keyword --------------------------------

def test_a_six_arm_switch_costs_the_same_as_the_six_branch_chain_doing_its_work():
    """The whole reason arms are counted. Read as one keyword, this scores 2 and
    the if/elseif chain beside it scores 7 for identical behaviour."""
    assert _only(P2_SWITCH).cyclomatic_complexity == _only(P1_CHAIN).cyclomatic_complexity


def test_the_hand_counted_switch_ccn():
    assert _only(P2_SWITCH).cyclomatic_complexity == P2_CCN


def test_the_default_arm_is_free():
    """C's `default:` costs nothing and PowerShell's `default` costs nothing, so a
    switch with one real arm costs the same 1 as an `if`."""
    code = ("function f {\n  switch ($x) {\n    1 { 'a' }\n"
            "    default { 'b' }\n  }\n}\n")

    assert _only(code).cyclomatic_complexity == 2


def test_the_switch_keyword_alone_costs_nothing():
    code = "function f {\n  switch ($x) {\n  }\n}\n"

    assert _only(code).cyclomatic_complexity == 1


def test_a_nested_switch_counts_the_inner_arms_too():
    code = ("function f {\n  switch ($x) {\n    1 {\n      switch ($y) {\n"
            "        'a' { 1 }\n        'b' { 2 }\n      }\n    }\n    2 { 3 }\n  }\n}\n")

    assert _only(code).cyclomatic_complexity == 5  # base + outer 2 + inner 2


def test_a_switch_type_accelerator_is_not_a_switch_statement():
    """`[switch]$Force` is how PowerShell declares a boolean parameter and it
    appears in most advanced functions. Armed by it, the arm counter would treat
    the next brace block as a switch body and charge a point for every statement
    block inside it."""
    assert _only(P10_ADVANCED).cyclomatic_complexity == P10_CCN


def test_a_param_block_costs_nothing_at_all():
    code = ("function f {\n    [CmdletBinding()]\n    param(\n"
            "        [Parameter(Mandatory = $true)][string]$Name,\n"
            "        [switch]$Force,\n"
            "        [ValidateSet('a','b')][string]$Mode = 'a'\n    )\n"
            "    return $Name\n}\n")

    assert _only(code).cyclomatic_complexity == 1


def _switch(subject, arms):
    return f"function f($m, $n) {{\n    switch {subject} {{\n{arms}    }}\n}}\n"


TWO_ARMS = "        'a' { return 1 }\n        'b' { return 2 }\n"


@pytest.mark.parametrize("subject", ["($m['k'])", "-regex ($m[0])", "($(Get-A; Get-B))",
                                     "-file $m[0]", "($n)\n   "])
def test_a_switch_counts_its_arms_whatever_its_subject_holds(subject):
    """A `]` or a `;` in the flags or subject ended the switch before its body
    (`switch ($m['k'])` counted neither arm). Only a `;` or a `}` outside the
    subject's parentheses ends a switch that opened no body now, and the body
    may start on the next line."""
    assert _only(_switch(subject, TWO_ARMS)).cyclomatic_complexity == 3


@pytest.mark.parametrize("arms, ccn", [
    ("        { $_ -gt 5 } { 'big' }\n        { $_ -lt 0 } { 'negative' }\n"
     "        default { 'small' }\n", 3),
    ("        1 { 'one' }\n        { $_ -gt 5 } { 'big' }\n        'x' { 'x' }\n", 4),
    ("        1 { 'one' }; default { 'many' }\n", 2),
    ("        { $_ } { 'truthy' }; { -not $_ } { 'falsy' }\n", 3),
    ("        (1 + 1) { 'two' }\n        default { 'other' }\n", 2),
    ("        $m.Max { 'max' }\n        $m.Min { 'min' }\n        default { 'mid' }\n", 3),
    ("        $m.default { 'the default value' }\n        $m['if'] { 'keyed' }\n", 3),
    ("        (default) { 'runs a command named default' }\n        default { 'other' }\n", 2),
])
def test_an_arm_counts_once_whatever_its_pattern_is(arms, ccn):
    """An arm is a pattern and the block it runs (about_Switch). A pattern may be
    a script block, and its own braces opened directly in the switch body like
    the block's, so a script-block arm counted twice. A pattern of several
    tokens is one arm, and only its first token can make it the default arm."""
    assert _only(_switch("($n)", arms)).cyclomatic_complexity == ccn


@pytest.mark.parametrize("line", ["git switch main", "Write-Output switch"])
def test_a_switch_word_that_starts_no_statement_opens_no_body(line):
    """`git switch main` runs git. Read as a switch statement, the next block
    became its body and each block opening directly inside it an arm."""
    code = ("function Update-Branch($x) {\n    " + line + "\n    if ($x) {\n"
            "        foreach ($y in $x) { $y }\n    }\n}\n")

    assert _only(code).cyclomatic_complexity == 3


@pytest.mark.parametrize("statement", [":outer switch ($n) {", "$r = switch ($n) {",
                                       "return $(switch ($n) {"])
def test_a_switch_after_a_label_or_an_assignment_still_counts(statement):
    closer = "})" if statement.endswith("$(switch ($n) {") else "}"
    code = ("function f($n) {\n    " + statement + "\n        1 { 'a' }\n        2 { 'b' }\n"
            "        default { 'c' }\n    " + closer + "\n}\n")

    assert _only(code).cyclomatic_complexity == 3


# --- hazard: comments ----------------------------------------------------------

def test_keywords_in_a_line_comment_are_not_conditions():
    code = "function f {\n  # if elseif while for foreach switch -and -or\n  return 1\n}\n"

    assert _only(code).cyclomatic_complexity == 1


def test_a_block_comment_contributes_no_conditions_and_no_braces():
    """`<#` and `#>` are not in lizard's shared pattern, so without the added rule
    the `<` and the `#` split apart, every keyword in the comment counts, and an
    unbalanced brace inside it ends the function early."""
    code = ("function f {\n<#\n if while for foreach switch elseif -and -or\n"
            " an unbalanced brace: {\n#>\n  return 1\n}\n")

    assert _only(code).cyclomatic_complexity == 1


def test_a_commented_out_function_is_not_a_function():
    code = "function real {\n  return 1\n}\n# function fake { }\n"

    assert [fn.name for fn in _functions(code)] == ["real"]


# --- hazard: here-strings ------------------------------------------------------

def test_a_double_quoted_here_string_leaks_no_conditions():
    code = ('function f {\n  $t = @"\nif ($true) { while ($x) { } }\n"@\n  return $t\n}\n')

    assert _only(code).cyclomatic_complexity == 1


def test_a_single_quoted_here_string_leaks_no_conditions():
    code = ("function f {\n  $t = @'\nif ($true) { foreach ($q in $r) { } }\n'@\n"
            "  return $t\n}\n")

    assert _only(code).cyclomatic_complexity == 1


def test_a_here_string_body_defines_no_function():
    code = ('function real {\n  $t = @"\nfunction fake { }\n"@\n}\n')

    assert [fn.name for fn in _functions(code)] == ["real"]


def test_every_trap_at_once_still_costs_the_base_one():
    """The full P6 probe: line comment, block comment, plain string, backtick
    escape and both here-strings, each holding keywords and stray braces."""
    assert _only(P6_TRAPS).cyclomatic_complexity == 1


def test_the_trap_probe_is_one_function_that_spans_its_whole_body():
    fn = _only(P6_TRAPS)

    assert (fn.name, fn.start_line, fn.end_line) == ("Get-P6Traps", 2, 19)


# --- hazard: quotes and the backtick escape ------------------------------------

def test_a_backtick_escaped_quote_does_not_end_the_string():
    r"""PowerShell escapes with a backtick, not a backslash. Under lizard's rule
    the string ends at the first `"` of `` `" ``, the rest of the line becomes
    code, and its `{` unbalances the function."""
    code = 'function f {\n  $s = "He said `"if ($z) { }`" and left"\n  return $s\n}\n'

    assert _only(code).cyclomatic_complexity == 1


def test_a_doubled_quote_escapes_inside_a_single_quoted_string():
    code = "function f {\n  $s = 'it''s if ($x) { }'\n  return $s\n}\n"

    assert _only(code).cyclomatic_complexity == 1


def test_keywords_inside_a_plain_string_are_not_conditions():
    code = 'function f {\n  return "if elseif while -and -or"\n}\n'

    assert _only(code).cyclomatic_complexity == 1


def test_quotes_inside_a_subexpression_pair_among_themselves():
    """`"$(Get-Item "x{")"` is one string. Ended at its second quote, it left
    `x{` in code, the `{` never closed, and function A had no row at all."""
    code = ('function A {\n  $v = "$(Get-Item "x{")"\n}\n\n'
            'function B {\n  if ($x) { 2 }\n}\n')

    assert [(f.name, f.start_line, f.end_line, f.cyclomatic_complexity)
            for f in _functions(code)] == [("A", 1, 3, 1), ("B", 5, 7, 2)]


# --- code inside a string: a $( ) subexpression in "..." -----------------------
#
# `"$($a -and $b)"` evaluates `$a -and $b`. The quotes make its value text; they
# do not make the expression text. Each line holds one decision inside the
# subexpression and reads what the same expression reads written bare.

SUBEXPRESSIONS = {
    "an -and": ('$x = "$($a -and $b)"', "$x = $($a -and $b)"),
    "text around it": ('$x = "v: $($a -or $b) end"', "$x = $($a -or $b)"),
    "an if": ('$x = "$(if ($a) { 1 })"', "$x = $(if ($a) { 1 })"),
    "an if and its else": ('$x = "$(if ($a) { 1 } else { 2 })"', "$x = $(if ($a) { 1 } else { 2 })"),
    "one inside another": ('$x = "$(G "$($a -and $b)")"', "$x = $(G $($a -and $b))"),
    "four levels of parens": ('$x = "$(f (g (h ($a -and $b))))"',
                              "$x = $(f (g (h ($a -and $b))))"),
    "eight levels of parens": ('$x = "$(' + "(" * 7 + "$a -and $b" + ")" * 8 + '"',
                               "$x = $(" + "(" * 7 + "$a -and $b" + ")" * 8),
}


def _columns(line):
    (record,) = analyze_source("hole.ps1", "function F {\n  " + line + "\n}\n")
    return record.ccn_std, record.ccn, record.cognitive, record.nesting


@pytest.mark.parametrize("quoted, bare", SUBEXPRESSIONS.values(), ids=SUBEXPRESSIONS.keys())
def test_a_subexpression_inside_a_string_counts_what_it_counts_bare(quoted, bare):
    """NIST SP 500-235 sec. 4.1: the decision counts wherever its expression
    sits, so ccn_std is 2 in both spellings."""
    assert (_columns(quoted), _columns(quoted)[0]) == (_columns(bare), 2)


def test_a_backtick_escaped_subexpression_is_text():
    """`` "`$($a -and $b)" `` prints `$($a -and $b)` and evaluates nothing."""
    assert _columns('$x = "`$($a -and $b)"')[:2] == (1, 1)


def test_a_subexpression_in_single_quotes_is_text():
    assert _columns("$x = '$($a -and $b)'")[:2] == (1, 1)


def test_a_subexpression_over_two_lines_keeps_every_later_line_number():
    code = ('function A {\n  $x = "$($a -and\n    $b)"\n}\n\n'
            'function B {\n  return 1\n}\n')
    spans = [(r.start, r.end, r.ccn) for r in analyze_source("two.ps1", code)]

    assert spans == [(1, 4, 2), (6, 8, 1)]


def test_quotes_four_parens_deep_in_a_subexpression_pair_among_themselves():
    """The string rule read three levels of parens. At four it did not match,
    the string ended at `"x{`, and the `{` left in code hid function B."""
    code = ('function A {\n  $v = "$(f (g (h ("x{"))))"\n}\n\n'
            'function B {\n  if ($x) { 2 }\n}\n')

    assert [(f.name, f.start_line, f.end_line, f.cyclomatic_complexity)
            for f in _functions(code)] == [("A", 1, 3, 1), ("B", 5, 7, 2)]


def test_parens_nine_levels_deep_are_the_documented_limit():
    """The string rule reads eight levels of parens inside a subexpression. At
    nine it does not match, the string ends at its first inner quote, and the
    `-and` counts nothing."""
    line = '$x = "$(' + "(" * 8 + "$a -and $b" + ")" * 9 + '"'
    assert _columns(line)[:2] == (1, 1)


def test_an_unpaired_quote_before_many_subexpressions_reads_in_linear_time():
    """With no closing quote left in the file, the string rule tried every way of
    reading each `$( )` after it as a subexpression or as text, twice the time
    per subexpression: 1.8 s for 22 of them. Its loops no longer give back what
    they matched, so this tokenizes at once; before, it did not finish."""
    code = 'function F {\n  $x = "' + " $(a)" * 40 + "\n}\n"
    assert [(f.name, f.end_line) for f in _functions(code)] == [("F", 3)]


# --- the declaration spellings -------------------------------------------------

def test_a_function_with_no_parameter_list_is_reported():
    """Go writes `func name(args) {` and always has the list; PowerShell writes
    `function Name {` and declares its parameters in the body. Without the
    override the `{` ends the search and the function disappears."""
    assert [fn.name for fn in _functions("function Get-Thing {\n  return 1\n}\n")] == ["Get-Thing"]


def test_a_function_with_a_header_parameter_list_is_reported_with_its_parameters():
    fn = _only("function Get-Thing ($a, $b) {\n  return 1\n}\n")

    assert (fn.name, len(fn.parameters)) == ("Get-Thing", 2)


def test_the_filter_keyword_declares_a_function():
    fn = _only(P8_FILTER)

    assert (fn.name, fn.cyclomatic_complexity) == ("Select-P8Positive", 2)


@pytest.mark.parametrize("keyword", ["function", "filter", "workflow", "configuration"])
def test_all_four_declaration_keywords_are_reported(keyword):
    assert [fn.name for fn in _functions(f"{keyword} Get-Thing {{\n  return 1\n}}\n")] == [
        "Get-Thing"]


def test_a_verb_noun_name_arrives_as_one_token():
    """Without the Verb-Noun rule the name splits and the function is reported
    under the last fragment, which would make two ratchet rows out of
    `Get-Thing` and `Set-Thing`."""
    assert [fn.name for fn in _functions("function Get-ChildItemSafely {\n  1\n}\n")] == [
        "Get-ChildItemSafely"]


def test_an_underscore_prefixed_name_survives():
    assert [fn.name for fn in _functions("function _Get-CBPath {\n  1\n}\n")] == ["_Get-CBPath"]


def test_a_function_declared_inside_another_is_reported_separately():
    assert {fn.name: fn.cyclomatic_complexity for fn in _functions(P7_NESTED)} == {
        "Get-P7Outer": 2, "Get-P7Inner": 2}


def _rows(code):
    return [(bare_name(r.long_name), r.start, r.end, r.ccn_std)
            for r in analyze_source("probe.ps1", code)]


IF_BODY = "    if ($x) {\n        return 1\n    }\n    return 0\n"
AFTER = "\nfunction Get-After($y) {\n    if ($y) {\n        return 2\n    }\n    return 0\n}\n"


@pytest.mark.parametrize("name", ["script:Get-Scoped", "global:Get-Scoped",
                                  "private:Get-Scoped", "Get.Dotted"])
def test_a_scoped_or_dotted_name_is_one_function(name):
    """`function [<scope:>]<name>` declares one function (about_Functions), and
    PowerShell's parser names `function Get.Dotted` Get.Dotted. The name used
    to end at its colon or dot, the function got no row, and its decisions
    counted toward none."""
    code = f"function {name}($x) {{\n{IF_BODY}}}\n" + AFTER

    assert _rows(code) == [(name, 1, 6, 2), ("Get-After", 8, 13, 2)]


@pytest.mark.parametrize("line", [
    "dotnet build --configuration $x.Configuration",
    "$f = $o.filter",
    "$h = @{ filter = '*.txt'; class = 'x' }",
    "Write-Output function workflow",
])
def test_a_declaring_word_that_declares_nothing_opens_nothing(line):
    """A declaring word declares only where it starts a statement and a name
    follows it. A native option, a member, a hashtable key and a bare argument
    opened a declaration: the function around the word lost its row, and a
    phantom row named after a later token could take its place."""
    code = ("function Invoke-Build($x) {\n    if ($x) {\n        " + line + "\n    }\n"
            "    return 0\n}\n\nfunction Get-Next($y) {\n    return $y\n}\n")

    assert _rows(code) == [("Invoke-Build", 1, 6, 2), ("Get-Next", 8, 10, 1)]


@pytest.mark.parametrize("statement, ccn", [
    ("filter status --short", 2),        # a name, then an argument where a body belongs
    ("configuration Release 'x64'", 2),
    ("param", 2),                        # no block after it: a command named param
    ("switch;", 2),                      # a switch that ends before its body
    ("if ($x) { switch }", 3),
    ("switch ($x) { 1 { switch } 2 { 'b' } 3 { 'c' } }", 5),
])
def test_an_unfinished_declaration_param_or_switch_costs_the_rows_around_it_nothing(statement, ccn):
    """PowerShell's parser rejects each of these but `param`, which it reads
    as a command, and crapkit still reads the file: the function around the
    statement keeps its row, its one parameter and its decisions (the `if`
    after it, and every arm of an outer switch), and the next function
    starts where it is written."""
    code = ("function Invoke-Build($x) {\n    " + statement + "\n    if ($x) {\n        return 1\n"
            "    }\n    return 0\n}\n\nfunction Get-Next($y) {\n    return $y\n}\n")
    rows = [(bare_name(r.long_name), r.start, r.end, r.ccn_std, r.params)
            for r in analyze_source("probe.ps1", code)]

    assert rows == [("Invoke-Build", 1, 7, ccn, 1), ("Get-Next", 9, 11, 1, 1)]


CLASS_IN_FUNCTION = """function Get-WithClass($x) {
    class Holder {
        [int] Pick([int]$y) {
            if ($y) {
                return 1
            }
            return 0
        }
    }
    return $x
}
"""


def test_a_class_method_decides_nothing_for_the_function_declaring_the_class():
    """The method is a function of its own (about_Classes), not a branch of the
    function around the class. Its `if` counted toward Get-WithClass in ccn and
    cognitive. Methods get no row of their own either."""
    (record,) = analyze_source("probe.ps1", CLASS_IN_FUNCTION)

    assert (bare_name(record.long_name), record.start, record.end, record.ccn_std,
            record.cognitive) == ("Get-WithClass", 1, 11, 1, 0)


@pytest.mark.parametrize("declaration", [
    "class Holder {\n    [int] Pick([int]$y) {\n        if ($y) { return 1 }\n        return 0\n    }\n}\n",
    "class Child : Holder {\n    Child() { if ($true) { } }\n}\n",
    "enum Color {\n    Red\n    Green\n}\n",
])
def test_a_type_declared_before_a_function_leaves_it_alone(declaration):
    code = declaration + "\nfunction Get-Next($x) {\n" + IF_BODY + "}\n"
    start = declaration.count("\n") + 2

    assert _rows(code) == [("Get-Next", start, start + 5, 2)]


# --- the param() block ---------------------------------------------------------

PARAM_BLOCKS = {
    "attributes and types": (P10_ADVANCED, 2),
    "comment-based help first": (
        "function Get-Greeting {\n    <#\n    .SYNOPSIS\n    Says hello.\n    #>\n"
        "    param([string]$Name, [int]$Count = 1)\n    $Name\n}\n", 2),
    "an attribute on the same line": (
        "function Get-Same {\n    [CmdletBinding()] Param(\n"
        "        [Parameter(Mandatory = $true)][ValidateScript({ $_ -gt 0 })][int]$Count,\n"
        "        [string[]]$Names = @('a', 'b'),\n        [switch]$Force\n    )\n    $Count\n}\n", 3),
    "a default that reads a variable": (
        "function Get-Default {\n    param($Name = $env:USERNAME, $Other)\n    $Name\n}\n", 2),
    "an empty block": ("function Get-None {\n    param()\n    1\n}\n", 0),
    "a null-conditional index in a default": (
        "function Get-Index {\n    param($First = ${xs}?[0], $Second)\n    $First\n}\n", 2),
    "a script block's own block": (
        "function Invoke-It {\n    $block = { param($a, $b) $a + $b }\n    & $block 1 2\n}\n", 0),
}


@pytest.mark.parametrize("shape", sorted(PARAM_BLOCKS))
def test_a_param_block_declares_the_parameters(shape):
    """`param(...)` opening the body is how an advanced function declares its
    parameters (about_Functions_Advanced_Parameters), and it read 0. The long
    name stays the bare function name: it is the ratchet key, and a key that
    grew the block's parameters would orphan every mark recorded before."""
    code, count = PARAM_BLOCKS[shape]
    (record,) = analyze_source("probe.ps1", code)

    assert (record.params, " " in record.long_name) == (count, False)


# --- only declarations, never top-level code -----------------------------------

def test_top_level_script_code_is_not_reported_as_a_function():
    """Statements outside any declaration belong to lizard's `*global*` pseudo
    function, exactly like Python module level. A script that is one long
    sequence reports nothing, and that is the answer, not a parse failure."""
    code = "$ErrorActionPreference = 'Stop'\nforeach ($f in $files) { Write-Host $f }\n"

    assert _functions(code) == []


def test_an_anonymous_script_block_is_not_reported():
    """It has no name to key a ratchet row on. Documented, not solved."""
    assert _functions(P9_BLOCK) == []


# --- through crapkit's own analysis path ---------------------------------------

def test_crapkit_reads_the_hand_counted_ccn_off_the_powershell_reader():
    (record,) = analyze_source("probe.ps1", P1_CHAIN)

    assert record.ccn == P1_CCN


def test_psm1_files_take_the_same_path_as_ps1_files():
    (record,) = analyze_source("Tools.psm1", P1_CHAIN)

    assert record.ccn == P1_CCN


def test_the_modified_column_counts_the_switch_arms_as_the_standard_one_does():
    """crapkit takes min(ccn_std, ccn_mod). lizard's modified rule adds a point
    for a `switch` opener and takes one back per `case`, and a PowerShell arm has
    no `case` to take it back. The opener used to get its point anyway, so every
    switch read one higher in ccn_mod than in ccn_std. Now it gets none: a
    PowerShell switch costs its arms in both columns, as a Rust match does."""
    (record,) = analyze_source("probe.ps1", P2_SWITCH)

    assert (record.ccn, record.ccn_std, record.ccn_mod) == (P2_CCN, P2_CCN, P2_CCN)


def test_a_switch_parameter_type_costs_nothing_in_any_column():
    """`[switch]$Force` declares a boolean parameter. The modified column gave
    the type name the point it gives a switch statement, and the cognitive
    column charged it +1 and read the function body as the switch's block."""
    code = ("function Test-SwitchParam([switch]$Force) {\n    if ($Force) {\n"
            "        return 1\n    }\n    return 0\n}\n")
    (record,) = analyze_source("probe.ps1", code)

    assert (record.ccn_std, record.ccn_mod, record.cognitive) == (2, 2, 1)


# --- keywords and operators in any case -----------------------------------------

MIXED_CASE = """Function Get-Mixed($a, $b, $xs) {
    If ($a -And $b) {
        Return 1
    } ElseIf ($a -Or $b -XOR $a) {
        Return 2
    } Else {
        ForEach ($x In $xs) {
            While ($x) { $x-- }
        }
    }
    Do { $a = $b } Until ($a)
    Switch ($a) {
        1 { 'one' }
        Default { 'many' }
    }
    Try { Get-Item $a } Catch { Return 3 } Finally { $b = 1 }
    For ($i = 0; $i -lt 3; $i++) { Trap { Continue } }
}
"""


def _numbers(record):
    return (record.start, record.end, record.ccn_std, record.ccn_mod, record.cognitive,
            record.nesting, record.nloc, record.params)


def test_the_case_a_keyword_is_written_in_changes_no_number():
    """PowerShell keywords and operators are not case-sensitive
    (about_Language_Keywords). The same function written in lower case is the
    reference: every keyword this reader or lizard reads appears above in
    another case."""
    (mixed,) = analyze_source("probe.ps1", MIXED_CASE)
    (lower,) = analyze_source("probe.ps1", MIXED_CASE.lower())

    assert _numbers(mixed) == _numbers(lower)
    assert (mixed.long_name.split()[0], mixed.ccn_std) == ("Get-Mixed", 13)


def test_an_upper_case_if_is_a_condition():
    code = "function Test-UpperCase($a) {\n    IF ($a) {\n        RETURN 1\n    }\n    return 0\n}\n"
    (record,) = analyze_source("probe.ps1", code)

    assert (record.ccn_std, record.cognitive, record.nesting) == (2, 1, 1)


def test_a_capitalized_or_is_a_short_circuit_condition():
    code = "function Test-CapitalOr($a, $b) {\n    if ($a -Or $b) {\n        return 1\n    }\n    return 0\n}\n"
    (record,) = analyze_source("probe.ps1", code)

    assert record.ccn_std == 3


def test_a_capitalized_default_arm_is_free():
    code = ("function Get-CapitalDefault($n) {\n    switch ($n) {\n        1 { return 'one' }\n"
            "        Default { return 'many' }\n    }\n}\n")
    (record,) = analyze_source("probe.ps1", code)

    assert record.ccn_std == 2


def test_a_capitalized_function_keyword_declares_a_function():
    code = "Function Get-Capital($x) {\n    if ($x) {\n        return 1\n    }\n    return 0\n}\n"

    assert [(bare_name(r.long_name), r.start, r.end, r.ccn_std)
            for r in analyze_source("probe.ps1", code)] == [("Get-Capital", 1, 6, 2)]


def test_a_capitalized_loop_after_a_label_still_counts():
    """A label is the statement's first token, and the loop keyword follows it."""
    code = "function Find-First($xs) {\n    :outer ForEach ($x in $xs) {\n        break outer\n    }\n}\n"
    (record,) = analyze_source("probe.ps1", code)

    assert record.ccn_std == 2


def test_a_keyword_word_that_starts_no_statement_keeps_its_spelling():
    """Only a statement's first word is a keyword to PowerShell. After a pipe,
    `ForEach` is the ForEach-Object alias, and after a parameter `Default` is
    an argument; read as keywords they would cost a loop and take the switch's
    first arm for its default."""
    code = ("function Get-Names($xs, $n) {\n    $xs | ForEach { $_.Name }\n"
            "    switch ($n) {\n        1 { Out-File -Encoding Default -FilePath a }\n    }\n}\n")
    (record,) = analyze_source("probe.ps1", code)

    assert (record.ccn_std, record.cognitive) == (2, 1)


@pytest.mark.parametrize("line", [
    "$xs | foreach { $_.Name }",
    "$xs |\n        foreach { $_.Name }",
    "$xs.foreach({ $_.Name })",
    "git switch $xs",
    "Write-Output if while",
    "Write-Output -InputObject catch",
    "Get-ChildItem -Filter:foreach",
    "return if",
])
def test_a_lower_case_keyword_word_that_names_a_command_an_argument_or_a_member_decides_nothing(line):
    """After a pipe a word is a command (`foreach` is the ForEach-Object alias),
    after a word or a parameter it is an argument, and after a `.` it is a
    member. PowerShell reads no keyword in any of those places. Written in
    lower case, each still cost a loop, a condition or a cognitive switch,
    while the same line with `ForEach` cost nothing."""
    (record,) = analyze_source("probe.ps1", f"function Get-Names($xs) {{\n    {line}\n}}\n")

    assert (record.ccn_std, record.ccn_mod, record.cognitive, record.nesting) == (1, 1, 0, 0)


@pytest.mark.parametrize("code, ccn", [
    ("function Get-A {\n    param($a) if ($a) { 1 }\n}\n", 2),
    ("function Get-A($a) {\n    $x = `\n        if ($a) { 1 } else { 2 }\n    $x\n}\n", 2),
    ("function Get-A($a) {\n    git status; if ($a) { 1 }\n}\n", 2),
    ("function Get-A($a) {\n    Write-Output $a\n    foreach ($x in $a) { $x }\n}\n", 2),
])
def test_a_keyword_that_starts_a_statement_after_a_bracket_or_a_backtick_still_counts(code, ccn):
    """The argument rule reads only a word, a parameter, a pipe or a dot
    before the keyword. A `)` ending a param() block and a backtick escaping
    the line break both leave the keyword starting its statement."""
    (record,) = analyze_source("probe.ps1", code)

    assert record.ccn_std == ccn


def test_a_dotted_function_name_keeps_the_spelling_of_a_keyword_part():
    """The dot in `function Get.foreach` joins a name, and the name is the
    ratchet key: the member rule leaves it as written."""
    assert _rows("function Get.foreach($x) {\n" + IF_BODY + "}\n")[0][:3] == ("Get.foreach", 1, 6)


# --- PowerShell 7 operators ------------------------------------------------------

def test_pipeline_chain_operators_are_conditions():
    """`&&` runs the right pipeline only when the left one succeeded and `||`
    only when it failed (about_Pipeline_Chain_Operators): one decision each."""
    code = ("function Test-Chain($p) {\n    Get-Item $p && Write-Output 'found'\n"
            "    Get-Item $p || Write-Output 'missing'\n}\n")
    (record,) = analyze_source("probe.ps1", code)

    assert record.ccn_std == 3


@pytest.mark.parametrize("line, ccn", [
    ("return $a ?? 0", 2),
    ("$a ??= 1", 2),
    ("return $a ?? $b ?? 0", 3),
])
def test_null_coalescing_is_one_decision(line, ccn):
    """`??` evaluates its right side only when the left is null, and `??=`
    assigns only then (about_Operators): one decision each, where two `?`
    tokens read as two ternaries."""
    (record,) = analyze_source("probe.ps1", f"function Test-Coalesce($a, $b) {{\n    {line}\n}}\n")

    assert record.ccn_std == ccn


@pytest.mark.parametrize("line", ["return ${a}?.Name", "return ${a}?[0]"])
def test_null_conditional_access_is_one_decision_that_opens_nothing(line):
    """PowerShell 7.1's `?.` and `?[]` read the member only when the braced
    variable before them is not null (about_Operators): one short-circuit
    decision, as `??` is, and no structure for the cognitive column. `?[`
    read as a ternary there and cost a cognitive point."""
    (record,) = analyze_source("probe.ps1", f"function Get-Safe($a) {{\n    {line}\n}}\n")

    assert (record.ccn_std, record.cognitive, record.nesting) == (2, 0, 0)


@pytest.mark.parametrize("body, numbers", [
    ("git fetch\n    if (-not $?) {\n        exit 1\n    }", (2, 1, 1)),
    ("git fetch\n    return $?", (1, 0, 0)),
    ("$ok? = $true\n    return $ok?.ToString()", (1, 0, 0)),
    ("${if} = 1\n    return ${if}", (1, 0, 0)),
    ("foreach ($r in @($a, ${env:ProgramFiles(x86)})) {\n        if ($r) {\n"
     "            return $r\n        }\n    }", (3, 3, 2)),
])
def test_a_variable_name_with_a_question_mark_or_braces_is_one_token(body, numbers):
    """`$?` is the automatic success variable, `?` is a legal character in any
    variable name (`$ok?`), and `${...}` spells a name with any characters
    (about_Variables). Split apart, the `?` read as a ternary, a braced
    keyword as a keyword, and the braces of `${env:ProgramFiles(x86)}` in a
    loop's condition as the loop's block, so the `if` inside read one level
    shallower in the cognitive and nesting columns."""
    (record,) = analyze_source("probe.ps1", f"function Get-Var($a) {{\n    {body}\n}}\n")

    assert (record.ccn_std, record.cognitive, record.nesting) == numbers


@pytest.mark.parametrize("condition, ccn", [
    ("$a -and $b -or $c", 4),
    ("$a -AND $b -Or $c", 4),
    ("$a -and $b -and $c -or $d", 5),
    ("$a -xor $b", 3),
])
def test_a_logical_operator_opens_no_nesting_level(condition, ccn):
    """An operator is no structure, so one `if` reads nesting 1 whatever its
    condition holds (Sonar Cognitive Complexity v1.7, App. B2), and each
    operator is one decision. `-and` and `-or` were loop words to lizard's
    ND column, so `$a -and $b -or $c` read nesting 3; spelled `&&` and `||`
    it read 2, the first operator in a condition adding a level."""
    code = (f"function Test-Logic($a, $b, $c, $d) {{\n    if ({condition}) {{\n"
            "        return 1\n    }\n    return 0\n}\n")
    (record,) = analyze_source("probe.ps1", code)

    assert (record.nesting, record.ccn_std) == (1, ccn)


def test_a_six_branch_elseif_chain_gets_the_sonar_cognitive_score():
    """if +1, five elseif +1 each, else +1. Zero here would mean the cognitive
    extension never learned `elseif`, and 2 would mean it read the chain as one
    if and one else."""
    (record,) = analyze_source("probe.ps1", P1_CHAIN)

    assert record.cognitive == 7


def test_the_powershell_connectives_score_one_cognitive_point_per_run():
    """`$a -and $b -and $c -or $d`: if +1, the `-and` run +1, the `-or` run +1."""
    (record,) = analyze_source("probe.ps1", P4_LOGIC)

    assert record.cognitive == 3


def test_powershell_takes_the_standard_chain_with_cognitive_at_index_zero():
    """PowerShellReader does not inherit the Swift preprocessor that drains the
    token stream ahead of index 0, so a 0 here would mean it needs the second
    chain analyze.py keeps for Swift and Kotlin."""
    (record,) = analyze_source("probe.ps1", P3_LOOPS)

    assert (record.cognitive, record.ccn) == (5, P3_CCN)


# --- real scripts from the consumer repo ---------------------------------------
#
# Read, counted by hand, and asserted here. They are not vendored: crapkit is
# public and these are not its files, so the assertions run where the checkout
# exists and skip where it does not. The corpus is 106 tracked .ps1/.psm1 files
# in which this reader reports 227 functions; 70 of the files declare none, being
# top-level scripts.
#
# These assert against files this repo does not own. When one of them is edited
# upstream the right response is to re-read it and update the numbers here, not
# to loosen the assertion: an exact count read off a real script is what caught
# the `[switch]` collision above.

CONSUMER_SCRIPTS = pathlib.Path(r"C:\Users\jfgag\openclaw\scripts")

needs_consumer_repo = pytest.mark.skipif(
    not CONSUMER_SCRIPTS.is_dir(), reason="consumer repo checkout not present")


def _real(name):
    analyzer = lizard.FileAnalyzer(lizard.get_extensions([]))
    return analyzer(str(CONSUMER_SCRIPTS / name)).function_list


@needs_consumer_repo
def test_ps_common_reports_its_nine_helpers_with_hand_counted_ccn():
    """_ps_common.ps1, 372 lines. Every helper carries a `<# .SYNOPSIS #>` block
    whose text holds `if (-not $mx) { exit 0 }` and `try { ... } finally { ... }`
    at lines 61-62: read as code, that brace pair ends Acquire-Mutex early.

    Acquire-Mutex: base 1 + three try/catch + if(82) = 5.
    Release-Mutex: base 1 + if(95) + two try/catch = 4.
    Write-RotatingLog: base 1 + if(130) + -and(130) + if(135) + -and(135)
        + if(138) + for(140) + if(143) = 8.
    Invoke-WithRetry: base 1 + while(180) + catch(184) + if(186) + if(190) = 5.
    Send-TelegramAlert: base 1 + if(230) + -or(230) + if(233) + if(237)
        + catch(242) + if(241) + two switch arms(246,247) + if(252) + if(255)
        + catch(282) + catch(280) + if(275) = 14.
    Get-CircuitBreakerState: base 1 + if(307) + catch(318) + foreach(311)
        + if(312) + if(313) = 6.
    Set-CircuitBreakerState: base 1 + catch(356) = 2.
    """
    assert [(f.name, f.cyclomatic_complexity) for f in _real("_ps_common.ps1")] == [
        ("Acquire-Mutex", 5), ("Release-Mutex", 4), ("Write-RotatingLog", 8),
        ("Invoke-WithRetry", 5), ("Send-TelegramAlert", 14), ("_Get-CBPath", 1),
        ("Get-CircuitBreakerState", 6), ("Set-CircuitBreakerState", 2),
        ("Test-CircuitBreakerTrip", 1)]


@needs_consumer_repo
def test_send_telegram_alert_pays_for_its_switch_arms_and_nothing_for_default():
    """Lines 245-249 are a three-line switch with two value arms and a `default`.
    Counted as one keyword this function reads 13, and the two arms it dispatches
    on disappear."""
    by_name = {f.name: f for f in _real("_ps_common.ps1")}
    alert = by_name["Send-TelegramAlert"]

    assert (alert.cyclomatic_complexity, alert.start_line, alert.end_line) == (14, 201, 286)


@needs_consumer_repo
def test_ensure_db_port_reports_the_one_helper_and_not_its_150_lines_of_script():
    """189 lines, one `function` at 42, and everything else is top-level code
    inside a `try { } finally { }`. `*global*` owns the rest, exactly like Python
    module level. Write-CBLog itself branches nowhere: base 1."""
    assert [(f.name, f.cyclomatic_complexity, f.start_line, f.end_line)
            for f in _real("ensure-db-port.ps1")] == [("Write-CBLog", 1, 42, 49)]


@needs_consumer_repo
def test_weekly_docker_prune_reports_three_functions_around_its_script_blocks():
    """infra/weekly-docker-prune.ps1: three declarations, then three
    `Invoke-Prune -Cmd { docker ... }` calls at top level whose script-block
    braces belong to no function. `ForEach-Object` at line 59 is one Verb-Noun
    token, not the `foreach` keyword, so none of the three branches."""
    assert [(f.name, f.cyclomatic_complexity) for f in
            _real(str(pathlib.Path("infra") / "weekly-docker-prune.ps1"))] == [
        ("Log", 1), ("Write-PruneAudit", 1), ("Invoke-Prune", 1)]
