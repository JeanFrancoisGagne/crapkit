function Get-Kind($n) {
    switch ($n) {
        1 { "one" }
        2 { "two" }
        default { "many" }
    }
}

function Get-Size($n) {
    switch ($n) {
        { $_ -gt 5 } { "big" }
        { $_ -lt 0 } { "negative" }
        default { "small" }
    }
}

function Test-ParamBlock {
    param(
        [string]$Name,
        [int]$Count
    )
    return $Name
}

function Test-Logic($a, $b, $c) {
    if ($a -and $b -or $c) {
        return 1
    }
    return 0
}

function Test-Loops($xs) {
    foreach ($x in $xs) {
        while ($x -gt 0) {
            $x--
        }
    }
    do {
        $xs = $null
    } until ($xs -eq $null)
    try {
        throw "x"
    } catch {
        return 1
    } finally {
        $null = 1
    }
}

function Test-Xor($a, $b) {
    return $a -xor $b
}

function Test-UpperCase($a) {
    IF ($a) {
        RETURN 1
    }
    return 0
}
