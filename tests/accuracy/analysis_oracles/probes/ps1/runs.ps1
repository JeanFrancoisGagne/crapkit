function Test-NotRun($a, $b, $c) {
    if (-not ($a -and $b) -and $c) {
        return 1
    }
    return 0
}

function Test-SplitRun($a, $b, $c) {
    if ($a -and
        $b -and $c) {
        return 1
    }
    return 0
}

function Test-ParenRun($a, $b, $c) {
    if (($a -and $b) -and $c) {
        return 1
    }
    return 0
}
