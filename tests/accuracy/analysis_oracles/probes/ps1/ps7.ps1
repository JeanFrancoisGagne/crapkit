function Test-Coalesce($a) {
    return $a ?? 0
}

function Test-Chain($p) {
    Get-Item $p && Write-Output 'found'
    Get-Item $p || Write-Output 'missing'
}

function Test-Ternary($x) {
    return $x ? 1 : 0
}

function Test-TernaryCondition($x, $a) {
    if ($x ? $a : 0) {
        return 1
    }
    return 0
}
