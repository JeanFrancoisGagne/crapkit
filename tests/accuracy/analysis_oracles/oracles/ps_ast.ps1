using namespace System.Management.Automation.Language
<#
.SYNOPSIS
    PowerShell functions and their McCabe numbers, read off the PowerShell Parser AST.

.DESCRIPTION
    One process reads a list of files (one path per line, UTF-8, relative to -Root),
    parses each with [System.Management.Automation.Language.Parser]::ParseFile and
    writes one JSON document to -Out (UTF-8, no byte order mark):

        {"host": {"edition": "Desktop"|"Core", "version": "5.1.x"|"7.x"},
         "files": [{"path": ..., "errors": [{"line": n, "id": ...}],
                    "stray_lines": [n, ...], "functions": [...]}]}

    A function is each FunctionDefinitionAst that is not a class member's body
    (PowerShell wraps a method body in one): `function`, `filter` and, in Windows
    PowerShell, `workflow`. For each: its name, kind and declaring keyword as
    written, its start and end line (the definition's extent, from the keyword to
    the closing brace), the parameter names of its header list and of its param()
    block, and the counts of the nodes it owns. A node belongs to the nearest
    function around it; a node inside a nested function or a class member belongs
    to that one instead.

    McCabe, NIST SP 500-235 (McCabe and Watson 1996) sec. 4.1: v(G) = the number of
    binary decisions + 1. Each decision node below is one binary branch of the flow
    graph, and nothing else adds to v(G):

      if_clauses   each if and elseif clause: its condition is true or not; `else`
                   tests nothing (IfStatementAst.Clauses)
      loops        each for, foreach, while, do-while and do-until: the loop runs
                   again or exits
      switch_arms  each switch clause: its test matches or not; the default arm
                   runs when none did and tests nothing (SwitchStatementAst.Clauses)
      catches      each catch clause: its handler runs or not
      traps        each trap statement: the same branch as a catch
      and_or       each -and and -or: they short-circuit, so the right operand runs
                   or not (about_Logical_Operators)
      chains       each && and || between pipelines (PowerShell 7): the right
                   pipeline runs or not (about_Pipeline_Chain_Operators)
      ternaries    each ?: (PowerShell 7): one of two operands runs
      coalesces    each ?? and ??= (PowerShell 7): the right side runs only when
                   the left is null (about_Operators)

    ccn_std is 1 + all of them. ccn_mod counts each switch that has a clause as one
    decision in place of its clauses (lizard 1.24.0 README, option -m).

    Counts that are no decision, listed so a transform or a set-aside can name them:
      switches          switch statements with at least one clause
      xor               each -xor: it evaluates both operands (about_Logical_Operators)
      flow_commands     each ForEach-Object, Where-Object, foreach, where, % and ?
                        command: a call, not a branch of this function's flow graph
      question_commands each ? command (the Where-Object alias)
      question_cost     for each ? command, 1 + the if, switch, loop, catch, trap and
                        ternary bodies around it, summed: its score read as a ternary
      script_block_arms each switch clause whose test is a script block
      bracket_subjects  each switch whose flags or subject hold a `]` or `;` token
      switch_types      each `switch` type name, as in [switch]$Force
      classes           each class or enum declared inside the function
      stray_keywords    each lower-case function, filter, workflow or configuration
                        word that declares nothing (`--configuration`, `$o.filter`)
      upper_decisions   each if, elseif, for, foreach, while, until, catch, trap or
                        switch keyword written with a capital (about_Language_Keywords:
                        keywords are not case-sensitive)
      upper_keywords    each else, do, try or finally keyword written with a capital
      upper_operators   each -and, -or or -xor written with a capital (the parser reads
                        -Or as the operator -or)
      upper_default     each switch whose default arm is written other than `default`
      nested_depth      for each if, switch, loop, catch, trap, ternary, ?? and ??=,
                        the script blocks between it and its function, summed
      in_conditions     each if, switch, loop, catch, trap, ternary, ?? or ??= that
                        sits in the condition of an if, loop, switch or ternary
                        rather than in its body
      split_runs        each -and or -or whose right operand starts on a later line
                        than its left operand ends
      paren_runs        each -and or -or in parentheses whose group is an operand of
                        the same operator: `($a -and $b) -and $c`
      negated_runs      the same with a -not or ! before the group:
                        `-not ($a -and $b) -and $c`
      self_calls        each command named as the function itself (a direct
                        recursive call)
