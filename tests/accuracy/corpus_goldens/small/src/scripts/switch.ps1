# PowerShell: a switch whose arm condition is a script block.
function Get-Size {
    param([int]$Bytes)
    switch ($Bytes) {
        { $_ -ge 1GB } { return "GB" }
        { $_ -ge 1MB } { return "MB" }
        { $_ -ge 1KB } { return "KB" }
        default { return "B" }
    }
}

function Test-Flag([string]$Name, [switch]$Strict) {
    if ($Strict -and -not $Name) {
        throw "a name is required"
    }
    return [bool]$Name
}
