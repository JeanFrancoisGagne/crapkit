function Test-SwitchParam([switch]$Force) {
    if ($Force) {
        return 1
    }
    return 0
}

function Get-Indexed($m) {
    switch ($m['k']) {
        'a' { return 1 }
        'b' { return 2 }
    }
    return 0
}

function Get-CapitalDefault($n) {
    switch ($n) {
        1 { return 'one' }
        Default { return 'many' }
    }
}

function Test-CapitalOr($a, $b) {
    if ($a -Or $b) {
        return 1
    }
    return 0
}