#>
param(
    [Parameter(Mandatory = $true)] [string] $Root,
    [Parameter(Mandatory = $true)] [string] $List,
    [Parameter(Mandatory = $true)] [string] $Out
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'

$Decisions = 'if_clauses', 'loops', 'switch_arms', 'catches', 'traps', 'and_or', 'chains',
    'ternaries', 'coalesces'
$Others = 'switches', 'xor', 'flow_commands', 'question_commands', 'question_cost',
    'script_block_arms', 'bracket_subjects', 'switch_types', 'classes', 'stray_keywords',
    'upper_decisions', 'upper_keywords', 'upper_operators', 'upper_default', 'nested_depth',
    'in_conditions', 'split_runs', 'paren_runs', 'negated_runs', 'self_calls'
$Loops = 'ForStatementAst', 'ForEachStatementAst', 'WhileStatementAst', 'DoWhileStatementAst',
    'DoUntilStatementAst'
$Nesting = $Loops + 'IfStatementAst', 'SwitchStatementAst', 'CatchClauseAst', 'TrapStatementAst',
    'TernaryExpressionAst'
$FlowCommands = 'ForEach-Object', 'foreach', '%', 'Where-Object', 'where', '?'
$UpperDecisions = 'If', 'ElseIf', 'For', 'Foreach', 'While', 'Until', 'Catch', 'Trap', 'Switch'
$UpperKeywords = 'Else', 'Do', 'Try', 'Finally'
$UpperOperators = 'And', 'Or', 'Xor'
$Declaring = 'Function', 'Filter', 'Workflow', 'Configuration'
$Unread = 'StringLiteral', 'StringExpandable', 'HereStringLiteral', 'HereStringExpandable', 'Comment'
$StrayWord = '(?:^|(?<=--)|(?<=[^\w$-]))(?:function|filter|workflow|configuration)(?![\w-])'

function Get-Owner($Node) {
    # The function a node belongs to and the script blocks between them: the nearest
    # FunctionDefinitionAst around it, or $null for script-level code and for a class
    # member's body.
    $depth = 0
    $p = $Node.Parent
    while ($null -ne $p -and -not ($p -is [FunctionDefinitionAst])) {
        if ($p -is [ScriptBlockExpressionAst]) { $depth += 1 }
        $p = $p.Parent
    }
    if ($null -ne $p -and $p.Parent -is [FunctionMemberAst]) { $p = $null }
    return @{ Function = $p; Depth = $depth }
}

function New-Counts {
    $counts = [ordered]@{}
    foreach ($name in ($Decisions + $Others)) { $counts[$name] = 0 }
    return $counts
}

function Get-OperatorKey($Node) {
    # A binary operator by name: `QuestionQuestion` exists only in PowerShell 7's TokenKind.
    switch ($Node.Operator.ToString()) {
        'And' { return 'and_or' }
        'Or' { return 'and_or' }
        'Xor' { return 'xor' }
        'QuestionQuestion' { return 'coalesces' }
    }
    return $null
}

function Test-Bracketed($Node) {
    # A `]` or `;` token from the switch keyword to the end of its subject.
    $last = $Node.Condition.Extent.EndOffset
    foreach ($offset in $script:Closers) {
        if ($offset -ge $Node.Extent.StartOffset -and $offset -lt $last) { return $true }
    }
    return $false
}

function Test-UpperDefault($Node) {
    # The word before a switch's default block, written other than `default`.
    if ($null -eq $Node.Default) { return $false }
    $before = $script:Text.Substring(0, $Node.Default.Extent.StartOffset)
    $word = [regex]::Match($before, '([A-Za-z]+)\s*$').Groups[1].Value
    return $word -cne 'default'
}

function Add-Switch($Counts, $Node) {
    # A switch adds its clauses, and its script-block tests, bracketed subject and
    # capitalized default on the side.
    $Counts.switch_arms += $Node.Clauses.Count
    if ($Node.Clauses.Count -gt 0) { $Counts.switches += 1 }
    foreach ($clause in $Node.Clauses) {
        if ($clause.Item1 -is [ScriptBlockExpressionAst]) { $Counts.script_block_arms += 1 }
    }
    if (Test-Bracketed $Node) { $Counts.bracket_subjects += 1 }
    if (Test-UpperDefault $Node) { $Counts.upper_default += 1 }
}

function Get-NodeKey($Node) {
    # Which count a node adds 1 to; $null when it adds to none (if and switch add more).
    $type = $Node.GetType().Name
    if ($Loops -contains $type) { return 'loops' }
    if ($type -eq 'BinaryExpressionAst') { return Get-OperatorKey $Node }
    if ($type -eq 'AssignmentStatementAst' -and $Node.Operator.ToString() -eq 'QuestionQuestionEquals') { return 'coalesces' }
    $keys = @{ CatchClauseAst = 'catches'; TrapStatementAst = 'traps'; PipelineChainAst = 'chains'
        TernaryExpressionAst = 'ternaries'; TypeDefinitionAst = 'classes' }
    if ($keys.ContainsKey($type)) { return $keys[$type] }
    return $null
}

function Add-Question($Counts, $Node) {
    $Counts.question_commands += 1
    $Counts.question_cost += 1 + (Get-BodyDepth $Node)
}

function Add-Command($Counts, $Node, $Function) {
    $name = $Node.GetCommandName()
    if ($FlowCommands -contains $name) { $Counts.flow_commands += 1 }
    if ($name -eq '?') { Add-Question $Counts $Node }
    if ($name -and $name -eq $Function.Name) { $Counts.self_calls += 1 }
}

function Test-Scored($Node) {
    # The nodes PSComplexity scores 1 + nesting, the flow commands left out.
    if ($Nesting -contains $Node.GetType().Name) { return $true }
    return (Get-NodeKey $Node) -eq 'coalesces'
}

function Get-ConditionParts($Node) {
    # The children of a statement that decide, as against the ones it runs.
    switch ($Node.GetType().Name) {
        'IfStatementAst' { return @($Node.Clauses | ForEach-Object { $_.Item1 }) }
        'SwitchStatementAst' { return @($Node.Condition) + @($Node.Clauses | ForEach-Object { $_.Item1 }) }
        'ForStatementAst' { return @($Node.Initializer, $Node.Condition, $Node.Iterator) }
        'ForEachStatementAst' { return @($Node.Variable, $Node.Condition) }
    }
    if ($Nesting -contains $Node.GetType().Name -and $Node.PSObject.Properties['Condition']) { return @($Node.Condition) }
    return @()
}

function Test-ConditionChild($Parent, $Child) {
    foreach ($part in (Get-ConditionParts $Parent)) {
        if ([object]::ReferenceEquals($part, $Child)) { return $true }
    }
    return $false
}

function Test-InCondition($Node) {
    # Whether the node sits in the condition of a statement between it and its function.
    $child = $Node
    for ($p = $Node.Parent; $null -ne $p -and -not ($p -is [FunctionDefinitionAst]); $p = $p.Parent) {
        if (Test-ConditionChild $p $child) { return $true }
        $child = $p
    }
    return $false
}

function Get-BodyDepth($Node) {
    # The if, switch, loop, catch, trap and ternary bodies around a node, up to its function.
    $depth = 0
    $child = $Node
    for ($p = $Node.Parent; $null -ne $p -and -not ($p -is [FunctionDefinitionAst]); $p = $p.Parent) {
        if ($Nesting -contains $p.GetType().Name -and -not (Test-ConditionChild $p $child)) { $depth += 1 }
        $child = $p
    }
    return $depth
}

function Test-Logical($Node) {
    return $Node -is [BinaryExpressionAst] -and ('And', 'Or') -contains $Node.Operator.ToString()
}

function Test-Wrapper($Node) {
    # Nodes between a parenthesized group and the operator it is an operand of.
    if ($Node -is [UnaryExpressionAst]) { return ('Not', 'Exclaim') -contains $Node.TokenKind.ToString() }
    return $Node -is [CommandExpressionAst] -or $Node -is [PipelineAst] -or $Node -is [ParenExpressionAst]
}

function Get-GroupKey($Node) {
    # paren_runs or negated_runs when the node's parenthesized group is an operand of its
    # own operator; $null otherwise.
    $seen = @()
    $p = $Node.Parent
    while (Test-Wrapper $p) { $seen += $p.GetType().Name; $p = $p.Parent }
    if ($seen -notcontains 'ParenExpressionAst' -or -not (Test-Logical $p)) { return $null }
    if ($p.Operator -ne $Node.Operator) { return $null }
    if ($seen -contains 'UnaryExpressionAst') { return 'negated_runs' }
    return 'paren_runs'
}

function Add-Run($Counts, $Node) {
    if ($Node.Left.Extent.EndLineNumber -lt $Node.Right.Extent.StartLineNumber) { $Counts.split_runs += 1 }
    $key = Get-GroupKey $Node
    if ($null -ne $key) { $Counts[$key] += 1 }
}

function Add-Scored($Counts, $Node, $Depth) {
    $Counts.nested_depth += $Depth
    if (Test-InCondition $Node) { $Counts.in_conditions += 1 }
}

function Add-Node($Counts, $Node, $Owner) {
    if ($Node -is [IfStatementAst]) { $Counts.if_clauses += $Node.Clauses.Count }
    elseif ($Node -is [SwitchStatementAst]) { Add-Switch $Counts $Node }
    elseif ($Node -is [CommandAst]) { Add-Command $Counts $Node $Owner.Function }
    else {
        $key = Get-NodeKey $Node
        if ($null -ne $key) { $Counts[$key] += 1 }
    }
    if (Test-Logical $Node) { Add-Run $Counts $Node }
    if (Test-Scored $Node) { Add-Scored $Counts $Node $Owner.Depth }
}

function Get-ParameterNames($Parameters) {
    $names = [System.Collections.Generic.List[string]]::new()
    if ($null -eq $Parameters) { return , $names }
    foreach ($parameter in $Parameters) { $names.Add($parameter.Name.VariablePath.UserPath) }
    return , $names
}

function Get-BlockParameters($Function) {
    $block = $Function.Body.ParamBlock
    if ($null -eq $block) { return Get-ParameterNames $null }
    return Get-ParameterNames $block.Parameters
}

function Get-Kind($Function) {
    if ($Function.IsWorkflow) { return 'workflow' }
    if ($Function.IsFilter) { return 'filter' }
    return 'function'
}

function Get-Numbers($Counts) {
    # ccn_std and ccn_mod from the decision counts (see the header).
    $decided = 0
    foreach ($name in $Decisions) { $decided += $Counts[$name] }
    $std = 1 + $decided
    return @{ ccn_std = $std; ccn_mod = $std - $Counts.switch_arms + $Counts.switches }
}

function Get-Record($Function, $Counts) {
    $numbers = Get-Numbers $Counts
    return [ordered]@{
        name          = $Function.Name
        kind          = Get-Kind $Function
        keyword       = ($Function.Extent.Text -split '\s+', 2)[0]
        start         = $Function.Extent.StartLineNumber
        end           = $Function.Extent.EndLineNumber
        header_params = Get-ParameterNames $Function.Parameters
        block_params  = Get-BlockParameters $Function
        ccn_std       = $numbers.ccn_std
        ccn_mod       = $numbers.ccn_mod
        counts        = $Counts
    }
}

function Get-InnermostAt($Functions, $Offset) {
    # Functions come in document order, so the last one holding the offset is the innermost.
    $found = $null
    foreach ($function in $Functions) {
        $inside = $function.Extent.StartOffset -le $Offset -and $Offset -lt $function.Extent.EndOffset
        if ($inside) { $found = $function }
    }
    return $found
}

function Get-TokenKey($Token) {
    # Which count a token adds 1 to, or $null.
    $kind = $Token.Kind.ToString()
    $upper = $Token.Text -cne $Token.Text.ToLowerInvariant()
    if ($upper -and $UpperDecisions -contains $kind) { return 'upper_decisions' }
    if ($upper -and $UpperKeywords -contains $kind) { return 'upper_keywords' }
    if ($upper -and $UpperOperators -contains $kind) { return 'upper_operators' }
    if ($Token.TokenFlags.ToString() -match 'TypeName' -and $Token.Text -ceq 'switch') { return 'switch_types' }
    if ($Unread -notcontains $kind -and $Declaring -notcontains $kind -and $Token.Text -cmatch $StrayWord) { return 'stray_keywords' }
    return $null
}

function Add-Tokens($Table, $Tokens, $Functions, $Stray) {
    foreach ($token in $Tokens) {
        $key = Get-TokenKey $token
        if ($null -eq $key) { continue }
        if ($key -eq 'stray_keywords') { $Stray.Add($token.Extent.StartLineNumber) }
        $owner = Get-InnermostAt $Functions $token.Extent.StartOffset
        if ($null -ne $owner) { $Table[$owner][$key] += 1 }
    }
}

function Read-File($Path) {
    $tokens = $null
    $errors = $null
    $ast = [Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    $script:Text = $ast.Extent.Text
    $script:Closers = @($tokens | Where-Object { $_.Kind -eq 'RBracket' -or $_.Kind -eq 'Semi' } | ForEach-Object { $_.Extent.StartOffset })
    $functions = @($ast.FindAll({ param($n) $n -is [FunctionDefinitionAst] -and -not ($n.Parent -is [FunctionMemberAst]) }, $true))
    $table = @{}
    foreach ($function in $functions) { $table[$function] = New-Counts }
    foreach ($node in $ast.FindAll({ $true }, $true)) {
        $owner = Get-Owner $node
        if ($null -ne $owner.Function) { Add-Node $table[$owner.Function] $node $owner }
    }
    $stray = [System.Collections.Generic.List[int]]::new()
    Add-Tokens $table $tokens $functions $stray
    return @{ Functions = $functions; Table = $table; Errors = $errors; Stray = $stray }
}

function Get-FileRecord($Relative) {
    $read = Read-File ([System.IO.Path]::Combine($Root, $Relative))
    $records = [System.Collections.Generic.List[object]]::new()
    foreach ($function in $read.Functions) { $records.Add((Get-Record $function $read.Table[$function])) }
    $errors = [System.Collections.Generic.List[object]]::new()
    foreach ($e in $read.Errors) { $errors.Add([ordered]@{ line = $e.Extent.StartLineNumber; id = $e.ErrorId }) }
    return [ordered]@{ path = $Relative; errors = $errors; stray_lines = $read.Stray; functions = $records }
}

$files = [System.Collections.Generic.List[object]]::new()
foreach ($line in [System.IO.File]::ReadAllLines($List, [System.Text.Encoding]::UTF8)) {
    if ($line) { $files.Add((Get-FileRecord $line)) }
}
$document = [ordered]@{
    host  = [ordered]@{ edition = $PSVersionTable.PSEdition; version = $PSVersionTable.PSVersion.ToString() }
    files = $files
}
$json = ConvertTo-Json -InputObject $document -Depth 8 -Compress
[System.IO.File]::WriteAllText($Out, $json, (New-Object System.Text.UTF8Encoding($false)))
