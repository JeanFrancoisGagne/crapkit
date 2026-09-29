function Get-Matching($xs) {
    return $xs | ? { $_ }
}

function Get-Doubled($xs) {
    return $xs | ForEach-Object { $_ * 2 }
}

function Get-Positive($xs) {
    return $xs | ForEach-Object { if ($_ -gt 0) { $_ } }
}

function Get-MatchingIf($xs) {
    if ($xs) {
        return $xs | ? { $_ }
    }
    return $null
}

function Invoke-Trapped($x) {
    trap { continue }
    return $x
}

function Get-Factorial($n) {
    if ($n -le 1) {
        return 1
    }
    return $n * (Get-Factorial ($n - 1))
}
