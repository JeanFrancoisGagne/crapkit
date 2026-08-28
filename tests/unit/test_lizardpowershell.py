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
    assert lizard_languages.get_reader_for("a.py").__name__ == "PythonReader"
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


def test_the_modified_column_does_not_refund_the_switch_arms():
    """crapkit takes min(ccn_std, ccn_mod). lizard's modified rule adds a point
    for a `switch` opener and subtracts one per arm only when the reader claims
    `case` as a keyword; this reader does not, so the arms are never refunded and
    the minimum stays the standard column."""
    (record,) = analyze_source("probe.ps1", P2_SWITCH)

    assert (record.ccn, record.ccn_std, record.ccn_mod) == (P2_CCN, P2_CCN, P2_CCN + 1)


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
